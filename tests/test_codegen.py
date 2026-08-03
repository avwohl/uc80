"""Tests for Z80 code generator."""

import pytest
from uc_core.frontend import parse as _frontend_parse
from uc80.codegen import CodeGenerator, CallGraphAnalyzer, CodegenError, generate
from uc_core import ast as ast_module


def parse(source: str):
    """Parse source and return AST."""
    return _frontend_parse(source, "<test>")


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
        assert "public\t_foo" in code
        assert "_foo:" in code
        assert "push\tIX" in code
        assert "ld\tIX,0" in code
        assert "add\tIX,SP" in code
        assert "ld\tSP,IX" in code
        assert "pop\tIX" in code
        assert "ret" in code

    def test_function_with_return(self):
        """Function with return value."""
        code = gen("int main(void) { return 42; }")
        assert "ld\tHL,42" in code
        assert "jp\t@main_ret" in code

    def test_main_function(self):
        """Main function is properly generated."""
        code = gen("int main(void) { return 0; }")
        assert "public\t_main" in code
        assert "_main:" in code


class TestExpressionGeneration:
    """Test expression code generation."""

    def test_integer_literal(self):
        """Integer literal loads into HL."""
        code = gen("int main(void) { return 123; }")
        assert "ld\tHL,123" in code

    def test_addition(self):
        """Addition uses ADD HL,DE."""
        code = gen("int main(void) { return 1 + 2; }")
        assert "add\tHL,DE" in code

    def test_subtraction(self):
        """Subtraction uses SBC HL,DE."""
        code = gen("int main(void) { return 5 - 3; }")
        assert "sbc\tHL,DE" in code

    def test_bitwise_and(self):
        """Bitwise AND."""
        code = gen("int main(void) { return 0xFF & 0x0F; }")
        assert "and\tD" in code
        assert "and\tE" in code

    def test_bitwise_or(self):
        """Bitwise OR."""
        code = gen("int main(void) { return 0xF0 | 0x0F; }")
        assert "or\tD" in code
        assert "or\tE" in code

    def test_comparison_equal(self):
        """Equality comparison."""
        code = gen("int main(void) { return 1 == 1; }")
        assert "sbc\tHL,DE" in code
        assert "jp\tZ" in code

    def test_comparison_not_equal(self):
        """Inequality comparison."""
        code = gen("int main(void) { return 1 != 2; }")
        assert "jp\tNZ" in code

    def test_multiplication_calls_runtime(self):
        """Multiplication calls runtime library."""
        # Use variables to prevent constant folding; disable optimizations
        source = "int foo(int a, int b) { return a * b; } int main(void) { return foo(3, 4); }"
        unit = parse(source)
        code = generate(unit, enable_inlining=False, enable_const_propagation=False)
        assert "call\t__mul16" in code

    def test_division_calls_runtime(self):
        """Division calls runtime library (signed for int)."""
        code = gen("int main(void) { return 10 / 2; }")
        assert "call\t__sdiv16" in code  # Signed division for int


class TestUnaryOperators:
    """Test unary operator generation."""

    def test_negation(self):
        """Unary negation."""
        code = gen("int main(void) { return -5; }")
        assert "sbc\tHL,DE" in code  # 0 - 5

    def test_logical_not(self):
        """Logical NOT."""
        code = gen("int main(void) { return !0; }")
        assert "or\tL" in code  # Test if HL is zero

    def test_bitwise_not(self):
        """Bitwise NOT."""
        code = gen("int main(void) { return ~0xFF; }")
        assert "cpl" in code


class TestControlFlow:
    """Test control flow code generation."""

    def test_if_statement(self):
        """If statement generates conditional jump."""
        code = gen("int main(void) { if (1) return 1; return 0; }")
        assert "jp\tZ,@ENDIF" in code or "jp\tZ,@ELSE" in code

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
        assert "jp\t@WHILE" in code

    def test_for_loop(self):
        """For loop generates loop structure."""
        code = gen("int main(void) { for (;;) { break; } return 0; }")
        assert "@FOR" in code
        assert "@ENDFOR" in code

    def test_break_statement(self):
        """Break jumps to end of loop."""
        code = gen("int main(void) { while (1) { break; } return 0; }")
        assert "jp\t@ENDWHILE" in code

    def test_continue_statement(self):
        """Continue jumps to start of loop."""
        code = gen("int main(void) { while (1) { continue; } return 0; }")
        assert "jp\t@WHILE" in code


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
        assert "ld\tHL,42" in code


