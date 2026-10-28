"""Block-local CSE of array element addresses, and chained constant stores.

See docs/addr_cse.md.  The pass runs on the AST of each function before
the frame is sized.  It finds the element address of ``a[i]``, ``a[i+1]``,
``p[i]``, ``s.arr[i]``, ``p->arr[i]`` and ``a[i][j]`` that the function
computes more than once on a straight run of statements, and it computes
the address once into a new local pointer::

    __acse0 = &a[i];  ...  *__acse0  ...  __acse0[1]  ...

The new local is an ordinary pointer local.  The register-locals pass
(BC, DE and IY) can move it into a register.

An address depends only on the variables in it.  A key is therefore made
of local variables that nothing can change but an assignment in the
function itself: an integer or pointer local, not volatile, with no ``&``
on its name.  A store through a pointer, a call and an interrupt cannot
change such a variable, so these do not end a run.  A run of statements
ends at an assignment to a variable of the key (anywhere inside a
statement), at a declaration of such a variable and at a label.

The second part of the file turns ``a = b = K`` with a constant K that
fits every target into ``b = K; a = K;``.
"""

from __future__ import annotations

import copy
import dataclasses
import heapq
import os
import sys

from uc_core import ast
from uc_core import ast_legacy as lt
from uc_core._const import make_int_lit

LOOP_W = float(os.environ.get("UC80_ACSE_LOOP_W", 8.0))
# the weight of a use that a branch may skip
BRANCH_W = float(os.environ.get("UC80_ACSE_BRANCH_W", 0.5))
NEED_CERT = int(os.environ.get("UC80_ACSE_NEED_CERT", 1))
_ASSIGN = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")
_ARITH = ("+", "-", "*", "<<", ">>", "&", "|", "^")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _is_node(x) -> bool:
    return (dataclasses.is_dataclass(x) and not isinstance(x, type)
            and type(x).__module__ != lt.__name__)


def _fields(node) -> tuple:
    cls = type(node)
    names = _FIELD_CACHE.get(cls)
    if names is None:
        names = tuple(f.name for f in dataclasses.fields(node)
                      if f.name not in ("pos", "location"))
        _FIELD_CACHE[cls] = names
    return names


_FIELD_CACHE: dict = {}


def _tok(text: str, kind: str = "IDENT"):
    from uc_core.c23_parser import Token
    return Token(name=kind, text=text, line=0, column=0, offset=0, file_id=0)


def _ident(name: str):
    return ast.Identifier(name=_tok(name))


def _int(k: int):
    if k < 0:
        return ast.UnaryOp(op="-", operand=make_int_lit(-k))
    return make_int_lit(k)


class _Var:
    __slots__ = ("uid", "name", "scope", "auto", "type")

    def __init__(self, uid, name, scope, auto, type_):
        self.uid, self.name, self.scope = uid, name, scope
        self.auto, self.type = auto, type_


class _Info:
    """What one statement of a block does, anywhere inside."""
    __slots__ = ("writes", "labels", "mem_w", "any_w", "call")

    def __init__(self):
        self.writes: set = set()      # uids of local variables assigned
        self.labels = False
        self.mem_w: set = set()       # roots (named objects) stored into
        self.any_w = False            # a store through a pointer
        self.call = False


class _Occ:
    __slots__ = ("node", "cont", "slot", "key", "off", "base", "core",
                 "leaves", "mem", "frames", "w", "elem", "size", "cost",
                 "depth", "dead", "bd")


class _Run:
    __slots__ = ("block", "occs", "gain", "key", "stamp")


