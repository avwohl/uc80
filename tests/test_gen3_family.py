"""The gen3 family: generated programs, 8 option sets each.

tests/gen3/*.c are 75 programs made by tests/gen3/generate.py (5 value types
x 3 pointer kinds x 5 bodies).  Each program is built with uc80 in 8 option
sets and run under cpmemu.  The test fails when two option sets give
different output, or when a build fails or times out.  A difference from the
host compiler is only reported, as a pytest warning: the programs use
implementation-defined conversions.

A plain run takes the quick subset: one program per type and pointer kind
(15 programs, 120 builds).  GEN3_FULL=1 runs all 75 programs
(600 builds, about 3 minutes with -n 6).
bench/gen3_harness.py does the full run and prints RLDIFF and hostdiff lines.
"""
import importlib.util
import os
import sys
import warnings
from pathlib import Path

import pytest

from .test_register_cc import needs_run

REPO = Path(__file__).resolve().parent.parent
GEN3 = REPO / "tests" / "gen3"

_spec = importlib.util.spec_from_file_location(
    "gen3_harness", REPO / "bench" / "gen3_harness.py")
H = importlib.util.module_from_spec(_spec)
sys.modules["gen3_harness"] = H
_spec.loader.exec_module(H)

ALL = sorted(p.name for p in GEN3.glob("h_*.c"))
FULL = os.environ.get("GEN3_FULL") == "1"
# Quick subset: the body m1..m5 rotates over the 15 type/pointer pairs.
_PAIRS = sorted({n.rsplit("_m", 1)[0] for n in ALL})
QUICK = [f"{p}_m{i % 5 + 1}.c" for i, p in enumerate(_PAIRS)]
NAMES = ALL if FULL else QUICK


def test_gen3_programs_present():
    assert len(ALL) == 75
    assert set(QUICK) <= set(ALL) and len(QUICK) == 15


@needs_run
@pytest.mark.parametrize("name", NAMES)
def test_gen3_option_sets_agree(name, tmp_path):
    src = GEN3 / name
    lib = H.lib_dir(tmp_path)
    out = [H.build_run(src, flags, tmp_path / f"v{i}", lib)
           for i, flags in enumerate(H.VARS)]
    ref = out[H.REF].strip()
    assert not ref.startswith(("BUILD-FAIL", "TIMEOUT")), ref
    assert ref, "the reference build printed nothing"
    for flags, got in zip(H.VARS, out):
        assert got.strip() == ref, f"RLDIFF {name} {flags}: {got.strip()!r} vs {ref!r}"
    host = H.host_run(src, tmp_path / "host")
    if host is not None and host.strip() != ref:
        warnings.warn(f"hostdiff(info) {name}")
