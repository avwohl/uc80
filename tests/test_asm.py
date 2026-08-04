"""Tests for basic inline assembly - `asm("...")` / `__asm__("...")`.

uc_core has always parsed every asm form, and uc80's codegen matched none of
them: `gen_statement`'s isinstance chain ended at GotoStmt and `gen_declaration`
returned for anything that was not a Declaration, so an asm block compiled to
nothing at all, with exit status 0 and no diagnostic.

Basic asm is now emitted verbatim.  The emitted block is bracketed with the
markers from asm_dce so that neither the peephole optimizer nor the assembly
dead-code eliminator may rewrite, reorder or delete hand-written code.
Extended asm (operand/clobber lists) and `asm goto` are rejected with a real
diagnostic instead of being silently dropped.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uc_core.frontend import parse as _frontend_parse
from uc_core import ast as ast_module

from uc80.asm_dce import (
    ASM_BEGIN_FILE_MARKER,
    ASM_BEGIN_MARKER,
    ASM_END_MARKER,
    eliminate_dead_code,
    is_asm_begin,
    is_asm_end,
)
from uc80.codegen import (
    CallGraphAnalyzer,
    CodegenError,
    _asm_template_text,
    _format_asm_block,
    generate,
)
from uc80.main import _optimize_outside_asm

LIB_DIR = Path(__file__).resolve().parent.parent / "src" / "uc80" / "lib"
CPMEMU = Path(__file__).resolve().parent.parent.parent / "cpmemu" / "src" / "cpmemu"


def parse(source: str):
    return _frontend_parse(source, "<test>")


def gen(source: str) -> str:
    return generate(parse(source))


def asm_region(code: str) -> list[str]:
    """The lines of the first inline-asm region, markers included."""
    region: list[str] = []
    inside = False
    for line in code.splitlines():
        if is_asm_begin(line):
            inside = True
        if inside:
            region.append(line)
        if is_asm_end(line):
            break
    return region


def run_compiler(*args):
    """Run the uc80 compiler with the given arguments."""
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", *args],
        capture_output=True,
        text=True,
    )


def compile_source(tmp_path, source, *extra_args, name="prog"):
    """Compile C source through the real CLI; return the emitted assembly."""
    c_file = tmp_path / f"{name}.c"
    c_file.write_text(source)
    output = tmp_path / f"{name}.mac"
    result = run_compiler(str(c_file), "-o", str(output), *extra_args)
    assert result.returncode == 0, f"Compiler failed: {result.stderr}"
    return output.read_text()


def find_asm_nodes(node, seen=None, found=None):
    """Every AsmDeclaration in an auto-AST.

    The identity-keyed `seen` set is not an optimization: auto-AST nodes
    carry back-references, so a plain recursive walk never terminates.
    """
    if seen is None:
        seen, found = set(), []
    if id(node) in seen:
        return found
    seen.add(id(node))
    if isinstance(node, ast_module.AsmDeclaration):
        found.append(node)
    if isinstance(node, (list, tuple)):
        for item in node:
            find_asm_nodes(item, seen, found)
    elif hasattr(node, "__dataclass_fields__"):
        for field_name in node.__dataclass_fields__:
            find_asm_nodes(getattr(node, field_name, None), seen, found)
    return found


class TestTemplateText:
    """The template is a list of adjacent string literals with escapes."""

    def test_escapes_are_decoded(self):
        unit = parse('int main(void){ asm("ld hl,1234\\n\\tnop"); return 0; }')
        node = find_asm_nodes(unit)[0]
        assert _asm_template_text(node) == "ld hl,1234\n\tnop"

    def test_adjacent_literals_are_concatenated(self):
        unit = parse('int main(void){ asm("nop\\n" "halt\\n"); return 0; }')
        node = find_asm_nodes(unit)[0]
        assert _asm_template_text(node) == "nop\nhalt\n"

    def test_hex_escape_is_decoded(self):
        unit = parse('int main(void){ asm("db\\t0\\x41"); return 0; }')
        node = find_asm_nodes(unit)[0]
        assert _asm_template_text(node) == "db\t0A"


class TestFormatAsmBlock:
    """um80 needs a mnemonic indented and a colon-less label in column 0."""

    def body(self, text, file_scope=False):
        return _format_asm_block(text, file_scope)[1:-1]

    def test_markers_bracket_the_block(self):
        lines = _format_asm_block("nop")
        assert lines[0] == ASM_BEGIN_MARKER
        assert lines[-1] == ASM_END_MARKER

    def test_file_scope_uses_its_own_opening_marker(self):
        lines = _format_asm_block("nop", file_scope=True)
        assert lines[0] == ASM_BEGIN_FILE_MARKER
        assert lines[-1] == ASM_END_MARKER

    def test_column_zero_mnemonic_is_indented(self):
        assert self.body("nop") == ["\tnop"]
        assert self.body("ld hl,1234") == ["\tld hl,1234"]

    def test_already_indented_line_is_untouched(self):
        assert self.body("\tld a,b") == ["\tld a,b"]

    def test_label_stays_in_column_zero(self):
        assert self.body("loop:") == ["loop:"]
        assert self.body("loop: djnz loop") == ["loop: djnz loop"]

    def test_label_named_after_a_mnemonic_stays_in_column_zero(self):
        """`set:` must not be indented just because SET is an opcode."""
        for label in ("set:", "in:", "or:", "end:", "page:"):
            assert self.body(label) == [label]

    def test_colonless_label_stays_in_column_zero(self):
        """MACRO-80 rejects an indented `BDOS EQU 5` or `TBL DW 1,2`."""
        assert self.body("BDOS\tequ\t5") == ["BDOS\tequ\t5"]
        assert self.body("TBL\tdw\t1,2,3") == ["TBL\tdw\t1,2,3"]

    def test_comment_is_untouched(self):
        assert self.body("; a comment") == ["; a comment"]

    def test_blank_lines_are_preserved(self):
        assert self.body("nop\n\nhalt") == ["\tnop", "", "\thalt"]

    def test_multi_line_template(self):
        assert self.body("ld hl,1234\n\tld (_marker),hl") == [
            "\tld hl,1234",
            "\tld (_marker),hl",
        ]


class TestAsmInFunction:
    """`asm("...")` inside a function body."""

    def test_reported_bug_emits_the_instructions(self):
        code = gen('int marker;\n'
                   'void f(void){ asm("ld hl,1234\\n\\tld (_marker),hl"); }\n'
                   'int main(void){ f(); return marker; }')
        assert "ld hl,1234" in code
        assert "ld (_marker),hl" in code

    def test_block_is_bracketed_by_markers(self):
        code = gen('int main(void){ asm("nop"); return 0; }')
        assert asm_region(code) == [ASM_BEGIN_MARKER, "\tnop", ASM_END_MARKER]

    def test_block_is_emitted_inside_the_function(self):
        code = gen('int main(void){ asm("nop"); return 0; }')
        lines = code.splitlines()
        start = next(i for i, l in enumerate(lines) if l.strip() == "_main:")
        end = next(i for i, l in enumerate(lines) if l.strip() == "@main_ret:")
        marker = next(i for i, l in enumerate(lines) if is_asm_begin(l))
        assert start < marker < end

    def test_double_underscore_spelling(self):
        code = gen('int main(void){ __asm__("nop"); return 0; }')
        assert "\tnop" in code

    def test_volatile_qualifier(self):
        code = gen('int main(void){ asm volatile("halt"); return 0; }')
        assert "\thalt" in code

    def test_gnu_volatile_spelling(self):
        code = gen('int main(void){ __asm__ __volatile__("halt"); return 0; }')
        assert "\thalt" in code

    def test_inline_qualifier_is_ignored(self):
        """`asm inline` only affects GCC's size estimate; uc80 has none."""
        code = gen('int main(void){ __asm__ inline("halt"); return 0; }')
        assert "\thalt" in code

    def test_adjacent_string_literals(self):
        code = gen('int main(void){ asm("nop\\n" "halt\\n"); return 0; }')
        assert "\tnop" in code
        assert "\thalt" in code

    def test_empty_template(self):
        code = gen('int main(void){ asm(""); return 0; }')
        assert asm_region(code) == [ASM_BEGIN_MARKER, "", ASM_END_MARKER]

    def test_nested_in_a_loop_and_conditional(self):
        code = gen('int marker;\n'
                   'int main(void){ int i;\n'
                   '  for(i=0;i<3;i++){ if(i==1){ asm("\\tld hl,7"); } }\n'
                   '  return marker; }')
        assert "\tld hl,7" in code

    def test_two_blocks_in_one_function(self):
        code = gen('int main(void){ asm("nop"); asm("halt"); return 0; }')
        assert code.count(ASM_BEGIN_MARKER) == 2
        assert code.count(ASM_END_MARKER) == 2


