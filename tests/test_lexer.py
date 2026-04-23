"""Tests for C24 lexer."""

import pytest
from uc_core.lexer import Lexer, LexerError, tokenize
from uc_core.tokens import TokenType


class TestBasicTokens:
    """Test basic token recognition."""

    def test_empty_input(self):
        tokens = tokenize("")
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.EOF

    def test_whitespace_only(self):
        tokens = tokenize("   \t\n\r  ")
        assert len(tokens) == 1
        assert tokens[0].type == TokenType.EOF

    def test_single_identifier(self):
        tokens = tokenize("foo")
        assert len(tokens) == 2
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "foo"

    def test_identifier_with_underscore(self):
        tokens = tokenize("_foo_bar_123")
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[0].value == "_foo_bar_123"


class TestKeywords:
    """Test keyword recognition."""

    def test_basic_keywords(self):
        keywords = [
            ("int", TokenType.INT),
            ("char", TokenType.CHAR),
            ("void", TokenType.VOID),
            ("return", TokenType.RETURN),
            ("if", TokenType.IF),
            ("else", TokenType.ELSE),
            ("while", TokenType.WHILE),
            ("for", TokenType.FOR),
            ("do", TokenType.DO),
            ("switch", TokenType.SWITCH),
            ("case", TokenType.CASE),
            ("default", TokenType.DEFAULT),
            ("break", TokenType.BREAK),
            ("continue", TokenType.CONTINUE),
            ("goto", TokenType.GOTO),
            ("struct", TokenType.STRUCT),
            ("union", TokenType.UNION),
            ("enum", TokenType.ENUM),
            ("typedef", TokenType.TYPEDEF),
            ("sizeof", TokenType.SIZEOF),
        ]
        for text, expected_type in keywords:
            tokens = tokenize(text)
            assert tokens[0].type == expected_type, f"Failed for {text}"

    def test_c24_keywords(self):
        """Test new C24 keywords."""
        keywords = [
            ("nullptr", TokenType.NULLPTR),
            ("constexpr", TokenType.CONSTEXPR),
            ("typeof", TokenType.TYPEOF),
            ("typeof_unqual", TokenType.TYPEOF_UNQUAL),
            ("static_assert", TokenType.STATIC_ASSERT),
            ("thread_local", TokenType.THREAD_LOCAL),
            ("alignas", TokenType.ALIGNAS),
            ("alignof", TokenType.ALIGNOF),
            ("bool", TokenType.BOOL),
        ]
        for text, expected_type in keywords:
            tokens = tokenize(text)
            assert tokens[0].type == expected_type, f"Failed for {text}"

    def test_legacy_keywords(self):
        """Test legacy underscore keywords."""
        keywords = [
            ("_Bool", TokenType.BOOL),
            ("_Alignas", TokenType.ALIGNAS),
            ("_Alignof", TokenType.ALIGNOF),
            ("_Static_assert", TokenType.STATIC_ASSERT),
            ("_Thread_local", TokenType.THREAD_LOCAL),
            ("_Generic", TokenType.GENERIC),
            ("_Noreturn", TokenType.NORETURN),
            ("_Atomic", TokenType.ATOMIC),
        ]
        for text, expected_type in keywords:
            tokens = tokenize(text)
            assert tokens[0].type == expected_type, f"Failed for {text}"

    def test_keyword_vs_identifier(self):
        """Keywords are case-sensitive; variants are identifiers."""
        tokens = tokenize("INT Int iNt")
        assert tokens[0].type == TokenType.IDENTIFIER
        assert tokens[1].type == TokenType.IDENTIFIER
        assert tokens[2].type == TokenType.IDENTIFIER


class TestIntegerLiterals:
    """Test integer literal parsing."""

    def test_decimal(self):
        tokens = tokenize("0 1 42 123456")
        assert tokens[0].value[0] == 0
        assert tokens[1].value[0] == 1
        assert tokens[2].value[0] == 42
        assert tokens[3].value[0] == 123456

    def test_hexadecimal(self):
        tokens = tokenize("0x0 0x1a 0XFF 0xDEADBEEF")
        assert tokens[0].value[0] == 0x0
        assert tokens[1].value[0] == 0x1a
        assert tokens[2].value[0] == 0xFF
        assert tokens[3].value[0] == 0xDEADBEEF

    def test_octal(self):
        tokens = tokenize("00 07 0777 0123")
        assert tokens[0].value[0] == 0
        assert tokens[1].value[0] == 7
        assert tokens[2].value[0] == 0o777
        assert tokens[3].value[0] == 0o123

    def test_binary(self):
        """Test C24 binary literals."""
        tokens = tokenize("0b0 0b1 0b1010 0B11111111")
        assert tokens[0].value[0] == 0b0
        assert tokens[1].value[0] == 0b1
        assert tokens[2].value[0] == 0b1010
        assert tokens[3].value[0] == 0b11111111

    def test_digit_separators(self):
        """Test C24 digit separators."""
        tokens = tokenize("1'000'000 0xFF'FF 0b1010'1010")
        assert tokens[0].value[0] == 1000000
        assert tokens[1].value[0] == 0xFFFF
        assert tokens[2].value[0] == 0b10101010

    def test_suffixes(self):
        """Test integer suffixes (value is same, suffix recorded)."""
        tokens = tokenize("42u 42U 42l 42L 42ul 42UL 42ll 42LL")
        for tok in tokens[:-1]:  # exclude EOF
            assert tok.type == TokenType.INT_LITERAL