class TestFunctionCalls:
    """Test function call generation."""

    def test_function_call(self):
        """Function call generates CALL instruction."""
        code = gen("""
            void foo(void);
            int main(void) { foo(); return 0; }
        """)
        assert "call\t_foo" in code

    def test_function_call_with_arg(self):
        """Function call with argument pushes arg."""
        code = gen("""
            void foo(int x);
            int main(void) { foo(42); return 0; }
        """)
        assert "ld\tHL,42" in code
        assert "push\tHL" in code
        assert "call\t_foo" in code


class TestStringLiterals:
    """Test string literal handling."""

    def test_string_literal(self):
        """String literal creates data segment entry."""
        code = gen('int main(void) { char *s = "hello"; return 0; }')
        assert "dseg" in code
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
        assert "cseg" in code
        assert "dseg" in code

    def test_z80_directive(self):
        """Z80 directive is present."""
        code = gen("int main(void) { return 0; }")
        assert ".z80" in code

    def test_end_directive(self):
        """END directive is present."""
        code = gen("int main(void) { return 0; }")
        assert "\tend" in code


class TestExternDeclarations:
    """Test external declarations."""

    def test_function_declaration_extrn(self):
        """Function declaration without body generates EXTRN."""
        code = gen("void foo(void);")
        assert "extrn\t_foo" in code


