"""Loop-invariant code motion of pure integer expressions (docs/licm.md).

The pass runs on the AST of each function before the frame is sized, in
the place where addrcse.py runs.  In a loop such as::

    for (y = 0; y < 24; y++)
        for (x = 0; x < 80; x++)
            i = y * 80 + x;

the product ``y * 80`` has the same value in every pass of the inner loop.
The pass computes the product once into a new local before the inner loop::

    for (y = 0; y < 24; y++) {
        { int __licm0 = y * 80;
          for (x = 0; x < 80; x++)
              i = __licm0 + x; }
    }

The new local is an ordinary local.  The register-locals pass (BC, DE and
IY) can move it into a register.

What the pass moves is an expression made of integer constants, of local
variables, of casts to integer types, of unary - ~ + and of the binary
operators + - * << >> & | ^ (a shift only by a constant).  A local variable
qualifies when nothing in the loop assigns it, its address is never taken
and it is not volatile.  Such an expression reads no memory, calls nothing
and cannot trap, so evaluating it once before the loop (even when the loop
runs zero times) changes nothing that the program can observe.  An
expression moves only when it costs at least ``UC80_LICM_MINCOST`` T-states
(default 40).

With the load part (docs/licm_load.md, switch --no-licm-load) the pass also
moves the load of an array element ``tab[i]`` and the address of a row
``m[i]`` of a two-dimensional array out of a loop that has no call, no store
through a pointer and no store to the same array.

The pass leaves a loop alone when the loop holds a label, or a case label
of a switch outside the loop (a jump into the loop would skip the moved
code).  It leaves a function alone that is naked, that is an interrupt
function, or that holds inline assembly, a statement expression or setjmp.
"""

from __future__ import annotations

import copy
import os
import sys

from uc_core import ast
from uc_core import ast_legacy as lt

from .addrcse import _fields, _ident, _int, _is_node, _tok, _unsafe

_int_lit = _int

_ASSIGN = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")
_ARITH = ("+", "-", "*", "<<", ">>", "&", "|", "^")
# nodes whose children are types or constants, not values to move
_SKIP = (ast.SizeofExpr, ast.SizeofType, ast.AlignofType, ast.TypeofExpr,
         ast.TypeofUnqualExpr, ast.GenericSelection, ast.OffsetofExpr,
         ast.CompoundLiteral, ast.CompoundLiteralEmpty, ast.StaticAssert,
         ast.StaticAssertWithMessage, ast.StmtExpr, ast.VaArgExpr,
         ast.TypeName, ast.TypeNameWithDeclarator, ast.TypesCompatibleP)


def _op(n) -> str:
    return getattr(n.op, "text", n.op)


def may_jump(n) -> bool:
    """A statement that holds a break, continue, goto or return."""
    stack = [n]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
            continue
        if not _is_node(x):
            continue
        if isinstance(x, (ast.BreakStmt, ast.ContinueStmt, ast.GotoStmt,
                          ast.ReturnStmt, ast.ReturnStmtValue)):
            return True
        for f in _fields(x):
            v = getattr(x, f, None)
            if v is not None and not isinstance(v, (str, int)):
                stack.append(v)
    return False


def _min_cost() -> int:
    try:
        return int(os.environ.get("UC80_LICM_MINCOST", 40))
    except ValueError:
        return 40


def _load_cost(name="UC80_LICM_MINCOST_LOAD") -> int:
    try:
        return int(os.environ.get(name, 40))
    except ValueError:
        return 40


_ANY = object()      # a store whose target the pass cannot name
_NOTHING = object()  # a store that changes no named memory


class _Var:
    __slots__ = ("name", "auto", "type")

    def __init__(self, name, auto, type_):
        self.name, self.auto, self.type = name, auto, type_


class _Cand:
    """One expression to move and every place that holds an equal one."""
    __slots__ = ("expr", "type", "places", "cost", "uncond")


