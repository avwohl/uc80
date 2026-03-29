"""C24 recursive descent parser for uc80 compiler.

Implements parsing per ISO/IEC 9899:2024 Section 6.
"""

from typing import Optional
from .tokens import Token, TokenType, SourceLocation
from .lexer import Lexer, LexerError
from . import ast


class ParseError(Exception):
    """Error during parsing."""
    def __init__(self, message: str, location: SourceLocation):
        self.message = message
        self.location = location
        super().__init__(f"{location}: {message}")


class Parser:
    """Recursive descent parser for C24."""

    # Type specifier keywords
    TYPE_SPECIFIERS = {
        TokenType.VOID, TokenType.CHAR, TokenType.SHORT, TokenType.INT,
        TokenType.LONG, TokenType.FLOAT, TokenType.DOUBLE, TokenType.SIGNED,
        TokenType.UNSIGNED, TokenType.BOOL, TokenType.STRUCT, TokenType.UNION,
        TokenType.ENUM, TokenType.COMPLEX, TokenType.IMAGINARY,
        TokenType.ATOMIC, TokenType.TYPEOF, TokenType.TYPEOF_UNQUAL,
    }

    # Type qualifiers
    TYPE_QUALIFIERS = {
        TokenType.CONST, TokenType.VOLATILE, TokenType.RESTRICT, TokenType.ATOMIC,
    }

    # Storage class specifiers
    STORAGE_CLASSES = {
        TokenType.TYPEDEF, TokenType.EXTERN, TokenType.STATIC,
        TokenType.AUTO, TokenType.REGISTER, TokenType.THREAD_LOCAL,
    }

    # Function specifiers
    FUNCTION_SPECIFIERS = {
        TokenType.INLINE, TokenType.NORETURN,
    }

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0
        self.typedefs: dict[str, ast.TypeNode] = {}  # Map typedef names to target types
        self._last_params: list[ast.ParamDecl] = []  # Store params from last function declarator

    def _current(self) -> Token:
        """Get current token."""
        if self.pos >= len(self.tokens):
            return self.tokens[-1]  # Return EOF
        return self.tokens[self.pos]

    def _peek(self, offset: int = 0) -> Token:
        """Look ahead at token."""
        pos = self.pos + offset
        if pos >= len(self.tokens):
            return self.tokens[-1]
        return self.tokens[pos]

    def _advance(self) -> Token:
        """Consume and return current token."""
        token = self._current()
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return token

    def _check(self, *types: TokenType) -> bool:
        """Check if current token matches any of the types."""
        return self._current().type in types

    def _match(self, *types: TokenType) -> Optional[Token]:
        """Consume token if it matches any of the types."""
        if self._check(*types):
            return self._advance()
        return None

    def _expect(self, token_type: TokenType, message: str = "") -> Token:
        """Consume token of expected type or raise error."""
        if self._check(token_type):
            return self._advance()
        if not message:
            message = f"Expected {token_type.name}"
        raise ParseError(message, self._current().location)

    def _error(self, message: str) -> ParseError:
        """Create a parse error at current location."""
        return ParseError(message, self._current().location)

    def _skip_gcc_attribute(self) -> None:
        """Skip GCC __attribute__((...)) syntax."""
        while (self._check(TokenType.IDENTIFIER) and
               self._current().value in ('__attribute__', '__attribute')):
            self._advance()  # __attribute__
            if self._match(TokenType.LPAREN):
                if self._match(TokenType.LPAREN):
                    # Skip until matching ))
                    depth = 2
                    while depth > 0 and not self._check(TokenType.EOF):
                        if self._match(TokenType.LPAREN):
                            depth += 1
                        elif self._match(TokenType.RPAREN):
                            depth -= 1
                        else:
                            self._advance()

    def _skip_alignas(self) -> None:
        """Skip _Alignas(...) specifier (Z80 has no alignment requirements)."""
        while self._check(TokenType.ALIGNAS):
            self._advance()  # _Alignas
            self._expect(TokenType.LPAREN)
            depth = 1
            while depth > 0 and not self._check(TokenType.EOF):
                if self._match(TokenType.LPAREN):
                    depth += 1
                elif self._match(TokenType.RPAREN):
                    depth -= 1
                else:
                    self._advance()

    def _is_type_name(self) -> bool:
        """Check if current position starts a type name."""
        # Skip any leading __attribute__ for the check
        saved_pos = self.pos
        while (self._check(TokenType.IDENTIFIER) and
               self._current().value in ('__attribute__', '__attribute')):
            self._advance()  # __attribute__
            if self._match(TokenType.LPAREN):
                if self._match(TokenType.LPAREN):
                    depth = 2
                    while depth > 0 and not self._check(TokenType.EOF):
                        if self._match(TokenType.LPAREN):
                            depth += 1
                        elif self._match(TokenType.RPAREN):
                            depth -= 1
                        else:
                            self._advance()

        result = False
        if self._check(*self.TYPE_SPECIFIERS):
            result = True
        elif self._check(*self.TYPE_QUALIFIERS):
            result = True
        elif self._check(TokenType.IDENTIFIER):
            result = self._current().value in self.typedefs

        self.pos = saved_pos  # Restore position
        return result

    # === Type Parsing ===

    def _parse_type_specifier(self) -> ast.TypeNode:
        """Parse type specifier."""
        # Skip leading __attribute__
        self._skip_gcc_attribute()

        loc = self._current().location

        # Collect type specifiers
        is_signed = None
        is_unsigned = False
        is_short = False
        is_long = 0
        is_const = False
        is_volatile = False
        is_complex = False
        base_type = None

        while True:
            if self._match(TokenType.CONST):
                is_const = True
            elif self._match(TokenType.VOLATILE):
                is_volatile = True
            elif self._match(TokenType.SIGNED):
                is_signed = True
            elif self._match(TokenType.UNSIGNED):
                is_unsigned = True
                is_signed = False
            elif self._match(TokenType.SHORT):
                is_short = True
            elif self._match(TokenType.LONG):
                is_long += 1
            elif self._check(TokenType.ATOMIC):
                # _Atomic type qualifier / specifier (ignored on Z80 - single threaded)
                self._advance()  # consume _Atomic
                if self._match(TokenType.LPAREN):
                    # _Atomic(type-name) form: parse inner type and use it
                    inner_type = self._parse_type_name()
                    self._expect(TokenType.RPAREN)
                    inner_type.is_const = inner_type.is_const or is_const
                    inner_type.is_volatile = inner_type.is_volatile or is_volatile
                    return inner_type
                # _Atomic without parens: just a qualifier, continue parsing
            elif self._match(TokenType.COMPLEX):
                is_complex = True
            elif self._match(TokenType.VOID):
                base_type = "void"
            elif self._match(TokenType.CHAR):
                base_type = "char"
            elif self._match(TokenType.INT):
                base_type = "int"
            elif self._match(TokenType.FLOAT):
                base_type = "float"
            elif self._match(TokenType.DOUBLE):
                base_type = "double"
            elif self._match(TokenType.BOOL):
                base_type = "bool"
            elif self._check(TokenType.STRUCT, TokenType.UNION):
                struct_type = self._parse_struct_type()
                # Check for trailing qualifiers: struct S const
                while self._match(TokenType.CONST):
                    is_const = True
                while self._match(TokenType.VOLATILE):
                    is_volatile = True
                struct_type.is_const = is_const
                struct_type.is_volatile = is_volatile
                return struct_type
            elif self._check(TokenType.ENUM):
                enum_type = self._parse_enum_type()
                # Check for trailing qualifiers: enum E const
                while self._match(TokenType.CONST):
                    is_const = True
                while self._match(TokenType.VOLATILE):
                    is_volatile = True
                enum_type.is_const = is_const
                enum_type.is_volatile = is_volatile
                return enum_type
            elif base_type is None and self._check(TokenType.IDENTIFIER) and self._current().value in self.typedefs:
                # Only match typedef name if we don't already have a base type
                # e.g., "int s" where s is a typedef - s should be the variable name, not a type
                typedef_name = self._advance().value
                target_type = self.typedefs[typedef_name]
                # Return a copy of the target type (applying any modifiers)
                # For now, just return the target type directly
                return target_type
            else:
                break

        # Determine final type name
        # Note: 'long int' and 'short int' are just 'long' and 'short'
        # The 'int' is optional and doesn't change the type
        if is_short:
            base_type = "short"
        elif is_long >= 2:
            base_type = "long long"
        elif is_long == 1:
            if base_type == "double":
                base_type = "long double"
            else:
                base_type = "long"
        elif base_type is None:
            if is_signed is not None or is_unsigned:
                base_type = "int"
            elif is_complex:
                base_type = "double"  # _Complex alone defaults to double
            else:
                raise self._error("Expected type specifier")

        # Handle complex types
        if is_complex:
            # _Complex requires floating-point base type
            if base_type not in ("float", "double", "long double"):
                if is_long >= 1:
                    base_type = "long double"
                else:
                    base_type = "double"  # Default to double for invalid combinations
            return ast.ComplexType(base_type=base_type, is_const=is_const,
                                   is_volatile=is_volatile, location=loc)

        return ast.BasicType(name=base_type, is_signed=is_signed,
                             is_const=is_const, is_volatile=is_volatile, location=loc)

    def _parse_struct_type(self) -> ast.StructType:
        """Parse struct/union type, including inline member definitions."""
        loc = self._current().location
        is_union = self._match(TokenType.UNION) is not None
        if not is_union:
            self._expect(TokenType.STRUCT)

        # Skip __attribute__ before name
        self._skip_gcc_attribute()

        name = None
        if self._check(TokenType.IDENTIFIER) and self._current().value not in ('__attribute__', '__attribute'):
            name = self._advance().value

        # Skip __attribute__ after name, before brace
        self._skip_gcc_attribute()

        # Parse inline member definitions if present
        members = []
        if self._check(TokenType.LBRACE):
            self._advance()  # consume {
            while not self._check(TokenType.RBRACE):
                member_type = self._parse_type_specifier()
                while True:
                    member_name, full_type = self._parse_declarator(member_type)
                    bit_width = None
                    if self._match(TokenType.COLON):
                        bit_width = self._parse_expression()
                    members.append(ast.StructMember(name=member_name if member_name else None,
                                                    member_type=full_type, bit_width=bit_width))
                    if not self._match(TokenType.COMMA):
                        break
                self._expect(TokenType.SEMICOLON)
            self._expect(TokenType.RBRACE)
            # Skip __attribute__ after closing brace
            self._skip_gcc_attribute()

        return ast.StructType(name=name, is_union=is_union, members=members, location=loc)

    def _parse_enum_type(self) -> ast.EnumType:
        """Parse enum type, including inline value definitions."""
        loc = self._current().location
        self._expect(TokenType.ENUM)

        name = None
        if self._check(TokenType.IDENTIFIER):
            name = self._advance().value

        # Parse inline enum values if present
        values = []
        if self._check(TokenType.LBRACE):
            self._advance()  # consume {
            while not self._check(TokenType.RBRACE):
                val_name = self._expect(TokenType.IDENTIFIER).value
                value = None
                if self._match(TokenType.ASSIGN):
                    value = self._parse_assignment_expression()
                values.append(ast.EnumValue(name=val_name, value=value, location=self._current().location))
                if not self._match(TokenType.COMMA):
                    break
            self._expect(TokenType.RBRACE)

        return ast.EnumType(name=name, values=values, location=loc)

    def _parse_declarator(self, base_type: ast.TypeNode) -> tuple[str, ast.TypeNode]:
        """Parse declarator, returning (name, full_type)."""
        # Skip __attribute__ before declarator
        self._skip_gcc_attribute()

        # Handle pointers
        pointer_stack = []  # Track pointer modifiers for later
        while self._match(TokenType.STAR):
            # Skip __attribute__ after *
            self._skip_gcc_attribute()
            is_const = self._match(TokenType.CONST) is not None
            is_volatile = self._match(TokenType.VOLATILE) is not None
            # Also consume restrict - we store it but don't enforce semantics
            self._match(TokenType.RESTRICT)
            # Skip __attribute__ after qualifiers
            self._skip_gcc_attribute()
            pointer_stack.append((is_const, is_volatile))

        # If no grouping, apply pointers directly to base type
        if not pointer_stack or not self._check(TokenType.LPAREN) or self._is_paren_function():
            # Apply pointers to base_type
            for is_const, is_volatile in pointer_stack:
                base_type = ast.PointerType(base_type=base_type, is_const=is_const, is_volatile=is_volatile)

        # Skip __attribute__ and _Alignas before declarator name
        self._skip_gcc_attribute()
        self._skip_alignas()

        # Handle parenthesized declarator
        if self._match(TokenType.LPAREN):
            # This could be a function or grouped declarator
            if self._is_type_name() or self._check(TokenType.RPAREN):
                # It's a function type - backtrack
                self.pos -= 1
                name = ""
            else:
                # Grouped declarator like (*fptr)
                # Parse inner declarator recursively with a placeholder
                name, inner_type = self._parse_declarator(base_type)
                # Save the function params from the inner declarator before they get overwritten
                saved_params = getattr(self, '_last_params', [])
                self._expect(TokenType.RPAREN)
                # Parse suffix (array/function) with original base type
                suffix_type = self._parse_declarator_suffix(base_type)
                # Restore saved params (they belong to the actual function, not the return type)
                if saved_params:
                    self._last_params = saved_params
                # Now substitute: inner_type wraps suffix_type
                # inner_type has base_type somewhere inside; replace it with suffix_type
                result_type = self._substitute_base_type(inner_type, base_type, suffix_type)
                return name, result_type
        elif self._check(TokenType.IDENTIFIER):
            name = self._advance().value
        else:
            name = ""  # Abstract declarator

        # Parse array and function suffixes
        result_type = self._parse_declarator_suffix(base_type)
        return name, result_type

    def _is_paren_function(self) -> bool:
        """Check if the upcoming LPAREN is for function params, not grouping."""
        if not self._check(TokenType.LPAREN):
            return False
        # Look ahead past LPAREN
        saved_pos = self.pos
        self._advance()  # consume LPAREN
        result = self._is_type_name() or self._check(TokenType.RPAREN)
        self.pos = saved_pos  # restore position
        return result

    def _substitute_base_type(self, type_node: ast.TypeNode, old_base: ast.TypeNode,
                              new_base: ast.TypeNode) -> ast.TypeNode:
        """Replace old_base with new_base inside type_node."""
        if type_node is old_base:
            return new_base
        if isinstance(type_node, ast.PointerType):
            return ast.PointerType(
                base_type=self._substitute_base_type(type_node.base_type, old_base, new_base),
                is_const=type_node.is_const,
                is_volatile=type_node.is_volatile
            )
        if isinstance(type_node, ast.ArrayType):
            return ast.ArrayType(
                base_type=self._substitute_base_type(type_node.base_type, old_base, new_base),
                size=type_node.size
            )
        if isinstance(type_node, ast.FunctionType):
            return ast.FunctionType(
                return_type=self._substitute_base_type(type_node.return_type, old_base, new_base),
                param_types=type_node.param_types,
                is_variadic=type_node.is_variadic
            )
        return type_node

    def _parse_declarator_suffix(self, base_type: ast.TypeNode) -> ast.TypeNode:
        """Parse array brackets and function parameters."""
        while True:
            if self._check(TokenType.LBRACKET):
                # Collect all consecutive array dimensions
                dims = []
                while self._match(TokenType.LBRACKET):
                    # Array - handle C99 array parameter qualifiers [static const N]
                    # Skip type qualifiers: static, const, volatile, restrict
                    while self._check(TokenType.STATIC, TokenType.CONST,
                                      TokenType.VOLATILE, TokenType.RESTRICT):
                        self._advance()
                    # Parse size expression if present
                    size = None
                    if not self._check(TokenType.RBRACKET):
                        if self._match(TokenType.STAR):
                            # VLA with [*] - variable length array placeholder
                            pass
                        else:
                            size = self._parse_expression()
                    self._expect(TokenType.RBRACKET)
                    dims.append(size)
                # Build type from right to left: first dimension is outermost
                # e.g. int arr[3][4] -> ArrayType(size=3, base=ArrayType(size=4, base=int))
                for size in reversed(dims):
                    base_type = ast.ArrayType(base_type=base_type, size=size)
            elif self._match(TokenType.LPAREN):
                # Function
                params = []
                is_variadic = False
                if not self._check(TokenType.RPAREN):
                    if self._check(TokenType.VOID) and self._peek(1).type == TokenType.RPAREN:
                        self._advance()  # void
                    else:
                        params, is_variadic = self._parse_parameter_list()
                self._expect(TokenType.RPAREN)
                # Skip __attribute__ after function parameters
                self._skip_gcc_attribute()
                # Store params for use by FunctionDecl creation
                self._last_params = params
                base_type = ast.FunctionType(return_type=base_type,
                                             param_types=[p.param_type for p in params],
                                             is_variadic=is_variadic)
            else:
                break
        return base_type

    def _parse_parameter_list(self) -> tuple[list[ast.ParamDecl], bool]:
        """Parse function parameter list (ANSI or K&R style)."""
        # Detect K&R style: first token is an identifier that's not a type keyword or typedef,
        # and the next token is ',' or ')'
        if (self._check(TokenType.IDENTIFIER) and
            self._current().value not in self.typedefs and
            self._peek(1).type in (TokenType.COMMA, TokenType.RPAREN)):
            # K&R-style identifier list
            return self._parse_kr_identifier_list()

        params = []
        is_variadic = False

        while True:
            if self._match(TokenType.ELLIPSIS):
                is_variadic = True
                break

            param = self._parse_parameter_declaration()
            params.append(param)

            if not self._match(TokenType.COMMA):
                break

        return params, is_variadic

    def _parse_kr_identifier_list(self) -> tuple[list[ast.ParamDecl], bool]:
        """Parse K&R-style identifier list: func(a, b, c)."""
        params = []
        while True:
            loc = self._current().location
            name = self._expect(TokenType.IDENTIFIER).value
            # Default to int type (will be updated by K&R declarations)
            params.append(ast.ParamDecl(name=name,
                param_type=ast.BasicType(name="int", location=loc), location=loc))
            if not self._match(TokenType.COMMA):
                break
        return params, False

    # Type keywords that start a declaration (used for K&R detection)
    _TYPE_START_TOKENS = {
        TokenType.VOID, TokenType.CHAR, TokenType.SHORT, TokenType.INT,
        TokenType.LONG, TokenType.FLOAT, TokenType.DOUBLE, TokenType.SIGNED,
        TokenType.UNSIGNED, TokenType.STRUCT, TokenType.UNION, TokenType.ENUM,
        TokenType.CONST, TokenType.VOLATILE, TokenType.BOOL, TokenType.COMPLEX,
        TokenType.REGISTER, TokenType.STATIC, TokenType.ATOMIC,
    }

    def _is_kr_declaration_start(self) -> bool:
        """Check if the current position has K&R parameter declarations."""
        tok = self._current()
        if tok.type in self._TYPE_START_TOKENS:
            return True
        if tok.type == TokenType.IDENTIFIER and tok.value in self.typedefs:
            return True
        return False

    def _parse_kr_declarations(self, params: list[ast.ParamDecl]) -> None:
        """Parse K&R-style parameter declarations and update param types."""
        while not self._check(TokenType.LBRACE) and not self._check(TokenType.EOF):
            base_type = self._parse_type_specifier()
            # Parse one or more declarators
            first = True
            while first or self._match(TokenType.COMMA):
                first = False
                name, full_type = self._parse_declarator(base_type)
                # Array parameters adjust to pointer type (C11 6.7.6.3p7)
                if isinstance(full_type, ast.ArrayType):
                    full_type = ast.PointerType(base_type=full_type.base_type)
                elif isinstance(full_type, ast.FunctionType):
                    full_type = ast.PointerType(base_type=full_type)
                # Update the matching parameter
                if name:
                    for p in params:
                        if p.name == name:
                            p.param_type = full_type
                            break
            self._expect(TokenType.SEMICOLON)

    def _parse_parameter_declaration(self) -> ast.ParamDecl:
        """Parse a single parameter declaration."""
        loc = self._current().location
        # C standard allows 'register' as storage-class specifier in parameters
        self._match(TokenType.REGISTER)
        base_type = self._parse_type_specifier()
        name, full_type = self._parse_declarator(base_type)
        # C11 6.7.6.3p7: Array parameters are adjusted to pointer type
        if isinstance(full_type, ast.ArrayType):
            full_type = ast.PointerType(base_type=full_type.base_type)
        # C11 6.7.6.3p8: Function parameters are adjusted to pointer-to-function
        elif isinstance(full_type, ast.FunctionType):
            full_type = ast.PointerType(base_type=full_type)
        return ast.ParamDecl(name=name if name else None, param_type=full_type, location=loc)

    def _parse_type_name(self) -> ast.TypeNode:
        """Parse a type name (for casts, sizeof)."""
        base_type = self._parse_type_specifier()
        _, full_type = self._parse_declarator(base_type)
        return full_type

    # === Expression Parsing ===

    def _parse_expression(self) -> ast.Expression:
        """Parse expression (comma operator level)."""
        return self._parse_comma_expression()

    def _parse_comma_expression(self) -> ast.Expression:
        """Parse comma expression."""
        left = self._parse_assignment_expression()
        while self._match(TokenType.COMMA):
            loc = self._current().location
            right = self._parse_assignment_expression()
            left = ast.BinaryOp(op=",", left=left, right=right, location=loc)
        return left

    def _parse_assignment_expression(self) -> ast.Expression:
        """Parse assignment expression."""
        left = self._parse_ternary_expression()

        assign_ops = {
            TokenType.ASSIGN: "=",
            TokenType.MUL_ASSIGN: "*=",
            TokenType.DIV_ASSIGN: "/=",
            TokenType.MOD_ASSIGN: "%=",
            TokenType.ADD_ASSIGN: "+=",
            TokenType.SUB_ASSIGN: "-=",
            TokenType.LSHIFT_ASSIGN: "<<=",
            TokenType.RSHIFT_ASSIGN: ">>=",
            TokenType.AND_ASSIGN: "&=",
            TokenType.XOR_ASSIGN: "^=",
            TokenType.OR_ASSIGN: "|=",
        }

        if self._current().type in assign_ops:
            op = assign_ops[self._advance().type]
            loc = self._current().location
            right = self._parse_assignment_expression()
            return ast.BinaryOp(op=op, left=left, right=right, location=loc)

        return left

    def _parse_ternary_expression(self) -> ast.Expression:
        """Parse ternary conditional expression."""
        cond = self._parse_logical_or()

        if self._match(TokenType.QUESTION):
            loc = self._current().location
            true_expr = self._parse_expression()
            self._expect(TokenType.COLON)
            false_expr = self._parse_ternary_expression()
            return ast.TernaryOp(condition=cond, true_expr=true_expr,
                                 false_expr=false_expr, location=loc)

        return cond

    def _parse_logical_or(self) -> ast.Expression:
        """Parse logical OR expression."""
        left = self._parse_logical_and()
        while self._match(TokenType.OR):
            loc = self._current().location
            right = self._parse_logical_and()
            left = ast.BinaryOp(op="||", left=left, right=right, location=loc)
        return left

    def _parse_logical_and(self) -> ast.Expression:
        """Parse logical AND expression."""
        left = self._parse_bitwise_or()
        while self._match(TokenType.AND):
            loc = self._current().location
            right = self._parse_bitwise_or()
            left = ast.BinaryOp(op="&&", left=left, right=right, location=loc)
        return left

    def _parse_bitwise_or(self) -> ast.Expression:
        """Parse bitwise OR expression."""
        left = self._parse_bitwise_xor()
        while self._match(TokenType.PIPE):
            loc = self._current().location
            right = self._parse_bitwise_xor()
            left = ast.BinaryOp(op="|", left=left, right=right, location=loc)
        return left

    def _parse_bitwise_xor(self) -> ast.Expression:
        """Parse bitwise XOR expression."""
        left = self._parse_bitwise_and()
        while self._match(TokenType.CARET):
            loc = self._current().location
            right = self._parse_bitwise_and()
            left = ast.BinaryOp(op="^", left=left, right=right, location=loc)
        return left

    def _parse_bitwise_and(self) -> ast.Expression:
        """Parse bitwise AND expression."""
        left = self._parse_equality()
        while self._match(TokenType.AMPERSAND):
            loc = self._current().location
            right = self._parse_equality()
            left = ast.BinaryOp(op="&", left=left, right=right, location=loc)
        return left

    def _parse_equality(self) -> ast.Expression:
        """Parse equality expression."""
        left = self._parse_relational()
        while True:
            if self._match(TokenType.EQ):
                loc = self._current().location
                right = self._parse_relational()
                left = ast.BinaryOp(op="==", left=left, right=right, location=loc)
            elif self._match(TokenType.NE):
                loc = self._current().location
                right = self._parse_relational()
                left = ast.BinaryOp(op="!=", left=left, right=right, location=loc)
            else:
                break
        return left

    def _parse_relational(self) -> ast.Expression:
        """Parse relational expression."""
        left = self._parse_shift()
        while True:
            if self._match(TokenType.LT):
                loc = self._current().location
                right = self._parse_shift()
                left = ast.BinaryOp(op="<", left=left, right=right, location=loc)
            elif self._match(TokenType.GT):
                loc = self._current().location
                right = self._parse_shift()
                left = ast.BinaryOp(op=">", left=left, right=right, location=loc)
            elif self._match(TokenType.LE):
                loc = self._current().location
                right = self._parse_shift()
                left = ast.BinaryOp(op="<=", left=left, right=right, location=loc)
            elif self._match(TokenType.GE):
                loc = self._current().location
                right = self._parse_shift()
                left = ast.BinaryOp(op=">=", left=left, right=right, location=loc)
            else:
                break
        return left

    def _parse_shift(self) -> ast.Expression:
        """Parse shift expression."""
        left = self._parse_additive()
        while True:
            if self._match(TokenType.LSHIFT):
                loc = self._current().location
                right = self._parse_additive()
                left = ast.BinaryOp(op="<<", left=left, right=right, location=loc)
            elif self._match(TokenType.RSHIFT):
                loc = self._current().location
                right = self._parse_additive()
                left = ast.BinaryOp(op=">>", left=left, right=right, location=loc)
            else:
                break
        return left

    def _parse_additive(self) -> ast.Expression:
        """Parse additive expression."""
        left = self._parse_multiplicative()
        while True:
            if self._match(TokenType.PLUS):
                loc = self._current().location
                right = self._parse_multiplicative()
                left = ast.BinaryOp(op="+", left=left, right=right, location=loc)
            elif self._match(TokenType.MINUS):
                loc = self._current().location
                right = self._parse_multiplicative()
                left = ast.BinaryOp(op="-", left=left, right=right, location=loc)
            else:
                break
        return left

    def _parse_multiplicative(self) -> ast.Expression:
        """Parse multiplicative expression."""
        left = self._parse_cast()
        while True:
            if self._match(TokenType.STAR):
                loc = self._current().location
                right = self._parse_cast()
                left = ast.BinaryOp(op="*", left=left, right=right, location=loc)
            elif self._match(TokenType.SLASH):
                loc = self._current().location
                right = self._parse_cast()
                left = ast.BinaryOp(op="/", left=left, right=right, location=loc)
            elif self._match(TokenType.PERCENT):
                loc = self._current().location
                right = self._parse_cast()
                left = ast.BinaryOp(op="%", left=left, right=right, location=loc)
            else:
                break
        return left

    def _parse_cast(self) -> ast.Expression:
        """Parse cast expression."""
        # Check for cast: (type-name) cast-expression
        if self._check(TokenType.LPAREN):
            # Look ahead to see if this is a cast or parenthesized expression
            saved_pos = self.pos
            self._advance()  # (
            if self._is_type_name():
                target_type = self._parse_type_name()
                if self._match(TokenType.RPAREN):
                    # Check for compound literal
                    if self._check(TokenType.LBRACE):
                        init = self._parse_initializer_list()
                        return ast.Compound(target_type=target_type, init=init,
                                            location=target_type.location)
                    # Regular cast
                    expr = self._parse_cast()
                    return ast.Cast(target_type=target_type, expr=expr,
                                    location=target_type.location)
            # Not a cast, restore position
            self.pos = saved_pos

        return self._parse_unary()

    def _parse_unary(self) -> ast.Expression:
        """Parse unary expression."""
        loc = self._current().location

        # Prefix operators
        if self._match(TokenType.INCREMENT):
            return ast.UnaryOp(op="++", operand=self._parse_unary(), is_prefix=True, location=loc)
        if self._match(TokenType.DECREMENT):
            return ast.UnaryOp(op="--", operand=self._parse_unary(), is_prefix=True, location=loc)
        if self._match(TokenType.AMPERSAND):
            return ast.UnaryOp(op="&", operand=self._parse_cast(), is_prefix=True, location=loc)
        if self._match(TokenType.STAR):
            return ast.UnaryOp(op="*", operand=self._parse_cast(), is_prefix=True, location=loc)
        if self._match(TokenType.PLUS):
            return ast.UnaryOp(op="+", operand=self._parse_cast(), is_prefix=True, location=loc)
        if self._match(TokenType.MINUS):
            return ast.UnaryOp(op="-", operand=self._parse_cast(), is_prefix=True, location=loc)
        if self._match(TokenType.TILDE):
            return ast.UnaryOp(op="~", operand=self._parse_cast(), is_prefix=True, location=loc)
        if self._match(TokenType.BANG):
            return ast.UnaryOp(op="!", operand=self._parse_cast(), is_prefix=True, location=loc)

        # sizeof
        if self._match(TokenType.SIZEOF):
            if self._check(TokenType.LPAREN):
                saved_pos = self.pos
                self._advance()  # (
                if self._is_type_name():
                    target_type = self._parse_type_name()
                    self._expect(TokenType.RPAREN)
                    return ast.SizeofType(target_type=target_type, location=loc)
                self.pos = saved_pos
            return ast.SizeofExpr(expr=self._parse_unary(), location=loc)

        # alignof
        if self._match(TokenType.ALIGNOF):
            self._expect(TokenType.LPAREN)
            target_type = self._parse_type_name()
            self._expect(TokenType.RPAREN)
            return ast.SizeofType(target_type=target_type, location=loc)  # Reuse SizeofType

        return self._parse_postfix()

    def _parse_postfix(self) -> ast.Expression:
        """Parse postfix expression."""
        expr = self._parse_primary()

        while True:
            loc = self._current().location
            if self._match(TokenType.LBRACKET):
                # Array subscript
                index = self._parse_expression()
                self._expect(TokenType.RBRACKET)
                expr = ast.Index(array=expr, index=index, location=loc)
            elif self._match(TokenType.LPAREN):
                # Function call
                args = []
                if not self._check(TokenType.RPAREN):
                    args.append(self._parse_assignment_expression())
                    while self._match(TokenType.COMMA):
                        args.append(self._parse_assignment_expression())
                self._expect(TokenType.RPAREN)
                expr = ast.Call(func=expr, args=args, location=loc)
            elif self._match(TokenType.DOT):
                # Member access
                member = self._expect(TokenType.IDENTIFIER).value
                expr = ast.Member(obj=expr, member=member, is_arrow=False, location=loc)
            elif self._match(TokenType.ARROW):
                # Pointer member access
                member = self._expect(TokenType.IDENTIFIER).value
                expr = ast.Member(obj=expr, member=member, is_arrow=True, location=loc)
            elif self._match(TokenType.INCREMENT):
                # Postfix increment
                expr = ast.UnaryOp(op="++", operand=expr, is_prefix=False, location=loc)
            elif self._match(TokenType.DECREMENT):
                # Postfix decrement
                expr = ast.UnaryOp(op="--", operand=expr, is_prefix=False, location=loc)
            else:
                break

        return expr

    def _parse_primary(self) -> ast.Expression:
        """Parse primary expression."""
        loc = self._current().location

        # _Generic selection (C11)
        if self._match(TokenType.GENERIC):
            return self._parse_generic_selection(loc)

        # Literals
        if self._check(TokenType.INT_LITERAL):
            token = self._advance()
            if len(token.value) == 3:
                value, suffix, base = token.value
            else:
                value, suffix = token.value
                base = 10
            is_long = 'l' in suffix
            is_unsigned = 'u' in suffix
            is_hex = base in (16, 8, 2)
            return ast.IntLiteral(value=value, is_long=is_long, is_unsigned=is_unsigned,
                                  is_hex=is_hex, location=loc)
        if self._check(TokenType.FLOAT_LITERAL):
            tok = self._advance()
            fval, has_f = tok.value if isinstance(tok.value, tuple) else (tok.value, False)
            return ast.FloatLiteral(value=fval, is_float=has_f, location=loc)
        if self._check(TokenType.CHAR_LITERAL) or self._check(TokenType.WIDE_CHAR_LITERAL):
            return ast.CharLiteral(value=self._advance().value, location=loc)
        if self._check(TokenType.STRING_LITERAL) or self._check(TokenType.WIDE_STRING_LITERAL):
            # Concatenate adjacent string literals; wide if any is wide
            tok = self._advance()
            is_wide = tok.type == TokenType.WIDE_STRING_LITERAL
            value = tok.value
            while self._check(TokenType.STRING_LITERAL) or self._check(TokenType.WIDE_STRING_LITERAL):
                tok = self._advance()
                if tok.type == TokenType.WIDE_STRING_LITERAL:
                    is_wide = True
                value += tok.value
            return ast.StringLiteral(value=value, is_wide=is_wide, location=loc)
        if self._match(TokenType.TRUE):
            return ast.BoolLiteral(value=True, location=loc)
        if self._match(TokenType.FALSE):
            return ast.BoolLiteral(value=False, location=loc)
        if self._match(TokenType.NULLPTR):
            return ast.NullptrLiteral(location=loc)

        # Identifier
        if self._check(TokenType.IDENTIFIER):
            return ast.Identifier(name=self._advance().value, location=loc)

        # Parenthesized expression or statement expression
        if self._match(TokenType.LPAREN):
            # Check for statement expression: ({ ... })
            if self._check(TokenType.LBRACE):
                body = self._parse_compound_statement()
                self._expect(TokenType.RPAREN)
                return ast.StmtExpr(body=body, location=loc)
            expr = self._parse_expression()
            self._expect(TokenType.RPAREN)
            return expr

        # Compound literal without cast
        if self._check(TokenType.LBRACE):
            return self._parse_initializer_list()

        raise self._error(f"Unexpected token: {self._current().type.name}")

    def _parse_generic_selection(self, loc: SourceLocation) -> ast.GenericSelection:
        """Parse _Generic selection expression."""
        self._expect(TokenType.LPAREN)
        controlling_expr = self._parse_assignment_expression()
        self._expect(TokenType.COMMA)

        associations = []
        while True:
            if self._match(TokenType.DEFAULT):
                # default: expr
                self._expect(TokenType.COLON)
                expr = self._parse_assignment_expression()
                associations.append((None, expr))
            else:
                # type: expr
                type_node = self._parse_type_name()
                self._expect(TokenType.COLON)
                expr = self._parse_assignment_expression()
                associations.append((type_node, expr))

            if not self._match(TokenType.COMMA):
                break

        self._expect(TokenType.RPAREN)
        return ast.GenericSelection(controlling_expr=controlling_expr,
                                    associations=associations, location=loc)

    def _parse_initializer_list(self) -> ast.InitializerList:
        """Parse initializer list { ... }."""
        loc = self._current().location
        self._expect(TokenType.LBRACE)

        values = []
        if not self._check(TokenType.RBRACE):
            values.append(self._parse_initializer())
            while self._match(TokenType.COMMA):
                if self._check(TokenType.RBRACE):
                    break  # Trailing comma allowed
                values.append(self._parse_initializer())

        self._expect(TokenType.RBRACE)
        return ast.InitializerList(values=values, location=loc)

    def _parse_initializer(self) -> ast.Expression:
        """Parse a single initializer (possibly designated)."""
        loc = self._current().location

        # Check for designated initializer
        designators = []
        while True:
            if self._match(TokenType.DOT):
                member = self._expect(TokenType.IDENTIFIER).value
                designators.append(member)
            elif self._match(TokenType.LBRACKET):
                start_expr = self._parse_expression()
                # Check for range designator [start ... end]
                if self._match(TokenType.ELLIPSIS):
                    end_expr = self._parse_expression()
                    self._expect(TokenType.RBRACKET)
                    designators.append(ast.RangeDesignator(start=start_expr, end=end_expr, location=loc))
                else:
                    self._expect(TokenType.RBRACKET)
                    designators.append(start_expr)
            else:
                break

        if designators:
            self._expect(TokenType.ASSIGN)
            value = self._parse_initializer()
            return ast.DesignatedInit(designators=designators, value=value, location=loc)

        # Regular initializer
        if self._check(TokenType.LBRACE):
            return self._parse_initializer_list()
        return self._parse_assignment_expression()

    # === Statement Parsing ===

    def _parse_statement(self) -> ast.Statement:
        """Parse a statement."""
        loc = self._current().location

        # Compound statement
        if self._check(TokenType.LBRACE):
            return self._parse_compound_statement()

        # Selection statements
        if self._match(TokenType.IF):
            return self._parse_if_statement(loc)
        if self._match(TokenType.SWITCH):
            return self._parse_switch_statement(loc)

        # Iteration statements
        if self._match(TokenType.WHILE):
            return self._parse_while_statement(loc)
        if self._match(TokenType.DO):
            return self._parse_do_while_statement(loc)
        if self._match(TokenType.FOR):
            return self._parse_for_statement(loc)

        # Jump statements
        if self._match(TokenType.GOTO):
            label = self._expect(TokenType.IDENTIFIER).value
            self._expect(TokenType.SEMICOLON)
            return ast.GotoStmt(label=label, location=loc)
        if self._match(TokenType.CONTINUE):
            self._expect(TokenType.SEMICOLON)
            return ast.ContinueStmt(location=loc)
        if self._match(TokenType.BREAK):
            self._expect(TokenType.SEMICOLON)
            return ast.BreakStmt(location=loc)
        if self._match(TokenType.RETURN):
            value = None
            if not self._check(TokenType.SEMICOLON):
                value = self._parse_expression()
            self._expect(TokenType.SEMICOLON)
            return ast.ReturnStmt(value=value, location=loc)

        # Case/default labels (in switch)
        if self._match(TokenType.CASE):
            value = self._parse_expression()
            self._expect(TokenType.COLON)
            stmt = self._parse_statement()
            return ast.CaseStmt(value=value, stmt=stmt, location=loc)
        if self._match(TokenType.DEFAULT):
            self._expect(TokenType.COLON)
            stmt = self._parse_statement()
            return ast.CaseStmt(value=None, stmt=stmt, location=loc)

        # Labeled statement
        if self._check(TokenType.IDENTIFIER) and self._peek(1).type == TokenType.COLON:
            label = self._advance().value
            self._advance()  # :
            stmt = self._parse_statement()
            return ast.LabelStmt(label=label, stmt=stmt, location=loc)

        # Expression statement (or empty)
        if self._match(TokenType.SEMICOLON):
            return ast.ExpressionStmt(expr=None, location=loc)

        expr = self._parse_expression()
        self._expect(TokenType.SEMICOLON)
        return ast.ExpressionStmt(expr=expr, location=loc)

    def _parse_compound_statement(self) -> ast.CompoundStmt:
        """Parse compound statement (block)."""
        loc = self._current().location
        self._expect(TokenType.LBRACE)

        items = []
        while not self._check(TokenType.RBRACE, TokenType.EOF):
            if self._is_declaration_start():
                items.append(self._parse_declaration())
            else:
                items.append(self._parse_statement())

        self._expect(TokenType.RBRACE)
        return ast.CompoundStmt(items=items, location=loc)

    def _parse_if_statement(self, loc: SourceLocation) -> ast.IfStmt:
        """Parse if statement."""
        self._expect(TokenType.LPAREN)
        condition = self._parse_expression()
        self._expect(TokenType.RPAREN)
        then_branch = self._parse_statement()
        else_branch = None
        if self._match(TokenType.ELSE):
            else_branch = self._parse_statement()
        return ast.IfStmt(condition=condition, then_branch=then_branch,
                          else_branch=else_branch, location=loc)

    def _parse_switch_statement(self, loc: SourceLocation) -> ast.SwitchStmt:
        """Parse switch statement."""
        self._expect(TokenType.LPAREN)
        expr = self._parse_expression()
        self._expect(TokenType.RPAREN)
        body = self._parse_statement()
        return ast.SwitchStmt(expr=expr, body=body, location=loc)

    def _parse_while_statement(self, loc: SourceLocation) -> ast.WhileStmt:
        """Parse while statement."""
        self._expect(TokenType.LPAREN)
        condition = self._parse_expression()
        self._expect(TokenType.RPAREN)
        body = self._parse_statement()
        return ast.WhileStmt(condition=condition, body=body, location=loc)

    def _parse_do_while_statement(self, loc: SourceLocation) -> ast.DoWhileStmt:
        """Parse do-while statement."""
        body = self._parse_statement()
        self._expect(TokenType.WHILE)
        self._expect(TokenType.LPAREN)
        condition = self._parse_expression()
        self._expect(TokenType.RPAREN)
        self._expect(TokenType.SEMICOLON)
        return ast.DoWhileStmt(body=body, condition=condition, location=loc)

    def _parse_for_statement(self, loc: SourceLocation) -> ast.ForStmt:
        """Parse for statement."""
        self._expect(TokenType.LPAREN)

        # Init
        init = None
        if not self._check(TokenType.SEMICOLON):
            if self._is_declaration_start():
                init = self._parse_declaration()
            else:
                init = self._parse_expression()
                self._expect(TokenType.SEMICOLON)
        else:
            self._advance()  # ;

        # Condition
        condition = None
        if not self._check(TokenType.SEMICOLON):
            condition = self._parse_expression()
        self._expect(TokenType.SEMICOLON)

        # Update
        update = None
        if not self._check(TokenType.RPAREN):
            update = self._parse_expression()
        self._expect(TokenType.RPAREN)

        body = self._parse_statement()
        return ast.ForStmt(body=body, init=init, condition=condition, update=update, location=loc)

    # === Declaration Parsing ===

    def _is_declaration_start(self) -> bool:
        """Check if current position starts a declaration."""
        if self._check(*self.STORAGE_CLASSES):
            return True
        if self._check(*self.TYPE_QUALIFIERS):
            return True
        if self._check(*self.TYPE_SPECIFIERS):
            return True
        if self._check(*self.FUNCTION_SPECIFIERS):
            return True
        if self._check(TokenType.STATIC_ASSERT):
            return True
        if self._check(TokenType.ALIGNAS):
            return True
        if self._check(TokenType.IDENTIFIER) and self._current().value in self.typedefs:
            # Check it's not a label (identifier followed by colon)
            if self._peek(1).type != TokenType.COLON:
                return True
        return False

    def _parse_declaration(self) -> ast.Declaration:
        """Parse a declaration."""
        loc = self._current().location

        # _Static_assert / static_assert
        if self._match(TokenType.STATIC_ASSERT):
            self._expect(TokenType.LPAREN)
            expr = self._parse_expression()
            msg = ""
            if self._match(TokenType.COMMA):
                msg_tok = self._expect(TokenType.STRING_LITERAL)
                msg = msg_tok.value
            self._expect(TokenType.RPAREN)
            self._expect(TokenType.SEMICOLON)
            # Evaluate at compile time - for now just skip (accept always)
            return ast.ExpressionStmt(expr=None, location=loc)

        # Storage class
        storage_class = None
        is_typedef = False
        is_inline = False

        # Track type qualifiers that appear before the type specifier
        pre_const = False
        pre_volatile = False

        while True:
            if self._match(TokenType.TYPEDEF):
                is_typedef = True
            elif self._match(TokenType.EXTERN):
                storage_class = "extern"
            elif self._match(TokenType.STATIC):
                storage_class = "static"
            elif self._match(TokenType.AUTO):
                storage_class = "auto"
            elif self._match(TokenType.REGISTER):
                storage_class = "register"
            elif self._match(TokenType.THREAD_LOCAL):
                storage_class = "thread_local"
            elif self._match(TokenType.INLINE):
                is_inline = True
            elif self._match(TokenType.NORETURN):
                pass  # Attribute, ignore for now
            elif self._match(TokenType.CONST):
                pre_const = True
            elif self._match(TokenType.VOLATILE):
                pre_volatile = True
            elif self._match(TokenType.ALIGNAS):
                # _Alignas(N) or _Alignas(type) - skip alignment specifier (Z80 has no alignment)
                self._expect(TokenType.LPAREN)
                depth = 1
                while depth > 0 and not self._check(TokenType.EOF):
                    if self._match(TokenType.LPAREN):
                        depth += 1
                    elif self._match(TokenType.RPAREN):
                        depth -= 1
                    else:
                        self._advance()
            else:
                break

        # Type specifier
        base_type = self._parse_type_specifier()
        # Apply pre-consumed qualifiers
        if pre_const:
            base_type.is_const = True
        if pre_volatile:
            base_type.is_volatile = True

        # Skip __attribute__ and _Alignas after type specifier
        self._skip_gcc_attribute()
        self._skip_alignas()

        # C allows declaration specifiers in any order, so storage class and
        # qualifiers can appear after the type specifier (e.g. struct{...} static const x;)
        while True:
            if not storage_class and self._match(TokenType.STATIC):
                storage_class = "static"
            elif not storage_class and self._match(TokenType.EXTERN):
                storage_class = "extern"
            elif not storage_class and self._match(TokenType.AUTO):
                storage_class = "auto"
            elif not storage_class and self._match(TokenType.REGISTER):
                storage_class = "register"
            elif not is_typedef and self._match(TokenType.TYPEDEF):
                is_typedef = True
            elif self._match(TokenType.CONST):
                base_type.is_const = True
            elif self._match(TokenType.VOLATILE):
                base_type.is_volatile = True
            elif self._match(TokenType.INLINE):
                is_inline = True
            else:
                break

        # Handle struct/enum definitions where members were already parsed inline
        if isinstance(base_type, ast.StructType) and base_type.members:
            if self._check(TokenType.SEMICOLON):
                # Bare struct definition: struct Point { ... };
                self._advance()  # consume semicolon
                return ast.StructDecl(name=base_type.name, members=base_type.members,
                                      is_union=base_type.is_union, is_definition=True, location=loc)
            elif is_typedef:
                # typedef struct { ... } Name;
                name, _ = self._parse_declarator(base_type)
                if name:
                    self.typedefs[name] = base_type
                    self._expect(TokenType.SEMICOLON)
                    return ast.TypedefDecl(name=name, target_type=base_type, location=loc)
        if isinstance(base_type, ast.EnumType) and base_type.values:
            if self._check(TokenType.SEMICOLON):
                # Bare enum definition: enum Color { ... };
                self._advance()  # consume semicolon
                return ast.EnumDecl(name=base_type.name, values=base_type.values, is_definition=True, location=loc)
            elif is_typedef:
                # typedef enum { ... } Name;
                name, _ = self._parse_declarator(base_type)
                if name:
                    self.typedefs[name] = base_type
                    self._expect(TokenType.SEMICOLON)
                    return ast.TypedefDecl(name=name, target_type=base_type, location=loc)

        # Declarators
        declarations = []
        first = True
        while first or self._match(TokenType.COMMA):
            first = False
            name, full_type = self._parse_declarator(base_type)

            if not name:
                if self._check(TokenType.SEMICOLON):
                    break
                raise self._error("Expected declarator name")

            # Check for function definition (ANSI or K&R style)
            if isinstance(full_type, ast.FunctionType) and (self._check(TokenType.LBRACE) or self._is_kr_declaration_start()):
                params = self._last_params
                # Parse K&R-style parameter declarations if present
                if not self._check(TokenType.LBRACE):
                    self._parse_kr_declarations(params)
                body = self._parse_compound_statement()
                return ast.FunctionDecl(
                    name=name, return_type=full_type.return_type, params=params,
                    body=body, is_variadic=full_type.is_variadic,
                    storage_class=storage_class, is_inline=is_inline,
                    location=loc
                )

            # Variable or typedef
            init = None
            if self._match(TokenType.ASSIGN):
                init = self._parse_initializer()

            if is_typedef:
                self.typedefs[name] = full_type
                declarations.append(ast.TypedefDecl(name=name, target_type=full_type, location=loc))
            else:
                declarations.append(ast.VarDecl(name=name, var_type=full_type,
                                                init=init, storage_class=storage_class, location=loc))

        self._expect(TokenType.SEMICOLON)

        if len(declarations) == 1:
            return declarations[0]
        # Multiple declarations - wrap in DeclarationList
        return ast.DeclarationList(declarations=declarations, location=loc)

    # === Top Level ===

    def parse(self) -> ast.TranslationUnit:
        """Parse entire translation unit."""
        loc = self._current().location
        declarations = []

        while not self._check(TokenType.EOF):
            declarations.append(self._parse_declaration())

        return ast.TranslationUnit(declarations=declarations, location=loc)


def parse(source: str, filename: str = "<stdin>") -> ast.TranslationUnit:
    """Convenience function to parse source code."""
    from .lexer import tokenize
    tokens = tokenize(source, filename)
    parser = Parser(tokens)
    return parser.parse()
