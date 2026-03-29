"""Token types for C24 lexer."""

from enum import Enum, auto
from dataclasses import dataclass
from typing import Any


class TokenType(Enum):
    # End of file
    EOF = auto()

    # Literals
    INT_LITERAL = auto()
    FLOAT_LITERAL = auto()
    CHAR_LITERAL = auto()
    STRING_LITERAL = auto()
    WIDE_STRING_LITERAL = auto()
    WIDE_CHAR_LITERAL = auto()

    # Identifier
    IDENTIFIER = auto()

    # Keywords (C24 Section 6.4.2)
    AUTO = auto()
    BREAK = auto()
    CASE = auto()
    CHAR = auto()
    CONST = auto()
    CONSTEXPR = auto()
    CONTINUE = auto()
    DEFAULT = auto()
    DO = auto()
    DOUBLE = auto()
    ELSE = auto()
    ENUM = auto()
    EXTERN = auto()
    FALSE = auto()
    FLOAT = auto()
    FOR = auto()
    GOTO = auto()
    IF = auto()
    INLINE = auto()
    INT = auto()
    LONG = auto()
    NULLPTR = auto()
    REGISTER = auto()
    RESTRICT = auto()
    RETURN = auto()
    SHORT = auto()
    SIGNED = auto()
    SIZEOF = auto()
    STATIC = auto()
    STATIC_ASSERT = auto()
    STRUCT = auto()
    SWITCH = auto()
    THREAD_LOCAL = auto()
    TRUE = auto()
    TYPEDEF = auto()
    TYPEOF = auto()
    TYPEOF_UNQUAL = auto()
    UNION = auto()
    UNSIGNED = auto()
    VOID = auto()
    VOLATILE = auto()
    WHILE = auto()
    ALIGNAS = auto()
    ALIGNOF = auto()
    ATOMIC = auto()
    BOOL = auto()
    COMPLEX = auto()
    DECIMAL128 = auto()
    DECIMAL32 = auto()
    DECIMAL64 = auto()
    GENERIC = auto()
    IMAGINARY = auto()
    NORETURN = auto()
    BITINT = auto()

    # Punctuators (C24 Section 6.4.7)
    LBRACKET = auto()      # [
    RBRACKET = auto()      # ]
    LPAREN = auto()        # (
    RPAREN = auto()        # )
    LBRACE = auto()        # {
    RBRACE = auto()        # }
    DOT = auto()           # .
    ARROW = auto()         # ->
    INCREMENT = auto()     # ++
    DECREMENT = auto()     # --
    AMPERSAND = auto()     # &
    STAR = auto()          # *
    PLUS = auto()          # +
    MINUS = auto()         # -
    TILDE = auto()         # ~
    BANG = auto()          # !
    SLASH = auto()         # /
    PERCENT = auto()       # %
    LSHIFT = auto()        # <<
    RSHIFT = auto()        # >>
    LT = auto()            # <
    GT = auto()            # >
    LE = auto()            # <=
    GE = auto()            # >=
    EQ = auto()            # ==
    NE = auto()            # !=
    CARET = auto()         # ^
    PIPE = auto()          # |
    AND = auto()           # &&
    OR = auto()            # ||
    QUESTION = auto()      # ?
    COLON = auto()         # :
    SEMICOLON = auto()     # ;
    ELLIPSIS = auto()      # ...
    ASSIGN = auto()        # =
    MUL_ASSIGN = auto()    # *=
    DIV_ASSIGN = auto()    # /=
    MOD_ASSIGN = auto()    # %=
    ADD_ASSIGN = auto()    # +=
    SUB_ASSIGN = auto()    # -=
    LSHIFT_ASSIGN = auto() # <<=
    RSHIFT_ASSIGN = auto() # >>=
    AND_ASSIGN = auto()    # &=
    XOR_ASSIGN = auto()    # ^=
    OR_ASSIGN = auto()     # |=
    COMMA = auto()         # ,
    HASH = auto()          # #
    HASHHASH = auto()      # ##

    # Attribute syntax (C24)
    COLONCOLON = auto()    # ::


# Map keywords to token types
KEYWORDS = {
    'auto': TokenType.AUTO,
    'break': TokenType.BREAK,
    'case': TokenType.CASE,
    'char': TokenType.CHAR,
    'const': TokenType.CONST,
    'constexpr': TokenType.CONSTEXPR,
    'continue': TokenType.CONTINUE,
    'default': TokenType.DEFAULT,
    'do': TokenType.DO,
    'double': TokenType.DOUBLE,
    'else': TokenType.ELSE,
    'enum': TokenType.ENUM,
    'extern': TokenType.EXTERN,
    # 'false' is NOT a C keyword - it's a macro in <stdbool.h>
    'float': TokenType.FLOAT,
    'for': TokenType.FOR,
    'goto': TokenType.GOTO,
    'if': TokenType.IF,
    'inline': TokenType.INLINE,
    'int': TokenType.INT,
    'long': TokenType.LONG,
    'nullptr': TokenType.NULLPTR,
    'register': TokenType.REGISTER,
    'restrict': TokenType.RESTRICT,
    'return': TokenType.RETURN,
    'short': TokenType.SHORT,
    'signed': TokenType.SIGNED,
    'sizeof': TokenType.SIZEOF,
    'static': TokenType.STATIC,
    'static_assert': TokenType.STATIC_ASSERT,
    'struct': TokenType.STRUCT,
    'switch': TokenType.SWITCH,
    'thread_local': TokenType.THREAD_LOCAL,
    # 'true' is NOT a C keyword - it's a macro in <stdbool.h>
    'typedef': TokenType.TYPEDEF,
    'typeof': TokenType.TYPEOF,
    'typeof_unqual': TokenType.TYPEOF_UNQUAL,
    'union': TokenType.UNION,
    'unsigned': TokenType.UNSIGNED,
    'void': TokenType.VOID,
    'volatile': TokenType.VOLATILE,
    'while': TokenType.WHILE,
    'alignas': TokenType.ALIGNAS,
    '_Alignas': TokenType.ALIGNAS,
    'alignof': TokenType.ALIGNOF,
    '_Alignof': TokenType.ALIGNOF,
    '_Atomic': TokenType.ATOMIC,
    'atomic': TokenType.ATOMIC,
    'bool': TokenType.BOOL,
    '_Bool': TokenType.BOOL,
    '_Complex': TokenType.COMPLEX,
    '_Decimal128': TokenType.DECIMAL128,
    '_Decimal32': TokenType.DECIMAL32,
    '_Decimal64': TokenType.DECIMAL64,
    '_Generic': TokenType.GENERIC,
    '_Imaginary': TokenType.IMAGINARY,
    '_Noreturn': TokenType.NORETURN,
    '_BitInt': TokenType.BITINT,
    '_Static_assert': TokenType.STATIC_ASSERT,
    '_Thread_local': TokenType.THREAD_LOCAL,
}


@dataclass
class SourceLocation:
    """Location in source file."""
    filename: str
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.filename}:{self.line}:{self.column}"


@dataclass
class Token:
    """A lexical token."""
    type: TokenType
    value: Any
    location: SourceLocation

    def __str__(self) -> str:
        if self.value is not None:
            return f"{self.type.name}({self.value!r})"
        return self.type.name

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, {self.location})"
