"""Division and remainder by a constant that the range of the dividend decides
(docs/range_div.md).

In ``temp - (temp / 100000L) * 100000L``, ``temp`` is a ``long`` that holds
the result of a function that returns an ``int``.  The value lies in
-32768..32767, so ``temp / 100000L`` is 0 and ``temp % 100000L`` is
``temp``.  A 32-bit division costs about 2000 T-states.  The pass finds
such operands and puts the result in the place of the operation.

The range of an expression comes from its type: an expression of a type of
16 bits or less holds a value of that type, whatever the code does.  A
cast keeps the range of the operand when the new type holds the range.  A
local variable of 32 bits has the union of the ranges of all values that
the function stores to it (``v = e`` and ``T v = e``), when it is not a
parameter, not ``static``, not ``volatile`` and its address is never
taken, and no store is ``++``, ``--`` or ``op=``.  A constant has its
value.  Everything else has no range.

``x / k`` with ``k > 0`` becomes 0 when -k < x < k (0 <= x < k for an
unsigned operation).  ``x % k`` becomes ``x`` under the same condition and
when ``x`` has the type of the result.  The dividend must have no side
effect.  The pass needs a 16-bit int.
"""

from __future__ import annotations

import os
import sys

from uc_core import ast
from uc_core import ast_legacy as lt
from uc_core._const import _make_token

from .addrcse import _fields, _is_node, _unsafe
from .licm import _SKIP

_ASSIGN = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")


def _op(n) -> str:
    return getattr(n.op, "text", n.op)