class _Fn:
    """One function: the scoped walk, the runs and the rewrite."""

    def __init__(self, gen, func, stats):
        from .reglocals_gen import address_taken_names
        self.g = gen
        self.func = func
        self.stats = stats
        self.addr_taken = address_taken_names(func.body)
        self.vol_names = gen._vol.names_for(func)
        self.uid = 0
        self.next_scope = 1
        self.chain = [0]
        self.vars: dict = {}
        self.frames: list = []        # (block, item, weight at the block)
        self.info: dict = {}          # id(item) -> _Info
        self.occs: list = []
        self.weight = 1.0
        self.switch_base: list = []
        self.block_chain: dict = {}
        self.noreg = 0
        self.bdepth = 0               # branch constructs around the walk
        self.skip_node = None
        self.anc: list = []           # ids of the registered Index ancestors
        self.mem_ok = (not gen.isr_funcs
                       and not os.environ.get("UC80_ACSE_NO_MEM"))
        self.vol_cache: dict = {}
        self.temps: list = []         # (name, block, decl) of each pointer
        self.min_gain = _env_float("UC80_ACSE_MIN_GAIN", 12.0)
        self.debug = bool(os.environ.get("UC80_ACSE_DEBUG"))

    # -- scope helpers --------------------------------------------------

    def new_var(self, name, type_, auto=True):
        self.uid += 1
        v = _Var(self.uid, name, self.chain[-1], auto, type_)
        self.vars[name] = v
        return v

    def mark_write(self, v) -> None:
        for _b, item, _w, _d in self.frames:
            self.info_for(item).writes.add(v.uid)

    def mark_mem(self, root) -> None:
        for _b, item, _w, _d in self.frames:
            inf = self.info_for(item)
            if root is None:
                inf.any_w = True
            else:
                inf.mem_w.add(root)

    def mark_call(self) -> None:
        for _b, item, _w, _d in self.frames:
            self.info_for(item).call = True

    def root_of(self, e):
        """The named object a store to the lvalue ``e`` changes, or None
        when ``e`` is reached through a pointer."""
        g = self.g
        if isinstance(e, ast.Identifier):
            v = self.vars.get(e.name.text)
            return f"v{v.uid}" if v is not None else f"g:{e.name.text}"
        if isinstance(e, ast.Index):
            if isinstance(g._get_expr_type(e.array), lt.ArrayType):
                return self.root_of(e.array)
            return None
        if isinstance(e, ast.Member):
            return self.root_of(e.obj)
        return None

    def note_store(self, lv) -> None:
        v = self.lookup_var(lv)
        if (v is not None and v.auto and v.name not in self.addr_taken
                and not isinstance(v.type, (lt.ArrayType, lt.StructType))):
            self.mark_write(v)
            return
        try:
            root = self.root_of(lv)
        except Exception:
            root = None
        self.mark_mem(root)

    def info_for(self, item) -> _Info:
        i = self.info.get(id(item))
        if i is None:
            i = self.info[id(item)] = _Info()
        return i

    def mark_labels(self, frames) -> None:
        for _b, item, _w, _d in frames:
            self.info_for(item).labels = True

    # -- the walk -------------------------------------------------------

    def run(self) -> None:
        from . import codegen as cg
        g = self.g
        func = self.func
        for p in cg.function_params(func):
            if not isinstance(p, ast.ParamDecl):
                continue
            pn = cg.declarator_ident(p.declarator)
            try:
                pn, pt = cg.resolve_type_from_decl(p.decl_specs, p.declarator)
                t = cg._to_legacy(pt)
                if isinstance(t, lt.ArrayType):
                    t = lt.PointerType(base_type=t.base_type)
            except Exception:
                t = None        # a name of unknown type still hides a global
            if pn:
                self.new_var(pn, t)
                g.ctx.locals[pn] = cg.Symbol(name=pn, sym_type=t, is_param=True)
        self.visit(func.body, None, None)
        self.select_and_apply()
        self.prune_temps()

    def visit(self, n, cont, slot) -> None:
        if isinstance(n, list):
            for i, x in enumerate(n):
                self.visit(x, n, i)
            return
        if isinstance(n, tuple):
            for x in n:
                self.visit(x, None, None)
            return
        if not _is_node(n):
            return
        h = _HANDLERS.get(type(n).__name__)
        if h is not None:
            h(self, n, cont, slot)
        else:
            self.children(n)

    def children(self, n) -> None:
        for f in _fields(n):
            v = getattr(n, f, None)
            if v is None:
                continue
            if isinstance(v, list):
                self.visit(v, None, None)
            elif isinstance(v, tuple):
                for x in v:
                    self.visit(x, None, None)
            elif _is_node(v):
                self.visit(v, n, f)

    def weighted(self, w, n, cont=None, slot=None) -> None:
        """Visit a part that a branch may skip (w < 1) or a loop repeats."""
        old = self.weight
        self.weight = old * w
        if w < 1:
            self.bdepth += 1
        try:
            self.visit(n, cont, slot)
        finally:
            self.weight = old
            if w < 1:
                self.bdepth -= 1

    # statements

    def h_compound(self, n, cont, slot) -> None:
        g = self.g
        saved_locals = dict(g.ctx.locals)
        saved_vars = dict(self.vars)
        self.chain.append(self.next_scope)
        self.next_scope += 1
        self.block_chain[id(n)] = frozenset(self.chain)
        try:
            i = 0
            while i < len(n.items):
                item = n.items[i]
                if isinstance(item, ast.ExpressionStmt) and self.chain_ok():
                    new = self.try_chain(item)
                    if new is not None:
                        n.items[i:i + 1] = new
                        item = n.items[i]
                self.frames.append((n, item, self.weight, self.bdepth))
                try:
                    self.visit(item, n.items, i)
                finally:
                    self.frames.pop()
                i += 1
        finally:
            self.chain.pop()
            self.vars = saved_vars
            g.ctx.locals = saved_locals

    def h_for(self, n, cont, slot) -> None:
        g = self.g
        saved_locals = dict(g.ctx.locals)
        saved_vars = dict(self.vars)
        self.chain.append(self.next_scope)
        self.next_scope += 1
        try:
            if n.init is not None:
                self.visit(n.init, n, "init")
            old = self.weight
            self.weight = old * LOOP_W
            try:
                for f in ("condition", "update", "body"):
                    v = getattr(n, f, None)
                    if v is not None:
                        if f == "body":
                            self.bare_stmt(n, f)
                        else:
                            self.visit(v, n, f)
            finally:
                self.weight = old
        finally:
            self.chain.pop()
            self.vars = saved_vars
            g.ctx.locals = saved_locals

    def bare_stmt(self, n, f) -> None:
        """A statement that is not an item of a block (a loop or if body)."""
        v = getattr(n, f)
        if isinstance(v, ast.ExpressionStmt) and self.chain_ok():
            new = self.try_chain(v)
            if new is not None:
                v = ast.CompoundStmt(items=new)
                setattr(n, f, v)
        self.visit(v, n, f)

    def h_while(self, n, cont, slot) -> None:
        old = self.weight
        self.weight = old * LOOP_W
        try:
            self.visit(n.condition, n, "condition")
            self.bare_stmt(n, "body")
        finally:
            self.weight = old

    def h_dowhile(self, n, cont, slot) -> None:
        old = self.weight
        self.weight = old * LOOP_W
        try:
            self.bare_stmt(n, "body")
            self.visit(n.condition, n, "condition")
        finally:
            self.weight = old

    def h_if(self, n, cont, slot) -> None:
        self.visit(n.condition, n, "condition")
        old = self.weight
        self.weight = old * BRANCH_W
        self.bdepth += 1
        try:
            self.bare_stmt(n, "then_branch")
            if isinstance(n, ast.IfStmtElse):
                self.bare_stmt(n, "else_branch")
        finally:
            self.weight = old
            self.bdepth -= 1

    def h_switch(self, n, cont, slot) -> None:
        self.visit(n.expr, n, "expr")
        self.switch_base.append(len(self.frames))
        old = self.weight
        self.weight = old * BRANCH_W
        self.bdepth += 1
        try:
            self.visit(n.body, n, "body")
        finally:
            self.weight = old
            self.bdepth -= 1
            self.switch_base.pop()

    def h_case(self, n, cont, slot) -> None:
        base = self.switch_base[-1] if self.switch_base else 0
        self.mark_labels(self.frames[base:])
        self.children(n)

    def h_label(self, n, cont, slot) -> None:
        self.mark_labels(self.frames)
        self.children(n)

    def h_exprstmt(self, n, cont, slot) -> None:
        self.children(n)

    def h_decl(self, n, cont, slot) -> None:
        from . import codegen as cg
        g = self.g
        sc = cg.decl_storage_class(n.decl_specs)
        if sc == "typedef":
            try:
                for nm, full, _i, _f in cg.iter_var_decls(n):
                    if nm is not None:
                        g.ctx.typedefs[nm] = full
            except Exception:
                pass
            return
        try:
            decls = list(cg.iter_var_decls(n))
        except Exception:
            decls = []
        ds = [d for d in n.declarators
              if isinstance(d, (ast.InitDeclarator, ast.InitDeclaratorWithInit))]
        if len(ds) != len(decls):
            ds = [None] * len(decls)
        for d, (nm, full, init, is_fn) in zip(ds, decls):
            if nm is None:
                continue
            try:
                t = cg._to_legacy(full)
            except Exception:
                t = None
            auto = sc not in ("static", "extern") and not is_fn
            g.ctx.locals[nm] = cg.Symbol(name=nm, sym_type=t,
                                         is_static=(sc == "static"))
            v = self.new_var(nm, t, auto)
            if init is not None and sc not in ("static", "extern"):
                self.visit(init, d, "init")
            if auto:
                self.mark_write(v)
                if (init is not None
                        and isinstance(t, (lt.ArrayType, lt.StructType))):
                    self.mark_mem(f"v{v.uid}")

    # expressions

    def lookup_var(self, e):
        if isinstance(e, ast.Identifier):
            return self.vars.get(e.name.text)
        return None

    def h_binary(self, n, cont, slot) -> None:
        op = n.op
        if op in _ASSIGN:
            self.note_store(n.left)
        if op in ("&&", "||"):
            self.visit(n.left, n, "left")
            self.weighted(BRANCH_W, n.right, n, "right")
            return
        self.children(n)

    def h_ternary(self, n, cont, slot) -> None:
        self.visit(n.condition, n, "condition")
        self.weighted(BRANCH_W, n.true_expr, n, "true_expr")
        self.weighted(BRANCH_W, n.false_expr, n, "false_expr")

    def h_unary(self, n, cont, slot) -> None:
        op = n.op
        if op in ("++", "--"):
            self.note_store(n.operand)
        elif op == "&" and isinstance(n.operand, ast.Index):
            self.skip_node = id(n.operand)
        self.children(n)

    def h_postfix(self, n, cont, slot) -> None:
        if n.op in ("++", "--"):
            self.note_store(n.operand)
        self.children(n)

    def h_call(self, n, cont, slot) -> None:
        self.mark_call()
        self.children(n)

    def h_unevaluated(self, n, cont, slot) -> None:
        self.noreg += 1
        try:
            self.children(n)
        finally:
            self.noreg -= 1

    def h_index(self, n, cont, slot) -> None:
        reg = None
        if (not self.noreg and self.skip_node != id(n) and cont is not None
                and self.frames):
            try:
                reg = self.classify(n, cont, slot)
            except Exception as e:     # a type the pass does not know
                if self.debug:
                    print(f"acse: classify failed: {e!r}", file=sys.stderr)
                reg = None
        if reg is not None:
            reg.depth = len(self.anc)
            self.occs.append(reg)
            self.anc.append(id(n))
        try:
            self.children(n)
        finally:
            if reg is not None:
                self.anc.pop()

    # -- classification -------------------------------------------------

    def leaf_ok(self, v, pointer) -> bool:
        if not v.auto or v.name in self.addr_taken:
            return False
        t = v.type
        if pointer:
            return (isinstance(t, lt.PointerType)
                    and not t.is_volatile
                    and not getattr(t.base_type, "is_volatile", False))
        if not isinstance(t, (lt.BasicType, lt.EnumType)):
            return False
        if t.is_volatile or self.g._is_float_type(t):
            return False
        return not (isinstance(t, lt.BasicType) and t.name == "void")

    def const_of(self, e):
        try:
            return self.g._const_int_operand(e)
        except Exception:
            return None

    def int_like(self, t) -> bool:
        g = self.g
        if not isinstance(t, (lt.BasicType, lt.EnumType)):
            return False
        if t.is_volatile or g._is_float_type(t):
            return False
        return not (isinstance(t, lt.BasicType)
                    and t.name in ("void", "bool", "_Bool"))

    def pure_int(self, e, leaves, mem):
        """Key of an integer expression whose value depends on the leaves
        (local variables) and on the memory roots in ``mem`` only."""
        g = self.g
        if isinstance(e, ast.Identifier):
            name = e.name.text
            v = self.vars.get(name)
            if v is not None:
                if self.leaf_ok(v, False):
                    leaves.append(v)
                    return f"v{v.uid}"
                if self.mem_ok and self.int_like(v.type):
                    leaves.append(v)     # its scope limits where a key goes
                    mem.append(f"v{v.uid}")
                    return f"m{v.uid}"
                return None
            sym = g.ctx.globals.get(name)
            if sym is not None:
                if (self.mem_ok and name not in g.ctx.function_names
                        and self.int_like(sym.sym_type)):
                    mem.append(f"g:{name}")
                    return f"g{name}"
                return None
        c = self.const_of(e)
        if c is not None:
            return f"#{c}"
        if isinstance(e, ast.BinaryOp) and e.op in _ARITH:
            a = self.pure_int(e.left, leaves, mem)
            if a is None:
                return None
            b = self.pure_int(e.right, leaves, mem)
            if b is None:
                return None
            return f"({e.op} {a} {b})"
        if isinstance(e, ast.UnaryOp) and e.op in ("-", "~", "+"):
            a = self.pure_int(e.operand, leaves, mem)
            return None if a is None else f"(u{e.op} {a})"
        if isinstance(e, ast.Cast):
            tt = e.target_type
            try:
                tt = g._resolve_typename(tt)
            except Exception:
                return None
            if not isinstance(tt, (lt.BasicType, lt.EnumType)):
                return None
            if g._is_float_type(tt) or getattr(tt, "name", "") in (
                    "void", "bool", "_Bool"):
                return None
            a = self.pure_int(e.expr, leaves, mem)
            return None if a is None else f"(cast {tt!r} {a})"
        if isinstance(e, ast.Index) and self.mem_ok:
            # a load from a named array: a key with a memory root
            if not self.int_like(g._get_expr_type(e)):
                return None
            if not isinstance(g._get_expr_type(e.array), lt.ArrayType):
                return None
            root = self.root_of(e.array)
            if root is None:
                return None
            a = self.pure_base(e.array, leaves, mem)
            if a is None:
                return None
            i = self.pure_int(e.index, leaves, mem)
            if i is None:
                return None
            mem.append(root)
            return f"L({a}[{i}])"
        return None

    def pure_base(self, e, leaves, mem):
        """Key of an expression that is an array (or a local pointer) and
        whose address (value) depends on the leaves only."""
        g = self.g
        if isinstance(e, ast.Identifier):
            name = e.name.text
            v = self.vars.get(name)
            if v is not None:
                if isinstance(v.type, lt.ArrayType):
                    leaves.append(v)    # its scope limits where a key may go
                    return f"A{v.uid}"
                if self.leaf_ok(v, True):
                    leaves.append(v)
                    return f"P{v.uid}"
                return None
            sym = g.ctx.globals.get(name)
            if sym is not None and isinstance(sym.sym_type, lt.ArrayType):
                return f"G{name}"
            return None
        if isinstance(e, ast.Member):
            mt = g._get_member_type(e)
            if not isinstance(mt, lt.ArrayType):
                return None
            k = self.pure_lvalue(e.obj, leaves, mem)
            return None if k is None else f"({k}.{e.member.text})"
        if isinstance(e, ast.ArrowMember):
            mt = g._get_member_type(e)
            if not isinstance(mt, lt.ArrayType):
                return None
            o = e.obj
            v = self.lookup_var(o)
            if v is None or not self.leaf_ok(v, True):
                return None
            leaves.append(v)
            return f"(P{v.uid}->{e.member.text})"
        if isinstance(e, ast.Index):
            t = g._get_expr_type(e)
            if not isinstance(t, lt.ArrayType):
                return None
            a = self.pure_base(e.array, leaves, mem)
            if a is None:
                return None
            i = self.pure_int(e.index, leaves, mem)
            return None if i is None else f"({a}[{i}])"
        return None

    def pure_lvalue(self, e, leaves, mem):
        """Key of a struct object that is named, not reached through memory."""
        g = self.g
        if isinstance(e, ast.Identifier):
            name = e.name.text
            v = self.vars.get(name)
            if v is not None:
                leaves.append(v)
                return f"S{v.uid}"
            sym = g.ctx.globals.get(name)
            if sym is not None and isinstance(sym.sym_type, lt.StructType):
                return f"G{name}"
            return None
        if isinstance(e, ast.Member):
            t = g._get_member_type(e)
            if not isinstance(t, lt.StructType):
                return None
            k = self.pure_lvalue(e.obj, leaves, mem)
            return None if k is None else f"({k}.{e.member.text})"
        if isinstance(e, ast.Index):
            t = g._get_expr_type(e)
            if not isinstance(t, lt.StructType):
                return None
            a = self.pure_base(e.array, leaves, mem)
            if a is None:
                return None
            i = self.pure_int(e.index, leaves, mem)
            return None if i is None else f"({a}[{i}])"
        return None

    def classify(self, n, cont, slot):
        g = self.g
        elem = g._get_expr_type(n)
        if elem is None or isinstance(
                elem, (lt.ArrayType, lt.FunctionType)):
            return None
        if getattr(elem, "is_volatile", False):
            return None
        if isinstance(elem, lt.BasicType) and elem.name == "void":
            return None
        size = g._type_size(elem)
        if size <= 0:
            return None
        if g._vol.touches(n, self.vol_names):
            return None
        idx = n.index
        off = 0
        core = idx
        if isinstance(idx, ast.BinaryOp) and idx.op in ("+", "-"):
            rc = idx.right
            lc = idx.left
            if isinstance(rc, ast.IntLiteral):
                k = self.const_of(rc)
                if k is not None and abs(k) <= 100:
                    off = k if idx.op == "+" else -k
                    core = lc
            elif idx.op == "+" and isinstance(lc, ast.IntLiteral):
                k = self.const_of(lc)
                if k is not None and abs(k) <= 100:
                    off = k
                    core = rc
        if self.const_of(core) is not None:
            return None
        leaves: list = []
        mem: list = []
        bk = self.pure_base(n.array, leaves, mem)
        if bk is None:
            return None
        ck = self.pure_int(core, leaves, mem)
        if ck is None:
            return None
        if not leaves and not mem:
            return None
        o = _Occ()
        o.node, o.cont, o.slot = n, cont, slot
        o.key = f"{bk}[{ck}]"
        o.off = off
        o.base = copy.deepcopy(n.array)
        o.core = copy.deepcopy(core)
        seen = {}
        for v in leaves:
            seen[v.uid] = v
        o.leaves = tuple(seen.values())
        o.mem = frozenset(mem)
        o.frames = tuple(self.frames)
        o.w = self.weight
        o.bd = self.bdepth
        o.elem = elem
        o.size = size
        o.cost = self.addr_cost(n.array, core, size)
        o.dead = False
        return o

    # a rough cost in T-states of computing the address

    def ecost(self, e) -> float:
        if isinstance(e, ast.IntLiteral) or self.const_of(e) is not None:
            return 10
        if isinstance(e, ast.Identifier):
            v = self.vars.get(e.name.text)
            if v is not None and isinstance(v.type, lt.ArrayType):
                return 10
            return 16
        if isinstance(e, ast.BinaryOp):
            a, b = self.ecost(e.left), self.ecost(e.right)
            if e.op in ("+", "-") and self.const_of(e.right) is not None:
                k = abs(self.const_of(e.right))
                return a + (6 * k if k <= 3 else 21)
            if e.op == "*":
                return a + b + 100
            if e.op in ("<<", ">>"):
                k = self.const_of(e.right)
                return a + (11 * k if k is not None and k < 5 else 100)
            return a + b + 11
        if isinstance(e, ast.UnaryOp):
            return self.ecost(e.operand) + 20
        if isinstance(e, ast.Cast):
            return self.ecost(e.expr) + 8
        if isinstance(e, ast.Index):
            return self.ecost(e.array) + self.ecost(e.index) + 33
        if isinstance(e, (ast.Member, ast.ArrowMember)):
            return self.ecost(e.obj) + 21
        return 30

    def addr_cost(self, base, core, size) -> float:
        c = self.ecost(base) + self.ecost(core) + 11
        if size & (size - 1) == 0:
            c += 11 * (size.bit_length() - 1)
        else:
            c += 100
        return c

    # -- runs and selection ---------------------------------------------

    def runs_of(self, occs):
        """The runs (block, occurrences) of one group of occurrences."""
        out = []
        blocks: dict = {}
        for o in occs:
            for fr in o.frames:
                blocks.setdefault(id(fr[0]), fr[0])
        for b in blocks.values():
            chain = self.block_chain.get(id(b))
            if chain is None:
                continue
            members = []
            for o in occs:
                if not all(v.scope in chain for v in o.leaves):
                    continue
                for fr in o.frames:
                    if fr[0] is b:
                        members.append((fr[1], (fr[2], fr[3]), o))
                        break
            if not members:
                continue
            pos = {id(it): k for k, it in enumerate(b.items)}
            leaf_uids = {v.uid for o in occs for v in o.leaves}
            mem = occs[0].mem
            bad = set()
            for it in b.items:
                inf = self.info.get(id(it))
                if inf is not None and (inf.labels
                                        or (inf.writes & leaf_uids)):
                    bad.add(pos[id(it)])
                elif mem and ((inf is not None and (
                        inf.call or inf.any_w or (inf.mem_w & mem)))
                        or self.vol_item(it)):
                    bad.add(pos[id(it)])
            members = [(pos[id(it)], w, o) for it, w, o in members
                       if id(it) in pos]
            members.sort(key=lambda t: t[0])
            cur: list = []
            last = None
            for k, w, o in members:
                if k in bad:
                    if cur:
                        out.append((b, cur))
                        cur = []
                    last = k
                    continue
                if cur and any(j in bad for j in range(last + 1, k)):
                    out.append((b, cur))
                    cur = []
                cur.append((k, w, o))
                last = k
            if cur:
                out.append((b, cur))
        return out

    def vol_item(self, it) -> bool:
        g = self.g
        if not g._vol.any:
            return False
        r = self.vol_cache.get(id(it))
        if r is None:
            r = self.vol_cache[id(it)] = bool(
                g._vol.touches(it, self.vol_names))
        return r

    def gain_of(self, members) -> float:
        """Estimated T-states that the pointer saves.  -1e9 when no access
        is certain: every access sits behind a branch, so the pointer may
        cost more than it saves (a pointer in front of a rare branch)."""
        first = members[0][2]
        c_addr = first.cost
        gain = -(c_addr + 16)
        certain = 0
        for _k, (w0, bd0), o in members:
            rel = o.w / w0 if w0 else 1.0
            use_orig = c_addr + (17 if o.off else 0)
            use_t = 16 if o.off == 0 else 30
            gain += rel * (use_orig - use_t)
            if o.bd == bd0:
                certain += 1
        if NEED_CERT and certain < NEED_CERT:
            return -1e9
        return gain

    def select_and_apply(self) -> None:
        if not self.occs:
            return
        groups: dict = {}
        for o in self.occs:
            groups.setdefault(o.key, []).append(o)
        heap: list = []
        stamp = [0]

        def push_group(key):
            occs = [o for o in groups[key] if not o.dead]
            if len(occs) < 2:
                return
            for b, members in self.runs_of(occs):
                if len(members) < 2:
                    continue
                gain = self.gain_of(members)
                if gain < self.min_gain:
                    continue
                r = _Run()
                r.block = b
                r.occs = members
                r.gain = gain
                r.key = key
                stamp[0] += 1
                r.stamp = stamp[0]
                mind = min(m[2].depth for m in members)
                heapq.heappush(heap, (mind, -gain, r.stamp, id(r), r))

        for key in groups:
            push_group(key)
        applied = 0
        while heap:
            _d, _g, _s, _i, r = heapq.heappop(heap)
            live = [m for m in r.occs if not m[2].dead]
            if len(live) != len(r.occs):
                # occurrences went away: judge the run again
                r2 = [m for m in live]
                if len(r2) < 2:
                    continue
                if self.gain_of(r2) < self.min_gain:
                    continue
                r.occs = r2
            if not self.gen_allows():
                break
            self.apply(r)
            applied += 1

    def prune_temps(self) -> None:
        """Drop a pointer that no access uses any more (a pointer that an
        outer pointer made useless after the inner one was made)."""
        if not self.temps:
            return
        uses: dict = {}
        stack = [self.func.body]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, ast.TypeofUnqualExpr):
                continue
            if isinstance(x, ast.Identifier):
                uses[x.name.text] = uses.get(x.name.text, 0) + 1
                continue
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append(v)
        for name, block, decl in self.temps:
            if uses.get(name, 0) == 0:
                block.items[:] = [it for it in block.items if it is not decl]
                self.stats["pruned"] = self.stats.get("pruned", 0) + 1

    def gen_allows(self) -> bool:
        lim = os.environ.get("UC80_ACSE_MAX")
        if lim is None:
            return True
        try:
            return self.stats["n"] < int(lim)
        except ValueError:
            return True

    def apply(self, r) -> None:
        g = self.g
        from . import codegen as cg
        members = sorted(r.occs, key=lambda t: t[0])
        first = members[0][2]
        self.stats["n"] += 1
        name = f"__acse{self.stats['n']}"
        base = copy.deepcopy(first.base)
        core = copy.deepcopy(first.core)
        init = ast.UnaryOp(op="&", operand=ast.Index(array=base, index=core))
        init._acse = True        # see address_taken_names
        spec = ast.TypeofUnqualExpr(operand=copy.deepcopy(init))
        spec._resolved = cg._from_legacy(lt.PointerType(base_type=first.elem))
        specs = [spec]
        if os.environ.get("UC80_ACSE_REGISTER"):
            specs.insert(0, ast.StorageClass(kw=_tok("register", "KW")))
        decl = ast.Declaration(
            decl_specs=specs,
            declarators=[ast.InitDeclaratorWithInit(
                declarator=ast.Declarator(name=_tok(name)), init=init)])
        items = r.block.items
        # the first item of the run that holds an occurrence
        target = None
        pos = {id(it): k for k, it in enumerate(items)}
        best = None
        for _k, _w, o in members:
            for fr in o.frames:
                if fr[0] is r.block:
                    p = pos.get(id(fr[1]))
                    if p is not None and (best is None or p < best):
                        best = p
                        target = fr[1]
                    break
        assert target is not None
        self.temps.append((name, r.block, decl))
        # by identity: two equal statements compare equal as dataclasses
        at = next(k for k, it in enumerate(items) if it is target)
        items.insert(at, decl)
        for _k, _w, o in members:
            use = _ident(name)
            if o.off == 0:
                new = ast.UnaryOp(op="*", operand=use)
            else:
                new = ast.Index(array=use, index=_int(o.off))
            if isinstance(o.cont, list):
                o.cont[o.slot] = new
            else:
                setattr(o.cont, o.slot, new)
            o.dead = True
            self.kill_inside(o.node)
        if self.debug:
            fn = cg.function_name(self.func)
            print(f"acse: {fn}: {name} key={first.key} uses={len(members)} "
                  f"gain={r.gain:.0f} mem={sorted(first.mem)}", file=sys.stderr)

    def kill_inside(self, node) -> None:
        stack = [node]
        ids = set()
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
                continue
            if not _is_node(x):
                continue
            ids.add(id(x))
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append(v)
        for o in self.occs:
            if id(o.node) in ids:
                o.dead = True

    # -- chained constant stores ------------------------------------------

    def chain_ok(self) -> bool:
        return self.g.enable_chain_store and not self.noreg

    def try_chain(self, stmt):
        """``a = b = K;`` as ``b = K; a = K;`` (or None)."""
        g = self.g
        e = stmt.expr
        lvals = []
        while (isinstance(e, ast.BinaryOp) and e.op == "="):
            lvals.append(e.left)
            e = e.right
        if len(lvals) < 2:
            return None
        k = self.const_of(e)
        if k is None:
            return None
        kexpr = e
        if g._vol.touches(stmt.expr, self.vol_names):
            return None
        sizes = []
        for lv in lvals:
            try:
                sz = self.store_fits(lv, k)
            except Exception:
                return None
            if not sz:
                return None
            sizes.append(sz)
        # A plain variable target is cheaper in the chain (one load of K
        # serves all of them).  Split off every other target, and keep
        # each run of adjacent plain variables as a chain of their own.
        # Two-byte and four-byte stores of K cost about the same in the
        # chain (the value stays in DE), so the split pays for a byte
        # target only: the chain widens the byte again after each store.
        if not any(sz == 1 and not isinstance(lv, ast.Identifier)
                   for lv, sz in zip(lvals, sizes)):
            return None
        out = []
        run: list = []

        def flush():
            if not run:
                return
            e = copy.deepcopy(kexpr)
            for lv in run:                   # right to left: x = y = K
                e = ast.BinaryOp(left=lv, op="=", right=e)
            out.append(ast.ExpressionStmt(expr=e))
            run.clear()

        for lv in reversed(lvals):
            if isinstance(lv, ast.Identifier):
                run.append(lv)
                continue
            flush()
            out.append(ast.ExpressionStmt(expr=ast.BinaryOp(
                left=lv, op="=", right=copy.deepcopy(kexpr))))
        flush()
        self.stats["chain"] = self.stats.get("chain", 0) + 1
        if self.debug:
            print(f"acse: chain of {len(lvals)} stores of {k}", file=sys.stderr)
        return out

    def store_fits(self, lv, k) -> int:
        """The size in bytes of the target ``lv`` when it holds K exactly
        (a pointer target: 2 when K is 0), else 0."""
        g = self.g
        if not isinstance(lv, (ast.Identifier, ast.Index, ast.Member,
                               ast.ArrowMember)):
            if not (isinstance(lv, ast.UnaryOp) and lv.op == "*"):
                return 0
        if isinstance(lv, (ast.Member, ast.ArrowMember)):
            try:
                if g._get_bitfield_info(lv) is not None:
                    return 0
            except Exception:
                return 0
        t = g._get_expr_type(lv)
        if t is None:
            return 0
        if isinstance(t, lt.PointerType):
            return 2 if k == 0 else 0
        if not isinstance(t, lt.BasicType):
            return 0
        if g._is_float_type(t) or g._is_bool_type(t) or t.name == "void":
            return 0
        if t.is_volatile:
            return 0
        size = g._type_size(t)
        if size not in (1, 2, 4):
            return 0
        bits = size * 8
        if g._is_signed_type(t):
            ok = -(1 << (bits - 1)) <= k < (1 << (bits - 1))
        else:
            ok = 0 <= k < (1 << bits)
        return size if ok else 0


