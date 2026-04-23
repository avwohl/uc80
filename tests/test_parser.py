"""Tests for C24 parser."""

import pytest
from uc_core.parser import Parser, ParseError, parse
from uc_core.lexer import tokenize
from uc_core import ast


def parse_expr(source: str) -> ast.Expression:
    """Parse a single expression."""
    tokens = tokenize(source + ";")
    parser = Parser(tokens)
    stmt = parser._parse_statement()
    assert isinstance(stmt, ast.ExpressionStmt)
    return stmt.expr


def parse_stmt(source: str) -> ast.Statement:
    """Parse a single statement."""
    tokens = tokenize(source)
    parser = Parser(tokens)
    return parser._parse_statement()


def parse_decl(source: str) -> ast.Declaration:
    """Parse a single declaration."""
    tokens = tokenize(source)
    parser = Parser(tokens)
    return parser._parse_declaration()


class TestLiterals:
    """Test literal parsing."""

    def test_int_literal(self):
        expr = parse_expr("42")
        assert isinstance(expr, ast.IntLiteral)
        assert expr.value == 42

    def test_float_literal(self):
        expr = parse_expr("3.14")
        assert isinstance(expr, ast.FloatLiteral)
        assert expr.value == pytest.approx(3.14)

    def test_char_literal(self):
        expr = parse_expr("'a'")
        assert isinstance(expr, ast.CharLiteral)
        assert expr.value == ord('a')

    def test_string_literal(self):
        expr = parse_expr('"hello"')
        assert isinstance(expr, ast.StringLiteral)
        assert expr.value == "hello"

    def test_string_concatenation(self):
        expr = parse_expr('"hello" " " "world"')
        assert isinstance(expr, ast.StringLiteral)
        assert expr.value == "hello world"

    def test_bool_literals(self):
        # true/false are currently treated as identifiers (stdbool.h macros)
        expr = parse_expr("true")
        assert isinstance(expr, ast.Identifier)
        assert expr.name == "true"

        expr = parse_expr("false")
        assert isinstance(expr, ast.Identifier)
        assert expr.name == "false"

    def test_nullptr(self):
        expr = parse_expr("nullptr")
        assert isinstance(expr, ast.NullptrLiteral)


class TestIdentifiers:
    """Test identifier parsing."""

    def test_simple_identifier(self):
        expr = parse_expr("foo")
        assert isinstance(expr, ast.Identifier)
        assert expr.name == "foo"

    def test_underscore_identifier(self):
        expr = parse_expr("_bar_123")
        assert isinstance(expr, ast.Identifier)
        assert expr.name == "_bar_123"


