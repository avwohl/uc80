"""C24 Lexer for uc80 compiler.

Implements tokenization per ISO/IEC 9899:2024 Section 6.4.
"""

from typing import Iterator, Optional
from .tokens import Token, TokenType, SourceLocation, KEYWORDS


class LexerError(Exception):
    """Error during lexical analysis."""
    def __init__(self, message: str, location: SourceLocation):
        self.message = message
        self.location = location
        super().__init__(f"{location}: {message}")


class Lexer:
    """Tokenizer for C24 source code."""

    def __init__(self, source: str, filename: str = "<stdin>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.column = 1

    def _location(self) -> SourceLocation:
        """Get current source location."""
        return SourceLocation(self.filename, self.line, self.column)

    def _peek(self, offset: int = 0) -> str:
        """Look at character at current position + offset."""
        pos = self.pos + offset
        if pos >= len(self.source):
            return '\0'
        return self.source[pos]

    def _advance(self) -> str:
        """Consume and return current character."""
        if self.pos >= len(self.source):
            return '\0'
        ch = self.source[self.pos]
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return ch

    def _match(self, expected: str) -> bool:
        """Consume character if it matches expected."""
        if self._peek() == expected:
            self._advance()
            return True
        return False

    def _skip_whitespace(self) -> None:
        """Skip whitespace characters."""
        while self._peek() in ' \t\n\r\v\f':
            self._advance()

    def _skip_line_comment(self) -> None:
        """Skip // comment to end of line."""
        while self._peek() not in ('\n', '\0'):
            self._advance()

    def _skip_block_comment(self) -> None:
        """Skip /* */ comment."""
        start_loc = self._location()
        while True:
            if self._peek() == '\0':
                raise LexerError("Unterminated block comment", start_loc)
            if self._peek() == '*' and self._peek(1) == '/':
                self._advance()  # *
                self._advance()  # /
                return
            self._advance()

    def _skip_whitespace_and_comments(self) -> None:
        """Skip all whitespace and comments."""
        while True:
            self._skip_whitespace()
            if self._peek() == '/' and self._peek(1) == '/':
                self._advance()
                self._advance()
                self._skip_line_comment()
            elif self._peek() == '/' and self._peek(1) == '*':
                self._advance()
                self._advance()
                self._skip_block_comment()
            else:
                break

    def _is_digit(self, ch: str) -> bool:
        """Check if character is decimal digit."""
        return '0' <= ch <= '9'

    def _is_hex_digit(self, ch: str) -> bool:
        """Check if character is hexadecimal digit."""
        return self._is_digit(ch) or 'a' <= ch.lower() <= 'f'

    def _is_octal_digit(self, ch: str) -> bool:
        """Check if character is octal digit."""
        return '0' <= ch <= '7'

    def _is_binary_digit(self, ch: str) -> bool:
        """Check if character is binary digit."""
        return ch in '01'

    def _is_identifier_start(self, ch: str) -> bool:
        """Check if character can start an identifier."""
        return ch.isalpha() or ch == '_'

    def _is_identifier_part(self, ch: str) -> bool:
        """Check if character can be part of an identifier."""
        return ch.isalnum() or ch == '_'

    def _read_identifier(self) -> str:
        """Read an identifier or keyword."""
        result = []
        while self._is_identifier_part(self._peek()):
            result.append(self._advance())
        return ''.join(result)

    def _read_number(self) -> Token:
        """Read a numeric literal (integer or float).

        Supports:
        - Decimal: 123
        - Hexadecimal: 0x1A, 0X1a
        - Octal: 0777
        - Binary: 0b1010, 0B1010 (C24)
        - Digit separators: 1'000'000 (C24)
        - Float: 3.14, 1e10, 1.5e-3
        - Suffixes: u, U, l, L, ll, LL, ul, UL, etc.
        """
        loc = self._location()
        result = []
        is_float = False
        base = 10

        # Handle float literals starting with dot (e.g., .5, .123e10)
        if self._peek() == '.':
            is_float = True
            result.append(self._advance())  # .
            while self._is_digit(self._peek()) or self._peek() == "'":
                if self._peek() == "'":
                    self._advance()
                else:
                    result.append(self._advance())
            # Check for exponent
            if self._peek().lower() == 'e':
                result.append(self._advance())
                if self._peek() in '+-':
                    result.append(self._advance())
                while self._is_digit(self._peek()):
                    result.append(self._advance())
            # Handle float suffix (f, F, l, L, i, I for imaginary)
            text = ''.join(result)
            has_f_suffix = False
            while self._peek().lower() in 'fli':
                ch = self._advance()
                if ch.lower() == 'f':
                    has_f_suffix = True
            return Token(TokenType.FLOAT_LITERAL, (float(text), has_f_suffix), loc)

        # Check for hex, octal, or binary prefix
        if self._peek() == '0':
            result.append(self._advance())
            if self._peek().lower() == 'x':
                # Hexadecimal
                result.append(self._advance())
                base = 16
                while self._is_hex_digit(self._peek()) or self._peek() == "'":
                    if self._peek() == "'":
                        self._advance()  # skip digit separator
                    else:
                        result.append(self._advance())
            elif self._peek().lower() == 'b':
                # Binary (C24)
                result.append(self._advance())
                base = 2
                while self._is_binary_digit(self._peek()) or self._peek() == "'":
                    if self._peek() == "'":
                        self._advance()
                    else:
                        result.append(self._advance())
            elif self._is_octal_digit(self._peek()):
                # Octal
                base = 8
                while self._is_octal_digit(self._peek()) or self._peek() == "'":
                    if self._peek() == "'":
                        self._advance()
                    else:
                        result.append(self._advance())
            # else: just 0

        else:
            # Decimal
            while self._is_digit(self._peek()) or self._peek() == "'":
                if self._peek() == "'":
                    self._advance()
                else:
                    result.append(self._advance())

        # Check for fractional part (only for decimal)
        # Accept "1." (no digits after dot) as a valid float literal,
        # but not "1.member" (dot followed by identifier start)
        # Special case: "5.f" and "5.F" are float literals (suffix), not member access
        if base == 10 and self._peek() == '.':
            next_after_dot = self._peek(1)
            # Check for float suffix: 5.f, 5.F, 5.l, 5.L (not followed by identifier part)
            is_float_suffix = (next_after_dot.lower() in ('f', 'l') and
                               not self._is_identifier_part(self._peek(2)))
            if self._is_digit(next_after_dot) or not self._is_identifier_start(next_after_dot) or is_float_suffix:
                is_float = True
                result.append(self._advance())  # .
                while self._is_digit(self._peek()) or self._peek() == "'":
                    if self._peek() == "'":
                        self._advance()
                    else:
                        result.append(self._advance())

        # Check for exponent (decimal or hex float)
        if base == 10 and self._peek().lower() == 'e':
            is_float = True
            result.append(self._advance())
            if self._peek() in '+-':
                result.append(self._advance())
            while self._is_digit(self._peek()):
                result.append(self._advance())
        elif base == 16 and self._peek().lower() == 'p':
            # Hex float exponent
            is_float = True
            result.append(self._advance())
            if self._peek() in '+-':
                result.append(self._advance())
            while self._is_digit(self._peek()):
                result.append(self._advance())

        # Read suffix (including 'i' for GCC imaginary extension - ignored)
        suffix = []
        while self._peek().lower() in 'uljfi':
            suffix.append(self._advance())

        text = ''.join(result)
        suffix_str = ''.join(suffix).lower()

        if is_float or 'f' in suffix_str:
            # Float literal
            try:
                if base == 16:
                    value = float.fromhex(text)
                else:
                    value = float(text)
            except ValueError:
                raise LexerError(f"Invalid float literal: {text}", loc)
            has_f_suffix = 'f' in suffix_str
            return Token(TokenType.FLOAT_LITERAL, (value, has_f_suffix), loc)
        else:
            # Integer literal - store (value, suffix) tuple
            try:
                value = int(text, base)
            except ValueError:
                raise LexerError(f"Invalid integer literal: {text}", loc)
            return Token(TokenType.INT_LITERAL, (value, suffix_str, base), loc)

    def _read_char_literal(self, wide: bool = False) -> Token:
        """Read a character literal. wide=True for L'x' wide character literals."""
        loc = self._location()
        self._advance()  # opening '

        if self._peek() == '\\':
            value = self._read_escape_sequence()
        elif self._peek() == "'":
            raise LexerError("Empty character literal", loc)
        elif self._peek() == '\0' or self._peek() == '\n':
            raise LexerError("Unterminated character literal", loc)
        else:
            value = ord(self._advance())

        if self._peek() != "'":
            raise LexerError("Multi-character character literal", loc)
        self._advance()  # closing '

        tok_type = TokenType.WIDE_CHAR_LITERAL if wide else TokenType.CHAR_LITERAL
        return Token(tok_type, value, loc)

    def _read_string_literal(self, wide: bool = False) -> Token:
        """Read a string literal. wide=True for L"str" wide string literals."""
        loc = self._location()
        self._advance()  # opening "
        result = []

        while self._peek() != '"':
            if self._peek() == '\0' or self._peek() == '\n':
                raise LexerError("Unterminated string literal", loc)
            if self._peek() == '\\':
                result.append(chr(self._read_escape_sequence()))
            else:
                result.append(self._advance())

        self._advance()  # closing "
        tok_type = TokenType.WIDE_STRING_LITERAL if wide else TokenType.STRING_LITERAL
        return Token(tok_type, ''.join(result), loc)

    def _read_escape_sequence(self) -> int:
        """Read an escape sequence and return its value."""
        loc = self._location()
        self._advance()  # backslash

        ch = self._advance()
        if ch == 'n':
            return ord('\n')
        elif ch == 't':
            return ord('\t')
        elif ch == 'r':
            return ord('\r')
        elif ch in '01234567':
            # Octal escape sequence (1-3 digits)
            value = int(ch)
            count = 1
            while self._is_octal_digit(self._peek()) and count < 3:
                value = value * 8 + int(self._advance())
                count += 1
            return value & 0xFF
        elif ch == 'x':
            # Hex escape
            value = 0
            if not self._is_hex_digit(self._peek()):
                raise LexerError("Invalid hex escape sequence", loc)
            while self._is_hex_digit(self._peek()):
                digit = self._advance().lower()
                if digit.isdigit():
                    value = value * 16 + int(digit)
                else:
                    value = value * 16 + (ord(digit) - ord('a') + 10)
            return value & 0xFF
        elif ch == '\\':
            return ord('\\')
        elif ch == "'":
            return ord("'")
        elif ch == '"':
            return ord('"')
        elif ch == 'a':
            return ord('\a')
        elif ch == 'b':
            return ord('\b')
        elif ch == 'f':
            return ord('\f')
        elif ch == 'v':
            return ord('\v')
        elif ch == '?':
            return ord('?')
        else:
            raise LexerError(f"Unknown escape sequence: \\{ch}", loc)

    def _read_punctuator(self) -> Token:
        """Read a punctuator/operator."""
        loc = self._location()
        ch = self._advance()

        # Two/three character operators
        if ch == '[':
            return Token(TokenType.LBRACKET, '[', loc)
        elif ch == ']':
            return Token(TokenType.RBRACKET, ']', loc)
        elif ch == '(':
            return Token(TokenType.LPAREN, '(', loc)
        elif ch == ')':
            return Token(TokenType.RPAREN, ')', loc)
        elif ch == '{':
            return Token(TokenType.LBRACE, '{', loc)
        elif ch == '}':
            return Token(TokenType.RBRACE, '}', loc)
        elif ch == '.':
            if self._peek() == '.' and self._peek(1) == '.':
                self._advance()
                self._advance()
                return Token(TokenType.ELLIPSIS, '...', loc)
            if self._is_digit(self._peek()):
                # Float literal starting with dot (e.g., .5, .123)
                self.pos -= 1  # Back up to re-read the dot
                self.column -= 1
                return self._read_number()
            return Token(TokenType.DOT, '.', loc)
        elif ch == '-':
            if self._match('>'):
                return Token(TokenType.ARROW, '->', loc)
            elif self._match('-'):
                return Token(TokenType.DECREMENT, '--', loc)
            elif self._match('='):
                return Token(TokenType.SUB_ASSIGN, '-=', loc)
            return Token(TokenType.MINUS, '-', loc)
        elif ch == '+':
            if self._match('+'):
                return Token(TokenType.INCREMENT, '++', loc)
            elif self._match('='):
                return Token(TokenType.ADD_ASSIGN, '+=', loc)
            return Token(TokenType.PLUS, '+', loc)
        elif ch == '&':
            if self._match('&'):
                return Token(TokenType.AND, '&&', loc)
            elif self._match('='):
                return Token(TokenType.AND_ASSIGN, '&=', loc)
            return Token(TokenType.AMPERSAND, '&', loc)
        elif ch == '*':
            if self._match('='):
                return Token(TokenType.MUL_ASSIGN, '*=', loc)
            return Token(TokenType.STAR, '*', loc)
        elif ch == '~':
            return Token(TokenType.TILDE, '~', loc)
        elif ch == '!':
            if self._match('='):
                return Token(TokenType.NE, '!=', loc)
            return Token(TokenType.BANG, '!', loc)
        elif ch == '/':
            if self._match('='):
                return Token(TokenType.DIV_ASSIGN, '/=', loc)
            return Token(TokenType.SLASH, '/', loc)
        elif ch == '%':
            if self._match('='):
                return Token(TokenType.MOD_ASSIGN, '%=', loc)
            return Token(TokenType.PERCENT, '%', loc)
        elif ch == '<':
            if self._match('<'):
                if self._match('='):
                    return Token(TokenType.LSHIFT_ASSIGN, '<<=', loc)
                return Token(TokenType.LSHIFT, '<<', loc)
            elif self._match('='):
                return Token(TokenType.LE, '<=', loc)
            return Token(TokenType.LT, '<', loc)
        elif ch == '>':
            if self._match('>'):
                if self._match('='):
                    return Token(TokenType.RSHIFT_ASSIGN, '>>=', loc)
                return Token(TokenType.RSHIFT, '>>', loc)
            elif self._match('='):
                return Token(TokenType.GE, '>=', loc)
            return Token(TokenType.GT, '>', loc)
        elif ch == '=':
            if self._match('='):
                return Token(TokenType.EQ, '==', loc)
            return Token(TokenType.ASSIGN, '=', loc)
        elif ch == '^':
            if self._match('='):
                return Token(TokenType.XOR_ASSIGN, '^=', loc)
            return Token(TokenType.CARET, '^', loc)
        elif ch == '|':
            if self._match('|'):
                return Token(TokenType.OR, '||', loc)
            elif self._match('='):
                return Token(TokenType.OR_ASSIGN, '|=', loc)
            return Token(TokenType.PIPE, '|', loc)
        elif ch == '?':
            return Token(TokenType.QUESTION, '?', loc)
        elif ch == ':':
            if self._match(':'):
                return Token(TokenType.COLONCOLON, '::', loc)
            return Token(TokenType.COLON, ':', loc)
        elif ch == ';':
            return Token(TokenType.SEMICOLON, ';', loc)
        elif ch == ',':
            return Token(TokenType.COMMA, ',', loc)
        elif ch == '#':
            if self._match('#'):
                return Token(TokenType.HASHHASH, '##', loc)
            return Token(TokenType.HASH, '#', loc)
        else:
            raise LexerError(f"Unexpected character: {ch!r}", loc)

    def next_token(self) -> Token:
        """Get the next token."""
        self._skip_whitespace_and_comments()

        loc = self._location()

        if self._peek() == '\0':
            return Token(TokenType.EOF, None, loc)

        # Wide character/string literal (L'x' or L"str")
        if self._peek() == 'L' and self._peek(1) in ("'", '"'):
            self._advance()  # consume 'L'
            if self._peek() == "'":
                return self._read_char_literal(wide=True)
            else:
                return self._read_string_literal(wide=True)

        # u8"str", u"str", U"str" string prefixes (treat as regular strings on Z80)
        if self._peek() == 'u':
            if self._peek(1) == '8' and self._peek(2) == '"':
                self._advance()  # consume 'u'
                self._advance()  # consume '8'
                return self._read_string_literal()
            elif self._peek(1) == '"':
                self._advance()  # consume 'u'
                return self._read_string_literal()
            elif self._peek(1) == "'":
                self._advance()  # consume 'u'
                return self._read_char_literal()
        if self._peek() == 'U' and self._peek(1) in ('"', "'"):
            self._advance()  # consume 'U'
            if self._peek() == "'":
                return self._read_char_literal()
            else:
                return self._read_string_literal()

        # Identifier or keyword
        if self._is_identifier_start(self._peek()):
            name = self._read_identifier()
            if name in KEYWORDS:
                return Token(KEYWORDS[name], name, loc)
            return Token(TokenType.IDENTIFIER, name, loc)

        # Number literal
        if self._is_digit(self._peek()):
            return self._read_number()

        # Character literal
        if self._peek() == "'":
            return self._read_char_literal()

        # String literal
        if self._peek() == '"':
            return self._read_string_literal()

        # Punctuator
        return self._read_punctuator()

    def tokenize(self) -> Iterator[Token]:
        """Tokenize entire source, yielding tokens."""
        while True:
            token = self.next_token()
            yield token
            if token.type == TokenType.EOF:
                break

    def tokenize_all(self) -> list[Token]:
        """Tokenize entire source, returning list of tokens."""
        return list(self.tokenize())


def tokenize(source: str, filename: str = "<stdin>") -> list[Token]:
    """Convenience function to tokenize source code."""
    return Lexer(source, filename).tokenize_all()
