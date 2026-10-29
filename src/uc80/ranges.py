"""Counters that are never negative (docs/range_compare.md).

A signed 16-bit compare of ``x`` with a constant costs 44 T-states, an
unsigned compare costs 22.  The two give the same answer when ``x`` is in
0..32767.  The loop variable of ``for (x = 0; x < 80; x++)`` is in that
range, but the compare does not know this.  This pass finds such
variables, once for each function.

A local variable is in the set when all of these hold:

  - its type is a signed 16-bit integer, not volatile, with automatic
    storage, and it is not a parameter;
  - its address is never taken;
  - every store to the variable is ``v = K`` (or an initializer) with a
    constant K in 0..32767, or the update clause of a ``for`` statement that
    is ``v++``, ``++v`` or ``v += K`` where the condition of that
    statement has the conjunct ``v < L`` or ``v <= L`` with L a constant and
    the value after one more step still fits in 0..32767;
  - the body and the condition of such a ``for`` statement store nothing to
    the variable;
  - the function has no label, no inline assembly, and no case label that
    a switch outside a loop owns inside the loop (a jump into a loop
    could skip the condition).

The first store to such a variable gives a value in range.  The update
runs only after the condition was true, and nothing changes the variable
between, so the update gives a value in range too.  By induction every
value of the variable is in range.
"""

from __future__ import annotations

import os
import sys

from uc_core import ast
from uc_core import ast_legacy as lt

from .addrcse import _fields, _is_node

_ASSIGN = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")
MAXV = 32767


def _op(n) -> str:
    return getattr(n.op, "text", n.op)


