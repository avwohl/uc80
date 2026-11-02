"""__div32w and __div16b: the divide routines for a narrow divisor (family K step 5 and 7).

__div32w (no 32/32 path) divides an unsigned 32-bit value when the divisor is
proved to be below 65536: a constant, an unsigned type of 16 bits or less, a
cast of such a value to a 32-bit type, or an AND with a small constant.
__div16b (47 bytes) divides an unsigned 16-bit value by a constant of 1 to
255.  Each test runs one C program with uc80 and with the host compiler and
compares a checksum of all results.  The options --no-div32w and --no-div16b
build the same program with the old routines.
"""

import random
import shutil
import subprocess
import sys

import pytest

from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO, _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available() or not shutil.which("cc"),
    reason="um80/ul80/cpmemu, the built libraries or cc are not available")


def run_both(tmp_path, source, *flags):
    src = tmp_path / "t.c"
    src.write_text(source)
    host = subprocess.run(["cc", "-O1", "-w", "-o", str(tmp_path / "host"),
                           str(src)], capture_output=True, text=True,
                          timeout=120)
    assert host.returncode == 0, host.stderr
    want = subprocess.run([str(tmp_path / "host")], capture_output=True,
                          text=True, timeout=60).stdout
    mac = tmp_path / "t.mac"
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src), "-o",
                        str(mac), *flags], capture_output=True, text=True,
                       cwd=str(REPO), timeout=300)
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(tmp_path / "t.rel")],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(["ul80", str(tmp_path / "t.rel"), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o",
                    str(tmp_path / "t.com")], check=True, capture_output=True,
                   timeout=120)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "t.com")],
                         capture_output=True, timeout=600)
    got = run.stdout.decode("latin-1").replace("\r", "")
    got = "\n".join(ln for ln in got.split("\n")
                    if not ln.startswith(("CPU mode", "Loaded ",
                                          "Program exit", "Note:")))
    return want.strip(), got.strip(), mac.read_text()


def dividends32():
    rnd = random.Random(32)
    vals = [0, 1, 2, 9, 10, 255, 256, 65535, 65536, 65537, 0x7FFFFFFF,
            0x80000000, 0xFFFFFFFF, 0xFFFF0000, 0x01000000, 0x00FF00FF,
            123456789, 1000000007 % (1 << 32)]
    vals += [rnd.getrandbits(32) for _ in range(14)]
    vals += [rnd.getrandbits(24) for _ in range(4)]
    vals += [rnd.getrandbits(16) for _ in range(4)]
    return vals


DIV32 = r"""
#include <stdio.h>
#include <stdint.h>
static uint32_t acc;
static void fold(uint32_t v) { acc = acc * 31u + v; }
static const uint32_t dv[] = { @DV@ };
static const uint16_t ds[] = { 1, 2, 3, 7, 10, 255, 256, 257, 1000, 4095,
                               4097, 12345, 32767, 40000, 65535 };
static const uint8_t db[] = { 1, 2, 3, 10, 128, 200, 255 };
int main(void)
{
    unsigned i, j;
    uint32_t d, v;
    uint16_t s;
    uint8_t b;
    for (i = 0; i < sizeof dv / sizeof dv[0]; i++) {
        d = dv[i];
        for (j = 0; j < sizeof ds / sizeof ds[0]; j++) {
            s = ds[j];
            fold(d / s); fold(d %% s);
            fold(d / (uint32_t) s); fold(d %% (uint32_t) s);
            fold(d / (s & 0x1FFF)); fold(d %% (s & 0x1FFF));
@WIDE1@
            v = d; v /= s; fold(v);
            v = d; v %%= s; fold(v);
        }
        for (j = 0; j < sizeof db / sizeof db[0]; j++) {
            b = db[j];
            fold(d / b); fold(d %% b);
        }
        fold(d / 10); fold(d %% 10); fold(d / 300); fold(d %% 300);
        fold(d / 65535); fold(d %% 65535); fold(d / 65536); fold(d %% 65536);
        fold(d / 1); fold(d %% 1);
@WIDE2@
    }
    printf("%%lu\n", (unsigned long) acc);
    return 0;
}
"""