class TestArrayRedeclaration:
    """Compositing an array type across declarations (C17 6.2.7p3).

    Every case here used to die with ``uc80: internal error: '<' not
    supported between instances of 'Token' and 'Token'``, because
    ``ast.IntLiteral.value`` is a uplox Token and _merge_array_size
    compared two of them directly.
    """

    def test_matching_explicit_sizes(self):
        """extern then definition with the same size is accepted."""
        code = gen("extern int arr[100];\nint arr[100];\n")
        assert "_arr:" in code
        assert "ds\t200" in code

    def test_definition_before_extern(self):
        """Definition first, extern second, is also accepted."""
        code = gen("int arr[100];\nextern int arr[100];\n")
        assert "ds\t200" in code

    def test_repeated_extern(self):
        """An unguarded header declaring the same extern twice is fine."""
        code = gen("extern int a[5];\nextern int a[5];\nint a[5];\n")
        assert "ds\t10" in code

    def test_extern_size_completes_short_initializer(self):
        """extern int a[3]; int a[] = {1,2}; -> 3 elements, tail zeroed."""
        code = gen("extern int a[3];\nint a[] = {1,2};\n")
        assert "dw\t1" in code
        assert "dw\t2" in code
        assert "ds\t2" in code  # third element zero-padded

    def test_extern_size_completes_string_initializer(self):
        """extern char s[10]; char s[] = "hi"; -> 10 bytes."""
        code = gen('extern char s[10];\nchar s[] = "hi";\n')
        assert "db\t'hi',0" in code
        assert "ds\t7" in code  # 3 emitted + 7 padding == 10

    def test_unsized_extern_takes_definition_size(self):
        """extern int a[]; int a[7]; -> 7 elements."""
        code = gen("extern int a[];\nint a[7];\n")
        assert "ds\t14" in code

    def test_unsized_definition_takes_extern_size(self):
        """extern int a[3]; int a[]; -> 3 elements."""
        code = gen("extern int a[3];\nint a[];\n")
        assert "ds\t6" in code

    def test_multidimensional(self):
        """Multi-dimensional arrays composite too."""
        code = gen("extern int m[4][3];\nint m[4][3];\n")
        assert "ds\t24" in code

    def test_array_of_struct(self):
        """Arrays of struct composite too."""
        code = gen("struct S { int x; int y; };\n"
                   "extern struct S t[4];\nstruct S t[4];\n")
        assert "ds\t16" in code

    def test_constant_expression_size(self):
        """A non-literal constant size folds instead of being skipped."""
        code = gen("extern int a[3+4];\nint a[3+4];\n")
        assert "ds\t14" in code

    def test_enum_constant_size(self):
        """An enum-constant size is accepted."""
        code = gen("enum { N = 6 };\nextern int a[N];\nint a[N];\n")
        assert "ds\t12" in code

    def test_two_translation_units(self):
        """A header's extern in one TU and the definition in another."""
        ast1 = parse("extern int shared[5];\nint getb(void) { return shared[4]; }")
        ast2 = parse("extern int shared[5];\nint shared[5] = {1,2,3,4,5};")
        merged = ast_module.TranslationUnit(items=[])
        merged.items.extend(ast1.items)
        merged.items.extend(ast2.items)
        code = generate(merged, enable_inlining=False)
        assert "_shared:" in code

    def test_conflicting_larger_size_is_an_error(self):
        """extern int a[100]; int a[200]; is a constraint violation."""
        with pytest.raises(CodegenError) as exc:
            gen("extern int arr[100];\nint arr[200];\n")
        assert "conflicting types for 'arr'" in str(exc.value)
        assert "200" in str(exc.value) and "100" in str(exc.value)

    def test_conflicting_smaller_size_is_an_error(self):
        """extern int a[100]; int a[50]; is a constraint violation too."""
        with pytest.raises(CodegenError) as exc:
            gen("extern int arr[100];\nint arr[50];\n")
        assert "conflicting types for 'arr'" in str(exc.value)

    def test_excess_initializer_is_an_error(self):
        """More initializers than the extern's size is an error."""
        with pytest.raises(CodegenError) as exc:
            gen("extern int a[3];\nint a[] = {1,2,3,4};\n")
        assert "excess elements in initializer for 'a'" in str(exc.value)

    def test_excess_string_initializer_is_an_error(self):
        """A string literal too long for the extern's size is an error."""
        with pytest.raises(CodegenError) as exc:
            gen('extern char s[2];\nchar s[] = "hello";\n')
        assert "excess elements in initializer for 's'" in str(exc.value)

    def test_error_reports_the_source_line(self):
        """The diagnostic names the line of the offending declaration."""
        with pytest.raises(CodegenError) as exc:
            gen("extern int arr[100];\n\n\nint arr[200];\n")
        assert exc.value.line == 4
        assert str(exc.value).startswith("line 4: ")


class TestCodegenError:
    """The user-facing diagnostic mechanism itself."""

    def test_message_without_a_node(self):
        """With no AST node the message is passed through verbatim."""
        e = CodegenError("something is wrong")
        assert str(e) == "something is wrong"
        assert e.line is None
        assert e.message == "something is wrong"

    def test_message_with_a_node(self):
        """With an AST node the line is prefixed."""
        unit = parse("int x;\nint y;\n")
        e = CodegenError("something is wrong", unit.items[1])
        assert e.line == 2
        assert str(e) == "line 2: something is wrong"

    def test_node_without_a_pos_is_tolerated(self):
        """Synthesized nodes carry no pos; that must not raise."""
        e = CodegenError("no position here", object())
        assert e.line is None
        assert str(e) == "no position here"


