"""Tests for hand-written assembly appended with `uc80 prog.c helper.mac`.

The appended assembly used to be emitted at the very end of the module, i.e.
after the `common //` (BSS) directive that the code generator emits last.
crt0 zeroes the whole COMMON region before main() runs, so a hand-written
routine placed there executed as zeroed memory.  It must be spliced in
*before* the COMMON directive: um80 cannot leave a COMMON block, so simply
re-establishing CSEG afterwards does not work.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uc80.asm_dce import eliminate_dead_code
from uc80.main import (
    _filter_hand_written_asm,
    _is_end_directive,
    _tail_insert_index,
)

LIB_DIR = Path(__file__).resolve().parent.parent / "src" / "uc80" / "lib"
CPMEMU = Path(__file__).resolve().parent.parent.parent / "cpmemu" / "src" / "cpmemu"

# A C program with BSS (the static array is uninitialised, so it lands in
# COMMON) that calls a routine supplied by hand-written assembly.
BSS_C = """
#include <stdio.h>
extern unsigned int kconst(void);
static int big[64];
int main(void) { big[0] = 1; printf("kconst=%u\\n", kconst()); return 0; }
"""

# The same program without any BSS - this case accidentally worked before the
# fix, because the appended code inherited DSEG instead of COMMON.
NO_BSS_C = """
#include <stdio.h>
extern unsigned int kconst(void);
int main(void) { printf("kconst=%u\\n", kconst()); return 0; }
"""

HELPER_MAC = """\t.z80
\tCSEG
\tPUBLIC\t_kconst
_kconst:
\tLD\tHL,4660
\tRET
\tEND
"""

# Hand-written assembly with its own DSEG data and a second CSEG switch.
DSEG_MAC = """\t.z80
\tCSEG
\tPUBLIC\t_kmsg
_kmsg:
\tLD\tHL,KTEXT
\tRET
\tDSEG
KTEXT:\tDB\t'from-asm',0
\tCSEG
\tPUBLIC\t_ktwo
_ktwo:
\tLD\tHL,2222
\tRET
\tEND
"""

# _ka reaches KHELP, which is not PUBLIC and must survive DCE.
MAC_A = """\t.z80
\tCSEG
\tPUBLIC\t_ka
_ka:
\tCALL\tKHELP
\tRET
KHELP:
\tLD\tHL,1111
\tRET
\tEND
"""

MAC_B = """\t.z80
\tCSEG
\tPUBLIC\t_kb
_kb:
\tLD\tHL,2222
\tRET
\tEND
"""


def run_compiler(*args):
    """Run the uc80 compiler with given arguments."""
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", *args],
        capture_output=True,
        text=True
    )


def index_of(lines, predicate):
    """Index of the first line satisfying `predicate`, or -1."""
    for i, line in enumerate(lines):
        if predicate(line):
            return i
    return -1


def common_index(code):
    """Index of the `common //` (BSS) directive, or -1 if the module has none."""
    lines = code.splitlines()
    return index_of(lines, lambda l: l.strip().upper().startswith("COMMON"))


def label_index(code, label):
    """Index of the line defining `label`, or -1."""
    lines = code.splitlines()
    return index_of(lines, lambda l: l.strip().startswith(label + ":"))


def compile_with_mac(tmp_path, c_source, mac_sources, *extra_args, name="prog"):
    """Compile C source plus hand-written .mac files; return the output text."""
    c_file = tmp_path / f"{name}.c"
    c_file.write_text(c_source)
    inputs = [str(c_file)]
    for i, mac_source in enumerate(mac_sources):
        mac_file = tmp_path / f"{name}_helper{i}.mac"
        mac_file.write_text(mac_source)
        inputs.append(str(mac_file))
    output = tmp_path / f"{name}.mac"
    result = run_compiler(*inputs, "-o", str(output), *extra_args)
    assert result.returncode == 0, f"Compiler failed: {result.stderr}"
    return output.read_text()


class TestMacSplicedBeforeBss:
    """Appended assembly must not land in the COMMON (BSS) block."""

    def test_appended_code_precedes_common(self, tmp_path):
        """The reported bug: _kconst used to sit inside COMMON."""
        code = compile_with_mac(tmp_path, BSS_C, [HELPER_MAC])
        bss = common_index(code)
        assert bss >= 0, "test program should have a BSS block"
        kconst = label_index(code, "_kconst")
        assert kconst >= 0, "hand-written routine was dropped"
        assert kconst < bss, "hand-written routine landed in BSS"

    def test_appended_code_precedes_common_without_dce(self, tmp_path):
        """--no-asm-dce does not reorder segments, so the splice must be right."""
        code = compile_with_mac(tmp_path, BSS_C, [HELPER_MAC], "--no-asm-dce")
        assert label_index(code, "_kconst") < common_index(code)

    def test_appended_code_precedes_common_without_optimizer(self, tmp_path):
        code = compile_with_mac(tmp_path, BSS_C, [HELPER_MAC], "--no-optimize")
        assert label_index(code, "_kconst") < common_index(code)

    def test_appended_code_precedes_common_separate_compilation(self, tmp_path):
        code = compile_with_mac(tmp_path, BSS_C, [HELPER_MAC], "--no-whole-program")
        assert label_index(code, "_kconst") < common_index(code)

    def test_no_bss_program_still_works(self, tmp_path):
        """The case that accidentally worked before must keep working."""
        code = compile_with_mac(tmp_path, NO_BSS_C, [HELPER_MAC])
        assert common_index(code) == -1, "program should have no BSS block"
        assert label_index(code, "_kconst") >= 0
        assert code.rstrip().upper().endswith("END")

    def test_single_end_directive(self, tmp_path):
        """The .mac's END is dropped; exactly one END terminates the module."""
        code = compile_with_mac(tmp_path, BSS_C, [HELPER_MAC])
        ends = [l for l in code.splitlines() if _is_end_directive(l)]
        assert len(ends) == 1