class TestAsmAtFileScope:
    """`asm("...")` between declarations."""

    def test_file_scope_block_is_emitted(self):
        code = gen('asm("\\tPUBLIC\\t_tbl\\n_tbl:\\tdw\\t11,22,33\\n");\n'
                   'int main(void){ return 0; }')
        assert "_tbl:\tdw\t11,22,33" in code

    def test_file_scope_uses_the_file_scope_marker(self):
        code = gen('asm("nop");\nint main(void){ return 0; }')
        assert ASM_BEGIN_FILE_MARKER in code

    def test_in_function_block_does_not_use_the_file_scope_marker(self):
        code = gen('int main(void){ asm("nop"); return 0; }')
        assert ASM_BEGIN_FILE_MARKER not in code

    def test_file_scope_block_lands_in_cseg(self):
        """It must precede the DSEG/COMMON sections the generator emits last."""
        code = gen('asm("_tbl:\\tdw\\t1\\n");\n'
                   'int gv = 5;\n'
                   'int main(void){ return gv; }')
        lines = code.splitlines()
        marker = next(i for i, l in enumerate(lines) if is_asm_begin(l))
        dseg = next(i for i, l in enumerate(lines) if l.strip().lower() == "dseg")
        assert marker < dseg

    def test_file_scope_block_keeps_its_source_position(self):
        code = gen('void f(void){}\n'
                   'asm("MID: nop\\n");\n'
                   'int main(void){ f(); return 0; }')
        lines = code.splitlines()
        f_at = next(i for i, l in enumerate(lines) if l.strip() == "_f:")
        marker = next(i for i, l in enumerate(lines) if is_asm_begin(l))
        assert f_at < marker