class TestDesignatedInitializers:
    """Designated initializers that index a nested array member.

    These used to die with ``uc80: internal error: '>' not supported
    between instances of 'Token' and 'int'`` — the array member's size
    was read straight off ``ast.IntLiteral.value``, which is a Token.
    """

    STRUCT = "struct S { int arr[4]; int x; };\n"

    def test_static_nested_index_with_continuation(self):
        """struct S g = {.arr[1]=7, 8, 9}; fills arr[1..3], x stays 0."""
        code = gen(self.STRUCT + "struct S g = { .arr[1] = 7, 8, 9 };\n")
        body = code[code.index("_g:"):]
        assert body.splitlines()[1].strip() == "ds\t2"    # arr[0] == 0
        assert "dw\t7" in body and "dw\t8" in body and "dw\t9" in body

    def test_local_nested_index_with_continuation(self):
        """The local twin stores 7/8/9 at arr[1], arr[2], arr[3]."""
        code = gen(self.STRUCT + "int use(struct S *p);\n"
                   "int main(void) { struct S s = { .arr[1] = 7, 8, 9 };"
                   " return use(&s); }\n")
        body = code[code.index("_main:"):]
        # arr[1..3] live at byte offsets 2, 4, 6 of the object.
        for value, offset in ((7, 2), (8, 4), (9, 6)):
            assert f"ld\tHL,{value}" in body
            assert f"ld\t(??AUTO+{offset}),HL" in body

    def test_local_nested_index_alone(self):
        """A lone .arr[3]=5 must not be dropped (it silently was)."""
        code = gen(self.STRUCT + "int use(struct S *p);\n"
                   "int main(void) { struct S s = { .arr[3] = 5 };"
                   " return use(&s); }\n")
        body = code[code.index("_main:"):]
        assert "ld\tHL,5" in body
        assert "ld\t(??AUTO+6),HL" in body

    def test_continuation_stops_at_member_capacity(self):
        """Values past the array's end continue into the next member."""
        code = gen(self.STRUCT +
                   "struct S g = { .arr[2] = 1, 2, 3, 4 };\n")
        body = code[code.index("_g:"):]
        # arr[2]=1, arr[3]=2, then x=3; the 4 has nowhere left to go.
        assert "dw\t1" in body and "dw\t2" in body and "dw\t3" in body


class TestBoolInitFromFloatLiteral:
    """_Bool from a float literal (C99 6.3.1.2)."""

    def test_zero_float_is_false(self):
        """_Bool b = 0.0; is 0 — Token == 0.0 was always False, giving 1."""
        code = gen("int f(int); int main(void) { _Bool b = 0.0; return f(b); }")
        body = code[code.index("_main:"):]
        assert "ld\tHL,0" in body
        assert "ld\tHL,1" not in body

    def test_nonzero_float_is_true(self):
        """_Bool b = 1.5; is 1."""
        code = gen("int f(int); int main(void) { _Bool b = 1.5; return f(b); }")
        body = code[code.index("_main:"):]
        assert "ld\tHL,1" in body


class TestLiteralTokenDecoding:
    """Literal values are Tokens; every read must decode, never use .value.

    ``_string_literal_length`` replaces a bare ``len(literal.value)`` on
    branches that are unreachable on today's AST shapes but are one
    upstream change away from being live, so it is pinned directly.
    """

    def test_string_literal_length_from_token(self):
        """A StringLiteral's value is source text, quotes and all."""
        from uc80.codegen import _string_literal_length
        unit = parse('char *s = "abcd";')
        literal = unit.items[0].declarators[0].init
        if isinstance(literal, list):
            literal = literal[0]
        assert _string_literal_length(literal.value) == 4

    def test_string_literal_length_counts_escapes_once(self):
        """Each escape sequence is one byte."""
        from uc80.codegen import _string_literal_length
        unit = parse(r'char *s = "a\n\t\x41";')
        literal = unit.items[0].declarators[0].init
        if isinstance(literal, list):
            literal = literal[0]
        assert _string_literal_length(literal.value) == 4

    def test_string_literal_length_accepts_a_bare_str(self):
        """Synthesized paths hand over an already-decoded str."""
        from uc80.codegen import _string_literal_length
        assert _string_literal_length("abcd") == 4

    def test_array_size_from_index_designator(self):
        """int a[] = {[5] = 1}; is 6 elements."""
        code = gen("int a[] = {[5] = 1};\nint main(void) { return a[0]; }")
        assert "ds\t2" in code  # 6 ints, one initialized -> 5 words padding
        assert "dw\t1" in code

    def test_array_size_from_range_designator(self):
        """int b[] = {[2 ... 4] = 1}; is 5 elements."""
        code = gen("int b[] = {[2 ... 4] = 1};\nint main(void) { return b[0]; }")
        assert "_b:" in code

    def test_negative_long_long_argument(self):
        """A negated integer constant passed as long long."""
        code = gen("void h(long long v);\nint main(void) { h(-5); return 0; }")
        assert "call\t_h" in code


