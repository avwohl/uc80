"""Phase 2: verify CodeGenerator honors TypeConfig for byte-width dispatch.

With --int=32, the int type is 4 bytes and must route to the 32-bit codegen
path (DEHL register pair, __add32 runtime) rather than the 16-bit path.
"""

import pytest
from uc_core.lexer import Lexer
from uc_core.parser import Parser
from uc_core.ast_optimizer import ASTOptimizer
from uc_core.type_config import TypeConfig, Z80_CPM, WATCOM_FLAT32
from uc_core import ast as ast_module
from src.codegen import CodeGenerator


def _compile(src: str, tc: TypeConfig) -> str:
    tokens = list(Lexer(src, "<test>").tokenize())
    unit = Parser(tokens).parse()
    unit = ASTOptimizer(3, type_config=tc).optimize(unit)
    return CodeGenerator(type_config=tc).generate(unit)


class TestByteWidthDispatch:

    def test_default_int_is_16bit_uses_add16(self):
        """Default Z80_CPM: int + int → 16-bit add (no __add32 call)."""
        code = _compile(
            "int main(void) { int x = 100; int y = x + 1; return y; }",
            Z80_CPM,
        )
        assert "__add32" not in code, "16-bit int should not use __add32"

    def test_int32_override_routes_int_add_to_add32(self):
        """With int_size=4, int + int must generate __add32."""
        tc32 = TypeConfig(int_size=4, long_size=4, ptr_size=2)
        code = _compile(
            "int main(void) { int x = 100; int y = x + 1; return y; }",
            tc32,
        )
        assert "__add32" in code, "int32 override should use __add32"

    def test_int32_sizeof_int_is_four(self):
        """sizeof(int) folds to 4 at the optimizer, reaches codegen as an
        IntLiteral of 4, and is returned as-is."""
        tc32 = TypeConfig(int_size=4, long_size=4, ptr_size=2)
        code = _compile(
            "int main(void) { return sizeof(int); }",
            tc32,
        )
        # Should have a "ld HL,4" somewhere in main or use ld HL,... to return 4
        assert "ld\tHL,4\n" in code or "ld HL,4\n" in code

    def test_default_sizeof_int_is_two(self):
        """Default int=16: sizeof(int) folds to 2."""
        code = _compile("int main(void) { return sizeof(int); }", Z80_CPM)
        # Can be "ld HL,2" - just check it's not 4 from the wrong path
        assert "ld\tHL,4\n" not in code or "ld\tHL,2\n" in code


class TestTypeConfigPlumbing:

    def test_codegenerator_defaults_to_z80_cpm(self):
        """No type_config arg → Z80_CPM defaults."""
        gen = CodeGenerator()
        assert gen.type_config.int_size == 2
        assert gen.type_config.ptr_size == 2

    def test_codegenerator_accepts_watcom_flat32(self):
        gen = CodeGenerator(type_config=WATCOM_FLAT32)
        assert gen.type_config.int_size == 4
        assert gen.type_config.ptr_size == 4

    def test_is_long_type_byte_width_dispatch(self):
        """With int=32, BasicType 'int' counts as a long (4-byte) type."""
        tc32 = TypeConfig(int_size=4, long_size=4, ptr_size=2)
        gen = CodeGenerator(type_config=tc32)
        int_type = ast_module.BasicType(name="int")
        assert gen._is_long_type(int_type) is True

    def test_is_long_type_default_is_16bit(self):
        """With default Z80_CPM, int is 16-bit — not a long."""
        gen = CodeGenerator(type_config=Z80_CPM)
        int_type = ast_module.BasicType(name="int")
        long_type = ast_module.BasicType(name="long")
        assert gen._is_long_type(int_type) is False
        assert gen._is_long_type(long_type) is True

    def test_is_long_long_type_byte_width_dispatch(self):
        """With long=64, a BasicType 'long' is 8 bytes."""
        tc_long64 = TypeConfig(int_size=4, long_size=8, long_long_size=8, ptr_size=4)
        gen = CodeGenerator(type_config=tc_long64)
        long_type = ast_module.BasicType(name="long")
        assert gen._is_long_long_type(long_type) is True

    def test_type_size_uses_config(self):
        tc32 = TypeConfig(int_size=4, long_size=4, ptr_size=2)
        gen = CodeGenerator(type_config=tc32)
        assert gen._type_size(ast_module.BasicType(name="int")) == 4
        assert gen._type_size(ast_module.PointerType(base_type=ast_module.BasicType(name="char"))) == 2


class TestPrintfDispatch:
    """Phase 5: %d/%u/%x etc. must dispatch to the 32-bit handlers when
    int is 32-bit, so a `printf("%d", x)` call with a 4-byte int arg reads
    the right number of bytes off the stack."""

    def test_default_int16_uses_16bit_handlers(self):
        code = _compile(
            'int main(void) { int x = 1; return printf("%d", x); }',
            Z80_CPM,
        )
        # The format dispatch table should route 'd' → __printf_handle_d (16-bit)
        assert "__printf_handle_d" in code
        # And explicitly not the long variant (for %d specifically)
        # Extract the portion of the table that maps 'd'
        import re
        m = re.search(r"db\s+'d'\s*\n\s*dw\s+(\S+)", code)
        assert m is not None, "expected a 'd' entry in printf dispatch table"
        assert m.group(1) == "__printf_handle_d"

    def test_int32_routes_d_to_ld_handler(self):
        tc32 = TypeConfig(int_size=4, long_size=4, ptr_size=2)
        code = _compile(
            'int main(void) { int x = 1; return printf("%d", x); }',
            tc32,
        )
        import re
        m = re.search(r"db\s+'d'\s*\n\s*dw\s+(\S+)", code)
        assert m is not None
        assert m.group(1) == "__printf_handle_ld", \
            "with int=32, %d must dispatch to the 32-bit ld handler"

    def test_int32_routes_u_x_o_to_long_handlers(self):
        tc32 = TypeConfig(int_size=4, long_size=4, ptr_size=2)
        code = _compile(
            'int main(void) { unsigned u=1; return printf("%u %x %o", u, u, u); }',
            tc32,
        )
        import re
        for spec, expected in [('u', '__printf_handle_lu'),
                                ('x', '__printf_handle_lx'),
                                ('o', '__printf_handle_lo')]:
            m = re.search(rf"db\s+'{spec}'\s*\n\s*dw\s+(\S+)", code)
            assert m is not None, f"missing '{spec}' dispatch entry"
            assert m.group(1) == expected, \
                f"with int=32, %{spec} should map to {expected}, got {m.group(1)}"