class TestUnsupportedAsm:
    """Extended asm and asm goto are rejected, not silently dropped."""

    def test_extended_asm_raises(self):
        with pytest.raises(CodegenError) as excinfo:
            gen('int main(void){ int x=1; asm("ld hl,%0" : : "r"(x)); return x; }')
        assert "extended asm" in str(excinfo.value)
        assert "operand" in str(excinfo.value)

    def test_extended_asm_with_output_operand_raises(self):
        with pytest.raises(CodegenError) as excinfo:
            gen('int main(void){ int x; asm("ld %0,hl" : "=r"(x)); return x; }')
        assert "extended asm" in str(excinfo.value)

    def test_clobber_list_raises(self):
        with pytest.raises(CodegenError) as excinfo:
            gen('int main(void){ asm("nop" : : : "memory"); return 0; }')
        assert "extended asm" in str(excinfo.value)

    def test_asm_goto_raises(self):
        with pytest.raises(CodegenError) as excinfo:
            gen('int main(void){ asm goto("jp %l0" : : : : lab); lab: return 0; }')
        assert "asm goto" in str(excinfo.value)

    def test_file_scope_extended_asm_raises(self):
        with pytest.raises(CodegenError) as excinfo:
            gen('int g;\nasm("ld hl,%0" : : "m"(g));\nint main(void){ return 0; }')
        assert "extended asm" in str(excinfo.value)

    def test_reserved_marker_in_template_raises(self):
        """A template may not forge the delimiters the guards depend on."""
        source = 'int main(void){ asm("%s"); return 0; }' % ASM_END_MARKER
        with pytest.raises(CodegenError) as excinfo:
            gen(source)
        assert "reserved" in str(excinfo.value)

    def test_diagnostic_carries_a_line_number(self):
        with pytest.raises(CodegenError) as excinfo:
            gen('int main(void){\n  int x=1;\n  asm("nop" : : "r"(x));\n  return x; }')
        assert excinfo.value.line == 3