class _Fn:
    def __init__(self, gen, func, stats):
        from .reglocals_gen import address_taken_names
        self.g = gen
        self.func = func
        self.stats = stats
        self.addr_taken = address_taken_names(func.body)
        self.vol_names = gen._vol.names_for(func)
        self.vars: dict = {}
        self.min_cost = _min_cost()
        self.debug = bool(os.environ.get("UC80_LICM_DEBUG"))
        self.load = bool(getattr(gen, "enable_licm_load", False))
        self.isr_names = getattr(gen, "_licm_isr_names", set())
        self.load_cost = _load_cost()
        self.addr_cost = _load_cost("UC80_LICM_MINCOST_ADDR")

    # -- the walk over statements ---------------------------------------

    def run(self) -> None:
        from . import codegen as cg
        g = self.g
        for p in cg.function_params(self.func):
            if not isinstance(p, ast.ParamDecl):
                continue
            pn = cg.declarator_ident(p.declarator)
            try:
                pn, pt = cg.resolve_type_from_decl(p.decl_specs, p.declarator)
                t = cg._to_legacy(pt)
                if isinstance(t, lt.ArrayType):
                    t = lt.PointerType(base_type=t.base_type)
            except Exception:
                t = None
            if pn:
                self.vars[pn] = _Var(pn, True, t)
                g.ctx.locals[pn] = cg.Symbol(name=pn, sym_type=t,
                                             is_param=True)
        self.stmt(self.func.body, None, None)

    def stmt(self, n, cont, slot) -> None:
        if isinstance(n, list):
            for i, x in enumerate(n):
                self.stmt(x, n, i)
            return
        if not _is_node(n):
            return
        if isinstance(n, ast.CompoundStmt):
            self.scoped(lambda: self.stmt(n.items, None, None))
        elif isinstance(n, ast.Declaration):
            self.declare(n)
        elif isinstance(n, (ast.ForStmt, ast.WhileStmt, ast.DoWhileStmt)):
            self.loop(n, cont, slot)
        elif isinstance(n, (ast.IfStmt, ast.IfStmtElse)):
            self.stmt(n.then_branch, n, "then_branch")
            if isinstance(n, ast.IfStmtElse):
                self.stmt(n.else_branch, n, "else_branch")
        elif isinstance(n, ast.SwitchStmt):
            self.stmt(n.body, n, "body")
        elif isinstance(n, (ast.CaseStmt, ast.DefaultStmt, ast.LabelStmt)):
            self.stmt(n.stmt, n, "stmt")

    def scoped(self, fn) -> None:
        g = self.g
        saved_locals = dict(g.ctx.locals)
        saved_vars = dict(self.vars)
        try:
            fn()
        finally:
            self.vars = saved_vars
            g.ctx.locals = saved_locals

    def declare(self, n) -> None:
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
        for nm, full, _init, is_fn in decls:
            if nm is None:
                continue
            try:
                t = cg._to_legacy(full)
            except Exception:
                t = None
            auto = sc not in ("static", "extern") and not is_fn
            g.ctx.locals[nm] = cg.Symbol(name=nm, sym_type=t,
                                         is_static=(sc == "static"))
            self.vars[nm] = _Var(nm, auto, t)

    def loop(self, n, cont, slot) -> None:
        def body():
            if isinstance(n, ast.ForStmt) and isinstance(
                    n.init, ast.Declaration):
                self.declare(n.init)
            wrapper_items = None
            if cont is not None and not self.runs_once(n):
                try:
                    wrapper_items = self.hoist(n)
                except RecursionError:
                    wrapper_items = None
            if wrapper_items:
                wrapper = ast.CompoundStmt(items=wrapper_items + [n])
                if isinstance(cont, list):
                    cont[slot] = wrapper
                else:
                    setattr(cont, slot, wrapper)
            self.stmt(n.body, n, "body")
        self.scoped(body)

    def runs_once(self, loop) -> bool:
        """A loop whose condition is a constant that is false: the body
        runs at most one time (``do { ... } while (0)``, the shape of a
        macro).  Moving a part out of the loop gains nothing."""
        c = getattr(loop, "condition", None)
        if c is None:
            return False
        k = self.const_of(c)
        return k is not None and k == 0

    # -- analysis of one loop -------------------------------------------

    def scan(self, loop):
        """(assigned names, declared names, ok) of the whole loop."""
        from . import codegen as cg
        assigned: set = set()
        declared: set = set()
        stores: list = []
        self.mem_call = False
        stack = [(loop, 0)]
        ok = True
        while stack:
            x, sw = stack.pop()
            if isinstance(x, list):
                stack.extend((y, sw) for y in x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, ast.LabelStmt):
                ok = False
            elif isinstance(x, (ast.CaseStmt, ast.DefaultStmt)) and sw == 0:
                ok = False
            elif isinstance(x, ast.BinaryOp) and _op(x) in _ASSIGN:
                stores.append(x.left)
                if isinstance(x.left, ast.Identifier):
                    assigned.add(x.left.name.text)
            elif isinstance(x, (ast.UnaryOp, ast.PostfixOp)) and _op(x) in (
                    "++", "--"):
                stores.append(x.operand)
                if isinstance(x.operand, ast.Identifier):
                    assigned.add(x.operand.name.text)
            elif isinstance(x, (ast.Call, ast.CallNoArgs)):
                self.mem_call = True
            elif isinstance(x, ast.Declaration):
                try:
                    for nm, _f, _i, _fn in cg.iter_var_decls(x):
                        if nm is not None:
                            declared.add(nm)
                except Exception:
                    ok = False
            if isinstance(x, ast.SwitchStmt):
                sw += 1
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append((v, sw))
        # what the stores of the loop can change (for the load part)
        self.mem_any = False
        self.mem_roots = set()
        if self.load:
            for lv in stores:
                try:
                    r = self.store_root(lv, declared)
                except Exception:
                    r = _ANY
                if r is _ANY:
                    self.mem_any = True
                elif r is not _NOTHING:
                    self.mem_roots.add(r)
        return assigned, declared, ok

    def store_root(self, lv, declared):
        """The name of the object that a store to ``lv`` changes, _ANY
        when the store goes through a pointer or the pass cannot tell,
        _NOTHING for a local scalar that no pointer can reach."""
        g = self.g
        if isinstance(lv, ast.Identifier):
            name = lv.name.text
            if name in declared:
                return _ANY
            v = self.vars.get(name)
            if (v is not None and v.auto and name not in self.addr_taken
                    and not isinstance(v.type, (lt.ArrayType,
                                                lt.StructType))):
                return _NOTHING
            return name
        if isinstance(lv, ast.Index):
            if isinstance(g._get_expr_type(lv.array), lt.ArrayType):
                return self.store_root(lv.array, declared)
            return _ANY
        if isinstance(lv, ast.Member):
            return self.store_root(lv.obj, declared)
        return _ANY

    def hoist(self, loop):
        g = self.g
        assigned, declared, ok = self.scan(loop)
        if not ok:
            return None
        self.assigned = assigned
        self.declared = declared
        found: dict = {}
        order: list = []
        cond = [0]       # > 0: the part may be skipped in a pass of the loop

        def see(e, cont, slot, in_decl_ok=True):
            """Walk an expression; collect maximal movable parts."""
            if isinstance(e, list):
                for i, x in enumerate(e):
                    see(x, e, i)
                return
            if isinstance(e, tuple):
                for x in e:
                    see(x, None, None)
                return
            if not _is_node(e) or isinstance(e, _SKIP):
                return
            if self.load and cont is not None and isinstance(e, ast.Index):
                try:
                    d = self.index_cand(e, cont, slot)
                except Exception as ex:
                    if self.debug:
                        print(f"licm: index_cand failed: {ex!r}",
                              file=sys.stderr)
                    d = None
                if d is not None:
                    key, init, typ, cost, wrap = d
                    c = found.get(key)
                    if c is None:
                        c = _Cand()
                        c.expr = init
                        c.type = typ
                        c.places = []
                        c.cost = cost
                        c.uncond = False
                        found[key] = c
                        order.append(key)
                    c.places.append((cont, slot, wrap))
                    if cond[0] == 0:
                        c.uncond = True
                    if wrap is not None:
                        see(getattr(wrap[0], wrap[1]), wrap[0], wrap[1])
                    return
            if (cont is not None and isinstance(
                    e, (ast.BinaryOp, ast.UnaryOp, ast.Cast))):
                cost = self.movable(e)
                if cost is not None and cost >= self.min_cost:
                    key = self.key(e)
                    if key is not None:
                        c = found.get(key)
                        if c is None:
                            c = _Cand()
                            c.expr = e
                            c.type = g._get_expr_type(e)
                            c.places = []
                            c.cost = cost
                            c.uncond = False
                            found[key] = c
                            order.append(key)
                        c.places.append((cont, slot, None))
                        if cond[0] == 0:
                            c.uncond = True
                        return
            walk(e)

        def walk(e):
            if isinstance(e, ast.BinaryOp) and _op(e) in ("&&", "||"):
                see(e.left, e, "left")
                cond[0] += 1
                see(e.right, e, "right")
                cond[0] -= 1
                return
            if isinstance(e, ast.TernaryOp):
                see(e.condition, e, "condition")
                cond[0] += 1
                see(e.true_expr, e, "true_expr")
                see(e.false_expr, e, "false_expr")
                cond[0] -= 1
                return
            for f in _fields(e):
                v = getattr(e, f, None)
                if v is None or isinstance(v, (str, int)):
                    continue
                if isinstance(v, list):
                    see(v, None, None)
                elif isinstance(v, tuple):
                    see(v, None, None)
                elif _is_node(v):
                    see(v, e, f)

        def stmts(n):
            if isinstance(n, list):
                # an item after a statement that may jump away is skipped
                # in some passes
                extra = 0
                for x in n:
                    stmts(x)
                    if not extra and may_jump(x):
                        extra = 1
                        cond[0] += 1
                cond[0] -= extra
                return
            if not _is_node(n):
                return
            if isinstance(n, ast.Declaration):
                from . import codegen as cg
                try:
                    sc = cg.decl_storage_class(n.decl_specs)
                except Exception:
                    return
                if sc in ("static", "extern", "typedef"):
                    return
                for d in n.declarators or []:
                    if isinstance(d, ast.InitDeclaratorWithInit):
                        see(d.init, d, "init")
                return
            if isinstance(n, (ast.CaseStmt,)):
                stmts(n.stmt)
                return
            if isinstance(n, ast.ExpressionStmt):
                see(n.expr, n, "expr")
                return
            if isinstance(n, ast.ForStmt):
                if isinstance(n.init, ast.Declaration):
                    stmts(n.init)
                elif n.init is not None:
                    see(n.init, n, "init")
                for f in ("condition", "update"):
                    v = getattr(n, f)
                    if v is not None:
                        see(v, n, f)
                stmts(n.body)
                return
            if isinstance(n, ast.ReturnStmtValue):
                see(n.value, n, "value")
                return
            # other statements: expression fields are values, statement
            # fields are walked as statements; a branch of an if and the
            # body of a switch run in some passes only
            for f in _fields(n):
                v = getattr(n, f, None)
                if v is None or isinstance(v, (str, int)):
                    continue
                if f in ("then_branch", "else_branch") or (
                        f == "body" and isinstance(n, ast.SwitchStmt)):
                    cond[0] += 1
                    stmts(v)
                    cond[0] -= 1
                elif f in ("body", "stmt", "items"):
                    stmts(v)
                elif isinstance(v, (list, tuple)):
                    see(v, None, None)
                elif _is_node(v):
                    see(v, n, f)

        # the loop itself: condition, update, body; the init of a for
        # runs once and stays where it is
        if isinstance(loop, ast.ForStmt):
            for f in ("condition", "update"):
                v = getattr(loop, f)
                if v is not None:
                    see(v, loop, f)
            stmts(loop.body)
        elif isinstance(loop, ast.WhileStmt):
            see(loop.condition, loop, "condition")
            stmts(loop.body)
        else:
            stmts(loop.body)
            see(loop.condition, loop, "condition")
        order = [k for k in order if found[k].uncond]
        if not order:
            return None
        items = []
        from . import codegen as cg
        only = os.environ.get("UC80_LICM_MAX")
        for key in order:
            c = found[key]
            if only is not None and self.stats["n"] >= int(only):
                break
            self.stats["n"] += 1
            name = f"__licm{self.stats['n']}"
            init = copy.deepcopy(c.expr)
            spec = ast.TypeofUnqualExpr(operand=copy.deepcopy(c.expr))
            spec._resolved = cg._from_legacy(c.type)
            decl = ast.Declaration(
                decl_specs=[spec],
                declarators=[ast.InitDeclaratorWithInit(
                    declarator=ast.Declarator(name=_tok(name)), init=init)])
            items.append(decl)
            for cont, slot, wrap in c.places:
                use = _ident(name)
                if wrap is not None:
                    use = ast.Index(array=use,
                                    index=getattr(wrap[0], wrap[1]))
                if isinstance(cont, list):
                    cont[slot] = use
                else:
                    setattr(cont, slot, use)
            g.ctx.locals[name] = cg.Symbol(name=name, sym_type=c.type)
            self.vars[name] = _Var(name, True, c.type)
            if self.debug:
                print(f"licm: {cg.function_name(self.func)}: {name} "
                      f"uses={len(c.places)} cost={c.cost} key={key}",
                      file=sys.stderr)
        return items

    # -- the load part: array elements and row addresses ----------------

    def ptr_leaf_ok(self, name) -> bool:
        v = self.vars.get(name)
        if v is None or not v.auto:
            return False
        if (name in self.addr_taken or name in self.assigned
                or name in self.declared or name in self.vol_names):
            return False
        t = v.type
        return (isinstance(t, lt.PointerType)
                and not getattr(t, "is_volatile", False))

    def idx_key(self, e, allow_const):
        """(key, cost) of an integer index that the loop does not change."""
        if self.const_of(e) is not None:
            if not allow_const:
                return None
            return f"#{self.const_of(e)}", 0
        cost = self.movable(e)
        if cost is None:
            return None
        key = self.key(e)
        if key is None:
            return None
        return key, cost + 16

    def bkey(self, e, ptr_ok):
        """(key, root name or None, cost) of an array (or, with ptr_ok, an
        invariant local pointer) whose address the loop does not change."""
        g = self.g
        if isinstance(e, ast.Identifier):
            name = e.name.text
            if name in self.declared:
                return None
            v = self.vars.get(name)
            if v is not None:
                if isinstance(v.type, lt.ArrayType):
                    return f"L:{name}", name, 0
                if ptr_ok and self.ptr_leaf_ok(name):
                    return f"P:{name}", None, 0
                return None
            sym = g.ctx.globals.get(name)
            if (sym is not None and name not in g.ctx.function_names
                    and isinstance(sym.sym_type, lt.ArrayType)):
                return f"G:{name}", name, 0
            return None
        if isinstance(e, ast.Index):
            if not isinstance(g._get_expr_type(e), lt.ArrayType):
                return None
            b = self.bkey(e.array, False)
            if b is None:
                return None
            ik = self.idx_key(e.index, True)
            if ik is None:
                return None
            sc = (0 if self.const_of(e.index) is not None
                  else self.scale_cost(g._get_expr_type(e)))
            return f"({b[0]}[{ik[0]}])", b[1], b[2] + ik[1] + sc
        return None

    def scale_cost(self, t) -> int:
        try:
            n = self.g._type_size(t)
        except Exception:
            return 60
        if n <= 1:
            return 0
        if n & (n - 1) == 0:
            return min(11 * (n.bit_length() - 1), 40)
        return 60

    def rvalue_use(self, cont, slot) -> bool:
        """True when the node in (cont, slot) is only read."""
        if isinstance(cont, ast.UnaryOp) and _op(cont) in ("&", "++", "--"):
            return False
        if isinstance(cont, ast.PostfixOp):
            return False
        if (isinstance(cont, ast.BinaryOp) and _op(cont) in _ASSIGN
                and slot == "left"):
            return False
        if isinstance(cont, (ast.Member, ast.ArrowMember)) and slot == "obj":
            return not isinstance(cont, ast.Member)
        return True

    def index_cand(self, e, cont, slot):
        """(key, init expression, type, cost, wrap) when the Index node
        ``e`` is a load or an address that moves out of the loop."""
        g = self.g
        t = g._get_expr_type(e)
        if isinstance(t, lt.ArrayType):
            # a row of a two-dimensional array in the place of a pointer
            if not (isinstance(cont, ast.Index) and slot == "array"):
                return None
            b = self.bkey(e.array, False)
            ik = self.idx_key(e.index, False)
            if b is None or ik is None:
                return None
            cost = b[2] + ik[1] + self.scale_cost(t) + 21
            if cost < self.load_cost:
                return None
            elem = t.base_type
            init = ast.UnaryOp(op="&", operand=ast.Index(
                array=copy.deepcopy(e), index=_int_lit(0)))
            init._acse = True
            return (f"R:{b[0]}[{ik[0]}]", init, lt.PointerType(
                base_type=elem), cost, None)
        # a load of one element
        scalar = (isinstance(t, lt.PointerType)
                  and not getattr(t, "is_volatile", False)) or self.int_type(t)
        if scalar and self.rvalue_use(cont, slot):
            if not isinstance(g._get_expr_type(e.array), lt.ArrayType):
                scalar = False
        else:
            scalar = False
        if scalar:
            b = self.bkey(e.array, False)
            ik = self.idx_key(e.index, False)
            if b is not None and ik is not None and b[1] is not None:
                root = b[1]
                if (not self.mem_any and not self.mem_call
                        and root not in self.mem_roots
                        and root not in self.isr_names):
                    cost = (b[2] + ik[1] + self.scale_cost(t) + 21
                            + 7 * min(g._type_size(t), 2))
                    if cost >= self.load_cost:
                        return (f"M:{b[0]}[{ik[0]}]", e, t, cost, None)
        # base[inv + var]: the address of base[inv] moves
        idx = e.index
        if (not isinstance(t, lt.ArrayType) and isinstance(idx, ast.BinaryOp)
                and _op(idx) == "+"):
            try:
                if g._type_size(g._get_expr_type(idx)) > 2:
                    return None
            except Exception:
                return None
            for inv_side, var_side in (("left", "right"), ("right", "left")):
                inv = getattr(idx, inv_side)
                var = getattr(idx, var_side)
                ik = self.idx_key(inv, False)
                if ik is None or self.movable(var) is not None:
                    continue
                if self.const_of(var) is not None:
                    continue
                b = self.bkey(e.array, True)
                if b is None:
                    return None
                cost = b[2] + ik[1] + 21
                if cost < self.addr_cost:
                    return None
                init = ast.UnaryOp(op="&", operand=ast.Index(
                    array=copy.deepcopy(e.array), index=copy.deepcopy(inv)))
                init._acse = True
                return (f"A:{b[0]}+{ik[0]}", init, lt.PointerType(
                    base_type=t), cost, (idx, var_side))
        return None

    # -- what may move --------------------------------------------------

    def int_type(self, t) -> bool:
        g = self.g
        if not isinstance(t, (lt.BasicType, lt.EnumType)):
            return False
        if getattr(t, "is_volatile", False) or g._is_float_type(t):
            return False
        if isinstance(t, lt.BasicType) and t.name in ("void", "bool",
                                                        "_Bool"):
            return False
        try:
            return 1 <= g._type_size(t) <= 4
        except Exception:
            return False

    def const_of(self, e):
        try:
            return self.g._const_int_operand(e)
        except Exception:
            return None

    def leaf_ok(self, name) -> bool:
        v = self.vars.get(name)
        if v is None or not v.auto:
            return False
        if (name in self.addr_taken or name in self.assigned
                or name in self.declared or name in self.vol_names):
            return False
        return self.int_type(v.type)

    def movable(self, e):
        """Cost in T-states of the integer expression ``e`` when it may
        move out of the loop, else None."""
        try:
            return self._movable(e, top=True)
        except Exception as ex:
            if self.debug:
                print(f"licm: movable failed: {ex!r}", file=sys.stderr)
            return None

    def _movable(self, e, top=False):
        g = self.g
        if isinstance(e, ast.Identifier):
            if self.leaf_ok(e.name.text):
                return 0
            if self.const_of(e) is not None and e.name.text not in self.vars:
                return 0
            return None
        if self.const_of(e) is not None:
            return 0
        t = g._get_expr_type(e)
        if not self.int_type(t):
            return None
        f = 1.0 if g._type_size(t) <= 2 else 2.5
        if isinstance(e, ast.BinaryOp):
            op = _op(e)
            if op not in _ARITH:
                return None
            a = self._movable(e.left)
            b = self._movable(e.right)
            if a is None or b is None:
                return None
            ca = self.const_of(e.left)
            cb = self.const_of(e.right)
            if ca is not None and cb is not None:
                return 0          # folded by the compiler
            if op in ("+", "-"):
                own = 12 * f
            elif op in ("&", "|", "^"):
                own = 16 * f
            elif op == "*":
                k = cb if cb is not None else ca
                if k is None:
                    own = 200 * f
                else:
                    k = abs(k)
                    if k <= 1:
                        own = 0
                    else:
                        shifts = k.bit_length() - 1
                        adds = bin(k).count("1") - 1
                        own = (11 * shifts + 12 * adds) * f
            else:
                if cb is None:
                    return None
                k = cb
                if k < 0 or k >= 32:
                    return None
                own = 20 if k >= 8 else 11 * k * f
            return a + b + own
        if isinstance(e, ast.UnaryOp):
            op = _op(e)
            if op not in ("-", "~", "+"):
                return None
            a = self._movable(e.operand)
            if a is None:
                return None
            return a + (0 if op == "+" else 10 * f)
        if isinstance(e, ast.Cast):
            tt = e.target_type
            try:
                tt = g._resolve_typename(tt)
            except Exception:
                return None
            if not self.int_type(tt):
                return None
            a = self._movable(e.expr)
            if a is None:
                return None
            return a + (10 if g._type_size(tt) > g._type_size(
                g._get_expr_type(e.expr)) and g._type_size(tt) > 2 else 0)
        return None

    def key(self, e):
        g = self.g
        if isinstance(e, ast.Identifier):
            if e.name.text in self.vars:
                return f"v:{e.name.text}"
            c = self.const_of(e)
            return None if c is None else f"#{c}"
        c = self.const_of(e)
        if c is not None:
            return f"#{c}:{g._get_expr_type(e)!r}"
        t = repr(g._get_expr_type(e))
        if isinstance(e, ast.BinaryOp):
            a, b = self.key(e.left), self.key(e.right)
            if a is None or b is None:
                return None
            return f"({_op(e)}:{t} {a} {b})"
        if isinstance(e, ast.UnaryOp):
            a = self.key(e.operand)
            return None if a is None else f"(u{_op(e)}:{t} {a})"
        if isinstance(e, ast.Cast):
            a = self.key(e.expr)
            if a is None:
                return None
            return f"(cast:{t} {a})"
        return None