class TestBinaryOperators:
    """Test binary operator parsing."""

    def test_arithmetic(self):
        expr = parse_expr("a + b")
        assert isinstance(expr, ast.BinaryOp)
        assert expr.op == "+"
        assert isinstance(expr.left, ast.Identifier)
        assert isinstance(expr.right, ast.Identifier)

    def test_precedence_mul_add(self):
        expr = parse_expr("a + b * c")
        assert isinstance(expr, ast.BinaryOp)
        assert expr.op == "+"
        assert isinstance(expr.right, ast.BinaryOp)
        assert expr.right.op == "*"

    def test_precedence_comparison(self):
        expr = parse_expr("a < b == c > d")
        assert isinstance(expr, ast.BinaryOp)
        assert expr.op == "=="

    def test_logical_operators(self):
        expr = parse_expr("a && b || c")
        assert isinstance(expr, ast.BinaryOp)
        assert expr.op == "||"
        assert isinstance(expr.left, ast.BinaryOp)
        assert expr.left.op == "&&"

    def test_bitwise_operators(self):
        expr = parse_expr("a & b | c ^ d")
        assert isinstance(expr, ast.BinaryOp)
        assert expr.op == "|"

    def test_shift_operators(self):
        expr = parse_expr("a << 2")
        assert isinstance(expr, ast.BinaryOp)
        assert expr.op == "<<"

    def test_assignment(self):
        expr = parse_expr("a = b")
        assert isinstance(expr, ast.BinaryOp)
        assert expr.op == "="

    def test_compound_assignment(self):
        ops = ["+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "^=", "|="]
        for op in ops:
            expr = parse_expr(f"a {op} b")
            assert isinstance(expr, ast.BinaryOp)
            assert expr.op == op


class TestUnaryOperators:
    """Test unary operator parsing."""

    def test_negation(self):
        expr = parse_expr("-x")
        assert isinstance(expr, ast.UnaryOp)
        assert expr.op == "-"
        assert expr.is_prefix

    def test_logical_not(self):
        expr = parse_expr("!x")
        assert isinstance(expr, ast.UnaryOp)
        assert expr.op == "!"

    def test_bitwise_not(self):
        expr = parse_expr("~x")
        assert isinstance(expr, ast.UnaryOp)
        assert expr.op == "~"

    def test_address_of(self):
        expr = parse_expr("&x")
        assert isinstance(expr, ast.UnaryOp)
        assert expr.op == "&"

    def test_dereference(self):
        expr = parse_expr("*p")
        assert isinstance(expr, ast.UnaryOp)
        assert expr.op == "*"

    def test_prefix_increment(self):
        expr = parse_expr("++x")
        assert isinstance(expr, ast.UnaryOp)
        assert expr.op == "++"
        assert expr.is_prefix

    def test_postfix_increment(self):
        expr = parse_expr("x++")
        assert isinstance(expr, ast.UnaryOp)
        assert expr.op == "++"
        assert not expr.is_prefix

    def test_sizeof_expr(self):
        expr = parse_expr("sizeof x")
        assert isinstance(expr, ast.SizeofExpr)

    def test_sizeof_type(self):
        expr = parse_expr("sizeof(int)")
        assert isinstance(expr, ast.SizeofType)


class TestTernaryOperator:
    """Test ternary operator parsing."""

    def test_simple_ternary(self):
        expr = parse_expr("a ? b : c")
        assert isinstance(expr, ast.TernaryOp)
        assert isinstance(expr.condition, ast.Identifier)
        assert isinstance(expr.true_expr, ast.Identifier)
        assert isinstance(expr.false_expr, ast.Identifier)

    def test_nested_ternary(self):
        expr = parse_expr("a ? b : c ? d : e")
        assert isinstance(expr, ast.TernaryOp)
        assert isinstance(expr.false_expr, ast.TernaryOp)


class TestPostfixOperators:
    """Test postfix operator parsing."""

    def test_function_call(self):
        expr = parse_expr("foo()")
        assert isinstance(expr, ast.Call)
        assert isinstance(expr.func, ast.Identifier)
        assert expr.args == []

    def test_function_call_with_args(self):
        expr = parse_expr("foo(a, b, c)")
        assert isinstance(expr, ast.Call)
        assert len(expr.args) == 3

    def test_array_subscript(self):
        expr = parse_expr("arr[0]")
        assert isinstance(expr, ast.Index)
        assert isinstance(expr.array, ast.Identifier)
        assert isinstance(expr.index, ast.IntLiteral)

    def test_member_access(self):
        expr = parse_expr("s.x")
        assert isinstance(expr, ast.Member)
        assert expr.member == "x"
        assert not expr.is_arrow

    def test_arrow_access(self):
        expr = parse_expr("p->x")
        assert isinstance(expr, ast.Member)
        assert expr.member == "x"
        assert expr.is_arrow

    def test_chained_access(self):
        expr = parse_expr("a.b->c.d")
        assert isinstance(expr, ast.Member)
        assert expr.member == "d"


class TestCasts:
    """Test cast parsing."""

    def test_simple_cast(self):
        expr = parse_expr("(int)x")
        assert isinstance(expr, ast.Cast)
        assert isinstance(expr.target_type, ast.BasicType)
        assert expr.target_type.name == "int"

    def test_pointer_cast(self):
        expr = parse_expr("(int*)p")
        assert isinstance(expr, ast.Cast)
        assert isinstance(expr.target_type, ast.PointerType)


class TestStatements:
    """Test statement parsing."""

    def test_expression_stmt(self):
        stmt = parse_stmt("x = 1;")
        assert isinstance(stmt, ast.ExpressionStmt)

    def test_empty_stmt(self):
        stmt = parse_stmt(";")
        assert isinstance(stmt, ast.ExpressionStmt)
        assert stmt.expr is None

    def test_compound_stmt(self):
        stmt = parse_stmt("{ x = 1; y = 2; }")
        assert isinstance(stmt, ast.CompoundStmt)
        assert len(stmt.items) == 2

    def test_if_stmt(self):
        stmt = parse_stmt("if (x) y = 1;")
        assert isinstance(stmt, ast.IfStmt)
        assert stmt.else_branch is None

    def test_if_else_stmt(self):
        stmt = parse_stmt("if (x) y = 1; else y = 2;")
        assert isinstance(stmt, ast.IfStmt)
        assert stmt.else_branch is not None

    def test_while_stmt(self):
        stmt = parse_stmt("while (x) x--;")
        assert isinstance(stmt, ast.WhileStmt)

    def test_do_while_stmt(self):
        stmt = parse_stmt("do x--; while (x);")
        assert isinstance(stmt, ast.DoWhileStmt)

    def test_for_stmt(self):
        stmt = parse_stmt("for (i = 0; i < 10; i++) x++;")
        assert isinstance(stmt, ast.ForStmt)
        assert stmt.init is not None
        assert stmt.condition is not None
        assert stmt.update is not None

    def test_for_stmt_empty(self):
        stmt = parse_stmt("for (;;) break;")
        assert isinstance(stmt, ast.ForStmt)
        assert stmt.init is None
        assert stmt.condition is None
        assert stmt.update is None

    def test_switch_stmt(self):
        stmt = parse_stmt("switch (x) { case 1: break; default: break; }")
        assert isinstance(stmt, ast.SwitchStmt)

    def test_break_stmt(self):
        stmt = parse_stmt("break;")
        assert isinstance(stmt, ast.BreakStmt)

    def test_continue_stmt(self):
        stmt = parse_stmt("continue;")
        assert isinstance(stmt, ast.ContinueStmt)

    def test_return_stmt(self):
        stmt = parse_stmt("return;")
        assert isinstance(stmt, ast.ReturnStmt)
        assert stmt.value is None

    def test_return_value_stmt(self):
        stmt = parse_stmt("return 42;")
        assert isinstance(stmt, ast.ReturnStmt)
        assert isinstance(stmt.value, ast.IntLiteral)

    def test_goto_stmt(self):
        stmt = parse_stmt("goto end;")
        assert isinstance(stmt, ast.GotoStmt)
        assert stmt.label == "end"

    def test_labeled_stmt(self):
        stmt = parse_stmt("end: return;")
        assert isinstance(stmt, ast.LabelStmt)
        assert stmt.label == "end"


class TestDeclarations:
    """Test declaration parsing."""

    def test_simple_var(self):
        decl = parse_decl("int x;")
        assert isinstance(decl, ast.VarDecl)
        assert decl.name == "x"
        assert isinstance(decl.var_type, ast.BasicType)
        assert decl.var_type.name == "int"

    def test_var_with_init(self):
        decl = parse_decl("int x = 42;")
        assert isinstance(decl, ast.VarDecl)
        assert isinstance(decl.init, ast.IntLiteral)

    def test_pointer_var(self):
        decl = parse_decl("int *p;")
        assert isinstance(decl, ast.VarDecl)
        assert isinstance(decl.var_type, ast.PointerType)

    def test_array_var(self):
        decl = parse_decl("int arr[10];")
        assert isinstance(decl, ast.VarDecl)
        assert isinstance(decl.var_type, ast.ArrayType)

    def test_unsigned_int(self):
        decl = parse_decl("unsigned int x;")
        assert isinstance(decl, ast.VarDecl)
        assert decl.var_type.is_signed is False

    def test_long_long(self):
        decl = parse_decl("long long x;")
        assert isinstance(decl, ast.VarDecl)
        assert decl.var_type.name == "long long"

    def test_const_var(self):
        decl = parse_decl("const int x = 1;")
        assert isinstance(decl, ast.VarDecl)
        assert decl.var_type.is_const

    def test_static_var(self):
        decl = parse_decl("static int x;")
        assert isinstance(decl, ast.VarDecl)
        assert decl.storage_class == "static"


class TestFunctions:
    """Test function parsing."""

    def test_function_decl(self):
        decl = parse_decl("int foo(void);")
        assert isinstance(decl, ast.VarDecl)  # No body = just declaration
        assert isinstance(decl.var_type, ast.FunctionType)

    def test_function_def(self):
        decl = parse_decl("int foo(void) { return 0; }")
        assert isinstance(decl, ast.FunctionDecl)
        assert decl.name == "foo"
        assert decl.body is not None

    def test_function_with_params(self):
        decl = parse_decl("int add(int a, int b) { return a + b; }")
        assert isinstance(decl, ast.FunctionDecl)
        assert decl.name == "add"

    def test_variadic_function(self):
        decl = parse_decl("int printf(const char *fmt, ...);")
        assert isinstance(decl, ast.VarDecl)
        assert isinstance(decl.var_type, ast.FunctionType)
        assert decl.var_type.is_variadic


class TestStructs:
    """Test struct parsing."""

    def test_struct_type(self):
        decl = parse_decl("struct Point p;")
        assert isinstance(decl, ast.VarDecl)
        assert isinstance(decl.var_type, ast.StructType)
        assert decl.var_type.name == "Point"

    def test_struct_definition(self):
        decl = parse_decl("struct Point { int x; int y; };")
        assert isinstance(decl, ast.StructDecl)
        assert decl.name == "Point"
        assert len(decl.members) == 2

    def test_union_definition(self):
        decl = parse_decl("union Data { int i; float f; };")
        assert isinstance(decl, ast.StructDecl)
        assert decl.is_union


class TestEnums:
    """Test enum parsing."""

    def test_enum_type(self):
        decl = parse_decl("enum Color c;")
        assert isinstance(decl, ast.VarDecl)
        assert isinstance(decl.var_type, ast.EnumType)

    def test_enum_definition(self):
        decl = parse_decl("enum Color { RED, GREEN, BLUE };")
        assert isinstance(decl, ast.EnumDecl)
        assert len(decl.values) == 3

    def test_enum_with_values(self):
        decl = parse_decl("enum { A = 1, B = 2 };")
        assert isinstance(decl, ast.EnumDecl)
        assert decl.values[0].name == "A"
        assert isinstance(decl.values[0].value, ast.IntLiteral)


class TestTypedefs:
    """Test typedef parsing."""

    def test_simple_typedef(self):
        decl = parse_decl("typedef int INT;")
        assert isinstance(decl, ast.TypedefDecl)
        assert decl.name == "INT"

    def test_typedef_pointer(self):
        decl = parse_decl("typedef int *PINT;")
        assert isinstance(decl, ast.TypedefDecl)
        assert isinstance(decl.target_type, ast.PointerType)


class TestInitializers:
    """Test initializer parsing."""

    def test_scalar_init(self):
        decl = parse_decl("int x = 42;")
        assert isinstance(decl.init, ast.IntLiteral)

    def test_array_init(self):
        decl = parse_decl("int arr[] = {1, 2, 3};")
        assert isinstance(decl.init, ast.InitializerList)
        assert len(decl.init.values) == 3

    def test_designated_init(self):
        decl = parse_decl("int arr[10] = { [5] = 42 };")
        assert isinstance(decl.init, ast.InitializerList)
        assert isinstance(decl.init.values[0], ast.DesignatedInit)


class TestCompletePrograms:
    """Test parsing complete programs."""

    def test_hello_world(self):
        source = '''
int main(void) {
    return 0;
}
'''
        unit = parse(source)
        assert isinstance(unit, ast.TranslationUnit)
        assert len(unit.declarations) == 1
        assert isinstance(unit.declarations[0], ast.FunctionDecl)

    def test_multiple_functions(self):
        source = '''
int add(int a, int b) {
    return a + b;
}

int main(void) {
    int result = add(1, 2);
    return result;
}
'''
        unit = parse(source)
        assert len(unit.declarations) == 2

    def test_global_variables(self):
        source = '''
int global = 42;
static int counter = 0;

int get_counter(void) {
    return counter++;
}
'''
        unit = parse(source)
        assert len(unit.declarations) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