class TestAsmCallGraph:
    """A function reached only from asm must not be eliminated."""

    def test_call_from_asm_marks_the_callee_live(self):
        unit = parse('int v;\n'
                     'static void helper(void){ v = 99; }\n'
                     'int main(void){ asm("\\tcall\\t_helper"); return v; }')
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        assert "helper" in analyzer.address_taken

    def test_call_from_file_scope_asm_marks_the_callee_live(self):
        unit = parse('int v;\n'
                     'static void helper(void){ v = 99; }\n'
                     'asm("\\tcall\\t_helper\\n");\n'
                     'int main(void){ return v; }')
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        assert "helper" in analyzer.address_taken

    def test_callee_survives_dead_function_elimination(self):
        code = gen('int v;\n'
                   'static void helper(void){ v = 99; }\n'
                   'int main(void){ asm("\\tcall\\t_helper"); return v; }')
        assert "_helper:" in code

    def test_unrelated_underscore_names_are_ignored(self):
        unit = parse('int main(void){ asm("\\tcall\\t_nosuchthing"); return 0; }')
        analyzer = CallGraphAnalyzer()
        analyzer.build_call_graph(unit)
        assert analyzer.address_taken == set()


class TestPeepholeGuard:
    """The peephole optimizer must not touch hand-written assembly."""

    ASM = "\n".join([
        "\t.z80",
        "\tcseg",
        "_delay:",
        ASM_BEGIN_MARKER,
        "\tld b,10",
        "dl_loop:",
        "\tpush bc",
        "\tpop bc",
        "\tdjnz dl_loop",
        ASM_END_MARKER,
        "\tret",
        "\tend",
    ])

    def optimize(self, text):
        from upeepz80 import PeepholeOptimizer
        return _optimize_outside_asm(PeepholeOptimizer(), text)

    def test_unguarded_optimizer_would_rewrite_the_block(self):
        """Without the guard the deliberate push/pop pair is deleted."""
        from upeepz80 import PeepholeOptimizer
        assert "push bc" not in PeepholeOptimizer().optimize(self.ASM)

    def test_guarded_optimizer_leaves_the_block_alone(self):
        result = self.optimize(self.ASM)
        assert "\tpush bc" in result
        assert "\tpop bc" in result

    def test_markers_survive_optimization(self):
        result = self.optimize(self.ASM)
        assert ASM_BEGIN_MARKER in result
        assert ASM_END_MARKER in result

    def test_code_outside_the_block_is_still_optimized(self):
        text = "\n".join([
            "\t.z80",
            "\tcseg",
            "_f:",
            "\tpush\tBC",
            "\tpop\tBC",
            ASM_BEGIN_MARKER,
            "\tpush bc",
            "\tpop bc",
            ASM_END_MARKER,
            "\tret",
            "\tend",
        ])
        result = self.optimize(text)
        assert "\tpush\tBC" not in result, "outside code was not optimized"
        assert "\tpush bc" in result, "inside code was rewritten"

    def test_text_without_asm_is_unchanged_by_the_guard(self):
        from upeepz80 import PeepholeOptimizer
        text = "\n".join(["\t.z80", "\tcseg", "_f:", "\tpush\tBC",
                          "\tpop\tBC", "\tret", "\tend"])
        assert self.optimize(text) == PeepholeOptimizer().optimize(text)