_HANDLERS = {
    "CompoundStmt": _Fn.h_compound,
    "ForStmt": _Fn.h_for,
    "WhileStmt": _Fn.h_while,
    "DoWhileStmt": _Fn.h_dowhile,
    "IfStmt": _Fn.h_if,
    "IfStmtElse": _Fn.h_if,
    "SwitchStmt": _Fn.h_switch,
    "CaseStmt": _Fn.h_case,
    "DefaultStmt": _Fn.h_case,
    "LabelStmt": _Fn.h_label,
    "ExpressionStmt": _Fn.h_exprstmt,
    "Declaration": _Fn.h_decl,
    "BinaryOp": _Fn.h_binary,
    "TernaryOp": _Fn.h_ternary,
    "UnaryOp": _Fn.h_unary,
    "PostfixOp": _Fn.h_postfix,
    "Call": _Fn.h_call,
    "CallNoArgs": _Fn.h_call,
    "Index": _Fn.h_index,
    "SizeofExpr": _Fn.h_unevaluated,
    "AlignofExpr": _Fn.h_unevaluated,
    "TypeofExpr": _Fn.h_unevaluated,
    "TypeofUnqualExpr": _Fn.h_unevaluated,
}


def _unsafe(body) -> bool:
    """A function the pass leaves alone: inline assembly, a statement
    expression or setjmp."""
    stack = [body]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
            continue
        if not _is_node(x):
            continue
        if isinstance(x, (ast.AsmDeclaration, ast.StmtExpr)):
            return True
        if (isinstance(x, ast.Identifier)
                and "setjmp" in x.name.text.lower()):
            return True
        for f in _fields(x):
            v = getattr(x, f, None)
            if v is not None and not isinstance(v, (str, int)):
                stack.append(v)
    return False