class TestMacSegmentDirectives:
    """A .mac controls its own segments; uc80 must not strip them."""

    def test_mac_dseg_survives(self, tmp_path):
        code = compile_with_mac(tmp_path, BSS_C.replace("kconst", "ktwo"), [DSEG_MAC])
        assert "KTEXT" in code, "hand-written DSEG data was dropped"
        assert label_index(code, "_kmsg") >= 0
        assert label_index(code, "_ktwo") >= 0
        assert label_index(code, "KTEXT") != label_index(code, "_kmsg")

    def test_mac_dseg_data_not_in_bss(self, tmp_path):
        """Hand-written data belongs in DSEG, not in the zeroed COMMON block."""
        code = compile_with_mac(tmp_path, BSS_C.replace("kconst", "ktwo"),
                                [DSEG_MAC], "--no-asm-dce")
        bss = common_index(code)
        assert bss >= 0
        assert label_index(code, "KTEXT") < bss

    def test_mac_block_starts_in_cseg(self, tmp_path):
        """The splice point is not in a known segment, so CSEG is re-established."""
        code = compile_with_mac(tmp_path, BSS_C, [HELPER_MAC], "--no-asm-dce")
        lines = code.splitlines()
        banner = index_of(lines, lambda l: l.strip() == "; Included assembly file")
        assert banner >= 0
        assert lines[banner + 1].strip().upper() == "CSEG"


class TestMultipleMacFiles:
    """Every .mac on the command line is appended, in order."""

    def test_two_mac_files(self, tmp_path):
        c_source = """
#include <stdio.h>
extern unsigned int ka(void);
extern unsigned int kb(void);
static int big[64];
int main(void) { big[0] = 1; printf("%u %u\\n", ka(), kb()); return 0; }
"""
        code = compile_with_mac(tmp_path, c_source, [MAC_A, MAC_B])
        bss = common_index(code)
        assert bss >= 0
        assert 0 <= label_index(code, "_ka") < bss
        assert 0 <= label_index(code, "_kb") < bss
        assert label_index(code, "_ka") < label_index(code, "_kb")