class TestAsmDceGuard:
    """The assembly DCE must not split, drop or strip an asm region."""

    def test_file_scope_region_survives_a_dead_neighbour(self):
        asm = "\n".join([
            "\t.z80", "\tcseg",
            "_dead:", "\tld\tHL,0", "\tret", "",
            ASM_BEGIN_FILE_MARKER, "_tbl:\tdw\t11,22,33", ASM_END_MARKER, "",
            "_main:", "\tld\tHL,(_tbl)", "\tret", "\tend",
        ])
        result = eliminate_dead_code(asm, entry_points={"_main"})
        assert "_dead:" not in result
        assert "_tbl:\tdw\t11,22,33" in result

    def test_file_scope_region_does_not_resurrect_the_next_function(self):
        asm = "\n".join([
            "\t.z80", "\tcseg",
            ASM_BEGIN_FILE_MARKER, "\tnop", ASM_END_MARKER, "",
            "_dead:", "\tld\tHL,0", "\tret", "",
            "_main:", "\tret", "\tend",
        ])
        result = eliminate_dead_code(asm, entry_points={"_main"})
        assert "_dead:" not in result

    def test_in_function_region_dies_with_its_function(self):
        """A dead function's asm must not be left stranded between live ones."""
        asm = "\n".join([
            "\t.z80", "\tcseg",
            "_dead:", "\tpush\tIX",
            ASM_BEGIN_MARKER, "\tld hl,1234", "strand:", "\tnop", ASM_END_MARKER,
            "@dead_ret:", "\tret", "",
            "_main:", "\tret", "\tend",
        ])
        result = eliminate_dead_code(asm, entry_points={"_main"})
        assert "strand:" not in result
        assert "ld hl,1234" not in result

    def test_label_inside_a_region_is_not_dropped(self):
        asm = "\n".join([
            "\t.z80", "\tcseg", "\tPUBLIC\t_f",
            "_f:", "\tpush\tIX",
            ASM_BEGIN_MARKER, "\tld hl,jtarget", "\tjp (hl)", "jtarget:",
            "\tnop", ASM_END_MARKER,
            "@f_ret:", "\tpop\tIX", "\tret", "",
            "_main:", "\tcall\t_f", "\tret", "\tend",
        ])
        result = eliminate_dead_code(asm, entry_points={"_main"})
        assert "jtarget:" in result

    def test_epilogue_after_a_computed_jump_survives(self):
        """`jp (hl)` reads as an unconditional terminator; the tail must stay."""
        asm = "\n".join([
            "\t.z80", "\tcseg", "\tPUBLIC\t_f",
            "_f:", "\tpush\tIX",
            ASM_BEGIN_MARKER, "\tld hl,jtarget", "\tjp (hl)", "jtarget:",
            "\tnop", ASM_END_MARKER,
            "@f_ret:", "\tpop\tIX", "\tret", "",
            "_main:", "\tcall\t_f", "\tret", "\tend",
        ])
        result = eliminate_dead_code(asm, entry_points={"_main"})
        assert "@f_ret:" in result

    def test_block_reached_only_from_asm_survives(self):
        asm = "\n".join([
            "\t.z80", "\tcseg", "\tPUBLIC\t_f",
            "_f:",
            ASM_BEGIN_MARKER, "\tcall\tKHELP", ASM_END_MARKER,
            "\tret", "",
            "KHELP:", "\tld\tHL,1111", "\tret", "",
            "_main:", "\tcall\t_f", "\tret", "\tend",
        ])
        result = eliminate_dead_code(asm, entry_points={"_main"})
        assert "KHELP:" in result

    def test_region_directives_are_not_hoisted_into_the_header(self):
        """A PUBLIC written by the user stays where the user put it."""
        asm = "\n".join([
            "\t.z80", "\tcseg",
            ASM_BEGIN_FILE_MARKER, "\tPUBLIC\t_tbl", "_tbl:\tdw\t1", ASM_END_MARKER,
            "", "_main:", "\tret", "\tend",
        ])
        result = eliminate_dead_code(asm, entry_points={"_main"})
        lines = result.splitlines()
        public_at = next(i for i, l in enumerate(lines)
                         if l.strip().upper().startswith("PUBLIC\t_TBL"))
        begin_at = next(i for i, l in enumerate(lines) if is_asm_begin(l))
        assert public_at > begin_at

    def test_marker_helpers(self):
        assert is_asm_begin(ASM_BEGIN_MARKER)
        assert is_asm_begin("  " + ASM_BEGIN_FILE_MARKER)
        assert not is_asm_begin(ASM_END_MARKER)
        assert is_asm_end("\t" + ASM_END_MARKER)
        assert not is_asm_end(ASM_BEGIN_MARKER)