class LicmMixin:
    """The hook of the pass in the code generator."""

    enable_licm = True
    enable_licm_load = True

    def _licm_isr_set(self, unit) -> set:
        """The names that an interrupt function, or a function that one
        calls, mentions.  A load of such a global array does not move."""
        from . import codegen as cg
        mentions: dict = {}
        for item in unit.items:
            if not isinstance(item, ast.FunctionDef) or item.body is None:
                continue
            nm = cg.function_name(item)
            if nm is None:
                continue
            names: set = set()
            stack = [item.body]
            while stack:
                x = stack.pop()
                if isinstance(x, list):
                    stack.extend(x)
                    continue
                if not _is_node(x):
                    continue
                if isinstance(x, ast.Identifier):
                    names.add(x.name.text)
                for f in _fields(x):
                    v = getattr(x, f, None)
                    if v is not None and not isinstance(v, (str, int)):
                        stack.append(v)
            mentions[nm] = names
        roots = (set(self.isr_funcs or {})
                 | set(getattr(self, "vector_funcs", None) or ()))
        out: set = set()
        todo = [r for r in roots if r in mentions]
        seen = set(todo)
        while todo:
            f = todo.pop()
            for nm in mentions.get(f, ()):
                out.add(nm)
                if nm in mentions and nm not in seen:
                    seen.add(nm)
                    todo.append(nm)
        return out

    def _licm_run(self, unit) -> dict:
        stats = {"n": 0}
        if not self.enable_licm:
            return stats
        from . import codegen as cg
        self._licm_isr_names = (self._licm_isr_set(unit)
                                if self.enable_licm_load else set())
        only = os.environ.get("UC80_LICM_ONLY")
        only = set(only.split(",")) if only else None
        saved_locals = self.ctx.locals
        for item in unit.items:
            if not isinstance(item, ast.FunctionDef) or item.body is None:
                continue
            name = cg.function_name(item)
            if name is None or (only is not None and name not in only):
                continue
            if (name in getattr(self, "naked", ())
                    or name in (self.isr_funcs or {})
                    or name in (getattr(self, "vector_funcs", None) or ())
                    or _unsafe(item.body)):
                continue
            snap = self._type_scope_snapshot()
            self.ctx.locals = {}
            try:
                self._register_body_types(item.body)
                fn = _Fn(self, item, stats)
                try:
                    fn.run()
                except RecursionError:
                    # every edit keeps the meaning of the function
                    pass
            finally:
                self.ctx.locals = saved_locals
                self._type_scope_restore(snap)
        return stats