WIDE1 = """            fold(d / (s + 65536UL)); fold(d %% (s + 65536UL));
            fold(d / ((uint32_t) s << 8 | 1)); fold(d %% ((uint32_t) s << 8 | 1));
"""
WIDE2 = "        fold(d / 100000UL); fold(d %% 100000UL);\n"


def div32_source(wide):
    src = DIV32.replace("@WIDE1@", WIDE1 if wide else "")
    src = src.replace("@WIDE2@", WIDE2 if wide else "")
    dv = ", ".join("%dU" % v if v < 2**31 else "%dUL" % v
                   for v in dividends32())
    return src.replace("%%", "%").replace("@DV@", dv)


def test_div32w_matches_host(tmp_path):
    want, got, text = run_both(tmp_path, div32_source(False))
    assert "call\t__div32w" in text and "call\t__mod32w" in text
    assert "\n__div32w:" in text and "__div32_big" not in text
    assert got == want


def test_div32_replaces_div32w_when_both_are_needed(tmp_path):
    """A divisor of 65536 or more needs __div32: every site calls it, and
    the image has one copy of the divide code."""
    want, got, text = run_both(tmp_path, div32_source(True))
    assert "call\t__div32w" not in text and "\n__div32w:" not in text
    assert "call\t__div32\n" in text
    assert got == want


def test_option_keeps_div32(tmp_path):
    want, got, text = run_both(tmp_path, div32_source(False), "--no-div32w")
    assert "\n__div32w:" not in text and "\n__mod32w:" not in text
    assert "call\t__div32w" not in text
    assert got == want


NARROW_ONLY = r"""
#include <stdio.h>
#include <stdint.h>
int main(void)
{
    uint32_t d = 4000000000UL, n = 0;
    uint16_t s;
    for (s = 3; s < 9; s++)
        n += d / s + d %% s;
    printf("%%lu\n", (unsigned long) n);
    return 0;
}
""".replace("%%", "%")


def test_div32w_leaves_out_the_32_by_32_path(tmp_path):
    """A program with only narrow divisors links no __div32_big."""
    want, got, text = run_both(tmp_path, NARROW_ONLY)
    assert got == want
    assert "__div32_big" not in text


def make_div16(ks, signed=False):
    lines = ["""#include <stdio.h>
static unsigned short acc;
static const unsigned xs[] = { %s };
#define FOLD(v) (acc = (unsigned short) (acc * 31u + (unsigned) (v)))
int main(void)
{
    unsigned i, x;
    for (i = 0; i < sizeof xs / sizeof xs[0]; i++) {
        x = xs[i];
""" % ", ".join("%dU" % v for v in dividends16())]
    for k in ks:
        lines.append(f"        FOLD(x / {k}U); FOLD(x % {k}U);\n")
    lines.append("""        { unsigned y = x; y /= 7U; FOLD(y); y = x; y %= 7U; FOLD(y); }
        { unsigned y = x; y /= 200U; FOLD(y); y = x; y %= 200U; FOLD(y); }
        FOLD((unsigned short) x / 10); FOLD((unsigned short) x % 10);
""" + ("""        FOLD(x / 256U); FOLD(x % 256U); FOLD(x / 300U); FOLD(x % 300U);
        FOLD((int) (x & 0x7FFF) / 10); FOLD((int) (x & 0x7FFF) % 10);
        FOLD((int) (x & 0x7FFF) / -7); FOLD(-(int) (x & 0x7FFF) % 7);
""" if signed else "") + """    }
    printf("%u\\n", acc);
    return 0;
}
""")
    return "".join(lines)


def dividends16():
    rnd = random.Random(16)
    vals = [0, 1, 2, 3, 9, 10, 11, 99, 100, 127, 128, 254, 255, 256, 257,
            1000, 4095, 4096, 32767, 32768, 32769, 65534, 65535]
    vals += [rnd.getrandbits(16) for _ in range(20)]
    vals += [rnd.getrandbits(8) for _ in range(6)]
    return vals


