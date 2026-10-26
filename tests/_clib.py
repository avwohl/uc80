"""Build a C program with the uc80 libraries and run it under cpmemu.

The tests for the C library functions share this helper.  decode_f32 and
encode_f32 convert between a Python float and the 32-bit pattern that a
uc80 float has, so a test can compare with the exact IEEE value.
"""

import math
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

skip_without_tools = pytest.mark.skipif(
    not (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
         and (LIB_DIR / "libc.lib").exists()
         and (LIB_DIR / "runtime.lib").exists()),
    reason="um80/ul80/cpmemu or built libraries not available")


def build_and_run(tmp_path, source, stdin=b"", args=(), raw=False):
    """Compile, link and run SOURCE; return its output without CRs (with them
    when RAW is true)."""
    c_file = tmp_path / "prog.c"
    c_file.write_text(source)
    mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac), *args], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         input=stdin, timeout=120,
                         env=dict(os.environ, PYTHONHASHSEED="0"),
                         cwd=str(tmp_path))
    out = run.stdout.decode("latin-1")
    if not raw:
        out = out.replace("\r", "")
    return "\n".join(ln for ln in out.split("\n")
                     if not ln.startswith(("CPU mode", "Loaded ",
                                           "Program exit", "Note:")))


def decode_f32(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def encode_f32(x):
    """The 32-bit pattern of the float nearest to X (inf when too large)."""
    try:
        return struct.unpack("<I", struct.pack("<f", x))[0]
    except OverflowError:
        return 0x7F800000 if x > 0 else 0xFF800000


def ulp_distance(a, b):
    """Distance in units in the last place between two 32-bit patterns."""
    fa, fb = decode_f32(a), decode_f32(b)
    if math.isnan(fa) or math.isnan(fb):
        return 0 if math.isnan(fa) and math.isnan(fb) else 1 << 30

    def order(x):
        return -(x & 0x7FFFFFFF) if x >> 31 else x
    return abs(order(a) - order(b))