class AddrCseMixin:
    """The hook of the pass in the code generator."""

    enable_addr_cse = True
    enable_chain_store = True

    def _acse_run(self, unit) -> dict:
        stats = {"n": 0, "chain": 0}
        if not (self.enable_addr_cse or self.enable_chain_store):
            return stats
        if self.type_config.ptr_size != 2:
            return stats
        from . import codegen as cg
        only = os.environ.get("UC80_ACSE_ONLY")
        only = set(only.split(",")) if only else None
        saved_locals = self.ctx.locals
        for item in unit.items:
            if not isinstance(item, ast.FunctionDef) or item.body is None:
                continue
            name = cg.function_name(item)
            if name is None or (only is not None and name not in only):
                continue
            if _unsafe(item.body):
                continue
            snap = self._type_scope_snapshot()
            self.ctx.locals = {}
            try:
                self._register_body_types(item.body)
                fn = _Fn(self, item, stats)
                if not self.enable_addr_cse:
                    fn.select_and_apply = lambda: None
                try:
                    fn.run()
                except RecursionError:
                    # A very deep expression.  Every edit that the pass
                    # makes keeps the meaning of the function, so what is
                    # done so far can stay.
                    if os.environ.get("UC80_ACSE_DEBUG"):
                        print(f"acse: {name}: recursion limit",
                              file=sys.stderr)
            finally:
                self.ctx.locals = saved_locals
                self._type_scope_restore(snap)
        return stats
