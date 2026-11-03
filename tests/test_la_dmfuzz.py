"""L4: random programs of x / y and x % y pairs (bench/tools/faml/dmfuzz.py)
give the same checksum with uc80 and with the host compiler."""

import subprocess
import sys

from ._faml import needs_toolchain
from .test_struct_return_abi import REPO

pytestmark = needs_toolchain


def test_dmfuzz_small_run(tmp_path):
    r = subprocess.run([sys.executable, "bench/tools/faml/dmfuzz.py",
                        "--count", "16", "--seed", "1000", "-j", "4",
                        "--work", str(tmp_path)],
                       capture_output=True, text=True, cwd=str(REPO),
                       timeout=900)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "'PASS': 16" in r.stdout
