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


def _compile_to_text(tmp_path, name, source):
    from uc80.main import main
    d = tmp_path / name
    d.mkdir()
    c = d / "prog.c"          # the module name is in the .mac header
    c.write_text(source)
    mac = d / "prog.mac"
    old = sys.argv
    sys.argv = ["uc80", str(c), "-o", str(mac)]
    try:
        assert main() in (0, None)
    finally:
        sys.argv = old
    return mac.read_text()


_MIXED = """
long g = 70000L;
float fl[40];
long long ll[40];
char ch[40];
int a[60];
struct S { int m; long n; } st[40];
int f(int i) {
    return a[i]+a[i+1]*2+(int)fl[i]+ch[i]+st[i].m+(int)st[i].n+(int)ll[i]
         + a[i+2]+a[i+3]+ch[i+1]+(int)(g + ({ char g = 3; g; }));
}
long h(long x, int i) { return x + a[i] + g + ({ long y = x; y + 1; }) + a[i+1]; }
double d(double x, int i) { return x + a[i] + fl[i] + a[i+1] * 2.5 + fl[i+1]; }
int main(void) { return f(1) + (int)h(2L, 3) + (int)d(1.5, 2); }
"""


def test_longer_lived_memos_do_not_change_the_code(tmp_path, monkeypatch):
    """The type-query memo of the code generator lives for a whole
    expression, and the float memo of uc_core for a whole statement.  The
    .mac text is the same as with the memos only for one query."""
    from uc80.codegen import CodeGenerator
    from uc_core.floatness import FloatInfo
    sources = {"mixed": _MIXED, "sum30": _sum_source(30)}
    with_memo = {k: _compile_to_text(tmp_path, "w" + k, v)
                 for k, v in sources.items()}
    monkeypatch.setattr(CodeGenerator, "gen_expr",
                        CodeGenerator._gen_expr_body)
    monkeypatch.setattr(FloatInfo, "maybe_float", FloatInfo._maybe_float)
    for k, v in sources.items():
        assert _compile_to_text(tmp_path, "o" + k, v) == with_memo[k], k
    assert with_memo["mixed"].count("\n") > 50


def test_very_long_sum_is_linear(tmp_path):
    """320 terms of ``a[i+k] + b[k]*2`` took 15 s with the per-call memo
    (quadratic); with the memo for the whole expression, 1.6 s."""
    n = 320
    terms = "+".join(f"(a[i+{k}]+b[{k}]*2)" for k in range(n))
    src = (f"int a[{n + 8}], b[{n + 8}];\n"
           f"int f(int i) {{ return {terms}; }}\n"
           "int main(void) { return f(1); }\n")
    t0 = time.monotonic()
    text = _compile_to_text(tmp_path, "long320", src)
    elapsed = time.monotonic() - t0
    assert text.count("\n") > 1500       # the code of the sum is there
    assert elapsed < 30, elapsed


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
    nl = 800
    t0 = time.monotonic()
    out = eliminate_dead_code(_dce_program(nl, 800), entry_points={"_main"})
    elapsed = time.monotonic() - t0
    assert "_d0:" in out and f"_d{nl - 1}:" in out
    assert "_dead:" not in out
    assert elapsed < 2, elapsed        # 28 s before, 0.01 s after


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
    # the row of a TOO-BIG program holds the sizes and the real image
    assert mod.real_image(38328, 100, 82048) == 82048 - 100
    assert mod.real_image(1000, 200, 900) == 1000
    row = {}

    class _B:
        image, bss = 38328, 100
    mod.too_big_row(row, _B, 82048)
    assert row == {"com": 82048, "bss": 100, "image": 82048 - 100,
                   "status": "TOO-BIG"}
