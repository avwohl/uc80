"""Tests for assembly-level dead code elimination."""

from src.asm_dce import AssemblyDCE, eliminate_dead_code


class TestAssemblyDCE:
    """Tests for assembly DCE."""

    def test_keep_reachable_code(self):
        """Reachable code is preserved."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tCALL\t_helper
\tRET

_helper:
\tLD\tHL,42
\tRET

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_helper:" in result

    def test_remove_unreachable(self):
        """Unreachable code is removed."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tRET

_unreachable:
\tLD\tHL,42
\tRET

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_unreachable:" not in result

    def test_follow_jumps(self):
        """Code reachable via jumps is preserved."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tJP\t_target

_target:
\tRET

_other:
\tRET

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_target:" in result
        assert "_other:" not in result

    def test_follow_conditional_jumps(self):
        """Code reachable via conditional jumps is preserved."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tJR\tZ,_zero
\tJP\tNZ,_nonzero
\tRET

_zero:
\tRET

_nonzero:
\tRET

_dead:
\tRET

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_zero:" in result
        assert "_nonzero:" in result
        assert "_dead:" not in result

    def test_preserve_dseg(self):
        """DSEG content is preserved."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tLD\tHL,(_data)
\tRET

\tDSEG
_data:
\tDW\t42

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "DSEG" in result
        assert "_data:" in result

    def test_public_functions_as_entry(self):
        """All PUBLIC functions are entry points by default."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
\tPUBLIC\t_api_func
_main:
\tRET

_api_func:
\tRET

_internal:
\tRET

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_api_func:" in result
        assert "_internal:" not in result

    def test_fall_through(self):
        """Fall-through blocks are preserved."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tLD\tA,1

_next:
\tLD\tB,2
\tRET

_dead:
\tRET

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_next:" in result
        assert "_dead:" not in result