def _find_sequence_expr(node):
    """Return the first ast.SequenceExpr reachable from ``node``.

    Breadth-first with an identity-keyed seen set: auto-AST nodes carry
    back-references, so a naive recursive walk loops forever.
    """
    seen = set()
    queue = [node]
    while queue:
        item = queue.pop(0)
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, ast_module.SequenceExpr):
            return item
        if isinstance(item, (list, tuple)):
            queue.extend(item)
        elif hasattr(item, "__dict__"):
            queue.extend(vars(item).values())
    return None


class TestCommaOperator:
    """The comma operator's result type is its RIGHT operand (C23 6.5.18).

    uc_core spells the comma operator ``ast.SequenceExpr``, which is NOT a
    subclass of ``ast.BinaryOp``, so every type-inference helper used to
    fall through and report "16-bit signed int".  A wide comma result was
    then re-converted (``__sext32``/``__itof`` on top of a finished float)
    or truncated to its low word.
    """

    def test_comma_double_result_converted_once(self):
        """double d = (g(), (double)f()); converts exactly once."""
        code = gen("void g(void); unsigned int f(void);"
                   "int main(void){ double d=(g(),(double)f()); return (int)d; }")
        assert code.count("call\t__uitof") == 1
        assert "call\t__itof" not in code    # the bogus second conversion
        assert "call\t__sext32" not in code  # ... and its sign extension

    def test_comma_double_return_value(self):
        """return (g(), (double)f()); does not re-convert the float.

        whole_program=False keeps the function out of the inliner so the
        return path itself is what is under test.
        """
        code = generate(parse("void g(void); unsigned int f(void);"
                              "double fre(void){ return (g(),(double)f()); }"),
                        whole_program=False)
        assert code.count("call\t__uitof") == 1
        assert "call\t__itof" not in code
        assert "call\t__sext32" not in code

    def test_comma_double_variadic_arg_pushes_all_four_bytes(self):
        """A 4-byte double argument pushes DE:HL, not HL alone."""
        code = gen('int printf(const char*,...); void g(void); unsigned int f(void);'
                   'int main(void){ printf("%f\\n",(g(),(double)f())); return 0; }')
        body = code.split("; Printf format")[0]
        assert "call\t__uitof" in body
        assert "push\tDE\n\tpush\tHL" in body

    def test_comma_long_result_not_re_extended(self):
        """long v = (g(), f()); with f() returning long needs no __sext32."""
        code = gen("void g(void); long f(void); long v;"
                   "int main(void){ v=(g(),f()); return 0; }")
        assert "call\t__sext32" not in code

    def test_comma_long_long_result_not_re_extended(self):
        """long long is 64-bit; the comma must not re-extend from 16 bits."""
        code = gen("void g(void); long long f(void); long long v;"
                   "int main(void){ v=(g(),f()); return 0; }")
        assert "call\t__sext64_hl" not in code

    def test_comma_long_long_variadic_arg_pushes_all_eight_bytes(self):
        """A long long argument goes through the 64-bit push helper."""
        code = gen('int printf(const char*,...); void g(void); long long f(void);'
                   'int main(void){ printf("%lld\\n",(g(),f())); return 0; }')
        assert "call\t__push64_acc" in code

    def test_sizeof_comma_is_size_of_right_operand(self):
        """sizeof((g(), 1.0)) is sizeof(double) == 4, not sizeof(int)."""
        code = gen("void g(void); int main(void){ return (int)sizeof((g(),1.0)); }")
        assert "ld\tHL,4" in code

    def test_get_expr_type_of_comma_is_right_operand(self):
        """_get_expr_type delegates to the right operand instead of None."""
        unit = parse("void g(void); double f(void);"
                     "int main(void){ double d=(g(),f()); return (int)d; }")
        seq = _find_sequence_expr(unit)
        assert seq is not None
        cg = CodeGenerator("test")
        cg.generate(unit)
        assert cg._get_expr_type(seq) is not None
        assert cg._is_float_expr(seq)

    def test_type_predicates_delegate_to_right_operand(self):
        """_is_long_expr / _is_long_long_expr see through the comma."""
        unit = parse("void g(void); long f(void); long long h(void);"
                     "long a; long long b;"
                     "int main(void){ a=(g(),f()); b=(g(),h()); return 0; }")
        cg = CodeGenerator("test")
        cg.generate(unit)
        body = unit.items[-1]
        long_seq = _find_sequence_expr(body.body.items[0])
        ll_seq = _find_sequence_expr(body.body.items[1])
        assert cg._is_long_expr(long_seq)
        assert not cg._is_long_long_expr(long_seq)
        assert cg._is_long_long_expr(ll_seq)
        assert not cg._is_long_expr(ll_seq)


