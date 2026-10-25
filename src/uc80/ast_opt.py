"""uc80's AST optimizer: uc_core's, with literal types kept.

uc_core's ASTOptimizer knows no variable types, and two of its rewrites
lose the type of a literal:

  * Strength reduction turns ``x * 8UL`` into ``x << 3`` and ``x / 8u``
    into ``x >> 3``.  A shift has the type of its promoted LEFT operand,
    so the U or L of the literal is lost: ``x * 8UL`` is then done in 16
    bits, and ``-8 / 8u`` shifts arithmetically.  Codegen knows the
    types, so it does these reductions itself.  (``x * 8`` is wrong for
    a float x as well; uc_core now skips float operands, and uc80 leaves
    the multiply to the code generator.)
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
from uc_core.ast_optimizer import ASTOptimizer, _binop
from uc_core._const import int_flags, int_value, make_int_lit

# Conversion ranks of the types a literal or a folded result can have.
_INT, _LONG, _LLONG = 1, 2, 3

_FOLD_OPS = frozenset(("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
                       "==", "!=", "<", ">", "<=", ">="))


class Uc80ASTOptimizer(ASTOptimizer):
    """See the module docstring."""

    # ---- identities that would lose the type of an operand

    def _simplify_identity(self, expr):
        """``x + 0L`` is a long and ``x * 1u`` an unsigned int.  uc_core
        drops only a literal with a U or LL suffix, as on a target whose
        long is as wide as its int; here an L matters too:
        ``(x + 0L) * y`` is a 32-bit multiply, and ``x`` alone was 16."""
        for e in (expr.left, expr.right):
            if isinstance(e, ast.IntLiteral) and any(int_flags(e)[:3]):
                return None
        return super()._simplify_identity(expr)

    def _simplify_zero(self, expr):
        """``x * 0``, ``x & 0``, ``0 / x``, ``0 << x`` have the type of x
        promoted (and of the literal), which uc_core cannot know: an
        int 0 replacing ``ui * 0`` made ``(ui * 0) + -129 > 15`` a signed
        comparison.  Left to the code generator."""
        return None

    # ---- strength reduction

    def _strength_reduce_mul(self, expr):
        """``x * 8`` is left to the code generator.  uc_core turns it into
        ``x << 3`` (or ``x + x``), which is wrong for a float or double
        ``x``, loses the U or L of a literal operand, and duplicates the
        operand of ``x * 2`` (``(a % 2) * 2`` computed ``a % 2`` twice).
        The code generator knows the types and has its own multiply by a
        constant."""
        return None

    @staticmethod
    def _is_unsigned_literal(*exprs):
        # Only the x / 2^n and x % 2^n reductions ask this.
        return False

    def _fold_ternary(self, expr):
        """K ? a : b is not folded to a or b unless both are integer
        literals of one type: the conditional has the common type of the
        two (1 ? ui : ul is unsigned long), which uc_core, knowing no
        variable types, cannot tell.  A constant condition costs one
        test in the generated code and the peephole removes that."""
        t, f = expr.true_expr, expr.false_expr
        if (isinstance(expr.condition, ast.IntLiteral)
                and isinstance(t, ast.IntLiteral)
                and isinstance(f, ast.IntLiteral)
                and self._literal_type(t)[:2] == self._literal_type(f)[:2]):
            self._stat("ternary_fold")
            self._changed = True
            return t if int_value(expr.condition) != 0 else f
        return expr

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
        if op in ("^", "-") and self._is_same_identifier(expr.left,
                                                         expr.right):
            # uc_core folds ``x ^ x`` and ``x - x`` to an int 0, but the
            # value has x's type: ``(-31932 + (ul ^ ul)) >> 31`` is an
            # unsigned long shift, and with an int it shifted a negative.
            return expr
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

    # (outer op, inner op) -> (new op, how c3 is made from c1 and c2)
    _NESTED = {
        ("+", "+"): ("+", lambda c1, c2: c1 + c2),
        ("+", "-"): ("+", lambda c1, c2: c2 - c1),
        ("-", "+"): ("+", lambda c1, c2: c1 - c2),
        ("-", "-"): ("-", lambda c1, c2: c1 + c2),
        ("*", "*"): ("*", lambda c1, c2: c1 * c2),
    }

    def _fits(self, value: int, rank: int, unsigned: bool) -> bool:
        bits = self._bits(rank)
        if unsigned:
            return 0 <= value < (1 << bits)
        return -(1 << (bits - 1)) <= value < (1 << (bits - 1))

    def _nested_const_fold(self, expr):
        """(x op c1) op c2 -> x op c3, with c3 of the common type of c1, c2.

        uc_core wraps c3 to the width of the literals, without a sign:
        ``(x - 31) - (-127)`` became ``x - 65440``, a long constant, and
        a long x then lost 65536 on the way.  c3 is made here from the
        exact values, and the fold is not made when c3 does not fit the
        common type of c1 and c2 (x may be wider than either).
        """
        inner = expr.left
        outer = expr.op.text if hasattr(expr.op, "text") else expr.op
        if self._maybe_float(inner):
            return None          # (f + 1) + 2 is not f + 3 in a float
        if (isinstance(expr.right, ast.IntLiteral)
                and isinstance(inner, ast.BinaryOp)
                and isinstance(inner.right, ast.IntLiteral)):
            iop = inner.op.text if hasattr(inner.op, "text") else inner.op
            rule = self._NESTED.get((outer, iop))
            if rule is not None:
                t1 = self._literal_type(inner.right)
                t2 = self._literal_type(expr.right)
                rank, unsigned = self._common(t1, t2)
                c3 = rule[1](self._wrap(t1[2], t1[0], t1[1]),
                             self._wrap(t2[2], t2[0], t2[1]))
                if not self._fits(c3, rank, unsigned):
                    return None
                new_op = rule[0]
                if new_op == "-" and c3 < 0 and self._fits(-c3, rank, unsigned):
                    new_op, c3 = "+", -c3
                elif new_op == "-" and c3 < 0:
                    return None
                self._stat("nested_fold")
                self._changed = True
                if c3 == 0 and new_op in ("+", "-"):
                    return inner.left
                return _binop(new_op, inner.left,
                              self._mint(c3, rank, unsigned))
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
