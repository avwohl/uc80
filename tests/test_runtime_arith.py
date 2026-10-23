"""Integer runtime helpers and string/memory functions, end to end.

The 16- and 32-bit multiply, divide, modulo and shift helpers
(rt_arith16, rt_arith32, rt_mul, rt_div8) have fast paths that depend on
the operand values: 8-bit operands, small negative operands, divisors
below 256 / 32768 / 65536 / 2^24, whole-byte shift counts and so on.
Each test compiles a C program that applies the operators to operand
tables chosen to reach every path, prints the results, and compares them
with Python.  Only operations with defined behaviour in C are used (no
division by zero, no INT_MIN / -1, shift counts below the width).

The string test checks the libc functions against naive loops inside the
program and prints the number of mismatches.
"""

import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")


def build_and_run(tmp_path, src, name="prog", extra=()):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(src)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", *extra, str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=300)
    return run.stdout.decode("latin-1").replace("\r", "")


def s16(x):
    x &= 0xFFFF
    return x - 0x10000 if x & 0x8000 else x


def s32(x):
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x & 0x80000000 else x


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def tmod(a, b):
    return a - tdiv(a, b) * b


E16 = [0, 1, 2, 3, 5, 7, 10, 100, 127, 128, 200, 255, 256, 257, 300, 1000,
       1024, 5678, 0x7FFF, 0x8000, 0x8001, 0xFF00, 0xFF01, 0xFFF9, 0xFFFF]
E32 = [0, 1, 3, 7, 10, 255, 256, 5599, 0x7FFF, 0x8000, 0xFFFF, 0x10000,
       0x12345, 0xFFFFFF, 0x1000000, 0x1234567, 0x7FFFFFFF, 0x80000000,
       0x80000001, 0xFFFF8000, 0xFFFFFF00, 0xFFFFFFF9, 0xFFFFFFFF, 27994000]


def pairs16():
    rng = random.Random(16)
    out = [(a, b) for a in E16 for b in E16]
    for _ in range(300):
        a = rng.choice([rng.randrange(256), rng.randrange(65536),
                        65536 - rng.randrange(1, 300)])
        b = rng.choice([rng.randrange(256), rng.randrange(65536),
                        65536 - rng.randrange(1, 300)])
        out.append((a, b))
    return out


def pairs32():
    rng = random.Random(32)
    out = [(a, b) for a in E32 for b in E32]

    def r():
        k = rng.random()
        if k < .2:
            return rng.randrange(256)
        if k < .4:
            return rng.randrange(65536)
        if k < .5:
            return rng.randrange(-32768, 32768) & 0xFFFFFFFF
        if k < .6:
            return rng.randrange(1 << 24)
        return rng.randrange(1 << 32)
    out += [(r(), r()) for _ in range(300)]
    return out


