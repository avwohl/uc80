"""A counted loop that stores a constant in a byte array becomes a block fill.

See docs/fill_idiom.md.  The loop

    for (i = C0; i < N; i++) a[i] = K;

(or ``<=``, or ``++i``, or ``i += 1``) stores K in N - C0 bytes of the
global array ``a``.  The code generator emits the init clause, one fill of
the bytes (the inline ``LDIR`` fill of blockops.py), and a store of the
final value of ``i``.  A loop pass costs about 126 T-states a byte, the
fill about 21.

The rewrite needs, and checks:

- the init clause is ``i = C0`` with a constant C0; ``i`` is an integer
  variable that is not volatile and whose type holds the final value;
- the test is ``i < N`` or ``i <= N`` with a constant N, the update is
  ``i++``, ``++i`` or ``i += 1``, and the body is one assignment ``a[i] = K``
  (alone or in a block) with a constant K;
- ``a`` is a global array of bytes that is not volatile, not const, not
  ``_Bool``, and the last index lies inside the array;
- the loop runs at least 3 times.
"""

from __future__ import annotations

from uc_core import ast
from uc_core import ast_legacy as lt
from uc_core.ast_optimizer import _binop
from uc_core._const import make_int_lit


def _op(n) -> str:
    o = n.op
    return o.text if hasattr(o, "text") else o


class FillIdiomMixin:
    enable_fill_idiom = True
    fill_idiom_count = 0

    def _fill_shape(self, stmt):
        """(i name, C0, N, inclusive, a name, K) of a loop of the form
        above, else None.  Checks the syntax only."""
        init = stmt.init
        if isinstance(init, ast.ExpressionStmt):
            init = init.expr
        if not (isinstance(init, ast.BinaryOp) and _op(init) == "="
                and isinstance(init.left, ast.Identifier)):
            return None
        i = init.left.name.text
        c0 = self._const_int_operand(init.right)
        cond = stmt.condition
        if c0 is None or not (
                isinstance(cond, ast.BinaryOp) and _op(cond) in ("<", "<=")
                and isinstance(cond.left, ast.Identifier)
                and cond.left.name.text == i):
            return None
        n = self._const_int_operand(cond.right)
        if n is None:
            return None
        upd = stmt.update
        ok = False
        if isinstance(upd, (ast.PostfixOp, ast.UnaryOp)):
            operand = upd.operand
            ok = (_op(upd) == "++" and isinstance(operand, ast.Identifier)
                  and operand.name.text == i)
        elif isinstance(upd, ast.BinaryOp) and _op(upd) == "+=":
            ok = (isinstance(upd.left, ast.Identifier)
                  and upd.left.name.text == i
                  and self._const_int_operand(upd.right) == 1)
        if not ok:
            return None
        body = stmt.body
        while isinstance(body, ast.CompoundStmt):
            if len(body.items or []) != 1:
                return None
            body = body.items[0]
        if isinstance(body, ast.ExpressionStmt):
            body = body.expr
        if not (isinstance(body, ast.BinaryOp) and _op(body) == "="
                and isinstance(body.left, ast.Index)
                and isinstance(body.left.array, ast.Identifier)
                and isinstance(body.left.index, ast.Identifier)
                and body.left.index.name.text == i):
            return None
        k = self._const_int_operand(body.right)
        if k is None:
            return None
        return (i, c0, n, _op(cond) == "<=", body.left.array.name.text, k)

    def _fill_try(self, stmt) -> bool:
        """Emit the loop ``stmt`` as a fill when it has the shape and the
        checks hold.  False: nothing was emitted."""
        if not self.enable_fill_idiom or not self.enable_blockops:
            return False
        shape = self._fill_shape(stmt)
        if shape is None:
            return False
        i, c0, n, inclusive, a, k = shape
        last = n if inclusive else n - 1
        count = last - c0 + 1
        if count < 3 or count > 0xFFFF or c0 < 0:
            return False
        final = last + 1
        isym, asym = self.ctx.lookup(i), self.ctx.lookup(a)
        if isym is None or asym is None or i == a:
            return False
        # the counter
        it = self._to_legacy_type(isym.sym_type)
        if not (isinstance(it, lt.BasicType) and not it.is_volatile
                and not self._is_float_type(it)
                and not self._is_long_long_type(it)
                and not self._is_bool_type(it)):
            return False
        size = self._type_size(it)
        signed = self._is_signed_type(it)
        top = (1 << (8 * size - (1 if signed else 0))) - 1
        if size not in (1, 2, 4) or final > top:
            return False
        if i in self._vol_names or a in self._vol_names:
            return False
        # the array: a global of bytes, with the last index inside
        at = self._to_legacy_type(asym.sym_type)
        if not (asym.is_global and isinstance(at, lt.ArrayType)
                and at.size is not None):
            return False
        et = self._to_legacy_type(at.base_type)
        if not (isinstance(et, lt.BasicType) and not et.is_volatile
                and not et.is_const and not self._is_bool_type(et)
                and not self._is_float_type(et)
                and self._type_size(et) == 1):
            return False
        length = (self._eval_const_expr(at.size)
                  if not isinstance(at.size, int) else at.size)
        if length is None or last >= length:
            return False
        label = asym.label()
        # init clause as the loop would run it
        init = stmt.init
        expr = init.expr if isinstance(init, ast.ExpressionStmt) else init
        with self._vol_region(expr):
            self.gen_expr_discard(expr)
        self._bo_fill(f"{label}+{c0}" if c0 else label, count, k & 0xFF)
        self._bo_count("fill_loop")
        self.fill_idiom_count += 1
        store = _binop("=", expr.left, make_int_lit(final))
        with self._vol_region(store):
            self.gen_expr_discard(store)
        return True