class TestFloatLiterals:
    """Test floating-point literal parsing."""

    def test_decimal_float(self):
        tokens = tokenize("3.14 0.5 .5 1. 1.0")
        # Float values are tuples: (value, has_f_suffix)
        assert tokens[0].value[0] == pytest.approx(3.14)
        assert tokens[1].value[0] == pytest.approx(0.5)
        assert tokens[2].value[0] == pytest.approx(0.5)   # .5
        assert tokens[3].value[0] == pytest.approx(1.0)   # 1.
        assert tokens[4].value[0] == pytest.approx(1.0)   # 1.0

    def test_exponent(self):
        tokens = tokenize("1e10 1E10 1e+10 1e-10 1.5e3")
        # Float values are tuples: (value, has_f_suffix)
        assert tokens[0].value[0] == pytest.approx(1e10)
        assert tokens[1].value[0] == pytest.approx(1e10)
        assert tokens[2].value[0] == pytest.approx(1e10)
        assert tokens[3].value[0] == pytest.approx(1e-10)
        assert tokens[4].value[0] == pytest.approx(1.5e3)

    def test_float_suffix(self):
        tokens = tokenize("3.14f 3.14F 3.14l 3.14L")
        for tok in tokens[:-1]:
            assert tok.type == TokenType.FLOAT_LITERAL


class TestCharLiterals:
    """Test character literal parsing."""

    def test_simple_char(self):
        tokens = tokenize("'a' 'Z' '0' ' '")
        assert tokens[0].value == ord('a')
        assert tokens[1].value == ord('Z')
        assert tokens[2].value == ord('0')
        assert tokens[3].value == ord(' ')

    def test_escape_sequences(self):
        tokens = tokenize(r"'\n' '\t' '\r' '\\' '\'' '\"' '\0'")
        assert tokens[0].value == ord('\n')
        assert tokens[1].value == ord('\t')
        assert tokens[2].value == ord('\r')
        assert tokens[3].value == ord('\\')
        assert tokens[4].value == ord("'")
        assert tokens[5].value == ord('"')
        assert tokens[6].value == 0

    def test_hex_escape(self):
        tokens = tokenize(r"'\x41' '\x00' '\xff'")
        assert tokens[0].value == 0x41
        assert tokens[1].value == 0x00
        assert tokens[2].value == 0xff

    def test_octal_escape(self):
        tokens = tokenize(r"'\0' '\07' '\077'")
        assert tokens[0].value == 0
        assert tokens[1].value == 7
        assert tokens[2].value == 0o77

    def test_empty_char_error(self):
        with pytest.raises(LexerError):
            tokenize("''")

    def test_unterminated_char(self):
        with pytest.raises(LexerError):
            tokenize("'a")


class TestStringLiterals:
    """Test string literal parsing."""

    def test_simple_string(self):
        tokens = tokenize('"hello" "world"')
        assert tokens[0].value == "hello"
        assert tokens[1].value == "world"

    def test_empty_string(self):
        tokens = tokenize('""')
        assert tokens[0].value == ""

    def test_escape_sequences(self):
        tokens = tokenize(r'"hello\nworld" "tab\there" "quote\"here"')
        assert tokens[0].value == "hello\nworld"
        assert tokens[1].value == "tab\there"
        assert tokens[2].value == 'quote"here'

    def test_unterminated_string(self):
        with pytest.raises(LexerError):
            tokenize('"hello')