def test_16bit_mul_div_mod_shift(tmp_path):
    ps = pairs16()
    table = ",\n".join(f"{{{a}u,{b}u}}" for a, b in ps)
    src = f"""
#include <stdio.h>
static const unsigned t[][2] = {{
{table}
}};
int main(void)
{{
    unsigned i;
    for (i = 0; i < sizeof t / sizeof t[0]; i++) {{
        unsigned a = t[i][0], b = t[i][1];
        int sa = (int)a, sb = (int)b;
        unsigned c = b & 15;
        printf("%x %x", a * b, (unsigned)(sa * sb));
        if (b) printf(" %x %x", a / b, a % b);
        if (sb && !(sa == -32767 - 1 && sb == -1))
            printf(" %x %x", (unsigned)(sa / sb), (unsigned)(sa % sb));
        printf(" %x %x %x\\n", a << c, a >> c, (unsigned)(sa >> c));
    }}
    return 0;
}}
"""
    out = build_and_run(tmp_path, src).splitlines()
    out = [l for l in out if l and l[0] in "0123456789abcdef"]
    assert len(out) == len(ps)
    for (a, b), line in zip(ps, out):
        sa, sb, c = s16(a), s16(b), b & 15
        want = [(a * b) & 0xFFFF, (sa * sb) & 0xFFFF]
        if b:
            want += [a // b, a % b]
        if sb and not (sa == -32768 and sb == -1):
            want += [tdiv(sa, sb) & 0xFFFF, tmod(sa, sb) & 0xFFFF]
        want += [(a << c) & 0xFFFF, a >> c, (sa >> c) & 0xFFFF]
        assert line.split() == [f"{w:x}" for w in want], (a, b, line)


def test_32bit_mul_div_mod_shift(tmp_path):
    ps = pairs32()
    table = ",\n".join(f"{{{a}ul,{b}ul}}" for a, b in ps)
    src = f"""
#include <stdio.h>
static const unsigned long t[][2] = {{
{table}
}};
int main(void)
{{
    unsigned i;
    for (i = 0; i < sizeof t / sizeof t[0]; i++) {{
        unsigned long a = t[i][0], b = t[i][1];
        long sa = (long)a, sb = (long)b;
        unsigned c = (unsigned)(b & 31);
        printf("%lx %lx", a * b, (unsigned long)(sa * sb));
        if (b) printf(" %lx %lx", a / b, a % b);
        if (sb && !(sa == -2147483647L - 1 && sb == -1))
            printf(" %lx %lx", (unsigned long)(sa / sb), (unsigned long)(sa % sb));
        printf(" %lx %lx %lx\\n", a << c, a >> c, (unsigned long)(sa >> c));
    }}
    return 0;
}}
"""
    out = build_and_run(tmp_path, src).splitlines()
    out = [l for l in out if l and l[0] in "0123456789abcdef"]
    assert len(out) == len(ps)
    m = 0xFFFFFFFF
    for (a, b), line in zip(ps, out):
        sa, sb, c = s32(a), s32(b), b & 31
        want = [(a * b) & m, (sa * sb) & m]
        if b:
            want += [a // b, a % b]
        if sb and not (sa == -(1 << 31) and sb == -1):
            want += [tdiv(sa, sb) & m, tmod(sa, sb) & m]
        want += [(a << c) & m, a >> c, (sa >> c) & m]
        assert line.split() == [f"{w:x}" for w in want], (a, b, line)


def test_32bit_helpers_library_linked(tmp_path):
    """--no-embed-runtime links the helpers from runtime.lib modules."""
    src = r"""
#include <stdio.h>
int main(void)
{
    volatile long a = -27994000L, b = 5599L, c = 40000L;
    volatile unsigned long u = 0xFFFFFFFFul, v = 0x12345ul;
    printf("%ld %ld %ld %lx %lx %lx\n", a / b, a % b, c * c, u / v, u % v,
           u * v);
    return 0;
}
"""
    out = build_and_run(tmp_path, src, extra=("--no-embed-runtime",))
    want = (f"{tdiv(-27994000, 5599)} {tmod(-27994000, 5599)} {40000 * 40000} "
            f"{0xFFFFFFFF // 0x12345:x} {0xFFFFFFFF % 0x12345:x} "
            f"{(0xFFFFFFFF * 0x12345) & 0xFFFFFFFF:x}")
    assert want in out, out


def test_string_and_memory_functions(tmp_path):
    src = r"""
#include <stdio.h>
#include <string.h>
static unsigned char buf[400], ref[400], s1[200], s2[200];
static unsigned seed = 12345;
static int fails;
static unsigned rnd(void) { seed = seed * 25173u + 13849u; return seed >> 3; }
static int sgn(int x) { return x < 0 ? -1 : x > 0; }
static void naive_move(unsigned char *d, const unsigned char *s, unsigned n)
{
    static unsigned char tmp[400];
    unsigned i;
    for (i = 0; i < n; i++) tmp[i] = s[i];
    for (i = 0; i < n; i++) d[i] = tmp[i];
}
int main(void)
{
    unsigned i, n, so, d, k;
    int r;
    char *p;
    for (n = 0; n < 150; n++) {
        for (i = 0; i < n; i++) s1[i] = (unsigned char)(1 + rnd() % 255);
        s1[n] = 0;
        if (strlen((char *)s1) != n) fails++;
        memset(buf, 0x77, sizeof buf);
        if (strcpy((char *)buf + 3, (char *)s1) != (char *)buf + 3) fails++;
        for (i = 0; i <= n; i++) if (buf[3 + i] != s1[i]) fails++;
        if (buf[4 + n] != 0x77) fails++;
        k = rnd() % 40;
        for (i = 0; i < k; i++) s2[i] = (unsigned char)(1 + rnd() % 255);
        s2[k] = 0;
        if (strcat((char *)buf + 3, (char *)s2) != (char *)buf + 3) fails++;
        for (i = 0; i <= k; i++) if (buf[3 + n + i] != s2[i]) fails++;
        for (d = 0; d < 4; d++) {
            int c = d == 3 ? 0 : (int)(rnd() % 256);
            char *q = 0;
            for (i = 0; i <= n; i++) if (s1[i] == (unsigned char)c) { q = (char *)s1 + i; break; }
            if (strchr((char *)s1, c) != q) fails++;
        }
    }
    for (k = 0; k < 2000; k++) {
        n = rnd() % 10;
        for (i = 0; i < n; i++) s1[i] = s2[i] = (unsigned char)(1 + rnd() % 255);
        s1[n] = s2[n] = 0;
        so = rnd() % (n + 1);
        s1[so] = (unsigned char)(rnd() % 256);
        if (rnd() & 1) s2[so] = (unsigned char)(rnd() % 256);
        if (so < n) { s1[so + 1] = 0; s2[so + 1] = 0; }
        r = 0;
        for (i = 0;; i++) {
            if (s1[i] != s2[i]) { r = (int)s1[i] - (int)s2[i]; break; }
            if (s1[i] == 0) break;
        }
        if (sgn(strcmp((char *)s1, (char *)s2)) != sgn(r)) fails++;
        r = 0;
        for (i = 0; i < n; i++) if (s1[i] != s2[i]) { r = (int)s1[i] - (int)s2[i]; break; }
        if (sgn(memcmp(s1, s2, n)) != sgn(r)) fails++;
    }
    for (n = 0; n < 18; n++)
        for (so = 0; so < 18; so++)
            for (d = 0; d < 18; d++) {
                for (i = 0; i < 50; i++) buf[i] = ref[i] = (unsigned char)rnd();
                if (memmove(buf + d, buf + so, n) != (char *)buf + d) fails++;
                naive_move(ref + d, ref + so, n);
                for (i = 0; i < 50; i++) if (buf[i] != ref[i]) { fails++; break; }
                if (so + n <= d || d + n <= so) {
                    if (memcpy(buf + d, buf + so, n) != (char *)buf + d) fails++;
                    naive_move(ref + d, ref + so, n);
                    for (i = 0; i < 50; i++) if (buf[i] != ref[i]) { fails++; break; }
                }
            }
    for (n = 0; n < 300; n += 7) {
        for (i = 0; i < 310; i++) buf[i] = 0x55;
        if (memset(buf + 2, 0x1A3, n) != (char *)buf + 2) fails++;
        for (i = 0; i < 310; i++)
            if (buf[i] != ((i >= 2 && i < n + 2) ? 0xA3 : 0x55)) { fails++; break; }
    }
    for (k = 0; k < 1000; k++) {
        n = rnd() % 30;
        for (i = 0; i < n; i++) buf[i] = (unsigned char)(rnd() % 8);
        d = rnd() % 9;
        p = 0;
        for (i = 0; i < n; i++) if (buf[i] == d) { p = (char *)buf + i; break; }
        if (memchr(buf, (int)d + 256, n) != p) fails++;
    }
    printf("fails=%d\n", fails);
    return 0;
}
"""
    out = build_and_run(tmp_path, src)
    assert "fails=0" in out, out
