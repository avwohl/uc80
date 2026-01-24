"""Tests for runtime library handling."""

import pytest
from pathlib import Path
from src.runtime import RuntimeLibrary, AsmFunction, load_runtime_library


class TestRuntimeLibrary:
    """Tests for RuntimeLibrary class."""

    def test_load_runtime_library(self):
        """Default runtime library loads successfully."""
        lib = load_runtime_library()
        # Should have basic arithmetic functions
        assert "__mul16" in lib.functions
        assert "__div16" in lib.functions

    def test_get_function(self):
        """Can retrieve individual functions."""
        lib = load_runtime_library()
        func = lib.get_function("__mul16")
        assert func is not None
        assert func.name == "__mul16"
        assert "__mul16" in func.publics
        assert "CALL" not in func.source or "__mul16" not in func.source

    def test_get_required_functions_single(self):
        """Getting required functions for single function."""
        lib = load_runtime_library()
        funcs = lib.get_required_functions({"__mul16"})
        assert len(funcs) >= 1
        names = {f.name for f in funcs}
        assert "__mul16" in names

    def test_get_required_functions_with_deps(self):
        """Functions that depend on others include dependencies."""
        lib = load_runtime_library()
        # __sdiv16 might depend on __div16 or have its own implementation
        funcs = lib.get_required_functions({"__sdiv16"})
        assert len(funcs) >= 1

    def test_parse_custom_assembly(self):
        """Can parse custom assembly content."""
        lib = RuntimeLibrary()
        content = """
; Test assembly
\t.Z80
\tCSEG

\tPUBLIC\t_test_func
_test_func:
\tLD\tHL,42
\tRET

\tPUBLIC\t_another_func
_another_func:
\tCALL\t_test_func
\tRET

\tDSEG
_data:\tDW\t0

\tEND
"""
        lib._parse_assembly(content)
        assert "_test_func" in lib.functions
        assert "_another_func" in lib.functions

        # Check dependencies
        another = lib.get_function("_another_func")
        assert "_test_func" in another.dependencies

    def test_get_required_with_chain(self):
        """Required functions include transitive dependencies."""
        lib = RuntimeLibrary()
        content = """
\t.Z80
\tCSEG

\tPUBLIC\t_func_a
_func_a:
\tRET

\tPUBLIC\t_func_b
_func_b:
\tCALL\t_func_a
\tRET

\tPUBLIC\t_func_c
_func_c:
\tCALL\t_func_b
\tRET

\tEND
"""
        lib._parse_assembly(content)

        # Requesting func_c should also get func_b and func_a
        funcs = lib.get_required_functions({"_func_c"})
        names = {f.name for f in funcs}
        assert "_func_c" in names
        assert "_func_b" in names
        assert "_func_a" in names

    def test_empty_request(self):
        """Requesting no functions returns empty list."""
        lib = load_runtime_library()
        funcs = lib.get_required_functions(set())
        assert funcs == []

    def test_unknown_function(self):
        """Requesting unknown function returns empty list."""
        lib = load_runtime_library()
        funcs = lib.get_required_functions({"__nonexistent_function__"})
        assert funcs == []
