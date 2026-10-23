"""uc80's AST optimizer: uc_core's, with literal types kept.

uc_core's ASTOptimizer knows no variable types, and two of its rewrites
lose the type of a literal:

  * Strength reduction turns ``x * 8UL`` into ``x << 3`` and ``x / 8u``
    into ``x >> 3``.  A shift has the type of its promoted LEFT operand,
    so the U or L of the literal is lost: ``x * 8UL`` is then done in 16
    bits, and ``-8 / 8u`` shifts arithmetically.  Codegen knows the
    types, so it does these reductions itself.
  * Constant folding mints the result without an L suffix, and decides
    signedness without looking at widths: ``-2L`` became an int, and
    ``-256L >= 65280u`` was compared unsigned although a 32-bit long
    holds every 16-bit unsigned int (C 6.3.1.8).  The nested fold
    ``(x + 2L) - 10`` -> ``x + -8`` dropped the L the same way.

Uc80ASTOptimizer turns those two reductions off and folds integer
constants itself, with the C types of the literals (C 6.4.4.1) and the
usual arithmetic conversions, for the sizes in the TypeConfig.
"""

from __future__ import annotations

from uc_core import ast
from uc_core.ast_optimizer import ASTOptimizer
from uc_core._const import int_flags, int_value, make_int_lit

# Conversion ranks of the types a literal or a folded result can have.
_INT, _LONG, _LLONG = 1, 2, 3

_FOLD_OPS = frozenset(("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
                       "==", "!=", "<", ">", "<=", ">="))


class Uc80ASTOptimizer(ASTOptimizer):
    """See the module docstring."""

    # ---- strength reduction that would lose the literal's type

    def _strength_reduce_mul(self, expr):
        for e in (expr.left, expr.right):
            if isinstance(e, ast.IntLiteral):
                rank, unsigned, _ = self._literal_type(e)
                if rank != _INT or unsigned:
                    return None
        return super()._strength_reduce_mul(expr)

    @staticmethod
    def _is_unsigned_literal(*exprs):
        # Only the x / 2^n and x % 2^n reductions ask this.
        return False

    # ---- typed constant folding

    def _bits(self, rank: int) -> int:
        tc = self.type_config
        return 8 * {_INT: tc.int_size, _LONG: tc.long_size,
                    _LLONG: tc.long_long_size}[rank]

    def _literal_type(self, lit) -> tuple[int, bool, int]:
        """(rank, unsigned, value) of an integer literal (C 6.4.4.1)."""
        is_long, is_ll, is_unsigned, is_hex = int_flags(lit)
        value = int_value(lit)
        ranks = [_LLONG] if is_ll else [_LONG, _LLONG] if is_long \
            else [_INT, _LONG, _LLONG]
        for rank in ranks:
            bits = self._bits(rank)
            if is_unsigned:
                if 0 <= value < (1 << bits):
                    return rank, True, value
                continue
            if -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
                return rank, False, value
            if is_hex and 0 <= value < (1 << bits):
                return rank, True, value
        return _LLONG, True, value & ((1 << self._bits(_LLONG)) - 1)

    def _wrap(self, value: int, rank: int, unsigned: bool) -> int:
        bits = self._bits(rank)
        value &= (1 << bits) - 1
        if not unsigned and value >= (1 << (bits - 1)):
            value -= 1 << bits
        return value

    def _mint(self, value: int, rank: int, unsigned: bool):
        lit = make_int_lit(value, unsigned=unsigned)
        suffix = {_INT: "", _LONG: "L", _LLONG: "LL"}[rank]
        if suffix:
            tok = lit.value
            lit = ast.IntLiteral(value=type(tok)(
                name=tok.name, text=tok.text + suffix, line=0, column=0,
                offset=0, file_id=0), pos=lit.pos)
        return lit

    def _common(self, a, b) -> tuple[int, bool]:
        """Usual arithmetic conversions (C 6.3.1.8) of two literal types."""
        (ra, ua, _), (rb, ub, _) = a, b
        rank = max(ra, rb)
        if ua == ub:
            return rank, ua
        ur, sr = (ra, rb) if ua else (rb, ra)
        if ur >= sr:
            return rank, True
        if self._bits(sr) > self._bits(ur):
            return rank, False          # the signed type holds them all
        return rank, True

    def _fold_typed(self, op: str, left, right):
        """Fold ``left op right``; None when C leaves it undefined."""
        lt_, rt_ = self._literal_type(left), self._literal_type(right)
        if op in ("<<", ">>"):
            rank, unsigned, a = lt_
            n = rt_[2]
            if not 0 <= n < self._bits(rank):
                return None
            a = self._wrap(a, rank, unsigned)
            result = a << n if op == "<<" else a >> n
            return self._mint(self._wrap(result, rank, unsigned),
                              rank, unsigned)
        rank, unsigned = self._common(lt_, rt_)
        a = self._wrap(lt_[2], rank, unsigned)
        b = self._wrap(rt_[2], rank, unsigned)
        if op in ("==", "!=", "<", ">", "<=", ">="):
            result = {"==": a == b, "!=": a != b, "<": a < b, ">": a > b,
                      "<=": a <= b, ">=": a >= b}[op]
            return make_int_lit(int(result))
        if op in ("/", "%"):
            if b == 0:
                return None
            q = abs(a) // abs(b)
            if (a < 0) != (b < 0):
                q = -q
            result = q if op == "/" else a - q * b
        else:
            result = {"+": a + b, "-": a - b, "*": a * b, "&": a & b,
                      "|": a | b, "^": a ^ b}[op]
        return self._mint(self._wrap(result, rank, unsigned), rank, unsigned)

    def _optimize_binary(self, expr):
        op = expr.op.text if hasattr(expr.op, "text") else expr.op
        if (op in _FOLD_OPS and isinstance(expr.left, ast.IntLiteral)
                and isinstance(expr.right, ast.IntLiteral)):
            folded = self._fold_typed(op, expr.left, expr.right)
            if folded is None:
                return expr
            self._stat("const_fold")
            self._changed = True
            return folded
        return super()._optimize_binary(expr)

    def _optimize_unary(self, expr):
        op = expr.op.text if hasattr(expr.op, "text") else expr.op
        if op in ("-", "~", "+") and isinstance(expr.operand, ast.IntLiteral):
            rank, unsigned, v = self._literal_type(expr.operand)
            v = self._wrap(v, rank, unsigned)
            result = {"-": -v, "~": ~v, "+": v}[op]
            self._stat("const_fold_unary")
            self._changed = True
            return self._mint(self._wrap(result, rank, unsigned),
                              rank, unsigned)
        return super()._optimize_unary(expr)

    def _nested_const_fold(self, expr):
        """(x op c1) op c2 -> x op c3, with c3 of the common type of c1, c2.

        uc_core mints c3 without the L suffix, so ``(x + 2L) - 10`` lost
        its long type.
        """
        inner = expr.left
        result = super()._nested_const_fold(expr)
        if (isinstance(result, ast.BinaryOp)
                and isinstance(result.right, ast.IntLiteral)
                and isinstance(expr.right, ast.IntLiteral)
                and isinstance(inner, ast.BinaryOp)
                and isinstance(inner.right, ast.IntLiteral)):
            rank, unsigned = self._common(self._literal_type(inner.right),
                                          self._literal_type(expr.right))
            if rank != _INT or unsigned:
                c3 = self._wrap(int_value(result.right), rank, unsigned)
                result.right = self._mint(c3, rank, unsigned)
        return result
