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

Level 3 (-O3) adds:
- Commutative normalization (canonical operand ordering)
- Common subexpression elimination (CSE)
- Copy propagation
- Dead store elimination
- Loop-invariant code motion
- Loop unrolling (small constant-bound loops)
"""

import copy
from . import ast


class ASTOptimizer:
    def __init__(self, opt_level: int = 2):
        self.stats: dict[str, int] = {}
        self._changed = False
        self.opt_level = opt_level
        # Level 3 state (reset per function):
        self._cse_cache: dict[str, ast.Expression] = {}
        self._cse_deps: dict[str, set[str]] = {}
        self._copies: dict[str, str] = {}
        self._var_types: dict[str, ast.TypeNode] = {}  # Track declared types for safe copy prop
        self._address_taken_vars: set[str] = set()

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
                if self.opt_level >= 3:
                    self._reset_level3_state()
                    # Track parameter types for copy propagation safety
                    for param in decl.params:
                        if param.name is not None:
                            self._var_types[param.name] = param.param_type
                    self._collect_address_taken(decl.body)
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

                # Level 3: try loop unrolling before normal optimization
                if self.opt_level >= 3 and isinstance(item, ast.ForStmt):
                    unrolled = self._try_loop_unroll(item)
                    if unrolled is not None:
                        # Unrolled into a CompoundStmt; optimize its contents
                        self._optimize_stmt(unrolled)
                        new_items.append(unrolled)
                        continue

                if isinstance(item, ast.Statement):
                    self._optimize_stmt(item)
                elif isinstance(item, ast.Declaration):
                    self._optimize_decl(item)
                new_items.append(item)

                # Level 3: invalidate caches after processing statement
                if self.opt_level >= 3:
                    self._invalidate_caches_for_stmt(item)

                # Check if this statement is a terminator
                if isinstance(item, (ast.ReturnStmt, ast.GotoStmt,
                                     ast.BreakStmt, ast.ContinueStmt)):
                    unreachable = True
            if len(new_items) != len(stmt.items):
                stmt.items = new_items

            # Level 3: dead store elimination on the final list
            if self.opt_level >= 3:
                stmt.items = self._eliminate_dead_stores(stmt.items)

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
        # Level 3: copy propagation on identifiers
        if self.opt_level >= 3 and isinstance(expr, ast.Identifier):
            if expr.name in self._copies:
                self._stat("copy_prop")
                self._changed = True
                return ast.Identifier(name=self._copies[expr.name],
                                      location=expr.location)

        if isinstance(expr, ast.BinaryOp):
            # For assignments, protect LHS from copy propagation
            if expr.op in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
                           "^=", "<<=", ">>="):
                # Optimize sub-expressions in LHS (array indices, pointer deref)
                # but NOT a simple Identifier target (no copy prop on lvalues)
                if isinstance(expr.left, ast.Index):
                    expr.left.array = self._optimize_expr(expr.left.array)
                    expr.left.index = self._optimize_expr(expr.left.index)
                elif isinstance(expr.left, ast.Member):
                    expr.left.obj = self._optimize_expr(expr.left.obj)
                expr.right = self._optimize_expr(expr.right)
                return self._optimize_binary(expr)
            expr.left = self._optimize_expr(expr.left)
            expr.right = self._optimize_expr(expr.right)
            result = self._optimize_binary(expr)
            # Level 3: CSE after binary optimization
            if self.opt_level >= 3:
                result = self._cse_lookup(result)
            return result
        elif isinstance(expr, ast.UnaryOp):
            # Don't optimize operand of address-of or dereference or ++/--
            if expr.op in ("&", "*", "++", "--"):
                return expr
            expr.operand = self._optimize_expr(expr.operand)
            result = self._optimize_unary(expr)
            # Level 3: CSE after unary optimization
            if self.opt_level >= 3:
                result = self._cse_lookup(result)
            return result
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
        elif isinstance(expr, ast.SizeofType):
            # Constant fold sizeof(type) at compile time
            size = self._sizeof_type(expr.target_type)
            if size is not None:
                self._stat("sizeof_fold")
                self._changed = True
                return ast.IntLiteral(value=size, location=expr.location)
            return expr
        elif isinstance(expr, ast.SizeofExpr):
            # sizeof(string_literal) → array length including null terminator
            if isinstance(expr.expr, ast.StringLiteral):
                self._stat("sizeof_fold")
                self._changed = True
                return ast.IntLiteral(value=len(expr.expr.value) + 1, location=expr.location)
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
            # C standard 6.4.4.1: hex/octal literals that exceed signed range
            # are effectively unsigned (e.g., 0xab00 is unsigned int on 16-bit)
            unsigned = (self._is_effectively_unsigned(left) or
                        self._is_effectively_unsigned(right))
            is_long = left.is_long or right.is_long or mask >= 0xFFFFFFFF
            result = self._fold_constants(op, left.value, right.value, unsigned, mask)
            if result is not None:
                # Convert masked result back to signed representation so that
                # _is_long_expr doesn't incorrectly promote to 32-bit.
                # e.g., (1 - 2) & 0xFFFF = 65535 must be stored as -1.
                if not unsigned:
                    half = (mask + 1) >> 1
                    if result >= half:
                        result -= (mask + 1)
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

        # === Level 3: Commutative normalization ===
        if self.opt_level >= 3:
            r = self._normalize_commutative(expr)
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
            # Convert masked result back to signed representation so that
            # _is_long_expr doesn't incorrectly promote to 32-bit.
            # e.g., -(1) & 0xFFFF = 65535 must be stored as -1, not 65535,
            # otherwise 65535 > 32767 triggers unwanted long promotion.
            if result is not None and not operand.is_unsigned and op in ("-", "~"):
                half = (mask + 1) >> 1
                if result >= half:
                    result -= (mask + 1)
            if result is not None:
                self._stat("const_fold_unary")
                self._changed = True
                # Per C99 6.5.3.3: ! operator always returns int (not unsigned, not long)
                if op == "!":
                    return ast.IntLiteral(
                        value=result,
                        is_long=False,
                        is_unsigned=False,
                        location=expr.location,
                    )
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
        """Reduce multiply by power-of-2 to shift or addition."""
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
        """Simplify full-mask identities: x & 0xFFFF → x, x | 0xFFFF → 0xFFFF, etc.

        IMPORTANT: These are only safe when the other operand is also 16-bit.
        If the other operand could be wider (32/64-bit), these are WRONG:
        - (long long)x & 0xFFFF ≠ x  (truncates upper bits)
        - (long long)x | 0xFFFF ≠ 0xFFFF  (loses upper bits)
        Only apply when the other operand is a non-long IntLiteral.
        """
        op = expr.op
        left = expr.left
        right = expr.right

        # Determine the effective mask value for 16-bit
        r_full = isinstance(right, ast.IntLiteral) and (right.value & 0xFFFF) == 0xFFFF and not right.is_long
        l_full = isinstance(left, ast.IntLiteral) and (left.value & 0xFFFF) == 0xFFFF and not left.is_long

        # Only safe when the OTHER operand is known to be 16-bit
        r_safe_16 = isinstance(right, ast.IntLiteral) and not right.is_long
        l_safe_16 = isinstance(left, ast.IntLiteral) and not left.is_long

        if op == "&":
            # x & 0xFFFF → x (only when x is 16-bit)
            if r_full and l_safe_16:
                self._stat("full_mask")
                self._changed = True
                return left
            if l_full and r_safe_16:
                self._stat("full_mask")
                self._changed = True
                return right
        elif op == "|":
            # x | 0xFFFF → 0xFFFF (only when x is 16-bit)
            if r_full and l_safe_16:
                self._stat("full_mask")
                self._changed = True
                return ast.IntLiteral(value=0xFFFF, location=expr.location)
            if l_full and r_safe_16:
                self._stat("full_mask")
                self._changed = True
                return ast.IntLiteral(value=0xFFFF, location=expr.location)
        elif op == "^":
            # x ^ 0xFFFF → ~x (only when x is 16-bit)
            if r_full and l_safe_16:
                self._stat("full_mask")
                self._changed = True
                return ast.UnaryOp(op="~", operand=left, location=expr.location)
            if l_full and r_safe_16:
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
                # Sign-extend: if high bit set, convert to negative
                sign_bit = (mask + 1) >> 1
                if combined >= sign_bit and not (right.is_unsigned or left.right.is_unsigned):
                    combined -= (mask + 1)
                self._stat("nested_fold")
                self._changed = True
                return ast.BinaryOp(op="+", left=x, right=ast.IntLiteral(
                    value=combined, is_long=is_long, location=right.location),
                    location=expr.location)

            # (x - c1) + c2 → x + (c2 - c1)
            if op == "+" and inner_op == "-":
                combined = (c2 - c1) & mask
                sign_bit = (mask + 1) >> 1
                if combined >= sign_bit and not (right.is_unsigned or left.right.is_unsigned):
                    combined -= (mask + 1)
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
                sign_bit = (mask + 1) >> 1
                if combined >= sign_bit and not (right.is_unsigned or left.right.is_unsigned):
                    combined -= (mask + 1)
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

    # === Level 3: Helper infrastructure ===

    def _reset_level3_state(self) -> None:
        """Reset all level-3 optimization state (called at function entry)."""
        self._cse_cache.clear()
        self._cse_deps.clear()
        self._copies.clear()
        self._var_types.clear()
        self._address_taken_vars.clear()

    def _collect_address_taken(self, node) -> None:
        """Pre-pass: find all variables whose address is taken (&var)."""
        if isinstance(node, ast.UnaryOp) and node.op == "&":
            if isinstance(node.operand, ast.Identifier):
                self._address_taken_vars.add(node.operand.name)
        # Recurse into children
        if isinstance(node, ast.CompoundStmt):
            for item in node.items:
                self._collect_address_taken(item)
        elif isinstance(node, ast.ExpressionStmt):
            if node.expr is not None:
                self._collect_address_taken(node.expr)
        elif isinstance(node, ast.IfStmt):
            self._collect_address_taken(node.condition)
            self._collect_address_taken(node.then_branch)
            if node.else_branch is not None:
                self._collect_address_taken(node.else_branch)
        elif isinstance(node, ast.WhileStmt):
            self._collect_address_taken(node.condition)
            self._collect_address_taken(node.body)
        elif isinstance(node, ast.DoWhileStmt):
            self._collect_address_taken(node.body)
            self._collect_address_taken(node.condition)
        elif isinstance(node, ast.ForStmt):
            if node.init is not None:
                self._collect_address_taken(node.init)
            if node.condition is not None:
                self._collect_address_taken(node.condition)
            if node.update is not None:
                self._collect_address_taken(node.update)
            self._collect_address_taken(node.body)
        elif isinstance(node, ast.SwitchStmt):
            self._collect_address_taken(node.expr)
            self._collect_address_taken(node.body)
        elif isinstance(node, ast.CaseStmt):
            self._collect_address_taken(node.stmt)
        elif isinstance(node, ast.LabelStmt):
            self._collect_address_taken(node.stmt)
        elif isinstance(node, ast.ReturnStmt):
            if node.value is not None:
                self._collect_address_taken(node.value)
        elif isinstance(node, ast.BinaryOp):
            self._collect_address_taken(node.left)
            self._collect_address_taken(node.right)
        elif isinstance(node, ast.UnaryOp):
            self._collect_address_taken(node.operand)
        elif isinstance(node, ast.TernaryOp):
            self._collect_address_taken(node.condition)
            self._collect_address_taken(node.true_expr)
            self._collect_address_taken(node.false_expr)
        elif isinstance(node, ast.Call):
            self._collect_address_taken(node.func)
            for arg in node.args:
                self._collect_address_taken(arg)
        elif isinstance(node, ast.Index):
            self._collect_address_taken(node.array)
            self._collect_address_taken(node.index)
        elif isinstance(node, ast.Cast):
            self._collect_address_taken(node.expr)
        elif isinstance(node, ast.VarDecl):
            if node.init is not None:
                self._collect_address_taken(node.init)
        elif isinstance(node, ast.DeclarationList):
            for d in node.declarations:
                self._collect_address_taken(d)

    @staticmethod
    def _expr_key(expr: ast.Expression) -> str | None:
        """Return a hashable key for a pure expression, or None if it has side effects."""
        if isinstance(expr, ast.IntLiteral):
            return f"INT:{expr.value}:{expr.is_long}:{expr.is_unsigned}"
        if isinstance(expr, ast.CharLiteral):
            return f"CHR:{expr.value}"
        if isinstance(expr, ast.Identifier):
            return f"ID:{expr.name}"
        if isinstance(expr, ast.BinaryOp):
            # Skip assignment and short-circuit ops
            if expr.op in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
                           "^=", "<<=", ">>=", "&&", "||", ","):
                return None
            lk = ASTOptimizer._expr_key(expr.left)
            rk = ASTOptimizer._expr_key(expr.right)
            if lk is None or rk is None:
                return None
            return f"BIN:{expr.op}:{lk}:{rk}"
        if isinstance(expr, ast.UnaryOp):
            if expr.op in ("++", "--", "*", "&"):
                return None  # side effects or pointer deref
            ok = ASTOptimizer._expr_key(expr.operand)
            if ok is None:
                return None
            return f"UNA:{expr.op}:{ok}"
        if isinstance(expr, ast.Cast):
            ek = ASTOptimizer._expr_key(expr.expr)
            if ek is None:
                return None
            # Include target type to distinguish e.g. (signed char)x vs (unsigned char)x
            tt = expr.target_type
            if isinstance(tt, ast.BasicType):
                tk = f"{tt.name}:{tt.is_signed}"
            elif isinstance(tt, ast.PointerType):
                tk = "ptr"
            else:
                tk = type(tt).__name__
            return f"CAST:{tk}:{ek}"
        # Calls, Index, Member, etc. are not pure for CSE purposes
        return None

    @staticmethod
    def _get_expr_vars(expr: ast.Expression) -> set[str]:
        """Get all variable names referenced in an expression tree."""
        result: set[str] = set()
        if isinstance(expr, ast.Identifier):
            result.add(expr.name)
        elif isinstance(expr, ast.BinaryOp):
            result.update(ASTOptimizer._get_expr_vars(expr.left))
            result.update(ASTOptimizer._get_expr_vars(expr.right))
        elif isinstance(expr, ast.UnaryOp):
            result.update(ASTOptimizer._get_expr_vars(expr.operand))
        elif isinstance(expr, ast.TernaryOp):
            result.update(ASTOptimizer._get_expr_vars(expr.condition))
            result.update(ASTOptimizer._get_expr_vars(expr.true_expr))
            result.update(ASTOptimizer._get_expr_vars(expr.false_expr))
        elif isinstance(expr, ast.Call):
            result.update(ASTOptimizer._get_expr_vars(expr.func))
            for arg in expr.args:
                result.update(ASTOptimizer._get_expr_vars(arg))
        elif isinstance(expr, ast.Index):
            result.update(ASTOptimizer._get_expr_vars(expr.array))
            result.update(ASTOptimizer._get_expr_vars(expr.index))
        elif isinstance(expr, ast.Cast):
            result.update(ASTOptimizer._get_expr_vars(expr.expr))
        elif isinstance(expr, ast.Member):
            result.update(ASTOptimizer._get_expr_vars(expr.obj))
        return result

    @staticmethod
    def _expr_has_side_effects(expr: ast.Expression) -> bool:
        """Check if an expression has side effects."""
        if isinstance(expr, (ast.Call, ast.StmtExpr)):
            return True
        if isinstance(expr, ast.UnaryOp):
            if expr.op in ("++", "--"):
                return True
            return ASTOptimizer._expr_has_side_effects(expr.operand)
        if isinstance(expr, ast.BinaryOp):
            if expr.op in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
                           "^=", "<<=", ">>="):
                return True
            return (ASTOptimizer._expr_has_side_effects(expr.left) or
                    ASTOptimizer._expr_has_side_effects(expr.right))
        if isinstance(expr, ast.TernaryOp):
            return (ASTOptimizer._expr_has_side_effects(expr.condition) or
                    ASTOptimizer._expr_has_side_effects(expr.true_expr) or
                    ASTOptimizer._expr_has_side_effects(expr.false_expr))
        if isinstance(expr, ast.Index):
            return (ASTOptimizer._expr_has_side_effects(expr.array) or
                    ASTOptimizer._expr_has_side_effects(expr.index))
        if isinstance(expr, ast.Cast):
            return ASTOptimizer._expr_has_side_effects(expr.expr)
        return False

    @staticmethod
    def _expr_references_var(expr: ast.Expression, name: str) -> bool:
        """Check if expression tree references a variable by name."""
        if isinstance(expr, ast.Identifier):
            return expr.name == name
        if isinstance(expr, ast.BinaryOp):
            return (ASTOptimizer._expr_references_var(expr.left, name) or
                    ASTOptimizer._expr_references_var(expr.right, name))
        if isinstance(expr, ast.UnaryOp):
            return ASTOptimizer._expr_references_var(expr.operand, name)
        if isinstance(expr, ast.TernaryOp):
            return (ASTOptimizer._expr_references_var(expr.condition, name) or
                    ASTOptimizer._expr_references_var(expr.true_expr, name) or
                    ASTOptimizer._expr_references_var(expr.false_expr, name))
        if isinstance(expr, ast.Call):
            if ASTOptimizer._expr_references_var(expr.func, name):
                return True
            return any(ASTOptimizer._expr_references_var(a, name) for a in expr.args)
        if isinstance(expr, ast.Index):
            return (ASTOptimizer._expr_references_var(expr.array, name) or
                    ASTOptimizer._expr_references_var(expr.index, name))
        if isinstance(expr, ast.Cast):
            return ASTOptimizer._expr_references_var(expr.expr, name)
        if isinstance(expr, ast.Member):
            return ASTOptimizer._expr_references_var(expr.obj, name)
        return False

    def _get_modified_vars_in_expr(self, expr: ast.Expression) -> set[str]:
        """Collect all variable names modified (assigned, incremented) in an expression."""
        result: set[str] = set()
        if isinstance(expr, ast.BinaryOp):
            if expr.op in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
                           "^=", "<<=", ">>="):
                if isinstance(expr.left, ast.Identifier):
                    result.add(expr.left.name)
            result.update(self._get_modified_vars_in_expr(expr.left))
            result.update(self._get_modified_vars_in_expr(expr.right))
        elif isinstance(expr, ast.UnaryOp):
            if expr.op in ("++", "--"):
                if isinstance(expr.operand, ast.Identifier):
                    result.add(expr.operand.name)
            result.update(self._get_modified_vars_in_expr(expr.operand))
        elif isinstance(expr, ast.TernaryOp):
            result.update(self._get_modified_vars_in_expr(expr.condition))
            result.update(self._get_modified_vars_in_expr(expr.true_expr))
            result.update(self._get_modified_vars_in_expr(expr.false_expr))
        elif isinstance(expr, ast.Call):
            for arg in expr.args:
                result.update(self._get_modified_vars_in_expr(arg))
        elif isinstance(expr, ast.Index):
            result.update(self._get_modified_vars_in_expr(expr.array))
            result.update(self._get_modified_vars_in_expr(expr.index))
        elif isinstance(expr, ast.Cast):
            result.update(self._get_modified_vars_in_expr(expr.expr))
        return result

    def _get_modified_vars_in_stmt(self, stmt: ast.Statement) -> set[str]:
        """Collect all variable names modified within a statement tree."""
        result: set[str] = set()
        if isinstance(stmt, ast.CompoundStmt):
            for item in stmt.items:
                if isinstance(item, ast.Statement):
                    result.update(self._get_modified_vars_in_stmt(item))
                elif isinstance(item, ast.VarDecl):
                    if item.init is not None:
                        result.update(self._get_modified_vars_in_expr(item.init))
                elif isinstance(item, ast.DeclarationList):
                    for d in item.declarations:
                        if isinstance(d, ast.VarDecl) and d.init is not None:
                            result.update(self._get_modified_vars_in_expr(d.init))
        elif isinstance(stmt, ast.ExpressionStmt):
            if stmt.expr is not None:
                result.update(self._get_modified_vars_in_expr(stmt.expr))
        elif isinstance(stmt, ast.IfStmt):
            result.update(self._get_modified_vars_in_expr(stmt.condition))
            result.update(self._get_modified_vars_in_stmt(stmt.then_branch))
            if stmt.else_branch is not None:
                result.update(self._get_modified_vars_in_stmt(stmt.else_branch))
        elif isinstance(stmt, ast.WhileStmt):
            result.update(self._get_modified_vars_in_expr(stmt.condition))
            result.update(self._get_modified_vars_in_stmt(stmt.body))
        elif isinstance(stmt, ast.DoWhileStmt):
            result.update(self._get_modified_vars_in_stmt(stmt.body))
            result.update(self._get_modified_vars_in_expr(stmt.condition))
        elif isinstance(stmt, ast.ForStmt):
            if stmt.init is not None:
                if isinstance(stmt.init, ast.Expression):
                    result.update(self._get_modified_vars_in_expr(stmt.init))
            if stmt.condition is not None:
                result.update(self._get_modified_vars_in_expr(stmt.condition))
            if stmt.update is not None:
                result.update(self._get_modified_vars_in_expr(stmt.update))
            result.update(self._get_modified_vars_in_stmt(stmt.body))
        elif isinstance(stmt, ast.SwitchStmt):
            result.update(self._get_modified_vars_in_expr(stmt.expr))
            result.update(self._get_modified_vars_in_stmt(stmt.body))
        elif isinstance(stmt, ast.CaseStmt):
            result.update(self._get_modified_vars_in_stmt(stmt.stmt))
        elif isinstance(stmt, ast.LabelStmt):
            result.update(self._get_modified_vars_in_stmt(stmt.stmt))
        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value is not None:
                result.update(self._get_modified_vars_in_expr(stmt.value))
        return result

    @staticmethod
    def _stmt_has_calls(stmt) -> bool:
        """Check if a statement tree contains any function calls."""
        if isinstance(stmt, ast.CompoundStmt):
            return any(ASTOptimizer._stmt_has_calls(item) for item in stmt.items)
        if isinstance(stmt, ast.ExpressionStmt):
            return stmt.expr is not None and ASTOptimizer._expr_has_calls(stmt.expr)
        if isinstance(stmt, ast.IfStmt):
            if ASTOptimizer._expr_has_calls(stmt.condition):
                return True
            if ASTOptimizer._stmt_has_calls(stmt.then_branch):
                return True
            if stmt.else_branch and ASTOptimizer._stmt_has_calls(stmt.else_branch):
                return True
            return False
        if isinstance(stmt, ast.WhileStmt):
            return ASTOptimizer._expr_has_calls(stmt.condition) or ASTOptimizer._stmt_has_calls(stmt.body)
        if isinstance(stmt, ast.DoWhileStmt):
            return ASTOptimizer._stmt_has_calls(stmt.body) or ASTOptimizer._expr_has_calls(stmt.condition)
        if isinstance(stmt, ast.ForStmt):
            if stmt.init and isinstance(stmt.init, ast.Expression) and ASTOptimizer._expr_has_calls(stmt.init):
                return True
            if stmt.condition and ASTOptimizer._expr_has_calls(stmt.condition):
                return True
            if stmt.update and ASTOptimizer._expr_has_calls(stmt.update):
                return True
            return ASTOptimizer._stmt_has_calls(stmt.body)
        if isinstance(stmt, ast.CaseStmt):
            return ASTOptimizer._stmt_has_calls(stmt.stmt)
        if isinstance(stmt, ast.LabelStmt):
            return ASTOptimizer._stmt_has_calls(stmt.stmt)
        if isinstance(stmt, ast.ReturnStmt):
            return stmt.value is not None and ASTOptimizer._expr_has_calls(stmt.value)
        if isinstance(stmt, ast.SwitchStmt):
            return ASTOptimizer._expr_has_calls(stmt.expr) or ASTOptimizer._stmt_has_calls(stmt.body)
        return False

    @staticmethod
    def _expr_has_calls(expr: ast.Expression) -> bool:
        """Check if an expression tree contains any function calls."""
        if isinstance(expr, ast.Call):
            return True
        if isinstance(expr, ast.BinaryOp):
            return ASTOptimizer._expr_has_calls(expr.left) or ASTOptimizer._expr_has_calls(expr.right)
        if isinstance(expr, ast.UnaryOp):
            return ASTOptimizer._expr_has_calls(expr.operand)
        if isinstance(expr, ast.TernaryOp):
            return (ASTOptimizer._expr_has_calls(expr.condition) or
                    ASTOptimizer._expr_has_calls(expr.true_expr) or
                    ASTOptimizer._expr_has_calls(expr.false_expr))
        if isinstance(expr, ast.Index):
            return ASTOptimizer._expr_has_calls(expr.array) or ASTOptimizer._expr_has_calls(expr.index)
        if isinstance(expr, ast.Cast):
            return ASTOptimizer._expr_has_calls(expr.expr)
        return False

    # === Level 3: Commutative normalization ===

    def _normalize_commutative(self, expr: ast.BinaryOp) -> ast.Expression | None:
        """Sort operands of commutative ops into canonical form.

        Order: Identifier < Complex < IntLiteral.
        This ensures a+5 and 5+a get the same CSE key.
        """
        if expr.op not in ("+", "*", "&", "|", "^", "==", "!="):
            return None

        def sort_key(e: ast.Expression) -> tuple:
            if isinstance(e, ast.Identifier):
                return (0, e.name)
            if isinstance(e, ast.IntLiteral):
                return (2, e.value)
            if isinstance(e, ast.CharLiteral):
                return (2, e.value)
            # Complex expression
            k = self._expr_key(e)
            return (1, k if k else "")

        lk = sort_key(expr.left)
        rk = sort_key(expr.right)
        if lk > rk:
            self._stat("commutative_norm")
            self._changed = True
            expr.left, expr.right = expr.right, expr.left
            return expr
        return None

    # === Level 3: Common Subexpression Elimination ===

    def _cse_lookup(self, expr: ast.Expression) -> ast.Expression:
        """Look up expression in CSE cache; return cached copy or cache this one."""
        key = self._expr_key(expr)
        if key is None:
            return expr  # Not a pure expression

        if key in self._cse_cache:
            self._stat("cse")
            self._changed = True
            return self._cse_cache[key]

        # Cache this expression
        self._cse_cache[key] = expr
        self._cse_deps[key] = self._get_expr_vars(expr)
        return expr

    def _invalidate_cse_for_var(self, name: str) -> None:
        """Invalidate all CSE entries that depend on a variable."""
        to_remove = [k for k, deps in self._cse_deps.items() if name in deps]
        for k in to_remove:
            del self._cse_cache[k]
            del self._cse_deps[k]

    def _clear_all_caches(self) -> None:
        """Clear all CSE and copy propagation caches (for non-linear control flow)."""
        self._cse_cache.clear()
        self._cse_deps.clear()
        self._copies.clear()

    def _invalidate_caches_for_stmt(self, item) -> None:
        """After processing a statement, invalidate caches as needed."""
        if isinstance(item, (ast.LabelStmt, ast.CaseStmt)):
            # Non-linear control flow: clear everything
            self._clear_all_caches()
            return

        if isinstance(item, ast.ExpressionStmt) and item.expr is not None:
            self._invalidate_caches_for_expr(item.expr)
        elif isinstance(item, ast.VarDecl) and item.init is not None:
            # Variable declaration with init: x is assigned
            self._invalidate_cse_for_var(item.name)
            self._invalidate_copies_for_var(item.name)
            # Track variable type
            if item.var_type is not None:
                self._var_types[item.name] = item.var_type
            # Track copy: if init is simple identifier with compatible type
            if isinstance(item.init, ast.Identifier):
                if self._types_compatible_for_copy(item.name, item.init.name):
                    self._copies[item.name] = item.init.name
        elif isinstance(item, (ast.IfStmt, ast.WhileStmt, ast.DoWhileStmt,
                               ast.ForStmt, ast.SwitchStmt)):
            # Control flow structures: conservatively clear caches
            self._clear_all_caches()
        elif isinstance(item, (ast.ReturnStmt, ast.GotoStmt)):
            self._clear_all_caches()

    def _invalidate_caches_for_expr(self, expr: ast.Expression) -> None:
        """Invalidate CSE/copy caches based on side effects in an expression."""
        if isinstance(expr, ast.BinaryOp):
            if expr.op in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
                           "^=", "<<=", ">>="):
                if isinstance(expr.left, ast.Identifier):
                    name = expr.left.name
                    self._invalidate_cse_for_var(name)
                    self._invalidate_copies_for_var(name)
                    # Track simple copy: x = y (only if types are compatible)
                    if expr.op == "=" and isinstance(expr.right, ast.Identifier):
                        if self._types_compatible_for_copy(name, expr.right.name):
                            self._copies[name] = expr.right.name
                elif isinstance(expr.left, (ast.UnaryOp, ast.Index)):
                    # Pointer/array write: clear all caches
                    self._clear_all_caches()
                    return
            self._invalidate_caches_for_expr(expr.left)
            self._invalidate_caches_for_expr(expr.right)
        elif isinstance(expr, ast.UnaryOp):
            if expr.op in ("++", "--"):
                if isinstance(expr.operand, ast.Identifier):
                    self._invalidate_cse_for_var(expr.operand.name)
                    self._invalidate_copies_for_var(expr.operand.name)
                else:
                    # Pointer increment etc.
                    self._clear_all_caches()
                    return
            self._invalidate_caches_for_expr(expr.operand)
        elif isinstance(expr, ast.Call):
            # Function call: clear entire cache (can modify globals, address-taken vars)
            self._clear_all_caches()
        elif isinstance(expr, ast.TernaryOp):
            self._invalidate_caches_for_expr(expr.condition)
            self._invalidate_caches_for_expr(expr.true_expr)
            self._invalidate_caches_for_expr(expr.false_expr)

    # === Level 3: Copy propagation ===

    def _invalidate_copies_for_var(self, name: str) -> None:
        """Remove copy entries involving a variable (as source or target)."""
        # Remove if name is a target
        self._copies.pop(name, None)
        # Remove entries where name is the source
        to_remove = [k for k, v in self._copies.items() if v == name]
        for k in to_remove:
            del self._copies[k]

    def _types_compatible_for_copy(self, target: str, source: str) -> bool:
        """Check if copy propagation is safe between two variables.

        Only propagate when both variables have the same basic type category
        (both int-like, both float, both pointers, etc.) to avoid miscompilation
        when assignments perform implicit type conversion (e.g., float -> int).
        """
        t1 = self._var_types.get(target)
        t2 = self._var_types.get(source)
        if t1 is None or t2 is None:
            return False  # Unknown type, don't propagate
        # Both must be BasicType with the same name, or both PointerType, etc.
        if type(t1) != type(t2):
            return False
        if isinstance(t1, ast.BasicType) and isinstance(t2, ast.BasicType):
            # Must be same type category (e.g., both "int", both "float")
            return t1.name == t2.name
        # For pointers, arrays, structs - same type means compatible
        return True

    # === Level 3: Dead store elimination ===

    def _eliminate_dead_stores(self, items: list) -> list:
        """Remove dead stores: consecutive assignments to same variable where first is unused."""
        if len(items) < 2:
            return items

        result = []
        i = 0
        while i < len(items):
            if i + 1 < len(items):
                # Check for consecutive assignments to same variable
                first = items[i]
                second = items[i + 1]
                first_target = self._get_simple_assign_target(first)
                second_target = self._get_simple_assign_target(second)

                if (first_target is not None and second_target is not None
                        and first_target == second_target):
                    first_rhs = self._get_assign_rhs(first)
                    second_rhs = self._get_assign_rhs(second)
                    if (first_rhs is not None
                            and not self._expr_has_side_effects(first_rhs)
                            and second_rhs is not None
                            and not self._expr_references_var(second_rhs, first_target)):
                        # Dead store: skip first assignment
                        self._stat("dead_store")
                        self._changed = True
                        i += 1  # Skip to second, which will be added next iteration
                        continue

            result.append(items[i])
            i += 1
        return result

    @staticmethod
    def _get_simple_assign_target(item) -> str | None:
        """If item is `x = expr;`, return 'x'. Otherwise None."""
        if not isinstance(item, ast.ExpressionStmt):
            return None
        expr = item.expr
        if not isinstance(expr, ast.BinaryOp) or expr.op != "=":
            return None
        if isinstance(expr.left, ast.Identifier):
            return expr.left.name
        return None

    @staticmethod
    def _get_assign_rhs(item) -> ast.Expression | None:
        """Get the RHS of a simple assignment statement."""
        if not isinstance(item, ast.ExpressionStmt):
            return None
        expr = item.expr
        if not isinstance(expr, ast.BinaryOp) or expr.op != "=":
            return None
        return expr.right

    # === Level 3: Loop unrolling ===

    def _try_loop_unroll(self, stmt: ast.ForStmt) -> ast.CompoundStmt | None:
        """Try to unroll a small constant-bound for loop."""
        # Match: for (var = const; var < const; var++) or similar
        init = stmt.init
        cond = stmt.condition
        update = stmt.update

        # Check init: var = const
        if not isinstance(init, ast.BinaryOp) or init.op != "=":
            return None
        if not isinstance(init.left, ast.Identifier):
            return None
        if not isinstance(init.right, ast.IntLiteral):
            return None
        var_name = init.left.name
        start_val = init.right.value

        # Check condition: var < const, var <= const, var != const
        if not isinstance(cond, ast.BinaryOp):
            return None
        if not isinstance(cond.left, ast.Identifier) or cond.left.name != var_name:
            return None
        if not isinstance(cond.right, ast.IntLiteral):
            return None
        end_val = cond.right.value
        cmp_op = cond.op

        # Check update: var++ or var += 1
        step = None
        if isinstance(update, ast.UnaryOp) and update.op == "++" and isinstance(update.operand, ast.Identifier) and update.operand.name == var_name:
            step = 1
        elif isinstance(update, ast.BinaryOp) and update.op == "+=" and isinstance(update.left, ast.Identifier) and update.left.name == var_name and isinstance(update.right, ast.IntLiteral):
            step = update.right.value
        if step is None or step <= 0:
            return None

        # Calculate iteration count
        if cmp_op == "<":
            iterations = max(0, (end_val - start_val + step - 1) // step)
        elif cmp_op == "<=":
            iterations = max(0, (end_val - start_val + step) // step)
        elif cmp_op == "!=":
            if (end_val - start_val) % step != 0:
                return None  # Would be infinite loop
            iterations = max(0, (end_val - start_val) // step)
        else:
            return None

        # Check criteria
        if iterations > 4 or iterations <= 0:
            return None

        # Check body size and no break/continue/goto/labels
        body = stmt.body
        body_stmts = self._get_flat_stmts(body)
        if body_stmts is None:
            return None
        if len(body_stmts) > 3:
            return None
        if self._contains_flow_control(body):
            return None

        # Unroll!
        self._stat("loop_unroll")
        self._changed = True

        items: list = []
        for i in range(iterations):
            val = start_val + i * step
            # var = val
            assign = ast.ExpressionStmt(
                expr=ast.BinaryOp(
                    op="=",
                    left=ast.Identifier(name=var_name, location=stmt.location),
                    right=ast.IntLiteral(value=val, location=stmt.location),
                    location=stmt.location,
                ),
                location=stmt.location,
            )
            items.append(assign)
            # Deep copy of body statements
            for s in body_stmts:
                items.append(copy.deepcopy(s))

        return ast.CompoundStmt(items=items, location=stmt.location)

    @staticmethod
    def _get_flat_stmts(body) -> list | None:
        """Get flat list of statements from a loop body. Returns None if not flattenable."""
        if isinstance(body, ast.CompoundStmt):
            # All items must be statements (no declarations)
            result = []
            for item in body.items:
                if not isinstance(item, ast.Statement):
                    return None
                result.append(item)
            return result
        if isinstance(body, ast.Statement):
            return [body]
        return None

    @staticmethod
    def _contains_flow_control(node) -> bool:
        """Check if a statement contains break, continue, goto, or labels."""
        if isinstance(node, (ast.BreakStmt, ast.ContinueStmt, ast.GotoStmt)):
            return True
        if isinstance(node, (ast.LabelStmt, ast.CaseStmt)):
            return True
        if isinstance(node, ast.CompoundStmt):
            return any(ASTOptimizer._contains_flow_control(item) for item in node.items)
        if isinstance(node, ast.IfStmt):
            if ASTOptimizer._contains_flow_control(node.then_branch):
                return True
            if node.else_branch and ASTOptimizer._contains_flow_control(node.else_branch):
                return True
        if isinstance(node, (ast.WhileStmt, ast.DoWhileStmt)):
            return ASTOptimizer._contains_flow_control(node.body)
        if isinstance(node, ast.ForStmt):
            return ASTOptimizer._contains_flow_control(node.body)
        if isinstance(node, ast.ExpressionStmt):
            return False
        if isinstance(node, ast.ReturnStmt):
            return False
        return False

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
    def _is_effectively_unsigned(lit: ast.IntLiteral) -> bool:
        """Check if a literal is effectively unsigned per C standard 6.4.4.1.

        For hex/octal constants without U suffix, the type is the first that fits:
        int → unsigned int → long int → unsigned long int → ...
        So a hex constant like 0xab00 (43776) that exceeds signed int range (32767)
        but fits unsigned int (65535) is effectively unsigned int.
        """
        if lit.is_unsigned:
            return True
        if not lit.is_hex:
            return False
        # Hex without L suffix
        if not lit.is_long:
            # unsigned int if > INT_MAX but <= UINT_MAX
            if lit.value > 0x7FFF and lit.value <= 0xFFFF:
                return True
            # Value exceeds 16-bit: promoted to long per 6.4.4.1
            # unsigned long if > LONG_MAX but <= ULONG_MAX
            if lit.value > 0x7FFFFFFF and lit.value <= 0xFFFFFFFF:
                return True
            return False
        # Hex with L suffix: unsigned long if > LONG_MAX but <= ULONG_MAX
        if lit.value > 0x7FFFFFFF and lit.value <= 0xFFFFFFFF:
            return True
        return False

    @staticmethod
    def _literal_mask(lit: ast.IntLiteral) -> int:
        """Get bitmask for an IntLiteral based on its type width.

        Per C standard 6.4.4.1, constant type is determined by value and suffix:
        - Decimal without suffix: int → long → long long
        - Hex/octal without suffix: int → unsigned int → long → unsigned long → ...
        - U suffix: unsigned int → unsigned long → unsigned long long
        If value exceeds the range of the current type, promote to next.
        """
        val = lit.value
        # Explicit L suffix
        if lit.is_long:
            if val > 2147483647 or val < -2147483648:
                return 0xFFFFFFFFFFFFFFFF
            return 0xFFFFFFFF
        # No suffix — determine type from value
        # Hex or U suffix: values up to 0xFFFF fit in unsigned int (16-bit)
        if lit.is_hex or lit.is_unsigned:
            if val > 0xFFFF or val < -32768:
                # Exceeds 16-bit range → promote to long (32-bit)
                if val > 0xFFFFFFFF:
                    return 0xFFFFFFFFFFFFFFFF
                return 0xFFFFFFFF
            return 0xFFFF
        # Decimal: values in [-32768, 32767] fit in int (16-bit)
        if val > 32767 or val < -32768:
            # Exceeds 16-bit signed → promote to long (32-bit)
            if val > 2147483647 or val < -2147483648:
                return 0xFFFFFFFFFFFFFFFF
            return 0xFFFFFFFF
        return 0xFFFF

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
        val = val & mask  # Ensure value is in unsigned range first
        sign_bit = (mask >> 1) + 1
        if val & sign_bit:
            return val - (mask + 1)
        return val

    @staticmethod
    def _sizeof_type(t: ast.TypeNode) -> int | None:
        """Compute sizeof(type) at compile time. Returns None if unknown."""
        if isinstance(t, ast.BasicType):
            name = t.name
            if name in ('char', 'signed char', 'unsigned char', 'bool'):
                return 1
            if name in ('short', 'unsigned short', 'int', 'unsigned int'):
                return 2
            if name in ('long', 'unsigned long', 'float', 'double'):
                return 4
            if name in ('long long', 'unsigned long long'):
                return 8
        if isinstance(t, ast.PointerType):
            return 2
        return None  # Structs, arrays — too complex for optimizer

    def _stat(self, name: str) -> None:
        self.stats[name] = self.stats.get(name, 0) + 1
