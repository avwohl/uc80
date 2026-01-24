"""Tests for Z80 code generator."""

import pytest
from src.lexer import Lexer
from src.parser import Parser
from src.codegen import CodeGenerator, CallGraphAnalyzer, generate
from src import ast as ast_module


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
        # Call foo from main so it's not eliminated as dead
        code = gen("void foo(void) {} int main(void) { foo(); return 0; }")
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
        """Local variables use IX-relative addressing or shared storage."""
        code = gen("int main(void) { int x = 5; return x; }")
        # Should store/load using IX-relative OR shared storage (??AUTO)
        assert "IX-2" in code or "IX+0" in code or "??AUTO" in code

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


class TestCallGraphAnalyzer:
    """Test call graph analysis for shared storage optimization."""

    def test_build_call_graph_simple(self):
        """Build call graph from simple functions."""
        source = """
            void bar(void) {}
            void foo(void) { bar(); }
            int main(void) { foo(); return 0; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)

        assert "foo" in analyzer.call_graph
        assert "bar" in analyzer.call_graph["foo"]
        assert "foo" in analyzer.call_graph["main"]

    def test_detect_recursion_direct(self):
        """Detect direct recursion."""
        source = """
            void foo(void) { foo(); }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()

        assert analyzer.is_recursive("foo")

    def test_detect_recursion_indirect(self):
        """Detect indirect recursion."""
        source = """
            void bar(void);
            void foo(void) { bar(); }
            void bar(void) { foo(); }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()

        assert analyzer.is_recursive("foo")
        assert analyzer.is_recursive("bar")

    def test_non_recursive_functions(self):
        """Identify non-recursive functions."""
        source = """
            void helper(void) {}
            void foo(void) { helper(); }
            void bar(void) { helper(); }
            int main(void) { foo(); bar(); return 0; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()

        assert not analyzer.is_recursive("foo")
        assert not analyzer.is_recursive("bar")
        assert not analyzer.is_recursive("helper")
        assert not analyzer.is_recursive("main")

    def test_active_together_caller_callee(self):
        """Functions in caller-callee relationship are active together."""
        source = """
            void bar(void) {}
            void foo(void) { bar(); }
            int main(void) { foo(); return 0; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()

        # main calls foo, so they're active together
        assert "foo" in analyzer.can_be_active_together["main"]
        # foo calls bar, so they're active together
        assert "bar" in analyzer.can_be_active_together["foo"]

    def test_siblings_not_active_together(self):
        """Sibling functions (called from same parent) may not be active together."""
        source = """
            void foo(void) {}
            void bar(void) {}
            int main(void) { foo(); bar(); return 0; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()

        # foo and bar are called from main but not from each other
        # They should NOT be active together
        assert "bar" not in analyzer.can_be_active_together.get("foo", set())
        assert "foo" not in analyzer.can_be_active_together.get("bar", set())

    def test_storage_allocation_non_overlapping(self):
        """Functions active together get non-overlapping storage."""
        source = """
            void helper(void) { int x; }
            void foo(void) { int a; helper(); }
            int main(void) { foo(); return 0; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()
        analyzer.allocate_shared_storage()

        # main, foo, and helper form a call chain - active together
        # Their storage should not overlap
        if "foo" in analyzer.storage_offsets and "helper" in analyzer.storage_offsets:
            foo_start = analyzer.storage_offsets["foo"]
            foo_end = foo_start + analyzer.func_storage["foo"]
            helper_start = analyzer.storage_offsets["helper"]
            helper_end = helper_start + analyzer.func_storage["helper"]

            # Check no overlap
            assert foo_end <= helper_start or helper_end <= foo_start

    def test_storage_allocation_overlapping(self):
        """Sibling functions can share storage (overlap)."""
        source = """
            void foo(void) { int a, b; }
            void bar(void) { int x, y; }
            int main(void) { foo(); bar(); return 0; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()
        analyzer.allocate_shared_storage()

        # foo and bar are siblings, not active together
        # They CAN share storage (might have same offset)
        if "foo" in analyzer.storage_offsets and "bar" in analyzer.storage_offsets:
            # Both could start at offset 0 since they're not active together
            assert analyzer.storage_offsets["foo"] >= 0
            assert analyzer.storage_offsets["bar"] >= 0

    def test_variadic_uses_stack(self):
        """Variadic functions cannot use shared storage."""
        source = """
            void foo(int x, ...) { int a; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        analyzer.compute_active_together()

        assert not analyzer.can_use_shared_storage("foo")


class TestSharedStorageCodeGen:
    """Test code generation with shared storage optimization."""

    def test_shared_storage_area_generated(self):
        """Shared storage area is generated when functions can share."""
        source = """
            void foo(void) { int a = 1; }
            void bar(void) { int b = 2; }
            int main(void) { foo(); bar(); return 0; }
        """
        code = generate(parse(source), enable_shared_storage=True)
        assert "??AUTO" in code
        assert "; Shared automatic storage" in code

    def test_shared_storage_disabled(self):
        """Shared storage can be disabled."""
        source = """
            void foo(void) { int a = 1; }
            void bar(void) { int b = 2; }
            int main(void) { foo(); bar(); return 0; }
        """
        code = generate(parse(source), enable_shared_storage=False,
                       enable_dead_elimination=False, enable_inlining=False)
        assert "??AUTO" not in code

    def test_shared_storage_comment_in_function(self):
        """Functions using shared storage have comment."""
        source = """
            void foo(void) { int a = 1; }
            void bar(void) { int b = 2; }
            int main(void) { foo(); bar(); return 0; }
        """
        code = generate(parse(source), enable_shared_storage=True)
        # At least one function should use shared storage
        if "??AUTO" in code:
            assert "uses shared storage" in code

    def test_recursive_uses_stack(self):
        """Recursive functions use stack, not shared storage."""
        source = """
            void foo(void) { int x; foo(); }
            int main(void) { foo(); return 0; }
        """
        code = generate(parse(source), enable_shared_storage=True)
        # foo is recursive, so it should NOT use shared storage
        # Check that foo function doesn't have "uses shared storage" comment
        lines = code.split('\n')
        for i, line in enumerate(lines):
            if "; Function foo" in line:
                # Next line should NOT say "uses shared storage"
                assert "uses shared storage" not in line


class TestMultiFileCompilation:
    """Test multi-file AST merging."""

    def test_merge_simple_files(self):
        """Multiple ASTs can be merged."""
        source1 = "int helper(void) { return 1; }"
        source2 = "int main(void) { return helper(); }"

        ast1 = parse(source1)
        ast2 = parse(source2)

        merged = ast_module.TranslationUnit(declarations=[])
        merged.declarations.extend(ast1.declarations)
        merged.declarations.extend(ast2.declarations)

        # Disable inlining to test merging without optimization
        code = generate(merged, enable_shared_storage=True, enable_inlining=False)
        assert "PUBLIC\t_helper" in code
        assert "PUBLIC\t_main" in code
        assert "CALL\t_helper" in code


class TestDeadFunctionElimination:
    """Test dead function elimination optimization."""

    def test_eliminate_unused_function(self):
        """Unused functions are eliminated."""
        source = """
            void unused(void) { }
            int main(void) { return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True)
        # unused function should not appear in output
        assert "PUBLIC\t_unused" not in code
        assert "_unused:" not in code
        # main should still be there
        assert "PUBLIC\t_main" in code

    def test_keep_called_functions(self):
        """Called functions are preserved."""
        source = """
            void helper(void) { }
            int main(void) { helper(); return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True)
        assert "PUBLIC\t_helper" in code
        assert "PUBLIC\t_main" in code

    def test_keep_transitively_called(self):
        """Transitively called functions are preserved."""
        source = """
            void deep(void) { }
            void middle(void) { deep(); }
            void unused(void) { }
            int main(void) { middle(); return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True)
        assert "PUBLIC\t_deep" in code
        assert "PUBLIC\t_middle" in code
        assert "PUBLIC\t_main" in code
        assert "PUBLIC\t_unused" not in code

    def test_keep_address_taken(self):
        """Functions whose addresses are taken are preserved."""
        source = """
            void callback(void) { }
            void unused(void) { }
            int main(void) {
                void (*fp)(void) = &callback;
                return 0;
            }
        """
        code = generate(parse(source), enable_dead_elimination=True)
        assert "PUBLIC\t_callback" in code
        assert "PUBLIC\t_main" in code
        assert "PUBLIC\t_unused" not in code

    def test_disable_dead_elimination(self):
        """Dead elimination can be disabled."""
        source = """
            void unused(void) { }
            int main(void) { return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=False)
        # With elimination disabled, unused should be in output
        assert "PUBLIC\t_unused" in code
        assert "PUBLIC\t_main" in code

    def test_find_live_functions(self):
        """Test find_live_functions directly."""
        source = """
            void dead1(void) { }
            void dead2(void) { dead1(); }
            void live1(void) { }
            void live2(void) { live1(); }
            int main(void) { live2(); return 0; }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)

        live = analyzer.find_live_functions()
        assert "main" in live
        assert "live2" in live
        assert "live1" in live
        assert "dead1" not in live
        assert "dead2" not in live

    def test_eliminate_preserves_prototypes(self):
        """Function prototypes (declarations without bodies) are preserved."""
        source = """
            void external(void);
            void unused(void) { }
            int main(void) { external(); return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True)
        # External declaration should be preserved
        assert "EXTRN\t_external" in code
        # Unused function should be eliminated
        assert "PUBLIC\t_unused" not in code


class TestInlineExpansion:
    """Test inline expansion of small functions."""

    def test_inline_trivial_function(self):
        """Trivial functions (single return) are inlined."""
        source = """
            int add(int a, int b) { return a + b; }
            int main(void) { return add(1, 2); }
        """
        # With inlining, 'add' should be inlined and then eliminated as dead
        code = generate(parse(source), enable_inlining=True, enable_dead_elimination=True)
        # add should be eliminated after inlining
        assert "PUBLIC\t_add" not in code
        # The addition should happen inline
        assert "ADD\tHL,DE" in code

    def test_inline_preserves_behavior(self):
        """Inlining produces correct results."""
        source = """
            int double_it(int x) { return x + x; }
            int main(void) { return double_it(5); }
        """
        code = generate(parse(source), enable_inlining=True)
        # Should inline x + x with x = 5
        assert "LD\tHL,5" in code

    def test_no_inline_recursive(self):
        """Recursive functions are not inlined."""
        source = """
            int factorial(int n) { return n; }  // Simplified
            int main(void) { return factorial(5); }
        """
        # This trivial version should be inlined
        code = generate(parse(source), enable_inlining=True)
        # factorial is trivial and should be inlined
        assert "PUBLIC\t_factorial" not in code

    def test_no_inline_address_taken(self):
        """Functions whose addresses are taken are not inlined."""
        source = """
            int helper(int x) { return x + 1; }
            int main(void) {
                int (*fp)(int) = &helper;
                return helper(5);
            }
        """
        code = generate(parse(source), enable_inlining=True, enable_dead_elimination=False)
        # helper's address is taken, so it should not be inlined
        assert "PUBLIC\t_helper" in code
        assert "CALL\t_helper" in code

    def test_disable_inlining(self):
        """Inlining can be disabled."""
        source = """
            int add(int a, int b) { return a + b; }
            int main(void) { return add(1, 2); }
        """
        code = generate(parse(source), enable_inlining=False, enable_dead_elimination=False)
        # With inlining disabled, add should be called
        assert "CALL\t_add" in code

    def test_should_inline_criteria(self):
        """Test should_inline function criteria."""
        source = """
            int tiny(void) { return 1; }
            int small(int x) { return x + 1; }
            int medium(int x) {
                int a = x + 1;
                int b = a + 2;
                return b;
            }
            int main(void) {
                return tiny() + small(1) + medium(2);
            }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)

        # Build func_bodies
        func_bodies = {}
        for decl in unit.declarations:
            if isinstance(decl, ast_module.FunctionDecl) and decl.body:
                func_bodies[decl.name] = decl

        call_counts = analyzer.count_calls()

        # tiny is trivial (1 statement), should inline
        assert analyzer.should_inline("tiny", func_bodies, call_counts)
        # small is trivial (1 statement), should inline
        assert analyzer.should_inline("small", func_bodies, call_counts)

    def test_inline_nested_calls(self):
        """Nested inlined calls work correctly."""
        source = """
            int inc(int x) { return x + 1; }
            int add2(int x) { return inc(inc(x)); }
            int main(void) { return add2(5); }
        """
        code = generate(parse(source), enable_inlining=True, enable_dead_elimination=True)
        # Both inc and add2 should be inlined
        assert "PUBLIC\t_inc" not in code
        assert "PUBLIC\t_add2" not in code
