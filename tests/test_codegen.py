"""Tests for Z80 code generator."""

import pytest
from src.lexer import Lexer
from src.parser import Parser
from src.codegen import CodeGenerator, generate


def parse(source: str):
    """Parse source and return AST."""
    lexer = Lexer(source, "<test>")
    tokens = list(lexer.tokenize())
    p = Parser(tokens)
    return p.parse()


def gen(source: str) -> str:
    """Parse and generate code from source."""
    unit = parse(source)
    return generate(unit)


class TestFunctionGeneration:
    """Test function code generation."""

    def test_empty_function(self):
        """Empty function generates prologue/epilogue."""
        code = gen("void foo(void) {}")
        assert "PUBLIC\t_foo" in code
        assert "_foo:" in code
        assert "PUSH\tIX" in code
        assert "LD\tIX,0" in code
        assert "ADD\tIX,SP" in code
        assert "LD\tSP,IX" in code
        assert "POP\tIX" in code
        assert "RET" in code

    def test_function_with_return(self):
        """Function with return value."""
        code = gen("int main(void) { return 42; }")
        assert "LD\tHL,42" in code
        assert "JP\t@main_ret" in code

    def test_main_function(self):
        """Main function is properly generated."""
        code = gen("int main(void) { return 0; }")
        assert "PUBLIC\t_main" in code
        assert "_main:" in code


class TestExpressionGeneration:
    """Test expression code generation."""

    def test_integer_literal(self):
        """Integer literal loads into HL."""
        code = gen("int main(void) { return 123; }")
        assert "LD\tHL,123" in code

    def test_addition(self):
        """Addition uses ADD HL,DE."""
        code = gen("int main(void) { return 1 + 2; }")
        assert "ADD\tHL,DE" in code

    def test_subtraction(self):
        """Subtraction uses SBC HL,DE."""
        code = gen("int main(void) { return 5 - 3; }")
        assert "SBC\tHL,DE" in code

    def test_bitwise_and(self):
        """Bitwise AND."""
        code = gen("int main(void) { return 0xFF & 0x0F; }")
        assert "AND\tD" in code
        assert "AND\tE" in code

    def test_bitwise_or(self):
        """Bitwise OR."""
        code = gen("int main(void) { return 0xF0 | 0x0F; }")
        assert "OR\tD" in code
        assert "OR\tE" in code

    def test_comparison_equal(self):
        """Equality comparison."""
        code = gen("int main(void) { return 1 == 1; }")
        assert "SBC\tHL,DE" in code
        assert "JP\tZ" in code

    def test_comparison_not_equal(self):
        """Inequality comparison."""
        code = gen("int main(void) { return 1 != 2; }")
        assert "JP\tNZ" in code

    def test_multiplication_calls_runtime(self):
        """Multiplication calls runtime library."""
        code = gen("int main(void) { return 3 * 4; }")
        assert "CALL\t__mul16" in code

    def test_division_calls_runtime(self):
        """Division calls runtime library (signed for int)."""
        code = gen("int main(void) { return 10 / 2; }")
        assert "CALL\t__sdiv16" in code  # Signed division for int


class TestUnaryOperators:
    """Test unary operator generation."""

    def test_negation(self):
        """Unary negation."""
        code = gen("int main(void) { return -5; }")
        assert "SBC\tHL,DE" in code  # 0 - 5

    def test_logical_not(self):
        """Logical NOT."""
        code = gen("int main(void) { return !0; }")
        assert "OR\tL" in code  # Test if HL is zero

    def test_bitwise_not(self):
        """Bitwise NOT."""
        code = gen("int main(void) { return ~0xFF; }")
        assert "CPL" in code


