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

import copy

from uc_core import ast
from uc_core.ast_optimizer import (ASTOptimizer, _binop, _idname, _iv, _op,
                                   make_identifier)
from uc_core._const import int_flags, int_value, make_int_lit

from .addrcse import _fields, _is_node

# Conversion ranks of the types a literal or a folded result can have.
_INT, _LONG, _LLONG = 1, 2, 3

_FOLD_OPS = frozenset(("+", "-", "*", "/", "%", "&", "|", "^", "<<", ">>",
                       "==", "!=", "<", ">", "<=", ">="))


def _has_declaration(node) -> bool:
    """A declaration anywhere in the statement."""
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
            continue
        if isinstance(x, ast.Declaration):
            return True
        if not _is_node(x):
            continue
        for f in _fields(x):
            v = getattr(x, f, None)
            if v is not None and not isinstance(v, (str, int)):
                stack.append(v)
    return False


def _has_type_use(node) -> bool:
    """sizeof, typeof, _Generic, alignof or offsetof in the statement."""
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
            continue
        if not _is_node(x):
            continue
        if type(x).__name__ in ("SizeofExpr", "SizeofType", "AlignofType",
                                "TypeofExpr", "TypeofType",
                                "TypeofUnqualExpr", "GenericSelection",
                                "_GenericSelection", "OffsetofExpr"):
            return True
        for f in _fields(x):
            v = getattr(x, f, None)
            if v is not None and not isinstance(v, (str, int)):
                stack.append(v)
    return False


def _const_address(node) -> bool:
    """``name[c1][c2]...`` with integer literals: a fixed address."""
    while isinstance(node, ast.Index):
        if not isinstance(node.index, ast.IntLiteral):
            return False
        node = node.array
    return isinstance(node, ast.Identifier)


def _cost(node) -> int:
    """A rough size in bytes of the code of a tree.  A variable costs 3
    bytes (a load or a store), an operator 2, a literal 2, a fixed
    address 3, an index that is not a literal 8 bytes more than the index
    expression, a call 8 bytes more than its arguments and a loop 12 bytes
    for its control.  A constant assignment to a plain variable is free:
    the dead stores of an unrolled loop go away."""
    if node is None or isinstance(node, (str, int)):
        return 0
    if isinstance(node, list):
        return sum(_cost(x) for x in node)
    if not _is_node(node):
        return 0
    name = type(node).__name__
    if name in ("IntLiteral", "CharLiteral", "FloatLiteral",
                "StringLiteral"):
        return 2
    if name == "Identifier":
        return 3
    if name == "Index":
        if _const_address(node):
            return 3
        return 8 + _cost(node.index) + (
            0 if isinstance(node.array, (ast.Identifier, ast.Index))
            else _cost(node.array))
    if name == "ExpressionStmt":
        e = node.expr
        if (isinstance(e, ast.BinaryOp) and _op(e) == "="
                and isinstance(e.left, ast.Identifier)
                and isinstance(e.right, ast.IntLiteral)):
            return 0
        return _cost(e)
    if name in ("Call", "CallNoArgs"):
        return 8 + sum(_cost(getattr(node, f, None)) for f in _fields(node)
                       if f != "func")
    if name == "ForStmt":
        return 12 + sum(_cost(getattr(node, f, None)) for f in _fields(node))
    if name in ("WhileStmt", "DoWhileStmt"):
        return 8 + sum(_cost(getattr(node, f, None)) for f in _fields(node))
    if name in ("IfStmt", "IfStmtElse"):
        return 4 + sum(_cost(getattr(node, f, None)) for f in _fields(node))
    total = 0
    for f in _fields(node):
        total += _cost(getattr(node, f, None))
    if name in ("BinaryOp", "UnaryOp", "PostfixOp", "TernaryOp", "Cast"):
        total += 2
    return total


def _size_of_tree(node) -> tuple[int, bool]:
    """(the number of nodes, whether a call is in the tree)."""
    count = 0
    calls = False
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
            continue
        if not _is_node(x):
            continue
        count += 1
        if type(x).__name__ in ("Call", "CallNoArgs", "AsmStmt",
                                "AsmDeclaration", "StmtExpr"):
            calls = True
        for f in _fields(x):
            v = getattr(x, f, None)
            if v is not None and not isinstance(v, (str, int)):
                stack.append(v)
    return count, calls


def _subst_name(node, name: str, val: int, unsigned: bool):
    """The tree with each use of the variable ``name`` replaced by the
    literal ``val`` (an unsigned literal for an unsigned int)."""
    if isinstance(node, ast.Identifier):
        if _idname(node) == name:
            return make_int_lit(val, unsigned=unsigned)
        return node
    if isinstance(node, list):
        for i, x in enumerate(node):
            node[i] = _subst_name(x, name, val, unsigned)
        return node
    if _is_node(node):
        for f in _fields(node):
            v = getattr(node, f, None)
            if v is None or isinstance(v, (str, int)):
                continue
            new = _subst_name(v, name, val, unsigned)
            if new is not v:
                setattr(node, f, new)
    return node