class TestPunctuators:
    """Test punctuator/operator recognition."""

    def test_single_char_punctuators(self):
        punctuators = [
            ("[", TokenType.LBRACKET),
            ("]", TokenType.RBRACKET),
            ("(", TokenType.LPAREN),
            (")", TokenType.RPAREN),
            ("{", TokenType.LBRACE),
            ("}", TokenType.RBRACE),
            (".", TokenType.DOT),
            ("&", TokenType.AMPERSAND),
            ("*", TokenType.STAR),
            ("+", TokenType.PLUS),
            ("-", TokenType.MINUS),
            ("~", TokenType.TILDE),
            ("!", TokenType.BANG),
            ("/", TokenType.SLASH),
            ("%", TokenType.PERCENT),
            ("<", TokenType.LT),
            (">", TokenType.GT),
            ("^", TokenType.CARET),
            ("|", TokenType.PIPE),
            ("?", TokenType.QUESTION),
            (":", TokenType.COLON),
            (";", TokenType.SEMICOLON),
            ("=", TokenType.ASSIGN),
            (",", TokenType.COMMA),
            ("#", TokenType.HASH),
        ]
        for text, expected_type in punctuators:
            tokens = tokenize(text)
            assert tokens[0].type == expected_type, f"Failed for {text!r}"

    def test_multi_char_punctuators(self):
        punctuators = [
            ("->", TokenType.ARROW),
            ("++", TokenType.INCREMENT),
            ("--", TokenType.DECREMENT),
            ("<<", TokenType.LSHIFT),
            (">>", TokenType.RSHIFT),
            ("<=", TokenType.LE),
            (">=", TokenType.GE),
            ("==", TokenType.EQ),
            ("!=", TokenType.NE),
            ("&&", TokenType.AND),
            ("||", TokenType.OR),
            ("...", TokenType.ELLIPSIS),
            ("*=", TokenType.MUL_ASSIGN),
            ("/=", TokenType.DIV_ASSIGN),
            ("%=", TokenType.MOD_ASSIGN),
            ("+=", TokenType.ADD_ASSIGN),
            ("-=", TokenType.SUB_ASSIGN),
            ("<<=", TokenType.LSHIFT_ASSIGN),
            (">>=", TokenType.RSHIFT_ASSIGN),
            ("&=", TokenType.AND_ASSIGN),
            ("^=", TokenType.XOR_ASSIGN),
            ("|=", TokenType.OR_ASSIGN),
            ("##", TokenType.HASHHASH),
        ]
        for text, expected_type in punctuators:
            tokens = tokenize(text)
            assert tokens[0].type == expected_type, f"Failed for {text!r}"


class TestComments:
    """Test comment handling."""

    def test_line_comment(self):
        tokens = tokenize("a // this is a comment\nb")
        assert len(tokens) == 3  # a, b, EOF
        assert tokens[0].value == "a"
        assert tokens[1].value == "b"

    def test_block_comment(self):
        tokens = tokenize("a /* comment */ b")
        assert len(tokens) == 3
        assert tokens[0].value == "a"
        assert tokens[1].value == "b"

    def test_multiline_block_comment(self):
        tokens = tokenize("a /* multi\nline\ncomment */ b")
        assert len(tokens) == 3
        assert tokens[0].value == "a"
        assert tokens[1].value == "b"

    def test_nested_block_comment_not_supported(self):
        # C doesn't support nested block comments
        tokens = tokenize("a /* outer /* inner */ b")
        assert tokens[1].value == "b"

    def test_unterminated_block_comment(self):
        with pytest.raises(LexerError):
            tokenize("/* unterminated")


class TestSourceLocation:
    """Test source location tracking."""

    def test_line_column(self):
        tokens = tokenize("a b\nc d")
        assert tokens[0].location.line == 1
        assert tokens[0].location.column == 1
        assert tokens[1].location.line == 1
        assert tokens[1].location.column == 3
        assert tokens[2].location.line == 2
        assert tokens[2].location.column == 1
        assert tokens[3].location.line == 2
        assert tokens[3].location.column == 3

    def test_filename(self):
        lexer = Lexer("x", "test.c")
        token = lexer.next_token()
        assert token.location.filename == "test.c"


class TestCompletePrograms:
    """Test tokenizing complete C programs."""

    def test_hello_world(self):
        source = '''
int main(void) {
    return 0;
}
'''
        tokens = tokenize(source)
        types = [t.type for t in tokens]
        assert TokenType.INT in types
        assert TokenType.IDENTIFIER in types
        assert TokenType.LPAREN in types
        assert TokenType.VOID in types
        assert TokenType.RPAREN in types
        assert TokenType.LBRACE in types
        assert TokenType.RETURN in types
        assert TokenType.INT_LITERAL in types
        assert TokenType.SEMICOLON in types
        assert TokenType.RBRACE in types
        assert TokenType.EOF in types

    def test_function_with_params(self):
        source = "int add(int a, int b) { return a + b; }"
        tokens = tokenize(source)
        values = [t.value for t in tokens if t.type == TokenType.IDENTIFIER]
        assert "add" in values
        assert "a" in values
        assert "b" in values

    def test_pointer_declaration(self):
        source = "int *ptr = &x;"
        tokens = tokenize(source)
        types = [t.type for t in tokens]
        assert TokenType.STAR in types
        assert TokenType.AMPERSAND in types

    def test_struct_declaration(self):
        source = """
struct Point {
    int x;
    int y;
};
"""
        tokens = tokenize(source)
        types = [t.type for t in tokens]
        assert TokenType.STRUCT in types
        assert types.count(TokenType.INT) == 2

    def test_for_loop(self):
        source = "for (int i = 0; i < 10; i++) { }"
        tokens = tokenize(source)
        types = [t.type for t in tokens]
        assert TokenType.FOR in types
        assert TokenType.INCREMENT in types
        assert TokenType.LT in types

    def test_complex_expression(self):
        source = "x = (a + b) * c / d - e % f;"
        tokens = tokenize(source)
        types = [t.type for t in tokens]
        assert TokenType.ASSIGN in types
        assert TokenType.PLUS in types
        assert TokenType.STAR in types
        assert TokenType.SLASH in types
        assert TokenType.MINUS in types
        assert TokenType.PERCENT in types


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