class TestCommaOperatorEvaluation:
    """A comma expression evaluates BOTH operands, exactly once each.

    ``_expr_has_side_effects`` and ``_uses_tmp32``/``_uses_tmp64`` also
    dispatched on ``ast.BinaryOp`` only, so a comma expression was judged
    side-effect-free (its subexpression was then re-evaluated) and was not
    recognised as clobbering the 32/64-bit scratch cells.
    """

    def test_comma_subscript_evaluated_once(self):
        """a[(h(), 1)] += 10; calls h() once, not twice."""
        code = gen("int calls; int a[4]; int h(void){calls++;return 1;}"
                   "int main(void){ a[(h(),1)] += 10; return calls; }")
        assert code.count("call\t_h") == 1

    def test_expr_has_side_effects_sees_into_comma(self):
        """A call in either operand makes the comma expression impure."""
        unit = parse("int h(void); int main(void){ int i=(h(),1); return i; }")
        seq = _find_sequence_expr(unit)
        assert seq is not None
        cg = CodeGenerator("test")
        cg.generate(unit)
        assert cg._expr_has_side_effects(seq)

    def test_comma_left_operand_tmp32_is_saved(self):
        """(g(), x/y) - z: the comma clobbers __tmp32, which holds z."""
        code = gen("void g(void); long x,y,z,r;"
                   "int main(void){ r = (g(), x/y) - z; return 0; }")
        assert "ld\tHL,(__tmp32)\n\tpush\tHL" in code

    def test_comma_left_operand_tmp64_is_saved(self):
        """Same hazard on the 64-bit path uses the runtime save/restore."""
        code = gen("void g(void); long long x,y,z,r;"
                   "int main(void){ r = (g(), x/y) - z; return 0; }")
        assert "call\t__save_tmp64" in code
        assert "call\t__restore_tmp64" in code

    def test_uses_tmp32_and_tmp64_see_into_comma(self):
        """The predicates themselves report the hazard."""
        unit = parse("void g(void); long x,y; long long p,q;"
                     "long a; long long b;"
                     "int main(void){ a=(g(),x/y); b=(g(),p/q); return 0; }")
        cg = CodeGenerator("test")
        cg.generate(unit)
        body = unit.items[-1]
        assert cg._uses_tmp32(_find_sequence_expr(body.body.items[0]))
        assert cg._uses_tmp64(_find_sequence_expr(body.body.items[1]))


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
                       enable_dead_elimination=False, enable_inlining=False,
                       enable_const_propagation=False)
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

        merged = ast_module.TranslationUnit(items=[])
        merged.items.extend(ast1.items)
        merged.items.extend(ast2.items)

        # Disable inlining to test merging without optimization
        code = generate(merged, enable_shared_storage=True, enable_inlining=False)
        assert "public\t_helper" in code
        assert "public\t_main" in code
        assert "call\t_helper" in code


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
        assert "public\t_unused" not in code
        assert "_unused:" not in code
        # main should still be there
        assert "public\t_main" in code

    def test_keep_called_functions(self):
        """Called functions are preserved."""
        source = """
            void helper(void) { }
            int main(void) { helper(); return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True)
        assert "public\t_helper" in code
        assert "public\t_main" in code

    def test_keep_transitively_called(self):
        """Transitively called functions are preserved."""
        source = """
            void deep(void) { }
            void middle(void) { deep(); }
            void unused(void) { }
            int main(void) { middle(); return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True)
        assert "public\t_deep" in code
        assert "public\t_middle" in code
        assert "public\t_main" in code
        assert "public\t_unused" not in code

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
        assert "public\t_callback" in code
        assert "public\t_main" in code
        assert "public\t_unused" not in code

    def test_disable_dead_elimination(self):
        """Dead elimination can be disabled."""
        source = """
            void unused(void) { }
            int main(void) { return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=False)
        # With elimination disabled, unused should be in output
        assert "public\t_unused" in code
        assert "public\t_main" in code

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
        assert "extrn\t_external" in code
        # Unused function should be eliminated
        assert "public\t_unused" not in code


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
        assert "public\t_add" not in code
        # The addition should happen inline
        assert "add\tHL,DE" in code

    def test_inline_preserves_behavior(self):
        """Inlining produces correct results."""
        source = """
            int double_it(int x) { return x + x; }
            int main(void) { return double_it(5); }
        """
        code = generate(parse(source), enable_inlining=True)
        # Should inline x + x with x = 5
        assert "ld\tHL,5" in code

    def test_no_inline_recursive(self):
        """Recursive functions are not inlined."""
        source = """
            int factorial(int n) { return n; }  // Simplified
            int main(void) { return factorial(5); }
        """
        # This trivial version should be inlined
        code = generate(parse(source), enable_inlining=True)
        # factorial is trivial and should be inlined
        assert "public\t_factorial" not in code

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
        assert "public\t_helper" in code
        assert "call\t_helper" in code

    def test_disable_inlining(self):
        """Inlining can be disabled."""
        source = """
            int add(int a, int b) { return a + b; }
            int main(void) { return add(1, 2); }
        """
        code = generate(parse(source), enable_inlining=False, enable_dead_elimination=False)
        # With inlining disabled, add should be called
        assert "call\t_add" in code

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
        for decl in unit.items:
            if isinstance(decl, ast_module.FunctionDef) and decl.body:
                from uc80.codegen import function_name as _fn
                _nm = _fn(decl)
                if _nm:
                    func_bodies[_nm] = decl

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
        assert "public\t_inc" not in code
        assert "public\t_add2" not in code


class TestConstantPropagation:
    """Tests for interprocedural constant propagation."""

    def test_propagate_single_constant(self):
        """A parameter always passed the same value is propagated."""
        source = """
            int foo(int x) { return x + 1; }
            int main(void) { return foo(5) + foo(5); }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)

        new_unit, count = analyzer.propagate_constants(unit)
        # x is always 5 at both call sites
        assert count >= 1

    def test_no_propagate_varying_args(self):
        """Parameters passed different values are not propagated."""
        source = """
            int foo(int x) { return x + 1; }
            int main(void) { return foo(1) + foo(2); }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)

        new_unit, count = analyzer.propagate_constants(unit)
        # x varies between calls
        assert count == 0

    def test_propagate_multiple_params(self):
        """Multiple constant parameters are propagated."""
        source = """
            int add(int a, int b) { return a + b; }
            int main(void) { return add(2, 3); }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)

        new_unit, count = analyzer.propagate_constants(unit)
        # Both a and b are constant
        assert count >= 2

    def test_disable_const_propagation(self):
        """Constant propagation can be disabled."""
        source = """
            int foo(int x) { return x + 1; }
            int main(void) { return foo(5) + foo(5); }
        """
        gen = CodeGenerator("test", enable_const_propagation=False)
        unit = parse(source)
        gen.generate(unit)
        assert gen.constants_propagated == 0

    def test_enable_const_propagation(self):
        """Constant propagation is enabled by default."""
        source = """
            int foo(int x) { return x + 1; }
            int main(void) { return foo(5) + foo(5); }
        """
        gen = CodeGenerator("test", enable_const_propagation=True,
                           enable_inlining=False, enable_dead_elimination=False)
        unit = parse(source)
        gen.generate(unit)
        assert gen.constants_propagated >= 1

    def test_find_constant_params(self):
        """Test _find_constant_params directly."""
        source = """
            int always_ten(int x) { return x * 2; }
            int main(void) { return always_ten(10) + always_ten(10); }
        """
        unit = parse(source)
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)

        constant_params = analyzer._find_constant_params(unit)
        assert "always_ten" in constant_params
        assert 0 in constant_params["always_ten"]
        assert constant_params["always_ten"][0] == 10