class TestMacLabelsSurviveDce:
    """uc80 cannot infer how hand-written assembly reaches its own labels."""

    C_SOURCE = """
#include <stdio.h>
extern unsigned int ka(void);
static int big[64];
int main(void) { big[0] = 1; printf("%u\\n", ka()); return 0; }
"""

    def test_private_label_survives_whole_program(self, tmp_path):
        code = compile_with_mac(tmp_path, self.C_SOURCE, [MAC_A])
        assert "KHELP" in code, "private hand-written label was eliminated"

    def test_private_label_survives_separate_compilation(self, tmp_path):
        code = compile_with_mac(tmp_path, self.C_SOURCE, [MAC_A], "--no-whole-program")
        assert "KHELP" in code

    def test_public_label_survives(self, tmp_path):
        code = compile_with_mac(tmp_path, self.C_SOURCE, [MAC_A])
        assert label_index(code, "_ka") >= 0
        assert "PUBLIC" in code.upper()


class TestTailInsertIndex:
    """Unit tests for the splice-point helper."""

    def test_finds_common_directive(self):
        lines = ["\tCSEG", "_main:", "\tRET", "\tcommon\t//", "_big:", "\tds\t4", "\tend"]
        assert _tail_insert_index(lines) == 3

    def test_keeps_bss_banner_with_directive(self):
        lines = ["\tCSEG", "_main:", "\tRET",
                 "; BSS - uninitialized static storage (zeroed by crt0)",
                 "\tcommon\t//", "\tend"]
        assert _tail_insert_index(lines) == 3

    def test_falls_back_to_end_directive(self):
        lines = ["\tCSEG", "_main:", "\tRET", "\tend"]
        assert _tail_insert_index(lines) == 3

    def test_end_with_start_address(self):
        lines = ["\tCSEG", "_main:", "\tRET", "\tEND\t_main"]
        assert _tail_insert_index(lines) == 3

    def test_no_common_and_no_end(self):
        lines = ["\tCSEG", "_main:", "\tRET"]
        assert _tail_insert_index(lines) == 3

    def test_common_wins_over_end(self):
        lines = ["\tcommon\t//", "_big:", "\tds\t2", "\tend"]
        assert _tail_insert_index(lines) == 0

    def test_label_named_commonbuf_is_not_a_directive(self):
        lines = ["\tCSEG", "COMMONBUF:", "\tds\t2", "\tend"]
        assert _tail_insert_index(lines) == 3


class TestIsEndDirective:
    def test_plain_end(self):
        assert _is_end_directive("\tend")
        assert _is_end_directive("END")

    def test_end_with_operand(self):
        assert _is_end_directive("\tEND\t_main")
        assert _is_end_directive("\tend start")

    def test_not_an_end(self):
        assert not _is_end_directive("_endgame:")
        assert not _is_end_directive("\tENDM")


class TestFilterHandWrittenAsm:
    def test_segment_directives_are_kept(self):
        lines, _, _ = _filter_hand_written_asm(DSEG_MAC)
        text = "\n".join(lines).upper()
        assert "CSEG" in text
        assert "DSEG" in text

    def test_z80_and_end_are_dropped(self):
        lines, _, _ = _filter_hand_written_asm(HELPER_MAC)
        text = "\n".join(lines)
        assert ".z80" not in text.lower()
        assert not any(_is_end_directive(l) for l in lines)

    def test_publics_are_collected(self):
        _, publics, _ = _filter_hand_written_asm(DSEG_MAC)
        assert publics == {"_kmsg", "_ktwo"}

    def test_all_labels_are_collected(self):
        _, _, labels = _filter_hand_written_asm(MAC_A)
        assert labels == {"_ka", "KHELP"}

    def test_multiple_publics_on_one_line(self):
        _, publics, _ = _filter_hand_written_asm(
            "\tPUBLIC\t_one,_two\n_one:\n\tRET\n_two:\n\tRET\n\tEND\n")
        assert publics == {"_one", "_two"}


