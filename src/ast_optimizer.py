"""AST-level expression optimizer for uc80.

Performs bottom-up transformations on expression nodes before codegen:
- Constant folding (all integer widths: 16/32/64-bit)
- Strength reduction (multiply/divide/modulo by power-of-2 → shifts/masks)
- Algebraic simplifications (identity/zero/full-mask elements)
- Dead code elimination (constant conditions, unreachable code)
- Double negation / NOT elimination
- Comparison simplifications (x == x → 1, etc.)
- Idempotent boolean simplifications
- Nested constant folding ((x + c1) + c2 → x + (c1+c2))
- Multi-pass optimization until convergence
"""

from . import ast


class ASTOptimizer:
    def __init__(self):
        self.stats: dict[str, int] = {}
        self._changed = False

    def optimize(self, tu: ast.TranslationUnit) -> ast.TranslationUnit:
        """Optimize all expressions in the translation unit (multi-pass)."""
        max_passes = 5
        for _ in range(max_passes):
            self._changed = False
            for decl in tu.declarations:
                self._optimize_decl(decl)
            if not self._changed:
                break
        return tu

    # === Declaration walkers ===

    def _optimize_decl(self, decl: ast.Declaration) -> None:
        if isinstance(decl, ast.FunctionDecl):
            if decl.body is not None:
                self._optimize_stmt(decl.body)
        elif isinstance(decl, ast.VarDecl):
            if decl.init is not None:
                decl.init = self._optimize_expr(decl.init)
        elif isinstance(decl, ast.DeclarationList):
            for d in decl.declarations:
                self._optimize_decl(d)

    # === Statement walkers (with dead code elimination) ===

    def _optimize_stmt(self, stmt: ast.Statement) -> None:
        if isinstance(stmt, ast.CompoundStmt):
            new_items: list = []
            unreachable = False
            for item in stmt.items:
                if unreachable:
                    # Keep items that contain labels/cases (reachable via goto/switch)
                    if self._contains_label(item):
                        unreachable = False
                        if isinstance(item, ast.Statement):
                            self._optimize_stmt(item)
                        elif isinstance(item, ast.Declaration):
                            self._optimize_decl(item)
                        new_items.append(item)
                    else:
                        self._stat("dead_code")
                        self._changed = True
                    continue
                if isinstance(item, ast.Statement):
                    self._optimize_stmt(item)
                elif isinstance(item, ast.Declaration):
                    self._optimize_decl(item)
                new_items.append(item)
                # Check if this statement is a terminator
                if isinstance(item, (ast.ReturnStmt, ast.GotoStmt,
                                     ast.BreakStmt, ast.ContinueStmt)):
                    unreachable = True
            if len(new_items) != len(stmt.items):
                stmt.items = new_items

        elif isinstance(stmt, ast.ExpressionStmt):
            if stmt.expr is not None:
                stmt.expr = self._optimize_expr(stmt.expr)

        elif isinstance(stmt, ast.IfStmt):
            stmt.condition = self._optimize_expr(stmt.condition)
            # Constant condition → dead code elimination
            # But only if eliminated branches don't contain labels (goto targets)
            if isinstance(stmt.condition, ast.IntLiteral):
                if stmt.condition.value != 0:
                    # Always true: eliminate else if it has no labels
                    self._optimize_stmt(stmt.then_branch)
                    if stmt.else_branch is not None:
                        if not self._contains_label(stmt.else_branch):
                            self._stat("dead_code")
                            self._changed = True
                            stmt.else_branch = None
                        else:
                            self._optimize_stmt(stmt.else_branch)
                else:
                    # Always false
                    then_has_labels = self._contains_label(stmt.then_branch)
                    if not then_has_labels:
                        self._stat("dead_code")
                        self._changed = True
                        if stmt.else_branch is not None:
                            self._optimize_stmt(stmt.else_branch)
                            stmt.then_branch = stmt.else_branch
                            stmt.else_branch = None
                            stmt.condition = ast.IntLiteral(value=1,
                                                            location=stmt.condition.location)
                        else:
                            stmt.then_branch = ast.CompoundStmt(items=[],
                                                                location=stmt.location)
                    else:
                        # Then-branch has labels, can't eliminate
                        self._optimize_stmt(stmt.then_branch)
                        if stmt.else_branch is not None:
                            self._optimize_stmt(stmt.else_branch)
            else:
                self._optimize_stmt(stmt.then_branch)
                if stmt.else_branch is not None:
                    self._optimize_stmt(stmt.else_branch)

        elif isinstance(stmt, ast.WhileStmt):
            stmt.condition = self._optimize_expr(stmt.condition)
            # while(0) → dead loop body (only if no labels inside)
            if (isinstance(stmt.condition, ast.IntLiteral) and stmt.condition.value == 0
                    and not self._contains_label(stmt.body)):
                self._stat("dead_code")
                self._changed = True
                stmt.body = ast.CompoundStmt(items=[], location=stmt.location)
            else:
                self._optimize_stmt(stmt.body)

        elif isinstance(stmt, ast.DoWhileStmt):
            self._optimize_stmt(stmt.body)
            stmt.condition = self._optimize_expr(stmt.condition)

        elif isinstance(stmt, ast.ForStmt):
            if stmt.init is not None:
                if isinstance(stmt.init, ast.Expression):
                    stmt.init = self._optimize_expr(stmt.init)
                elif isinstance(stmt.init, ast.Declaration):
                    self._optimize_decl(stmt.init)
            if stmt.condition is not None:
                stmt.condition = self._optimize_expr(stmt.condition)
            if stmt.update is not None:
                stmt.update = self._optimize_expr(stmt.update)
            self._optimize_stmt(stmt.body)

        elif isinstance(stmt, ast.SwitchStmt):
            stmt.expr = self._optimize_expr(stmt.expr)
            self._optimize_stmt(stmt.body)

        elif isinstance(stmt, ast.CaseStmt):
            if stmt.value is not None:
                stmt.value = self._optimize_expr(stmt.value)
            self._optimize_stmt(stmt.stmt)

        elif isinstance(stmt, ast.LabelStmt):
            self._optimize_stmt(stmt.stmt)

        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value is not None:
                stmt.value = self._optimize_expr(stmt.value)

    @staticmethod
    def _contains_label(node) -> bool:
        """Check if a statement (or any nested statement) contains a label or case."""
        if isinstance(node, (ast.LabelStmt, ast.CaseStmt)):
            return True
        if isinstance(node, ast.CompoundStmt):
            return any(ASTOptimizer._contains_label(item) for item in node.items)
        if isinstance(node, ast.IfStmt):
            if ASTOptimizer._contains_label(node.then_branch):
                return True
            if node.else_branch and ASTOptimizer._contains_label(node.else_branch):
                return True
        if isinstance(node, (ast.WhileStmt, ast.DoWhileStmt, ast.ForStmt)):
            return ASTOptimizer._contains_label(node.body)
        if isinstance(node, ast.SwitchStmt):
            return ASTOptimizer._contains_label(node.body)
        return False

    # === Expression optimizer (bottom-up) ===

    def _optimize_expr(self, expr: ast.Expression) -> ast.Expression:
        """Recursively optimize an expression bottom-up."""
        if isinstance(expr, ast.BinaryOp):
            expr.left = self._optimize_expr(expr.left)
            expr.right = self._optimize_expr(expr.right)
            return self._optimize_binary(expr)
        elif isinstance(expr, ast.UnaryOp):
            # Don't optimize operand of address-of or dereference or ++/--
            if expr.op in ("&", "*", "++", "--"):
                return expr
            expr.operand = self._optimize_expr(expr.operand)
            return self._optimize_unary(expr)
        elif isinstance(expr, ast.TernaryOp):
            expr.condition = self._optimize_expr(expr.condition)
            expr.true_expr = self._optimize_expr(expr.true_expr)
            expr.false_expr = self._optimize_expr(expr.false_expr)
            # Constant condition folding
            if isinstance(expr.condition, ast.IntLiteral):
                self._stat("ternary_fold")
                self._changed = True
                return expr.true_expr if expr.condition.value != 0 else expr.false_expr
            return expr
        elif isinstance(expr, ast.Call):
            expr.args = [self._optimize_expr(a) for a in expr.args]
            return expr
        elif isinstance(expr, ast.Index):
            expr.array = self._optimize_expr(expr.array)
            expr.index = self._optimize_expr(expr.index)
            return expr
        elif isinstance(expr, ast.Cast):
            expr.expr = self._optimize_expr(expr.expr)
            return expr
        elif isinstance(expr, ast.InitializerList):
            expr.values = [self._optimize_expr(v) if isinstance(v, ast.Expression) else v
                           for v in expr.values]
            return expr
        return expr

    # === Binary operation optimizer ===

    def _optimize_binary(self, expr: ast.BinaryOp) -> ast.Expression:
        op = expr.op
        left = expr.left
        right = expr.right

        # Skip assignment operators
        if op in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="):
            return expr

        # Skip logical short-circuit and comma (side effects)
        if op in ("&&", "||", ","):
            return expr

        # === Constant folding: both operands are IntLiteral ===
        if isinstance(left, ast.IntLiteral) and isinstance(right, ast.IntLiteral):
            # Use wider mask of the two operands
            mask = max(self._literal_mask(left), self._literal_mask(right))
            unsigned = left.is_unsigned or right.is_unsigned
            is_long = left.is_long or right.is_long
            result = self._fold_constants(op, left.value, right.value, unsigned, mask)
            if result is not None:
                self._stat("const_fold")
                self._changed = True
                return ast.IntLiteral(
                    value=result,
                    is_long=is_long,
                    is_unsigned=unsigned,
                    location=expr.location,
                )

        # === Strength reduction: multiply by power-of-2 ===
        if op == "*":
            result = self._strength_reduce_mul(expr)
            if result is not None:
                return result

        # === Strength reduction: divide/modulo by power-of-2 (unsigned only) ===
        if op == "/" and isinstance(right, ast.IntLiteral):
            shift = self._log2_if_power_of_2(right.value)
            if shift is not None and self._is_unsigned_literal(left, right):
                self._stat("div_to_shift")
                self._changed = True
                return ast.BinaryOp(op=">>", left=left, right=ast.IntLiteral(
                    value=shift, location=right.location),
                    location=expr.location)

        if op == "%" and isinstance(right, ast.IntLiteral):
            shift = self._log2_if_power_of_2(right.value)
            if shift is not None and self._is_unsigned_literal(left, right):
                self._stat("mod_to_and")
                self._changed = True
                return ast.BinaryOp(op="&", left=left, right=ast.IntLiteral(
                    value=right.value - 1, is_unsigned=True, location=right.location),
                    location=expr.location)

        # === Algebraic identity elements ===
        r = self._simplify_identity(expr)
        if r is not None:
            return r

        # === Algebraic zero elements ===
        r = self._simplify_zero(expr)
        if r is not None:
            return r

        # === Full-mask identities ===
        r = self._simplify_full_mask(expr)
        if r is not None:
            return r

        # === Self-referential identities (only for side-effect-free operands) ===
        if self._is_same_identifier(left, right):
            if op == "&" or op == "|":
                self._stat("self_identity")
                self._changed = True
                return left
            if op == "^" or op == "-":
                self._stat("self_zero")
                self._changed = True
                return ast.IntLiteral(value=0, location=expr.location)
            # Comparison simplifications
            if op == "==":
                self._stat("self_cmp")
                self._changed = True
                return ast.IntLiteral(value=1, location=expr.location)
            if op == "!=":
                self._stat("self_cmp")
                self._changed = True
                return ast.IntLiteral(value=0, location=expr.location)
            if op == "<" or op == ">":
                self._stat("self_cmp")
                self._changed = True
                return ast.IntLiteral(value=0, location=expr.location)
            if op == "<=" or op == ">=":
                self._stat("self_cmp")
                self._changed = True
                return ast.IntLiteral(value=1, location=expr.location)

        # === Idempotent boolean simplifications ===
        r = self._simplify_idempotent(expr)
        if r is not None:
            return r

        # === Nested constant folding ===
        r = self._nested_const_fold(expr)
        if r is not None:
            return r

        return expr

    # === Unary operation optimizer ===

    def _optimize_unary(self, expr: ast.UnaryOp) -> ast.Expression:
        op = expr.op
        operand = expr.operand

        # Constant folding for unary operations
        if isinstance(operand, ast.IntLiteral):
            val = operand.value
            mask = self._literal_mask(operand)
            result = None
            if op == "-":
                result = (-val) & mask
            elif op == "+":
                result = val
            elif op == "~":
                result = (~val) & mask
            elif op == "!":
                result = 1 if val == 0 else 0
            if result is not None:
                self._stat("const_fold_unary")
                self._changed = True
                return ast.IntLiteral(
                    value=result,
                    is_long=operand.is_long,
                    is_unsigned=operand.is_unsigned,
                    location=expr.location,
                )

        # Double negation: -(-x) → x, ~(~x) → x
        if isinstance(operand, ast.UnaryOp) and operand.op == op and op in ("-", "~"):
            self._stat("double_neg")
            self._changed = True
            return operand.operand

        # NOTE: !!x is NOT the same as x (!!5 == 1, not 5)
        # Only -(-x) and ~(~x) are safe identity eliminations

        return expr

    # === Strength reduction helpers ===

    def _strength_reduce_mul(self, expr: ast.BinaryOp) -> ast.Expression | None:
        """Reduce multiply by power-of-2 to shift."""
        left = expr.left
        right = expr.right

        # x * 2^n → x << n  (also handles 2^n * x)
        const, other = None, None
        if isinstance(right, ast.IntLiteral):
            const, other = right, left
        elif isinstance(left, ast.IntLiteral):
            const, other = left, right

        if const is None:
            return None

        val = const.value

        # x * 0 → 0 (handled by zero elements)
        # x * 1 → x (handled by identity elements)
        if val <= 1:
            return None

        shift = self._log2_if_power_of_2(val)
        if shift is not None:
            self._stat("mul_to_shift")
            self._changed = True
            if shift == 1:
                # x * 2 → x + x (single ADD HL,HL)
                return ast.BinaryOp(op="+", left=other, right=other,
                                    location=expr.location)
            return ast.BinaryOp(op="<<", left=other, right=ast.IntLiteral(
                value=shift, location=const.location),
                location=expr.location)

        return None

    # === Algebraic simplification helpers ===

    def _simplify_identity(self, expr: ast.BinaryOp) -> ast.Expression | None:
        """Remove identity elements: x+0→x, x*1→x, etc."""
        op = expr.op
        left = expr.left
        right = expr.right

        l_zero = isinstance(left, ast.IntLiteral) and left.value == 0
        r_zero = isinstance(right, ast.IntLiteral) and right.value == 0
        l_one = isinstance(left, ast.IntLiteral) and left.value == 1
        r_one = isinstance(right, ast.IntLiteral) and right.value == 1

        result = None
        if op == "+":
            if r_zero:
                result = left
            elif l_zero:
                result = right
        elif op == "-":
            if r_zero:
                result = left
        elif op == "*":
            if r_one:
                result = left
            elif l_one:
                result = right
        elif op == "/":
            if r_one:
                result = left
        elif op == "%":
            # x % 1 → 0
            if r_one:
                self._stat("identity")
                self._changed = True
                return ast.IntLiteral(value=0, location=expr.location)
        elif op in ("<<", ">>"):
            if r_zero:
                result = left
        elif op == "|":
            if r_zero:
                result = left
            elif l_zero:
                result = right
        elif op == "^":
            if r_zero:
                result = left
            elif l_zero:
                result = right
        elif op == "&":
            # x & 0 handled in _simplify_zero
            pass

        if result is not None:
            self._stat("identity")
            self._changed = True
        return result

    def _simplify_zero(self, expr: ast.BinaryOp) -> ast.Expression | None:
        """Simplify zero-producing operations: x*0→0, x&0→0, etc."""
        op = expr.op
        left = expr.left
        right = expr.right

        l_zero = isinstance(left, ast.IntLiteral) and left.value == 0
        r_zero = isinstance(right, ast.IntLiteral) and right.value == 0

        if op == "*":
            if r_zero or l_zero:
                self._stat("zero_element")
                self._changed = True
                return ast.IntLiteral(value=0, location=expr.location)
        elif op == "&":
            if r_zero or l_zero:
                self._stat("zero_element")
                self._changed = True
                return ast.IntLiteral(value=0, location=expr.location)
        elif op == "/" and l_zero:
            self._stat("zero_element")
            self._changed = True
            return ast.IntLiteral(value=0, location=expr.location)
        elif op == "%" and l_zero:
            self._stat("zero_element")
            self._changed = True
            return ast.IntLiteral(value=0, location=expr.location)
        elif op in ("<<", ">>") and l_zero:
            self._stat("zero_element")
            self._changed = True
            return ast.IntLiteral(value=0, location=expr.location)

        return None

    def _simplify_full_mask(self, expr: ast.BinaryOp) -> ast.Expression | None:
        """Simplify full-mask identities: x & 0xFFFF → x, x | 0xFFFF → 0xFFFF, etc."""
        op = expr.op
        left = expr.left
        right = expr.right

        # Determine the effective mask value for 16-bit
        r_full = isinstance(right, ast.IntLiteral) and (right.value & 0xFFFF) == 0xFFFF and not right.is_long
        l_full = isinstance(left, ast.IntLiteral) and (left.value & 0xFFFF) == 0xFFFF and not left.is_long

        if op == "&":
            # x & 0xFFFF → x
            if r_full:
                self._stat("full_mask")
                self._changed = True
                return left
            if l_full:
                self._stat("full_mask")
                self._changed = True
                return right
        elif op == "|":
            # x | 0xFFFF → 0xFFFF
            if r_full:
                self._stat("full_mask")
                self._changed = True
                return ast.IntLiteral(value=0xFFFF, location=expr.location)
            if l_full:
                self._stat("full_mask")
                self._changed = True
                return ast.IntLiteral(value=0xFFFF, location=expr.location)
        elif op == "^":
            # x ^ 0xFFFF → ~x
            if r_full:
                self._stat("full_mask")
                self._changed = True
                return ast.UnaryOp(op="~", operand=left, location=expr.location)
            if l_full:
                self._stat("full_mask")
                self._changed = True
                return ast.UnaryOp(op="~", operand=right, location=expr.location)

        return None

    def _simplify_idempotent(self, expr: ast.BinaryOp) -> ast.Expression | None:
        """Simplify idempotent boolean patterns: (a & b) & b → a & b, etc."""
        op = expr.op
        left = expr.left
        right = expr.right

        # (a OP b) OP b → a OP b   (for & and |)
        if op in ("&", "|") and isinstance(left, ast.BinaryOp) and left.op == op:
            if self._is_same_identifier(right, left.right):
                self._stat("idempotent")
                self._changed = True
                return left
            if self._is_same_identifier(right, left.left):
                self._stat("idempotent")
                self._changed = True
                return left

        return None

    # === Nested constant folding ===

    def _nested_const_fold(self, expr: ast.BinaryOp) -> ast.Expression | None:
        """Fold nested constants: (x + c1) + c2 → x + (c1+c2), etc."""
        op = expr.op
        right = expr.right
        left = expr.left

        if not isinstance(right, ast.IntLiteral):
            return None

        c2 = right.value

        if isinstance(left, ast.BinaryOp) and isinstance(left.right, ast.IntLiteral):
            inner_op = left.op
            c1 = left.right.value
            x = left.left
            # Use wider mask of the two constants
            is_long = right.is_long or left.right.is_long
            mask = max(self._literal_mask(right), self._literal_mask(left.right))

            # (x + c1) + c2 → x + (c1 + c2)
            if op == "+" and inner_op == "+":
                combined = (c1 + c2) & mask
                self._stat("nested_fold")
                self._changed = True
                return ast.BinaryOp(op="+", left=x, right=ast.IntLiteral(
                    value=combined, is_long=is_long, location=right.location),
                    location=expr.location)

            # (x - c1) + c2 → x + (c2 - c1)
            if op == "+" and inner_op == "-":
                combined = (c2 - c1) & mask
                self._stat("nested_fold")
                self._changed = True
                if combined == 0:
                    return x
                return ast.BinaryOp(op="+", left=x, right=ast.IntLiteral(
                    value=combined, is_long=is_long, location=right.location),
                    location=expr.location)

            # (x + c1) - c2 → x + (c1 - c2)
            if op == "-" and inner_op == "+":
                combined = (c1 - c2) & mask
                self._stat("nested_fold")
                self._changed = True
                if combined == 0:
                    return x
                return ast.BinaryOp(op="+", left=x, right=ast.IntLiteral(
                    value=combined, is_long=is_long, location=right.location),
                    location=expr.location)

            # (x - c1) - c2 → x - (c1 + c2)
            if op == "-" and inner_op == "-":
                combined = (c1 + c2) & mask
                self._stat("nested_fold")
                self._changed = True
                return ast.BinaryOp(op="-", left=x, right=ast.IntLiteral(
                    value=combined, is_long=is_long, location=right.location),
                    location=expr.location)

            # (x * c1) * c2 → x * (c1 * c2)
            if op == "*" and inner_op == "*":
                combined = (c1 * c2) & mask
                self._stat("nested_fold")
                self._changed = True
                return ast.BinaryOp(op="*", left=x, right=ast.IntLiteral(
                    value=combined, is_long=is_long, location=right.location),
                    location=expr.location)

            # (x << c1) << c2 → x << (c1 + c2)
            if op == "<<" and inner_op == "<<":
                combined = c1 + c2
                self._stat("nested_fold")
                self._changed = True
                return ast.BinaryOp(op="<<", left=x, right=ast.IntLiteral(
                    value=combined, location=right.location),
                    location=expr.location)

            # (x >> c1) >> c2 → x >> (c1 + c2)
            if op == ">>" and inner_op == ">>":
                combined = c1 + c2
                self._stat("nested_fold")
                self._changed = True
                return ast.BinaryOp(op=">>", left=x, right=ast.IntLiteral(
                    value=combined, location=right.location),
                    location=expr.location)

        return None

    # === Utility methods ===

    @staticmethod
    def _log2_if_power_of_2(n: int) -> int | None:
        """Return log2(n) if n is a power of 2, else None."""
        if n <= 0 or (n & (n - 1)) != 0:
            return None
        return n.bit_length() - 1

    @staticmethod
    def _is_same_identifier(a: ast.Expression, b: ast.Expression) -> bool:
        """Check if two expressions are the same simple identifier."""
        return (isinstance(a, ast.Identifier) and isinstance(b, ast.Identifier)
                and a.name == b.name)

    @staticmethod
    def _is_unsigned_literal(*exprs: ast.Expression) -> bool:
        """Check if any IntLiteral is unsigned."""
        return any(isinstance(e, ast.IntLiteral) and e.is_unsigned for e in exprs)

    @staticmethod
    def _literal_mask(lit: ast.IntLiteral) -> int:
        """Get bitmask for an IntLiteral based on its type width.

        Matches codegen's _is_long_long_expr heuristic:
        - is_long=False → 16-bit (int)
        - is_long=True, value fits 32-bit signed → 32-bit (long)
        - is_long=True, value exceeds 32-bit signed → 64-bit (long long)
        """
        if not lit.is_long:
            return 0xFFFF
        if lit.value > 2147483647 or lit.value < -2147483648:
            return 0xFFFFFFFFFFFFFFFF
        return 0xFFFFFFFF

    def _fold_constants(self, op: str, a: int, b: int, unsigned: bool,
                        mask: int = 0xFFFF) -> int | None:
        """Evaluate a constant binary expression. Returns None if not foldable."""
        try:
            if op == "+":
                return (a + b) & mask
            elif op == "-":
                return (a - b) & mask
            elif op == "*":
                return (a * b) & mask
            elif op == "/":
                if b == 0:
                    return None
                if unsigned:
                    return (a & mask) // (b & mask)
                else:
                    sa = self._to_signed(a, mask)
                    sb = self._to_signed(b, mask)
                    return int(sa / sb) & mask
            elif op == "%":
                if b == 0:
                    return None
                if unsigned:
                    return (a & mask) % (b & mask)
                else:
                    sa = self._to_signed(a, mask)
                    sb = self._to_signed(b, mask)
                    q = int(sa / sb)
                    return (sa - q * sb) & mask
            elif op == "&":
                return a & b
            elif op == "|":
                return a | b
            elif op == "^":
                return a ^ b
            elif op == "<<":
                return (a << b) & mask
            elif op == ">>":
                if unsigned:
                    return (a & mask) >> b
                else:
                    sa = self._to_signed(a, mask)
                    return (sa >> b) & mask
            elif op == "==":
                return 1 if (a & mask) == (b & mask) else 0
            elif op == "!=":
                return 1 if (a & mask) != (b & mask) else 0
            elif op == "<":
                if unsigned:
                    return 1 if (a & mask) < (b & mask) else 0
                else:
                    return 1 if self._to_signed(a, mask) < self._to_signed(b, mask) else 0
            elif op == ">":
                if unsigned:
                    return 1 if (a & mask) > (b & mask) else 0
                else:
                    return 1 if self._to_signed(a, mask) > self._to_signed(b, mask) else 0
            elif op == "<=":
                if unsigned:
                    return 1 if (a & mask) <= (b & mask) else 0
                else:
                    return 1 if self._to_signed(a, mask) <= self._to_signed(b, mask) else 0
            elif op == ">=":
                if unsigned:
                    return 1 if (a & mask) >= (b & mask) else 0
                else:
                    return 1 if self._to_signed(a, mask) >= self._to_signed(b, mask) else 0
        except (ZeroDivisionError, OverflowError, ValueError):
            return None
        return None

    @staticmethod
    def _to_signed(val: int, mask: int) -> int:
        """Convert unsigned int to signed using mask width."""
        sign_bit = (mask >> 1) + 1
        if val & sign_bit:
            return val - (mask + 1)
        return val

    def _stat(self, name: str) -> None:
        self.stats[name] = self.stats.get(name, 0) + 1