class TestAsmThroughTheDriver:
    """The whole pipeline: preprocessor, optimizer, DCE, peephole."""

    SOURCE = """
#include <stdio.h>
int marker;
void f(void) { asm("ld hl,1234\\n\\tld (_marker),hl"); }
int main(void) { f(); printf("marker=%d\\n", marker); return 0; }
"""

    def test_default_options(self, tmp_path):
        code = compile_source(tmp_path, self.SOURCE)
        assert "ld hl,1234" in code

    def test_no_optimize(self, tmp_path):
        code = compile_source(tmp_path, self.SOURCE, "--no-optimize")
        assert "ld hl,1234" in code

    def test_no_asm_dce(self, tmp_path):
        code = compile_source(tmp_path, self.SOURCE, "--no-asm-dce")
        assert "ld hl,1234" in code

    def test_separate_compilation(self, tmp_path):
        code = compile_source(tmp_path, self.SOURCE, "--no-whole-program")
        assert "ld hl,1234" in code

    def test_extended_asm_is_a_clean_error(self, tmp_path):
        c_file = tmp_path / "ext.c"
        c_file.write_text('int main(void){ int x=1; asm("ld hl,%0" : : "r"(x)); return x; }')
        result = run_compiler(str(c_file), "-o", str(tmp_path / "ext.mac"))
        assert result.returncode == 1
        assert "uc80: error:" in result.stderr
        assert "extended asm" in result.stderr
        assert "internal error" not in result.stderr
        assert "Traceback" not in result.stderr

    def test_asm_goto_is_a_clean_error(self, tmp_path):
        c_file = tmp_path / "gotoa.c"
        c_file.write_text('int main(void){ asm goto("jp %l0" : : : : lab); lab: return 0; }')
        result = run_compiler(str(c_file), "-o", str(tmp_path / "gotoa.mac"))
        assert result.returncode == 1
        assert "uc80: error:" in result.stderr
        assert "asm goto" in result.stderr
        assert "internal error" not in result.stderr
        assert "Traceback" not in result.stderr