@pytest.mark.parametrize("part", range(4))
def test_div16b_every_divisor(tmp_path, part):
    ks = list(range(1 + part, 256, 4))
    want, got, text = run_both(tmp_path, make_div16(ks))
    assert "call\t__div16b" in text
    assert got == want


def test_div16b_gives_way_when_div16_is_linked_anyway(tmp_path):
    """A signed division or a divisor above 255 needs __div16: every site
    then calls __div16, so the image has one copy."""
    want, got, text = run_both(tmp_path, make_div16([3, 10, 200], True))
    assert "\n__div16b:" not in text and "call\t__div16b" not in text
    assert "call\t__div16\n" in text
    assert got == want


def test_option_keeps_div16(tmp_path):
    want, got, text = run_both(tmp_path, make_div16([1, 10, 128, 255]),
                               "--no-div16b")
    assert "\n__div16b:" not in text
    assert got == want


SHR32 = r"""
#include <stdio.h>
#include <stdint.h>
static uint32_t acc;
static void fold(uint32_t v) { acc = acc * 31u + v; }
static const uint32_t xs[] = { 0, 1, 0x80000000UL, 0xFFFFFFFFUL, 0x12345678UL,
                               0xDEADBEEFUL, 0x00FF00FFUL, 0x7FFFFFFFUL,
                               65535, 65536, 0xFF000000UL };
int main(void)
{
    unsigned i;
    uint8_t n;
    for (i = 0; i < sizeof xs / sizeof xs[0]; i++) {
        uint32_t x = xs[i];
        for (n = 0; n < 8; n++)
            fold(x >> (n * 4));
        for (n = 0; n < 8; n++)
            fold(x >> ((unsigned) n << 2));
        for (n = 0; n < 4; n++)
            fold(x >> (n * 8));
        for (n = 0; n < 8; n++) {
            uint32_t v = x;
            v >>= n * 4;
            fold(v);
        }
@ANY@
        fold(x >> 8); fold(x >> 5); fold(x / 4096UL);
    }
    printf("%lu\n", (unsigned long) acc);
    return 0;
}
"""

ANY_COUNT = """        for (n = 0; n < 32; n++)
            fold(x >> n);
        for (n = 0; n < 32; n++) {
            uint32_t v = x;
            v >>= n;
            fold(v);
        }
"""


def test_shr32c_for_counts_that_are_multiples_of_4(tmp_path):
    src = SHR32.replace("@ANY@", "")
    want, got, text = run_both(tmp_path, src)
    assert "call\t__shr32c" in text and "\n__shr32c:" in text
    assert "\n__shr32:" not in text
    assert got == want


def test_shr32_when_any_count_is_possible(tmp_path):
    """A count that is not a multiple of 4 needs __shr32: every site calls
    it, and the image has one copy."""
    src = SHR32.replace("@ANY@", ANY_COUNT)
    want, got, text = run_both(tmp_path, src)
    assert "call\t__shr32c" not in text and "\n__shr32c:" not in text
    assert "call\t__shr32\n" in text
    assert got == want


def test_option_keeps_shr32(tmp_path):
    src = SHR32.replace("@ANY@", "")
    want, got, text = run_both(tmp_path, src, "--no-shr32-loop")
    assert "call\t__shr32c" not in text and "\n__shr32c:" not in text
    assert got == want


def test_shr32_when_a_libc_rounding_routine_is_linked(tmp_path):
    """floor() calls __shr32 inside the library: the verifier found that
    both __shr32 and __shr32c were linked.  Every site calls __shr32."""
    src = SHR32.replace("@ANY@", "").replace(
        "    printf(\"%lu\\n\"",
        "    printf(\"%d\\n\", (int) floor((double) (acc & 0xFFFFu) / 16.0));\n"
        "    printf(\"%lu\\n\"").replace(
        "#include <stdio.h>", "#include <stdio.h>\n#include <math.h>", 1)
    assert "math.h" in src
    want, got, text = run_both(tmp_path, src)
    assert "call\t__shr32c" not in text and "\n__shr32c:" not in text
    assert "call\t__shr32\n" in text
    assert got == want
