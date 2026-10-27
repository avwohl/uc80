"""Seeds of bench/ccfuzz.py that once gave a wrong answer.

Seeds 10140, 10657 and 11636 with --no-ast-optimize and seed 40035 with -O0
printed a checksum that differed from the host compiler.  Each program is
generated, built with the flags, run under cpmemu and compared with the host
output, which is what ccfuzz does for every seed.
"""

import importlib.util
import shutil
import types
from pathlib import Path

import pytest

from tests.test_struct_return_abi import CPMEMU, pytestmark  # noqa: F401

REPO = Path(__file__).resolve().parent.parent

needs_host_cc = pytest.mark.skipif(shutil.which("cc") is None,
                                   reason="no host C compiler")


def _ccfuzz():
    spec = importlib.util.spec_from_file_location(
        "ccfuzz_under_test", REPO / "bench" / "ccfuzz.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CPMEMU = str(CPMEMU)
    return mod


@needs_host_cc
@pytest.mark.parametrize("seed,flags", [
    (10140, "--no-ast-optimize"),
    (10657, "--no-ast-optimize"),
    (11636, "--no-ast-optimize"),
    (40035, "-O0"),
    (90258, "--no-ast-optimize"),
    (80548, "-O0"),
    (80548, ""),
])
def test_ccfuzz_seed_matches_the_host(tmp_path, seed, flags):
    mod = _ccfuzz()
    opts = types.SimpleNamespace(work=str(tmp_path), ll=False, flags=flags,
                                 exact=True, keep=None, keep_work=False)
    _seed, status, note = mod.one(seed, opts)
    assert status == "PASS", (status, note)