class RangeMixin:
    enable_range_cmp = True
    _nonneg_names: frozenset = frozenset()

    def _range_facts(self, func) -> frozenset:
        if not self.enable_range_cmp:
            return frozenset()
        try:
            names = self._range_facts_1(func)
        except Exception:
            return frozenset()
        if os.environ.get("UC80_RANGE_DEBUG"):
            from . import codegen as cg
            print(f"range: {cg.function_name(func)}: {sorted(names)}",
                  file=sys.stderr)
        return names

    def _small_const(self, e):
        c = self._const_int_operand(e)
        return c if isinstance(c, int) else None

    def _range_facts_1(self, func) -> frozenset:
        from . import codegen as cg
        from .reglocals_gen import address_taken_names
        decl_ok: dict = {}          # name -> every declaration qualifies
        bad: set = set()
        guarded: set = set()        # ids of update nodes that are fine
        for p in cg.function_params(func):
            if isinstance(p, ast.ParamDecl):
                nm = cg.declarator_ident(p.declarator)
                if nm:
                    bad.add(nm)
        vol = self._vol.names_for(func)
        # pass 1: labels, asm, declarations, for statements
        stack = [(func.body, 0, 0)]       # node, loop depth, switch depth
        loops_sw = []                      # unused
        while stack:
            x, ld, sw = stack.pop()
            if isinstance(x, list):
                stack.extend((y, ld, sw) for y in x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, (ast.LabelStmt, ast.AsmDeclaration,
                              ast.StmtExpr)):
                return frozenset()
            if isinstance(x, (ast.CaseStmt, ast.DefaultStmt)) and sw == 0 \
                    and ld > 0:
                return frozenset()
            if isinstance(x, ast.Declaration):
                self._range_decl(x, decl_ok, bad)
            if isinstance(x, ast.ForStmt):
                self._range_for(x, guarded, bad)
            nld, nsw = ld, sw
            if isinstance(x, (ast.ForStmt, ast.WhileStmt, ast.DoWhileStmt)):
                nld = ld + 1
                nsw = 0       # a switch outside this loop does not count
            if isinstance(x, ast.SwitchStmt):
                nsw = sw + 1
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append((v, nld, nsw))
        # pass 2: every store
        stack = [func.body]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, ast.BinaryOp) and _op(x) in _ASSIGN:
                if isinstance(x.left, ast.Identifier) and id(x) not in guarded:
                    nm = x.left.name.text
                    c = self._small_const(x.right) if _op(x) == "=" else None
                    if c is None or not 0 <= c <= MAXV:
                        bad.add(nm)
            elif isinstance(x, (ast.UnaryOp, ast.PostfixOp)) and _op(x) in (
                    "++", "--"):
                if isinstance(x.operand, ast.Identifier):
                    if id(x) not in guarded:
                        bad.add(x.operand.name.text)
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append(v)
        taken = address_taken_names(func.body)
        return frozenset(nm for nm, ok in decl_ok.items()
                         if ok and nm not in bad and nm not in taken
                         and nm not in vol)

    def _range_decl(self, n, decl_ok, bad) -> None:
        from . import codegen as cg
        sc = cg.decl_storage_class(n.decl_specs)
        if sc == "typedef":
            return
        for nm, full, init, is_fn in cg.iter_var_decls(n):
            if nm is None:
                continue
            ok = sc not in ("static", "extern") and not is_fn
            t = self._to_legacy_type(full)
            ok = (ok and isinstance(t, lt.BasicType)
                  and t.name in ("int", "short")
                  and t.is_signed is not False
                  and not t.is_volatile and self._type_size(t) == 2)
            if ok and init is not None:
                c = self._small_const(init)
                ok = c is not None and 0 <= c <= MAXV
            decl_ok[nm] = decl_ok.get(nm, True) and ok

    def _to_legacy_type(self, full):
        from . import codegen as cg
        return cg._to_legacy(full)

    def _range_for(self, n, guarded, bad) -> None:
        u = n.update
        v = step = None
        if (isinstance(u, (ast.UnaryOp, ast.PostfixOp)) and _op(u) == "++"
                and isinstance(u.operand, ast.Identifier)):
            v, step = u.operand.name.text, 1
        elif (isinstance(u, ast.BinaryOp) and _op(u) == "+="
                and isinstance(u.left, ast.Identifier)):
            c = self._small_const(u.right)
            if c is not None and 1 <= c <= MAXV:
                v, step = u.left.name.text, c
        if v is None:
            return
        # the conjuncts of the condition
        conj = []
        todo = [n.condition]
        while todo:
            c = todo.pop()
            if isinstance(c, ast.BinaryOp) and _op(c) == "&&":
                todo += [c.left, c.right]
            elif c is not None:
                conj.append(c)
        best = None
        for c in conj:
            if not (isinstance(c, ast.BinaryOp)
                    and _op(c) in ("<", "<=", ">", ">=")):
                continue
            op, l, r = _op(c), c.left, c.right
            if op in (">", ">="):
                l, r, op = r, l, "<" if op == ">" else "<="
            if not (isinstance(l, ast.Identifier) and l.name.text == v):
                continue
            k = self._small_const(r)
            if k is None:
                continue
            top = k - 1 if op == "<" else k      # the largest value v has
            if top + step <= MAXV:
                best = c
        if best is None:
            bad.add(v)
            return
        # nothing in the condition or the body stores to v
        stack = [n.condition, n.body]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
                continue
            if not _is_node(x):
                continue
            tgt = None
            if isinstance(x, ast.BinaryOp) and _op(x) in _ASSIGN:
                tgt = x.left
            elif isinstance(x, (ast.UnaryOp, ast.PostfixOp)) and _op(x) in (
                    "++", "--"):
                tgt = x.operand
            if isinstance(tgt, ast.Identifier) and tgt.name.text == v:
                bad.add(v)
                return
            for f in _fields(x):
                y = getattr(x, f, None)
                if y is not None and not isinstance(y, (str, int)):
                    stack.append(y)
        guarded.add(id(u))

    # -- the use ---------------------------------------------------------

    def _is_nonneg_operand(self, e) -> bool:
        """``e`` is in 0..32767: a constant there, or a local of the set."""
        k = self._cmp_const(e)
        if k is not None:
            return 0 <= k <= MAXV
        if not self._nonneg_names or not isinstance(e, ast.Identifier):
            return False
        sym = self._simple_sym(e)
        return (sym is not None and not sym.is_global and not sym.is_param
                and not sym.is_static and sym.name in self._nonneg_names
                and self._type_size(sym.sym_type) == 2)

    def _both_nonneg(self, left, right) -> bool:
        return (self.enable_range_cmp and self._is_nonneg_operand(left)
                and self._is_nonneg_operand(right))