class _Fn:
    def __init__(self, gen, func, stats):
        self.g = gen
        self.func = func
        self.stats = stats
        self.debug = bool(os.environ.get("UC80_RANGEDIV_DEBUG"))
        self.stores: dict | None = None
        self.memo: dict = {}
        self.busy: set = set()

    # -- the walk ---------------------------------------------------------

    def run(self) -> None:
        from . import codegen as cg
        g = self.g
        for p in cg.function_params(self.func):
            if not isinstance(p, ast.ParamDecl):
                continue
            try:
                pn, pt = cg.resolve_type_from_decl(p.decl_specs, p.declarator)
                t = cg._to_legacy(pt)
                if isinstance(t, lt.ArrayType):
                    t = lt.PointerType(base_type=t.base_type)
            except Exception:
                continue
            if pn:
                g.ctx.locals[pn] = cg.Symbol(name=pn, sym_type=t,
                                             is_param=True)
        self.visit(self.func.body, None, None)

    def visit(self, n, cont, slot) -> None:
        if isinstance(n, list):
            for i, x in enumerate(n):
                self.visit(x, n, i)
            return
        if isinstance(n, tuple):
            for x in n:
                self.visit(x, None, None)
            return
        if not _is_node(n) or isinstance(n, _SKIP):
            return
        if isinstance(n, (ast.CompoundStmt, ast.ForStmt)):
            g = self.g
            saved = dict(g.ctx.locals)
            try:
                self.children(n)
            finally:
                g.ctx.locals = saved
            return
        if isinstance(n, ast.Declaration):
            self.declaration(n)
            return
        self.children(n)
        if (cont is not None and isinstance(n, ast.BinaryOp)
                and _op(n) in ("/", "%")):
            try:
                new = self.fold(n)
            except Exception as ex:
                if self.debug:
                    print(f"rangediv: failed: {ex!r}", file=sys.stderr)
                new = None
            if new is not None:
                self.stats["n"] += 1
                if self.debug:
                    from . import codegen as cg
                    print(f"rangediv: {cg.function_name(self.func)}: "
                          f"{_op(n)} folded", file=sys.stderr)
                if isinstance(cont, list):
                    cont[slot] = new
                else:
                    setattr(cont, slot, new)

    def children(self, n) -> None:
        for f in _fields(n):
            v = getattr(n, f, None)
            if v is None or isinstance(v, (str, int)):
                continue
            if isinstance(v, (list, tuple)):
                self.visit(v, None, None)
            elif _is_node(v):
                self.visit(v, n, f)

    def declaration(self, n) -> None:
        from . import codegen as cg
        g = self.g
        try:
            sc = cg.decl_storage_class(n.decl_specs)
            decls = [] if sc == "typedef" else list(cg.iter_var_decls(n))
        except Exception:
            return
        inits = [d for d in n.declarators
                 if isinstance(d, ast.InitDeclaratorWithInit)]
        for nm, full, init, is_fn in decls:
            if nm is None:
                continue
            try:
                t = cg._to_legacy(full)
            except Exception:
                t = None
            g.ctx.locals[nm] = cg.Symbol(name=nm, sym_type=t,
                                         is_static=(sc == "static"))
        if sc in ("static", "extern", "typedef"):
            return
        for d in inits:
            self.visit(d.init, d, "init")

    # -- ranges -----------------------------------------------------------

    def ty(self, e):
        return self.g._get_expr_type(e)

    def is_int(self, t) -> bool:
        g = self.g
        return (isinstance(t, (lt.BasicType, lt.EnumType))
                and not g._is_float_type(t)
                and not getattr(t, "is_volatile", False)
                and not (isinstance(t, lt.BasicType)
                         and t.name in ("void", "_Bool", "bool")))

    def bounds(self, t):
        """The range of a value of the integer type ``t``, or None."""
        if not self.is_int(t):
            return None
        g = self.g
        if isinstance(t, lt.EnumType):
            return None
        return g._type_bounds(t)

    def const_of(self, e):
        try:
            c = self.g._const_int_operand(e)
        except Exception:
            return None
        return c if isinstance(c, int) else None

    def scan_stores(self) -> None:
        """name -> list of the expressions stored by ``v = e``; the name
        maps to None when a store has another form."""
        from . import codegen as cg
        from .reglocals_gen import address_taken_names
        stores: dict = {}
        bad: set = set()
        for p in cg.function_params(self.func):
            if isinstance(p, ast.ParamDecl):
                nm = cg.declarator_ident(p.declarator)
                if nm:
                    bad.add(nm)
        stack = [self.func.body]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
                continue
            if isinstance(x, tuple):
                stack.extend(x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, ast.BinaryOp) and _op(x) in _ASSIGN \
                    and isinstance(x.left, ast.Identifier):
                nm = x.left.name.text
                if _op(x) == "=":
                    stores.setdefault(nm, []).append(x.right)
                else:
                    bad.add(nm)
            elif isinstance(x, (ast.UnaryOp, ast.PostfixOp)) and _op(x) in (
                    "++", "--") and isinstance(x.operand, ast.Identifier):
                bad.add(x.operand.name.text)
            elif isinstance(x, ast.Declaration):
                try:
                    sc = cg.decl_storage_class(x.decl_specs)
                except Exception:
                    sc = None
                for d in x.declarators or []:
                    if isinstance(d, ast.InitDeclaratorWithInit):
                        try:
                            nm = cg.declarator_ident(d.declarator)
                        except Exception:
                            nm = None
                        if nm and sc not in ("static", "extern"):
                            stores.setdefault(nm, []).append(d.init)
                        elif nm:
                            bad.add(nm)
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append(v)
        taken = address_taken_names(self.func.body)
        self.vol = self.g._vol.names_for(self.func)
        for nm in bad | set(taken):
            stores[nm] = None
        self.stores = stores

    def local_range(self, name):
        if self.stores is None:
            self.scan_stores()
        if name in self.memo:
            return self.memo[name]
        if name in self.busy:
            return None
        sym = self.g.ctx.locals.get(name)
        if sym is None or sym.is_param or getattr(sym, "is_static", False):
            return None
        st = self.stores.get(name, [])
        if st is None or name in self.vol:
            return None
        self.busy.add(name)
        try:
            r = None
            for e in st:
                a = self.rng(e)
                if a is None:
                    r = None
                    break
                r = a if r is None else (min(r[0], a[0]), max(r[1], a[1]))
            else:
                if r is None:        # no store at all: no value known
                    r = None
        finally:
            self.busy.discard(name)
        self.memo[name] = r
        return r

    def rng(self, e):
        """(lo, hi) of the integer expression ``e``, or None."""
        c = self.const_of(e)
        if c is not None:
            return (c, c)
        t = self.ty(e)
        tb = self.bounds(t)
        if isinstance(e, ast.Identifier):
            sym = self.g.ctx.locals.get(e.name.text)
            if sym is not None and not getattr(sym, "is_global", False):
                lr = self.local_range(e.name.text)
                if lr is not None:
                    return lr
            return tb if tb is not None and self.g._type_size(t) <= 2 \
                else None
        if isinstance(e, ast.Cast):
            tt = self.g._resolve_typename(e.target_type)
            tbt = self.bounds(tt)
            if tbt is None:
                return None
            a = self.rng(e.expr)
            if a is not None and tbt[0] <= a[0] and a[1] <= tbt[1]:
                return a
            return tbt          # the cast wraps into the type
        if tb is not None and self.g._type_size(t) <= 2:
            return tb
        return None

    # -- the fold ---------------------------------------------------------

    def fold(self, n):
        from . import codegen as cg
        g = self.g
        if g.type_config.int_size != 2:
            return None
        k = self.const_of(n.right)
        if k is None or k <= 0:
            return None
        rt = self.ty(n)
        lt_ = self.ty(n.left)
        if not (self.is_int(rt) and self.is_int(lt_)):
            return None
        size = g._type_size(rt)
        if size not in (2, 4):
            return None
        if cg._ast_has_side_effects(n.left):
            return None
        r = self.rng(n.left)
        if r is None:
            return None
        unsigned = getattr(rt, "is_signed", True) is False
        if unsigned:
            if r[0] < 0 or r[1] >= k:
                return None
        elif not (-k < r[0] and r[1] < k):
            return None
        if _op(n) == "/":
            text = "0" + ("U" if unsigned else "") + ("L" if size == 4 else "")
            return ast.IntLiteral(value=_make_token("INT_LIT", text),
                                  pos=ast._Pos())
        # x % k is x when x has the type of the result
        if (g._type_size(lt_) == size
                and (getattr(lt_, "is_signed", True) is False) == unsigned):
            return n.left
        return None


class RangeDivMixin:
    """The hook of the pass in the code generator."""

    enable_range_div = True

    def _rangediv_run(self, unit) -> dict:
        stats = {"n": 0}
        if not self.enable_range_div:
            return stats
        from . import codegen as cg
        only = os.environ.get("UC80_RANGEDIV_ONLY")
        only = set(only.split(",")) if only else None
        saved_locals = self.ctx.locals
        for item in unit.items:
            if not isinstance(item, ast.FunctionDef) or item.body is None:
                continue
            name = cg.function_name(item)
            if name is None or (only is not None and name not in only):
                continue
            if name in getattr(self, "naked", ()) or _unsafe(item.body):
                continue
            snap = self._type_scope_snapshot()
            self.ctx.locals = {}
            try:
                self._register_body_types(item.body)
                try:
                    _Fn(self, item, stats).run()
                except RecursionError:
                    pass
            finally:
                self.ctx.locals = saved_locals
                self._type_scope_restore(snap)
        return stats