# a debug aid: UC80_UNROLL_DEBUG=1 prints the cost of each loop that the
# unroller tries
_DEBUG = bool(__import__("os").environ.get("UC80_UNROLL_DEBUG"))
# a counted loop of 1 to 4 passes is unrolled when the copies, after
# folding, cost at most this many bytes more than the loop (_cost)
UNROLL_SLACK = 4


class Uc80ASTOptimizer(ASTOptimizer):
    """See the module docstring."""

    # uc80 --no-dead-test turns the removal of an empty ``if`` off
    dead_test = True
    # uc80 --no-loop-unroll turns the unrolling of a short counted loop off
    loop_unroll = True

    # ---- a short counted loop (docs/k2_code_size.md)

    def _try_loop_unroll(self, stmt):
        """uc_core's unroller, rewritten.  The init clause of a ``for`` is
        an ExpressionStmt in the tree, so uc_core never matched a loop with
        ``i = 0``.  The counter must keep the value that the loop leaves
        (the copies of uc_core stop one step before it), and it must not
        be assigned in the body or have its address taken.  A local
        counter of a plain integer type becomes a literal in each copy,
        and the copies fold.  The loop is unrolled only when the folded
        copies cost no more than the loop (``_cost``)."""
        if not self.loop_unroll:
            return None
        # A short loop that is the whole body of another loop is the only
        # place that the caller never visits (it visits the items of a
        # block).  It is unrolled first, in place.
        if isinstance(stmt.body, ast.ForStmt):
            inner = self._try_loop_unroll(stmt.body)
            if inner is not None:
                stmt.body = inner
        init = stmt.init
        view = stmt
        name = None
        if isinstance(init, ast.ExpressionStmt):
            init = init.expr
            view = copy.copy(stmt)
            view.init = init
        if (isinstance(init, ast.BinaryOp) and _op(init) == "="
                and isinstance(init.left, ast.Identifier)
                and isinstance(init.right, ast.IntLiteral)):
            name = _idname(init.left)
            start = _iv(init.right)
        else:
            return None                 # the declaration form: left alone
        cond, upd = stmt.condition, stmt.update
        if not (isinstance(cond, ast.BinaryOp)
                and isinstance(cond.left, ast.Identifier)
                and _idname(cond.left) == name
                and isinstance(cond.right, ast.IntLiteral)):
            return None
        end = _iv(cond.right)
        if (isinstance(upd, (ast.UnaryOp, ast.PostfixOp))
                and _op(upd) == "++"
                and isinstance(upd.operand, ast.Identifier)
                and _idname(upd.operand) == name):
            step = 1
        elif (isinstance(upd, ast.BinaryOp) and _op(upd) == "+="
                and isinstance(upd.left, ast.Identifier)
                and _idname(upd.left) == name
                and isinstance(upd.right, ast.IntLiteral)):
            step = _iv(upd.right)
        else:
            return None
        if step is None or step <= 0 or not (0 <= start <= 32767
                                             and 0 <= end <= 32767):
            return None
        if name in self._address_taken_vars:
            return None
        if name in self._get_modified_vars_in_stmt(stmt.body):
            return None
        cmp_op = _op(cond)
        if cmp_op == "<":
            n = max(0, (end - start + step - 1) // step)
        elif cmp_op == "<=":
            n = max(0, (end - start + step) // step)
        elif cmp_op == "!=":
            if (end - start) % step:
                return None
            n = max(0, (end - start) // step)
        else:
            return None
        if not 1 <= n <= 4:
            return None
        body = stmt.body
        stmts = self._get_flat_stmts(body)
        if (stmts is None or len(stmts) > 40
                or self._contains_flow_control(body)
                or _has_declaration(body)):
            return None
        # an inner short loop is unrolled first: the size of the outer
        # decision then counts the folded inner copies
        stmts = [self._try_loop_unroll(x) or x
                 if isinstance(x, ast.ForStmt) else x for x in stmts]
        nodes, calls = _size_of_tree(stmts)
        if n > 1 and calls:
            return None
        kind = {"int": "s", "short": "s", "int short": "s",
                "unsigned": "u", "int unsigned": "u",
                "char unsigned": "c"}.get(self._var_types.get(name), None)
        if kind is not None and _has_type_use(stmts):
            kind = None         # sizeof(v), _Generic(v): the type matters
        if (kind is None and name not in self._var_types
                and _size_of_tree(stmts)[1]):
            return None         # a call can change a global counter
        items: list = []
        for i in range(n):
            val = start + i * step
            if kind is None:
                items.append(ast.ExpressionStmt(expr=_binop(
                    "=", make_identifier(name), make_int_lit(val))))
            for st in stmts:
                st = copy.deepcopy(st)
                if kind is not None:
                    st = _subst_name(st, name, val, kind == "u")
                items.append(st)
        items.append(ast.ExpressionStmt(expr=_binop(
            "=", make_identifier(name), make_int_lit(start + n * step))))
        result = ast.CompoundStmt(items=items)
        # The unrolled copies must not be bigger than the loop: fold them
        # (a counter that is a constant gives a constant index) and count
        # the nodes.
        snap = self._snapshot()
        self._optimize_stmt(result)
        if _DEBUG:
            import sys
            print(f"unroll {name} kind={kind}: cost {_cost(result)} against "
                  f"{_cost(stmt)} (slack {UNROLL_SLACK})",
                  file=sys.stderr)
        if _cost(result) > _cost(stmt) + UNROLL_SLACK:
            self._restore(snap)
            return None
        self._stat("loop_unroll")
        self._changed = True
        return result

    # ---- an if with nothing to do (docs/dead_test.md)

    def _is_empty_stmt(self, stmt) -> bool:
        """No code: ``;``, or a block of such statements."""
        if isinstance(stmt, ast.EmptyStmt):
            return True
        return (isinstance(stmt, ast.CompoundStmt)
                and all(self._is_empty_stmt(i) for i in stmt.items or []))

    def _dead_if(self, stmt) -> bool:
        """``if (c) ;`` where c has no side effect and reads no volatile
        object.  A call, an assignment, ``++`` and a volatile access make
        the test a statement of its own."""
        if isinstance(stmt, ast.IfStmt):
            pass
        elif isinstance(stmt, ast.IfStmtElse):
            if not self._is_empty_stmt(stmt.else_branch):
                return False
        else:
            return False
        return (self._is_empty_stmt(stmt.then_branch)
                and self._known_pure(stmt.condition)
                and not self._expr_is_impure(stmt.condition))

    def _known_pure(self, e) -> bool:
        """True only for node types known to have no side effect.  The
        uc_core test has no case for a comma expression, a statement
        expression and other node types and calls them pure, so a
        positive list is used here."""
        if isinstance(e, (ast.Identifier, ast.IntLiteral, ast.CharLiteral,
                          ast.FloatLiteral, ast.StringLiteral)):
            return True
        if isinstance(e, (ast.UnaryOp, ast.PostfixOp)):
            op = e.op.text if hasattr(e.op, "text") else e.op
            return op not in ("++", "--") and self._known_pure(e.operand)
        if isinstance(e, ast.BinaryOp):
            op = e.op.text if hasattr(e.op, "text") else e.op
            return (op not in ("=", "+=", "-=", "*=", "/=", "%=", "&=",
                               "|=", "^=", "<<=", ">>=", ",")
                    and self._known_pure(e.left) and self._known_pure(e.right))
        if isinstance(e, ast.TernaryOp):
            return all(self._known_pure(x) for x in
                       (e.condition, e.true_expr, e.false_expr))
        if isinstance(e, ast.Cast):
            return self._known_pure(e.expr)
        if isinstance(e, ast.Index):
            return self._known_pure(e.array) and self._known_pure(e.index)
        if isinstance(e, (ast.Member, ast.ArrowMember)):
            return self._known_pure(e.obj)
        return False

    def _optimize_stmt(self, stmt) -> None:
        super()._optimize_stmt(stmt)
        if not self.dead_test:
            return
        if isinstance(stmt, ast.CompoundStmt):
            keep = [i for i in stmt.items or [] if not self._dead_if(i)]
            if len(keep) != len(stmt.items or []):
                stmt.items = keep
                self._stat("dead_test")
                self._changed = True
        elif (isinstance(stmt, (ast.ForStmt, ast.WhileStmt, ast.DoWhileStmt))
              and self._dead_if(stmt.body)):
            stmt.body = ast.EmptyStmt(pos=stmt.pos)
            self._stat("dead_test")
            self._changed = True

    # ---- identities that would lose the type of an operand

    def _simplify_identity(self, expr):
        """``x + 0L`` is a long and ``x * 1u`` an unsigned int.  uc_core
        drops only a literal with a U or LL suffix, as on a target whose
        long is as wide as its int; here an L matters too:
        ``(x + 0L) * y`` is a 32-bit multiply, and ``x`` alone was 16."""
        for e in (expr.left, expr.right):
            if isinstance(e, ast.IntLiteral) and any(int_flags(e)[:3]):
                return None
        op = expr.op.text if hasattr(expr.op, "text") else expr.op
        if op == "%" and not isinstance(expr.left, ast.IntLiteral):
            # ``x % 1`` is a 0 of the type of x promoted, not an int 0:
            # ``1 ? s : (l % 1)`` is a long and ``u /= (...)`` divides
            # by a long.  Left to the code generator.
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
                if t1[0] != t2[0]:
                    # The inner operation wraps at the width of its own
                    # type: `(u + 65535u) - 256ULL` is `3 - 256` in 64
                    # bits, not `u + 65279`.
                    return None
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
        if (isinstance(expr.right, ast.IntLiteral)
                and isinstance(inner, ast.BinaryOp)
                and isinstance(inner.right, ast.IntLiteral)
                and self._literal_type(inner.right)[0]
                != self._literal_type(expr.right)[0]
                and (expr.op.text if hasattr(expr.op, "text") else expr.op)
                in ("+", "-", "*")):
            return None
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
