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

    def test_remove_unreferenced_data(self):
        """Unreferenced data blocks are removed."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tRET

\tDSEG
_unused_data:
\tDW\t0
_another_unused:
\tDS\t10

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_unused_data" not in result
        assert "_another_unused" not in result
        # DSEG should not be emitted if all data removed
        assert "DSEG" not in result

    def test_keep_referenced_remove_unreferenced_data(self):
        """Referenced data is kept, unreferenced is removed."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tLD\tHL,(_used_data)
\tRET

\tDSEG
_used_data:
\tDW\t42
_unused_data:
\tDW\t0

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "_used_data" in result
        assert "_unused_data" not in result

    def test_preserve_public_data(self):
        """PUBLIC data labels are always preserved."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tRET

\tDSEG
\tPUBLIC\t_exported_data
_exported_data:
\tDW\t42
_internal_data:
\tDW\t0

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_exported_data" in result
        assert "_internal_data" not in result

    def test_data_reference_in_called_function(self):
        """Data referenced from called function is kept."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tCALL\t_helper
\tRET

_helper:
\tLD\tHL,_buffer
\tRET

_dead:
\tRET

\tDSEG
_buffer:
\tDS\t256
_unused:
\tDS\t256

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_helper:" in result
        assert "_buffer" in result
        assert "_dead:" not in result
        assert "_unused" not in result

    def test_data_reference_in_comment_ignored(self):
        """Data labels in comments are not considered references."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\t; This references _comment_data
\tRET

\tDSEG
_comment_data:
\tDW\t0

\tEND
"""
        result = eliminate_dead_code(asm)
        # The data block itself should not be included (no DSEG section)
        # Note: _comment_data appears in the comment, but the actual data block is removed
        assert "DSEG" not in result
        assert "_comment_data:" not in result  # Data label definition removed

    def test_shared_storage_with_offset(self):
        """??AUTO style labels with +offset are properly recognized."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tLD\tHL,(??AUTO+0)
\tLD\tDE,(??AUTO+2)
\tRET

\tDSEG
??AUTO:
\tDS\t4
_unused_data:
\tDW\t0

\tEND
"""
        result = eliminate_dead_code(asm)
        assert "_main:" in result
        assert "??AUTO:" in result
        assert "DS\t4" in result
        assert "_unused_data" not in result
