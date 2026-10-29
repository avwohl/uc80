"""Compile time stays small for long expressions and for large programs.

Three causes of very long compile times are covered:

  - the type queries of the code generator (_get_expr_type and the
    _is_* helpers) walked both operands again at every level of a
    left-deep ``a[i] + a[i+1] + ...``: 20, 40 and 80 terms took 2, 16
    and 224 seconds;
  - the dead-data pass of asm_dce matched a regular expression for every
    data label against every line of every live function;
  - bench/run.py reported a program whose .com does not fit in 64 KB
    as HANG, because ticks refuses the file.

The time limits are many times the measured time, so a loaded machine
does not fail them, and far below the time before the repair.
"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

from uc80.asm_dce import eliminate_dead_code
from ._clib import build_and_run, skip_without_tools

REPO = Path(__file__).resolve().parent.parent


def _sum_source(n):
    terms = "+".join(f"a[i+{k}]" for k in range(n))
    return (f"#include <stdio.h>\nint a[{n + 8}];\n"
            f"int f(int i) {{ return {terms}; }}\n"
            "int main(void) {\n"
            f"  int k; for (k = 0; k < {n + 8}; k++) a[k] = k % 7;\n"
            '  printf("%d\\n", f(2));\n  return 0;\n}\n')


@skip_without_tools
def test_long_sum_compiles_fast_and_runs(tmp_path):
    n = 100
    t0 = time.monotonic()
    out = build_and_run(tmp_path, _sum_source(n))
    elapsed = time.monotonic() - t0
    assert out.strip() == str(sum((k + 2) % 7 for k in range(n)))
    # 100 terms took about 10 minutes before the repair, 3 seconds after.
    assert elapsed < 90, elapsed


def test_long_sum_mac_is_the_same_for_each_depth(tmp_path):
    """The code of a long sum is a chain of the same two steps: the
    memo of the type queries changes the time, not the code."""
    from uc80.main import main
    sizes = []
    for n in (6, 12):
        c = tmp_path / f"s{n}.c"
        c.write_text(_sum_source(n))
        mac = tmp_path / f"s{n}.mac"
        old = sys.argv
        sys.argv = ["uc80", str(c), "-o", str(mac)]
        try:
            assert main() in (0, None)
        finally:
            sys.argv = old
        text = mac.read_text()
        sizes.append(text.count("\n"))
    assert sizes[0] < sizes[1]


def _dce_program(nlabels, nlines):
    """A main that reads every ``nlabels``-th data object, many lines."""
    out = ["\t.Z80", "\tCSEG", "\tPUBLIC\t_main", "_main:"]
    for i in range(nlines):
        out.append(f"\tLD\tHL,(_d{i % nlabels})")
        out.append("\tCALL\t_f")
    out += ["\tRET", "_f:", "\tRET", "\tDSEG"]
    for i in range(nlabels):
        nxt = f"_d{i + 1}" if i % 50 != 49 and i + 1 < nlabels else "0"
        out.append(f"_d{i}:\tDW\t{nxt}")
    out.append("_dead:\tDW\t7")
    out.append("\tEND")
    return "\n".join(out) + "\n"


def test_dead_data_pass_is_not_quadratic():
    nl = 3000
    t0 = time.monotonic()
    out = eliminate_dead_code(_dce_program(nl, 3000), entry_points={"_main"})
    elapsed = time.monotonic() - t0
    assert "_d0:" in out and f"_d{nl - 1}:" in out
    assert "_dead:" not in out
    assert elapsed < 5, elapsed        # 9 million patterns: 30 s and more before


class TestDeadDataLabelBoundaries:
    """The label match keeps its old rule: a label is a whole name, and a
    ``?`` or ``@`` in front belongs to the name."""

    ASM = """
\t.Z80
\tCSEG
\tPUBLIC\t_main
_main:
\tLD\tHL,_a+2
\tLD\tDE,(@?x)  ; _commented
\tLD\tBC,_abc
\tRET
\tDSEG
_a:\tDW\t_chain
_ab:\tDW\t1
_abc:\tDW\t2
_chain:\tDW\t_tail
_tail:\tDW\t3
@?x:\tDW\t4
?x:\tDW\t5
_commented:\tDW\t6
_unused:\tDW\t7
\tEND
"""

    def test_whole_names_prefixes_comments_and_chains(self):
        out = eliminate_dead_code(self.ASM, entry_points={"_main"})
        for kept in ("_a:", "_abc:", "_chain:", "_tail:", "@?x:"):
            assert kept in out, kept
        for gone in ("_ab:", "?x:\tDW\t5", "_commented:", "_unused:"):
            assert gone not in out, gone


def test_too_big_com_is_not_a_hang():
    spec = importlib.util.spec_from_file_location(
        "bench_run_for_test", REPO / "bench" / "run.py")
    sys.path.insert(0, str(REPO / "bench"))
    try:
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    assert mod.fits_address_space(65280)
    assert not mod.fits_address_space(65281)
    assert not mod.fits_address_space(69376)       # empire-trefonas
    # rogue36-size: ul80 wrapped __BSS_START to 38,328 for a 82,048-byte
    # .com, and the row said FITS.
    assert mod.image_end(38328 - 0x100, 0, 82048) > 0xE400
    assert mod.image_end(1000, 200, 900) == 0x100 + 1200
