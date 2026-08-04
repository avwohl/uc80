"""One printf dispatch table per program, whoever the linker picks.

Every unit that calls printf emits its own ``PUBLIC __printf_format_table``,
because the codegen table has to beat the 16-bit-int default that libc's
lc_printf_all module supplies.  L80 keeps the FIRST definition of a
multiply-defined global and links on regardless, so under
``--no-whole-program`` the units must not disagree about what is in it.

ac009ca made them disagree: its per-specifier float filter registered only
the conversions each unit's own literal format strings used, so a %f-only
unit and a %e-only unit emitted different tables and link order decided which
one worked.  A unit calling no printf at all emitted an EMPTY table, which
disabled every conversion in the program when it linked first.

Nothing downstream catches this -- ul80 records a multiply-defined global but
its command line does not report a recorded error when the link succeeds --
so the compiler has to be the one that keeps the tables identical.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

# a.c prints %d and %f; b.c prints %e, %g and %d.  Before the fix each unit
# registered only its own float conversions.
A_C = """#include <stdio.h>
void bfn(void);
int main(void){ printf("A=[%d][%f]|\\n", 7, 1.5); bfn(); return 0; }
"""
B_C = """#include <stdio.h>
void bfn(void){ printf("B=[%e][%g][%d]|\\n", 28.0, 28.0, 3); }
"""
# helper.c calls no printf at all, and so must define no table.
HELPER_C = "int helper(int x){ return x + 1; }\n"
MAIN2_C = """#include <stdio.h>
int helper(int);
int main(void){ printf("h=[%d] f=[%f] e=[%e]|\\n", helper(4), 1.5, 1.5); return 0; }
"""

WIDEN_WARNING = "cannot see the format strings in the other translation units"


def compile_unit(tmp_path, name, source, *extra_args):
    """Compile one translation unit; returns (mac_path, stderr)."""
    c_file = tmp_path / (name + ".c")
    c_file.write_text(source)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac), *extra_args],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return mac, r.stderr


def base_table(mac_path):
    """The base dispatch table's entries, or None when the unit defines none."""
    text = mac_path.read_text()
    m = re.search(r"^__printf_format_table:\n(.*?)^\tdb\t0\t",
                  text, re.S | re.M)
    if m is None:
        return None
    return re.findall(r"db\t'(.)'\n\tdw\t(\S+)", m.group(1))


def table_specs(mac_path):
    entries = base_table(mac_path)
    return None if entries is None else [c for c, _ in entries]


class TestTablesAgreeAcrossUnits:
    """Text-level checks -- fast, and they pin the exact failure mode."""

    def test_units_using_different_float_conversions_agree(self, tmp_path):
        a, _ = compile_unit(tmp_path, "a", A_C, "--no-whole-program")
        b, _ = compile_unit(tmp_path, "b", B_C, "--no-whole-program")
        assert base_table(a) == base_table(b)

    def test_all_six_float_conversions_are_registered(self, tmp_path):
        """A unit printing only %f must still carry %e/%g for its siblings."""
        a, _ = compile_unit(tmp_path, "a", A_C, "--no-whole-program")
        assert set("fFeEgG") <= set(table_specs(a))

    def test_unit_without_printf_defines_no_table(self, tmp_path):
        helper, _ = compile_unit(tmp_path, "helper", HELPER_C,
                                 "--no-whole-program")
        assert base_table(helper) is None
        assert "__printf_format_table" not in helper.read_text()

    def test_explicit_printf_flags_also_agree(self, tmp_path):
        flags = ("--no-whole-program", "--printf", "int", "--printf", "float")
        a, _ = compile_unit(tmp_path, "a", A_C, *flags)
        b, _ = compile_unit(tmp_path, "b", B_C, *flags)
        assert base_table(a) == base_table(b)
        assert set("fFeEgG") <= set(table_specs(a))

    def test_whole_program_still_filters_by_specifier(self, tmp_path):
        """The size optimisation is sound when one unit is the whole program.

        Guards the fix against being applied too widely: %e/%g/%E/%G pull in
        handlers a %f-only program has no use for.
        """
        a, _ = compile_unit(tmp_path, "a", A_C)
        specs = set(table_specs(a))
        assert "f" in specs
        assert not (specs & set("eEgG"))

    def test_whole_program_unit_without_printf_still_emits_a_table(self,
                                                                  tmp_path):
        """Unchanged behaviour; only separate compilation needed narrowing."""
        helper, _ = compile_unit(tmp_path, "helper", HELPER_C)
        assert base_table(helper) == []