class TestExtraEntryPoints:
    """asm_dce's extra_entry_points keeps hand-written labels alive."""

    ASM = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tRET

KHELP:
\tLD\tHL,1111
\tRET

\tEND
"""

    def test_label_dead_without_extra_entry_points(self):
        result = eliminate_dead_code(self.ASM)
        assert "KHELP:" not in result

    def test_label_kept_with_extra_entry_points(self):
        result = eliminate_dead_code(self.ASM, extra_entry_points={"KHELP"})
        assert "KHELP:" in result
        assert "_main:" in result

    def test_extra_entry_points_add_to_explicit_entry_points(self):
        result = eliminate_dead_code(self.ASM, entry_points={"_main"},
                                     extra_entry_points={"KHELP"})
        assert "KHELP:" in result

    def test_extra_entry_points_do_not_enable_whole_program_mode(self):
        """Unreferenced PUBLIC data still survives in non-explicit mode."""
        asm = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tRET

KHELP:
\tRET

\tDSEG
\tPUBLIC\t_table
_table:
\tDW\t0

\tEND
"""
        result = eliminate_dead_code(asm, extra_entry_points={"KHELP"})
        assert "_table:" in result, "PUBLIC data must survive in non-explicit mode"
        assert "KHELP:" in result
        # Contrast: explicit entry points do drop unreferenced PUBLIC data.
        explicit = eliminate_dead_code(asm, entry_points={"_main"},
                                       extra_entry_points={"KHELP"})
        assert "_table:" not in explicit

    def test_caller_entry_point_set_is_not_mutated(self):
        entry = {"_main"}
        eliminate_dead_code(self.ASM, entry_points=entry,
                            extra_entry_points={"KHELP"})
        assert entry == {"_main"}


def _toolchain_available():
    """True when um80/ul80/cpmemu and the built libraries are all present."""
    return (shutil.which("um80") and shutil.which("ul80")
            and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestMacInputEndToEnd:
    """Assemble, link and run the result - the .mac layout is not enough."""

    def build_and_run(self, tmp_path, c_source, mac_sources, *extra_args):
        code = compile_with_mac(tmp_path, c_source, mac_sources, *extra_args)
        mac = tmp_path / "prog.mac"
        mac.write_text(code)
        rel = tmp_path / "prog.rel"
        com = tmp_path / "prog.com"
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             text=True, timeout=30, env=env)
        return run.stdout

    def test_helper_returns_its_constant(self, tmp_path):
        out = self.build_and_run(tmp_path, BSS_C, [HELPER_MAC])
        assert "kconst=4660" in out

    def test_helper_returns_its_constant_without_dce(self, tmp_path):
        out = self.build_and_run(tmp_path, BSS_C, [HELPER_MAC], "--no-asm-dce")
        assert "kconst=4660" in out

    def test_no_bss_program(self, tmp_path):
        out = self.build_and_run(tmp_path, NO_BSS_C, [HELPER_MAC])
        assert "kconst=4660" in out

    def test_mac_with_own_dseg(self, tmp_path):
        c_source = """
#include <stdio.h>
extern char *kmsg(void);
extern unsigned int ktwo(void);
static int big[64];
int main(void) { big[0] = 1; printf("%s %u\\n", kmsg(), ktwo()); return 0; }
"""
        out = self.build_and_run(tmp_path, c_source, [DSEG_MAC])
        assert "from-asm 2222" in out

    def test_two_mac_files(self, tmp_path):
        c_source = """
#include <stdio.h>
extern unsigned int ka(void);
extern unsigned int kb(void);
static int big[64];
int main(void) { big[0] = 1; printf("%u %u\\n", ka(), kb()); return 0; }
"""
        out = self.build_and_run(tmp_path, c_source, [MAC_A, MAC_B])
        assert "1111 2222" in out