def _toolchain_available():
    """True when um80/ul80/cpmemu and the built libraries are all present."""
    return (shutil.which("um80") and shutil.which("ul80")
            and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestAsmEndToEnd:
    """Assemble, link and run: the emitted text alone proves nothing."""

    def build_and_run(self, tmp_path, source, *extra_args, name="prog"):
        code = compile_source(tmp_path, source, *extra_args, name=name)
        mac = tmp_path / f"{name}.mac"
        mac.write_text(code)
        rel = tmp_path / f"{name}.rel"
        com = tmp_path / f"{name}.com"
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        link = [str(rel)]
        if "--no-whole-program" in extra_args:
            link.insert(0, str(LIB_DIR / "crt0.rel"))
        subprocess.run(["ul80", *link, str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             text=True, timeout=30, env=env)
        return run.stdout

    MARKER_C = """
#include <stdio.h>
int marker;
void f(void) { asm("ld hl,1234\\n\\tld (_marker),hl"); }
int main(void) { f(); printf("marker=%d\\n", marker); return 0; }
"""

    def test_reported_bug(self, tmp_path):
        assert "marker=1234" in self.build_and_run(tmp_path, self.MARKER_C)

    def test_reported_bug_no_optimize(self, tmp_path):
        assert "marker=1234" in self.build_and_run(tmp_path, self.MARKER_C,
                                                   "--no-optimize")

    def test_reported_bug_no_asm_dce(self, tmp_path):
        assert "marker=1234" in self.build_and_run(tmp_path, self.MARKER_C,
                                                   "--no-asm-dce")

    def test_reported_bug_separate_compilation(self, tmp_path):
        assert "marker=1234" in self.build_and_run(tmp_path, self.MARKER_C,
                                                   "--no-whole-program")

    def test_file_scope_data_and_code(self, tmp_path):
        source = """
#include <stdio.h>
asm("BDOS\\tequ\\t5\\n"
    "\\tPUBLIC\\t_tbl\\n"
    "_tbl:\\tdw\\t11,22,33\\n"
    "\\tPUBLIC\\t_dbg\\n"
    "_dbg:\\n"
    "\\tret\\n");
extern int tbl[];
extern void dbg(void);
int marker;
int main(void) {
  dbg();
  asm("ld hl,7\\n\\tld (_marker),hl");
  printf("%d %d %d %d\\n", tbl[0], tbl[1], tbl[2], marker);
  return 0;
}
"""
        assert "11 22 33 7" in self.build_and_run(tmp_path, source)

    def test_every_asm_spelling(self, tmp_path):
        source = """
#include <stdio.h>
int m1, m2, m3, m4, m5;
int main(void) {
  asm("\\tld hl,1\\n\\tld (_m1),hl");
  __asm__("\\tld hl,2\\n\\tld (_m2),hl");
  asm volatile("\\tld hl,3\\n\\tld (_m3),hl");
  __asm__ __volatile__("\\tld hl,4\\n\\tld (_m4),hl");
  asm("\\tld hl,5\\n" "\\tld (_m5),hl\\n");
  printf("%d %d %d %d %d\\n", m1, m2, m3, m4, m5);
  return 0;
}
"""
        assert "1 2 3 4 5" in self.build_and_run(tmp_path, source)

    def test_labels_survive_dead_code_elimination(self, tmp_path):
        """A computed jump inside asm: its target and the epilogue must live."""
        source = """
#include <stdio.h>
int marker;
void jumper(void) {
  asm("\\tld hl,jt_ok\\n"
      "\\tjp (hl)\\n"
      "jt_bad:\\n"
      "\\tld hl,111\\n"
      "\\tld (_marker),hl\\n"
      "\\tret\\n"
      "jt_ok:\\n"
      "\\tld hl,222\\n"
      "\\tld (_marker),hl\\n");
}
int main(void) { jumper(); printf("marker=%d\\n", marker); return 0; }
"""
        assert "marker=222" in self.build_and_run(tmp_path, source)

    def test_function_called_only_from_asm(self, tmp_path):
        source = """
#include <stdio.h>
int v;
static void helper(void) { v = 99; }
int main(void) { asm("\\tcall\\t_helper"); printf("v=%d\\n", v); return 0; }
"""
        assert "v=99" in self.build_and_run(tmp_path, source)

    def test_data_referenced_only_from_asm(self, tmp_path):
        """Whole-program DCE keeps data whose only reference is in asm."""
        source = """
#include <stdio.h>
static int gv = 77;
int out;
int main(void) {
  asm("\\tld hl,(_gv)\\n\\tld (_out),hl");
  printf("%d\\n", out);
  return 0;
}
"""
        assert "77" in self.build_and_run(tmp_path, source)

    def test_own_segment_directives(self, tmp_path):
        """A block may switch segment as long as it switches back."""
        source = """
#include <stdio.h>
asm("\\tdseg\\n_mybuf:\\tdb\\t65,66,0\\n\\tcseg\\n\\tPUBLIC\\t_mybuf\\n");
extern char mybuf[];
int main(void) { printf("[%s]\\n", mybuf); return 0; }
"""
        assert "[AB]" in self.build_and_run(tmp_path, source)

    def test_delay_loop_is_not_optimized_away(self, tmp_path):
        source = """
#include <stdio.h>
void delay(void) {
  asm("\\tld b,10\\n"
      "dl_loop:\\n"
      "\\tpush bc\\n"
      "\\tpop bc\\n"
      "\\tdjnz dl_loop\\n");
}
int main(void) { delay(); puts("done"); return 0; }
"""
        code = compile_source(tmp_path, source, name="delay")
        assert "\tpush bc" in code
        assert "\tpop bc" in code
        assert "done" in self.build_and_run(tmp_path, source, name="delay2")
