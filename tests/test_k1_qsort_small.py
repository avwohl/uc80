"""__qsort_s: qsort of at most 32 elements of at most 8 bytes (family K step 6).

The compiler calls __qsort_s (lc_qsort_s.mac) in place of qsort when the
element count is a constant of 32 or less and the element size is a constant
of 1 to 8.  The tests run one C program with uc80 and with the host compiler
and compare the printed checksums.  The comparators use a total order (all
bytes of the element), so the sorted result is unique, as qsort is not
stable.  The program sorts 0 to 32 elements of the sizes 1, 2, 3, 4, 6 and
8 in five patterns (random, few distinct values, ascending, descending,
organ pipe), and one array of 9-byte elements and one of 33 elements, which
use the generic qsort.  A comparator that calls qsort checks the
reentrancy.
"""

import shutil
import subprocess
import sys

import pytest

from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO, _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available() or not shutil.which("cc"),
    reason="um80/ul80/cpmemu, the built libraries or cc are not available")

COUNTS = [0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 16, 17, 20, 31, 32]
SIZES = [1, 2, 3, 4, 6, 8]


def make_source():
    out = ["""#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
static uint32_t seed = 12345;
static uint32_t rnd(void)
{
    seed = seed * 1103515245UL + 12345UL;
    return (seed >> 8) & 0xFFFFFFUL;
}
static int bad;
#define MKCMP(S) static int cmp##S(const void *a, const void *b) { \\
    const unsigned char *x = a, *y = b; int i; \\
    for (i = S - 1; i >= 0; i--) \\
        if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1; \\
    return 0; }
MKCMP(1) MKCMP(2) MKCMP(3) MKCMP(4) MKCMP(6) MKCMP(8) MKCMP(9)
static unsigned char buf[40 * 9];
static void fill(int n, int s, int pat)
{
    int i, j;
    uint32_t x;
    for (i = 0; i < n; i++) {
        switch (pat) {
        case 0: x = rnd(); x ^= rnd() << 12; break;
        case 1: x = rnd() % 4; break;
        case 2: x = (uint32_t) i * 3; break;
        case 3: x = (uint32_t) (n - i) * 3; break;
        default: x = (uint32_t) (i < n / 2 ? i : n - i); break;
        }
        for (j = 0; j < s; j++)
            buf[i * s + j] = j < 4 ? (unsigned char) (x >> (8 * j))
                                   : (unsigned char) ((x + j) % (pat == 1 ? 3 : 251));
    }
}
static void report(int n, int s, int pat, int (*cmp)(const void *, const void *))
{
    uint32_t h = 0;
    int i;
    for (i = 1; i < n; i++)
        if (cmp(buf + (i - 1) * s, buf + i * s) > 0)
            bad++;
    for (i = 0; i < n * s; i++)
        h = h * 31 + buf[i];
    printf("%d %d %d %lu\\n", n, s, pat, (unsigned long) h);
}
"""]
    for s in SIZES:
        cases = "\n".join(
            f"    case {n}: qsort(buf, {n}, {s}, cmp{s}); break;"
            for n in COUNTS)
        out.append(f"""static void sort{s}(int n)
{{
    switch (n) {{
{cases}
    }}
}}
""")
    out.append("""static int inner_calls;
static int cmpn(const void *a, const void *b)
{
    unsigned short t[5];
    int i;
    const unsigned short *x = a, *y = b;
    t[0] = 50; t[1] = 20; t[2] = 40; t[3] = 10; t[4] = 30;
    qsort(t, 5, 2, cmp2);
    for (i = 1; i < 5; i++)
        if (t[i - 1] > t[i]) bad++;
    inner_calls++;
    return *x < *y ? -1 : *x > *y;
}
int main(void)
{
    int n, s, pat, k;
    static const int ns[] = {""" + ", ".join(map(str, COUNTS)) + """};
    for (pat = 0; pat < 5; pat++) {
        for (k = 0; k < (int) (sizeof ns / sizeof ns[0]); k++) {
            n = ns[k];
""" + "\n".join(
        f"            fill(n, {s}, pat); sort{s}(n); report(n, {s}, pat, cmp{s});"
        for s in SIZES) + """
        }
    }
    /* generic qsort: 9-byte elements, 33 elements */
    fill(12, 9, 0); qsort(buf, 12, 9, cmp9); report(12, 9, 0, cmp9);
    fill(33, 2, 0); qsort(buf, 33, 2, cmp2); report(33, 2, 0, cmp2);
    /* the comparator calls qsort */
    fill(12, 2, 0); qsort(buf, 12, 2, cmpn); report(12, 2, 0, cmp2);
    printf("inner %d bad %d\\n", inner_calls > 0, bad);
    return 0;
}
""")
    return "".join(out)


def run_both(tmp_path, *flags):
    src = tmp_path / "t.c"
    src.write_text(make_source())
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


def test_small_qsort_matches_host(tmp_path):
    want, got, text = run_both(tmp_path)
    assert "call\t__qsort_s" in text
    assert "call\t_qsort" in text            # 9-byte and 33-element calls
    assert got == want
    assert want.endswith("inner 1 bad 0")


def test_option_keeps_generic_qsort(tmp_path):
    want, got, text = run_both(tmp_path, "--no-qsort-small")
    assert "__qsort_s" not in text
    assert got == want


def test_small_qsort_with_int32(tmp_path):
    want, got, text = run_both(tmp_path, "--int=32")
    assert got == want