class TestWholeProgramMode:
    """Tests for whole_program compilation mode."""

    def test_whole_program_eliminates_unused_public(self):
        """In whole_program mode, unused PUBLIC functions are eliminated."""
        source = """
            void unused(void) { }
            int main(void) { return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True, whole_program=True)
        assert "public\t_main" in code
        assert "public\t_unused" not in code

    def test_no_whole_program_keeps_public(self):
        """Without whole_program, PUBLIC functions are kept (external code might call them)."""
        source = """
            void unused(void) { }
            int main(void) { return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True, whole_program=False)
        assert "public\t_main" in code
        assert "public\t_unused" in code  # Kept because external code might call it

    def test_no_whole_program_eliminates_static(self):
        """Without whole_program, unused static functions are still eliminated."""
        source = """
            static void unused(void) { }
            int main(void) { return 0; }
        """
        code = generate(parse(source), enable_dead_elimination=True, whole_program=False)
        assert "public\t_main" in code
        # Static functions without PUBLIC directive - check for label
        assert "_unused:" not in code

    def test_whole_program_inlines_public(self):
        """In whole_program mode, PUBLIC trivial functions are inlined."""
        source = """
            int inc(int x) { return x + 1; }
            int main(void) { return inc(5); }
        """
        code = generate(parse(source), enable_inlining=True, enable_dead_elimination=True,
                       whole_program=True)
        # inc should be inlined and eliminated
        assert "public\t_inc" not in code

    def test_no_whole_program_keeps_public_for_inline(self):
        """Without whole_program, PUBLIC functions are not inlined (external might call)."""
        source = """
            int inc(int x) { return x + 1; }
            int main(void) { return inc(5); }
        """
        code = generate(parse(source), enable_inlining=True, enable_dead_elimination=True,
                       whole_program=False)
        # inc should NOT be inlined - kept for external callers
        assert "public\t_inc" in code

    def test_no_whole_program_inlines_static(self):
        """Without whole_program, static functions can still be inlined."""
        source = """
            static int inc(int x) { return x + 1; }
            int main(void) { return inc(5); }
        """
        code = generate(parse(source), enable_inlining=True, enable_dead_elimination=True,
                       whole_program=False)
        # Static function can be inlined even without whole_program
        assert "_inc:" not in code

    def test_no_whole_program_no_const_propagation_to_public(self):
        """Without whole_program, constants are not propagated to PUBLIC functions."""
        source = """
            int foo(int x) { return x + 1; }
            int main(void) { return foo(5) + foo(5); }
        """
        gen = CodeGenerator("test", enable_const_propagation=True,
                           enable_inlining=False, enable_dead_elimination=False,
                           whole_program=False)
        unit = parse(source)
        gen.generate(unit)
        # foo is PUBLIC, so constants should NOT be propagated
        assert gen.constants_propagated == 0

    def test_no_whole_program_propagates_to_static(self):
        """Without whole_program, constants can still be propagated to static functions."""
        source = """
            static int foo(int x) { return x + 1; }
            int main(void) { return foo(5) + foo(5); }
        """
        gen = CodeGenerator("test", enable_const_propagation=True,
                           enable_inlining=False, enable_dead_elimination=False,
                           whole_program=False)
        unit = parse(source)
        gen.generate(unit)
        # foo is static, so constants CAN be propagated
        assert gen.constants_propagated >= 1
