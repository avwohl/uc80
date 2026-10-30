"""Index narrowing (docs/narrow_index.md).

A pointer holds 16 bits.  In ``a[i * n + j]`` with 32-bit ``i``, ``n`` and
``j``, only the low 16 bits of the index reach the address, and the low 16
bits of a sum, a difference, a product, a left shift by a constant and the
bit operations depend on the low 16 bits of the operands only.  The pass
computes such an index in 16 bits::

    a[(unsigned)i * (unsigned)n + (unsigned)j]

A 32-bit multiply costs a call to a library routine and about 30 times
the T-states of the 16-bit multiply.

The pass changes the index of ``a[e]`` and the integer operand of
``p + e``, ``e + p``, ``p - e``, ``p += e`` and ``p -= e`` when ``e`` is a
32-bit integer expression whose top operator is one of ``+ - * << & | ^``,
unary ``-`` or ``~``.  Every leaf of ``e`` that is not one of these (a
variable, a call, a division, a right shift, a comparison, a cast to a
type narrower than 16 bits) is converted to ``unsigned``: the same value
modulo 65536.  A leaf is evaluated once, as before.  The pass needs a
16-bit ``int`` and a 16-bit pointer, and it leaves an index of 64 bits and
a float alone.
"""

from __future__ import annotations

import os
import sys

from uc_core import ast
from uc_core import ast_legacy as lt
from uc_core._const import make_int_lit

from .addrcse import _fields, _is_node, _unsafe
from .licm import _SKIP

_ARITH = ("+", "-", "*", "<<", "&", "|", "^")
_U16 = lt.BasicType(name="int", is_signed=False)


class _Abort(Exception):
    pass


def _op(n) -> str:
    return getattr(n.op, "text", n.op)


class _Fn:
    def __init__(self, gen, func, stats):
        self.g = gen
        self.func = func
        self.stats = stats
        self.debug = bool(os.environ.get("UC80_NARROW_DEBUG"))

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

    # -- the walk ---------------------------------------------------------

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
        if isinstance(n, ast.Index):
            self.index(n)
        elif isinstance(n, ast.BinaryOp):
            self.pointer_op(n)

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

    # -- the rewrite ------------------------------------------------------

    def ty(self, e):
        return self.g._get_expr_type(e)

    def is_int(self, t) -> bool:
        g = self.g
        return (isinstance(t, (lt.BasicType, lt.EnumType))
                and not g._is_float_type(t)
                and not (isinstance(t, lt.BasicType)
                         and t.name in ("void", "_Bool", "bool")))

    def wide32(self, e) -> bool:
        try:
            t = self.ty(e)
            return self.is_int(t) and self.g._type_size(t) == 4
        except Exception:
            return False

    def is_ptr(self, e) -> bool:
        try:
            return isinstance(self.ty(e), (lt.PointerType, lt.ArrayType))
        except Exception:
            return False

    def index(self, n) -> None:
        if self.is_ptr(n.array) and self.wide32(n.index):
            new = self.try_narrow(n.index)
            if new is not None:
                n.index = new

    def pointer_op(self, n) -> None:
        op = _op(n)
        if op in ("+", "-", "+=", "-=") and self.is_ptr(n.left):
            if self.wide32(n.right):
                new = self.try_narrow(n.right)
                if new is not None:
                    n.right = new
        elif op == "+" and self.is_ptr(n.right) and self.wide32(n.left):
            new = self.try_narrow(n.left)
            if new is not None:
                n.left = new

    def worthwhile(self, e) -> bool:
        while (isinstance(e, ast.Cast)):
            e = e.expr
        if isinstance(e, ast.BinaryOp):
            return _op(e) in _ARITH
        return isinstance(e, ast.UnaryOp) and _op(e) in ("-", "~")

    def try_narrow(self, e):
        if not self.worthwhile(e):
            return None
        try:
            new = self.narrow(e)
        except _Abort:
            return None
        except Exception as ex:
            if self.debug:
                print(f"narrow: failed: {ex!r}", file=sys.stderr)
            return None
        self.stats["n"] += 1
        if self.debug:
            from . import codegen as cg
            print(f"narrow: {cg.function_name(self.func)}: index narrowed",
                  file=sys.stderr)
        return new

    def const_of(self, e):
        try:
            return self.g._const_int_operand(e)
        except Exception:
            return None

    def narrow(self, e):
        g = self.g
        c = self.const_of(e)
        if c is not None:
            return make_int_lit(c & 0xFFFF, unsigned=True)
        if isinstance(e, ast.BinaryOp) and _op(e) in _ARITH:
            op = _op(e)
            if op == "<<":
                k = self.const_of(e.right)
                if k is None or not 0 <= k <= 15:
                    return self.leaf(e)
                return ast.BinaryOp(left=self.narrow(e.left), op=e.op,
                                    right=make_int_lit(k), pos=e.pos)
            return ast.BinaryOp(left=self.narrow(e.left), op=e.op,
                                right=self.narrow(e.right), pos=e.pos)
        if isinstance(e, ast.UnaryOp) and _op(e) in ("-", "~", "+"):
            return ast.UnaryOp(op=e.op, operand=self.narrow(e.operand),
                               pos=e.pos)
        if isinstance(e, ast.Cast):
            tt = g._resolve_typename(e.target_type)
            if self.is_int(tt) and g._type_size(tt) >= 2:
                return self.narrow(e.expr)
        return self.leaf(e)

    def leaf(self, e):
        g = self.g
        t = self.ty(e)
        if not self.is_int(t):
            raise _Abort()
        size = g._type_size(t)
        if size > 4:
            raise _Abort()
        if (size == 2 and isinstance(t, lt.BasicType)
                and t.name in ("int", "short") and t.is_signed is False
                and not t.is_volatile):
            return e
        return ast.Cast(target_type=_U16, expr=e,
                        pos=getattr(e, "pos", None) or ast._Pos())


class NarrowMixin:
    """The hook of the pass in the code generator."""

    enable_narrow_index = True

    def _narrow_run(self, unit) -> dict:
        stats = {"n": 0}
        if not self.enable_narrow_index:
            return stats
        if (self.type_config.ptr_size != 2
                or self.type_config.int_size != 2):
            return stats
        from . import codegen as cg
        only = os.environ.get("UC80_NARROW_ONLY")
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
