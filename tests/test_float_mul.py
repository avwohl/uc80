"""__fmul (rt_float.mac) must be a correctly rounded IEEE single multiply.

The old routine truncated the 48-bit mantissa product, so a product could be
wrong by hundreds of units in the last place after a few multiplies (the
mandelbrot and whetstone benchmarks).  The test generates operand bit patterns
with a small LCG on the Z80, multiplies them with the compiled float code, and
prints a checksum for each block of products.  A Python model that rounds the
exact double product to float32 (struct pack, round to nearest even) makes the
same checksums.  A block that disagrees points at the bad products.

Operand exponents keep every product normal: results that would be subnormal
are flushed to zero by the runtime, which is not what is checked here.
"""

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

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
         and (LIB_DIR / "libc.lib").exists()
         and (LIB_DIR / "runtime.lib").exists()),
    reason="um80/ul80/cpmemu or the built libraries are missing")

BLOCK = 500

C_SOURCE = r"""
#include <stdio.h>
typedef union { float f; unsigned long u; } U;
static unsigned long s;
static unsigned long rnd(void) { s = s * 1664525UL + 1013904223UL; return s; }
static unsigned long mk(void) {
    unsigned long m = (rnd() >> 8) & 0x7FFFFFUL;
    unsigned long k = (rnd() >> 16) & 15;
    unsigned long e = 100 + ((rnd() >> 16) & 63);
    unsigned long sg = (rnd() >> 16) & 1;
    if (k == 0) m = 0;
    else if (k == 1) m = 0x7FFFFFUL;
    else if (k == 2) m = 0x400000UL;
    else if (k == 3) m &= 0x7FF000UL;
    else if (k == 4) m |= 0x000FFFUL;
    return (sg << 31) | (e << 23) | m;
}
int main(void) {
    int b, i;
    s = SEED;
    for (b = 0; b < BLOCKS; b++) {
        unsigned long sum = 0;
        for (i = 0; i < BLOCK; i++) {
            U x, y, z;
            x.u = mk();
            y.u = mk();
            z.f = x.f * y.f;
            sum = sum * 31 + z.u;
        }
        printf("%08lx\n", sum);
    }
    return 0;
}
"""


class Lcg:
    def __init__(self, seed):
        self.s = seed

    def rnd(self):
        self.s = (self.s * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.s

    def mk(self):
        m = (self.rnd() >> 8) & 0x7FFFFF
        k = (self.rnd() >> 16) & 15
        e = 100 + ((self.rnd() >> 16) & 63)
        sg = (self.rnd() >> 16) & 1
        if k == 0:
            m = 0
        elif k == 1:
            m = 0x7FFFFF
        elif k == 2:
            m = 0x400000
        elif k == 3:
            m &= 0x7FF000
        elif k == 4:
            m |= 0x000FFF
        return (sg << 31) | (e << 23) | m


def f32(bits):
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def model_sums(seed, blocks):
    g = Lcg(seed)
    out = []
    for _ in range(blocks):
        total = 0
        for _ in range(BLOCK):
            a = f32(g.mk())
            b = f32(g.mk())
            # a*b is exact in a double (24 + 24 bits); one rounding to float32.
            bits = struct.unpack("<I", struct.pack("<f", a * b))[0]
            total = (total * 31 + bits) & 0xFFFFFFFF
        out.append("%08x" % total)
    return out


def run_target(tmp_path, seed, blocks):
    src = ("#define SEED %dUL\n#define BLOCKS %d\n#define BLOCK %d\n" % (seed, blocks, BLOCK)
           + C_SOURCE)
    c_file = tmp_path / "fmul.c"
    c_file.write_text(src)
    mac, rel, com = (tmp_path / ("fmul" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file), "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True, capture_output=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True, text=True,
                         timeout=600, env=dict(os.environ, PYTHONHASHSEED="0"))
    return run.stdout.replace("\r", "").split()


@pytest.mark.parametrize("seed,blocks", [(12345, 20), (987654321, 100)])
def test_products_are_correctly_rounded(tmp_path, seed, blocks):
    got = run_target(tmp_path, seed, blocks)
    want = model_sums(seed, blocks)
    bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w]
    assert len(got) == len(want)
    assert not bad, "blocks of %d products that differ: %s" % (BLOCK, bad)


def test_simple_products_and_rounding_carry(tmp_path):
    """1.0 * 1.0, a tie to even, and a mantissa that rounds up to 2.0."""
    src = r"""
#include <stdio.h>
typedef union { float f; unsigned long u; } U;
static float mul(float a, float b) { volatile float x = a, y = b; return x * y; }
int main(void) {
    U r;
    r.f = mul(1.0f, 1.0f);            printf("%08lx\n", r.u);
    r.f = mul(3.0f, 0.5f);            printf("%08lx\n", r.u);
    r.f = mul(1.99999988f, 1.99999988f); printf("%08lx\n", r.u);
    r.f = mul(1.00000012f, 1.00000012f); printf("%08lx\n", r.u);
    r.f = mul(-1.5f, 2.5f);           printf("%08lx\n", r.u);
    r.f = mul(0.0f, 5.0f);            printf("%08lx\n", r.u);
    return 0;
}
"""
    c_file = tmp_path / "s.c"
    c_file.write_text(src)
    mac, rel, com = (tmp_path / ("s" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file), "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True, capture_output=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True, text=True,
                         timeout=60, env=dict(os.environ, PYTHONHASHSEED="0"))
    pairs = [(1.0, 1.0), (3.0, 0.5), (1.99999988, 1.99999988),
             (1.00000012, 1.00000012), (-1.5, 2.5), (0.0, 5.0)]
    want = []
    for a, b in pairs:
        a, b = struct.unpack("<f", struct.pack("<f", a))[0], struct.unpack("<f", struct.pack("<f", b))[0]
        want.append("%08x" % struct.unpack("<I", struct.pack("<f", a * b))[0])
    assert run.stdout.replace("\r", "").split() == want