class TestControlFlow:
    """Test control flow code generation."""

    def test_if_statement(self):
        """If statement generates conditional jump."""
        code = gen("int main(void) { if (1) return 1; return 0; }")
        assert "JP\tZ,@ENDIF" in code or "JP\tZ,@ELSE" in code

    def test_if_else_statement(self):
        """If-else generates both branches."""
        code = gen("int main(void) { if (1) return 1; else return 0; }")
        assert "@ELSE" in code
        assert "@ENDIF" in code

    def test_while_loop(self):
        """While loop generates loop structure."""
        code = gen("int main(void) { while (1) { } return 0; }")
        assert "@WHILE" in code
        assert "@ENDWHILE" in code
        assert "JP\t@WHILE" in code

    def test_for_loop(self):
        """For loop generates loop structure."""
        code = gen("int main(void) { for (;;) { break; } return 0; }")
        assert "@FOR" in code
        assert "@ENDFOR" in code

    def test_break_statement(self):
        """Break jumps to end of loop."""
        code = gen("int main(void) { while (1) { break; } return 0; }")
        assert "JP\t@ENDWHILE" in code

    def test_continue_statement(self):
        """Continue jumps to start of loop."""
        code = gen("int main(void) { while (1) { continue; } return 0; }")
        assert "JP\t@WHILE" in code


class TestLocalVariables:
    """Test local variable handling."""

    def test_local_variable_declaration(self):
        """Local variables use IX-relative addressing."""
        code = gen("int main(void) { int x = 5; return x; }")
        # Should store to IX-2 and load from IX-2
        assert "IX-2" in code or "IX+0" in code

    def test_local_variable_with_init(self):
        """Local variable initialization."""
        code = gen("int main(void) { int x = 42; return x; }")
        assert "LD\tHL,42" in code


class TestFunctionCalls:
    """Test function call generation."""

    def test_function_call(self):
        """Function call generates CALL instruction."""
        code = gen("""
            void foo(void);
            int main(void) { foo(); return 0; }
        """)
        assert "CALL\t_foo" in code

    def test_function_call_with_arg(self):
        """Function call with argument pushes arg."""
        code = gen("""
            void foo(int x);
            int main(void) { foo(42); return 0; }
        """)
        assert "LD\tHL,42" in code
        assert "PUSH\tHL" in code
        assert "CALL\t_foo" in code


class TestStringLiterals:
    """Test string literal handling."""

    def test_string_literal(self):
        """String literal creates data segment entry."""
        code = gen('int main(void) { char *s = "hello"; return 0; }')
        assert "DSEG" in code
        assert "@STR" in code
        assert "'hello',0" in code


class TestLogicalOperators:
    """Test short-circuit logical operators."""

    def test_logical_and(self):
        """Logical AND short-circuits."""
        code = gen("int main(void) { return 1 && 2; }")
        assert "@AND_F" in code  # False label
        assert "@AND_E" in code  # End label

    def test_logical_or(self):
        """Logical OR short-circuits."""
        code = gen("int main(void) { return 0 || 1; }")
        assert "@OR_T" in code  # True label
        assert "@OR_E" in code  # End label


class TestTernaryOperator:
    """Test ternary conditional operator."""

    def test_ternary(self):
        """Ternary generates conditional branches."""
        code = gen("int main(void) { return 1 ? 10 : 20; }")
        assert "@TERN_E" in code
        assert "@TERN_END" in code


class TestSegments:
    """Test segment directives."""

    def test_cseg_dseg(self):
        """Code and data segments are properly declared."""
        code = gen('int main(void) { char *s = "test"; return 0; }')
        assert "CSEG" in code
        assert "DSEG" in code

    def test_z80_directive(self):
        """Z80 directive is present."""
        code = gen("int main(void) { return 0; }")
        assert ".Z80" in code

    def test_end_directive(self):
        """END directive is present."""
        code = gen("int main(void) { return 0; }")
        assert "\tEND" in code


class TestExternDeclarations:
    """Test external declarations."""

    def test_function_declaration_extrn(self):
        """Function declaration without body generates EXTRN."""
        code = gen("void foo(void);")
        assert "EXTRN\t_foo" in code