class TestWideningIsAnnounced:
    def test_auto_detection_warns(self, tmp_path):
        _, err = compile_unit(tmp_path, "a", A_C, "--no-whole-program")
        assert WIDEN_WARNING in err
        assert "--printf" in err

    def test_explicit_features_are_taken_as_given(self, tmp_path):
        _, err = compile_unit(tmp_path, "a", A_C, "--no-whole-program",
                              "--printf", "int", "--printf", "float")
        assert WIDEN_WARNING not in err

    def test_unit_without_printf_is_quiet(self, tmp_path):
        _, err = compile_unit(tmp_path, "helper", HELPER_C,
                              "--no-whole-program")
        assert WIDEN_WARNING not in err

    def test_whole_program_is_quiet(self, tmp_path):
        _, err = compile_unit(tmp_path, "a", A_C)
        assert WIDEN_WARNING not in err


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists()
            and (LIB_DIR / "crt0.rel").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestLinkOrderOnEmulator:
    """Matching tables is the mechanism; identical output is the promise."""

    def build(self, tmp_path, units, order, name, *extra_args):
        rels = {}
        for unit, source in units.items():
            mac, _ = compile_unit(tmp_path, unit, source,
                                  "--no-whole-program", *extra_args)
            rel = tmp_path / (unit + ".rel")
            subprocess.run(["um80", str(mac), "-o", str(rel)],
                           check=True, capture_output=True, text=True)
            rels[unit] = rel
        com = tmp_path / (name + ".com")
        subprocess.run(["ul80", str(LIB_DIR / "crt0.rel"),
                        *[str(rels[u]) for u in order],
                        str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             text=True, timeout=30, env=env)
        return run.stdout.replace("\r", "")

    EXPECT_AB = "A=[7][1.500000]|\nB=[2.800000e+01][28][3]|\n"

    def test_float_unit_first(self, tmp_path):
        out = self.build(tmp_path, {"a": A_C, "b": B_C}, ["a", "b"], "ab")
        assert out == self.EXPECT_AB

    def test_float_unit_last(self, tmp_path):
        """The same program, linked the other way round."""
        out = self.build(tmp_path, {"a": A_C, "b": B_C}, ["b", "a"], "ba")
        assert out == self.EXPECT_AB

    EXPECT_HM = "h=[5] f=[1.500000] e=[1.500000e+00]|\n"

    def test_printfless_unit_linked_first(self, tmp_path):
        out = self.build(tmp_path, {"helper": HELPER_C, "main2": MAIN2_C},
                         ["helper", "main2"], "hm")
        assert out == self.EXPECT_HM

    def test_printfless_unit_linked_last(self, tmp_path):
        out = self.build(tmp_path, {"helper": HELPER_C, "main2": MAIN2_C},
                         ["main2", "helper"], "mh")
        assert out == self.EXPECT_HM

    def test_explicit_flags_work_in_both_orders(self, tmp_path):
        flags = ("--printf", "int", "--printf", "float")
        first = self.build(tmp_path, {"a": A_C, "b": B_C}, ["a", "b"],
                           "abx", *flags)
        second = self.build(tmp_path, {"a": A_C, "b": B_C}, ["b", "a"],
                            "bax", *flags)
        assert first == second == self.EXPECT_AB
