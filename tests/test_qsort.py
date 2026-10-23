"""qsort, bsearch and memcmp/strcmp in the hand-written libc (lc_qsort.mac).

The old qsort was a selection sort that pushed its two element pointers in
the wrong order: uc80 pushes arguments right to left, so compar(a, b) must
push b first and a last.  The old code pushed a first, called compar(min,
j) where it meant compar(j, min), and so sorted in DESCENDING order with an
ascending comparator.  It was also O(n^2) for every input (12.5 million
comparator calls for 5000 elements).

The new qsort is a non-recursive median-of-three quicksort with an
insertion-sort finish.  These tests check the order, element sizes 1..7
(with the size-2 swap fast path and the generic swap), the patterns that
break naive quicksorts (sorted, reversed, all equal, few distinct, organ
pipe), the comparator call count (O(n log n), not O(n^2)), a comparator
that itself calls qsort, and bsearch (including nmemb > 32767, where
low + high overflows 16 bits).

Expected output is computed here in Python with sorted() on the same data.
The comparators use a total order (all bytes of the element), so the
sorted result is unique even though qsort is not stable.
"""

import math
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


def build_and_run(tmp_path, src, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(src)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120, env=env)
    return run.stdout.decode("latin-1").replace("\r", "")


def test_ascending_comparator_sorts_ascending(tmp_path):
    """The regression: an ascending comparator must give ascending order."""
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
static int up(const void *a, const void *b)
{ int x = *(const int *)a, y = *(const int *)b; return (x > y) - (x < y); }
static int down(const void *a, const void *b) { return up(b, a); }
int small[6] = {5, 3, 9, 1, 7, 2};
int big[25] = {14, 3, 22, 9, 0, 17, 5, 24, 11, 8, 19, 1, 13,
               6, 21, 2, 16, 10, 23, 4, 18, 7, 12, 20, 15};
int main(void)
{
    int i;
    qsort(small, 6, sizeof(int), up);
    for (i = 0; i < 6; i++) printf("%d ", small[i]);
    printf("\n");
    qsort(big, 25, sizeof(int), up);
    for (i = 0; i < 25; i++) printf("%d ", big[i]);
    printf("\n");
    qsort(big, 25, sizeof(int), down);
    for (i = 0; i < 25; i++) printf("%d ", big[i]);
    printf("\n");
    return 0;
}
""")
    lines = out.split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith("1 "))
    assert lines[i].split() == ["1", "2", "3", "5", "7", "9"]
    assert lines[i + 1].split() == [str(v) for v in range(25)]
    assert lines[i + 2].split() == [str(v) for v in range(24, -1, -1)]


def _patterns(rng, n):
    yield "random", [rng.randrange(65536) for _ in range(n)]
    yield "sorted", list(range(n))
    yield "reversed", list(range(n, 0, -1))
    yield "equal", [7] * n
    yield "few", [rng.randrange(3) for _ in range(n)]
    yield "organ", [min(i, n - i) for i in range(n)]
    yield "sorted+1", list(range(1, n)) + [0]


@pytest.mark.parametrize("size", [1, 2, 3, 4, 7])
def test_sizes_and_patterns_match_python_sort(tmp_path, size):
    """Element sizes 1..7, compared bytewise (memcmp) as a total order."""
    rng = random.Random(size)
    cases = []
    for n in (0, 1, 2, 3, 8, 9, 10, 17, 64, 200):
        for pat, keys in _patterns(rng, n):
            elems = []
            for k in keys:
                b = k.to_bytes(4, "big")[-size:] if size < 4 else \
                    k.to_bytes(4, "big") + bytes(rng.randrange(2)
                                                 for _ in range(size - 4))
                elems.append(b)
            cases.append((pat, elems))
    decls, calls = [], []
    for i, (pat, elems) in enumerate(cases):
        data = b"".join(elems) or b"\0"
        decls.append("static unsigned char c%d[%d] = {%s};" % (
            i, len(data), ",".join(str(x) for x in data)))
        calls.append("run(c%d, %d);" % (i, len(elems)))
    src = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define SZ %d
static int cmp(const void *a, const void *b) { return memcmp(a, b, SZ); }
static void run(unsigned char *p, unsigned n)
{
    unsigned i;
    qsort(p, n, SZ, cmp);
    for (i = 0; i < n * SZ; i++) printf("%%02x", p[i]);
    printf("\n");
}
%s
int main(void)
{
%s
    printf("end\n");
    return 0;
}
""" % (size, "\n".join(decls), "\n".join(calls))
    out = build_and_run(tmp_path, src)
    lines = out.split("\n")
    start = next(k for k, l in enumerate(lines)
                 if l.startswith("Loaded")) + 1 if any(
        l.startswith("Loaded") for l in lines) else 0
    got = lines[start:start + len(cases)]
    for (pat, elems), line in zip(cases, got):
        want = b"".join(sorted(elems)).hex()
        assert line == want, "%s n=%d size=%d" % (pat, len(elems), size)


def test_comparison_count_is_n_log_n(tmp_path):
    """No O(n^2) case: count comparator calls for 1000 elements."""
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
#define N 1000
static int a[N];
static unsigned long calls;
static int up(const void *x, const void *y)
{ int p = *(const int *)x, q = *(const int *)y; calls++; return (p > q) - (p < q); }
static unsigned seed = 1;
static int rnd(void) { seed = seed * 25173u + 13849u; return (int)(seed >> 1); }
static void go(const char *name)
{
    int i, ok = 1;
    calls = 0;
    qsort(a, N, sizeof(int), up);
    for (i = 1; i < N; i++) if (a[i - 1] > a[i]) ok = 0;
    printf("%s %lu %s\n", name, calls, ok ? "ok" : "BAD");
}
int main(void)
{
    int i;
    for (i = 0; i < N; i++) a[i] = rnd();          go("random");
    for (i = 0; i < N; i++) a[i] = i;               go("sorted");
    for (i = 0; i < N; i++) a[i] = N - i;           go("reversed");
    for (i = 0; i < N; i++) a[i] = 5;               go("equal");
    for (i = 0; i < N; i++) a[i] = 5; a[0] = 9;     go("equal+1");
    for (i = 0; i < N; i++) a[i] = rnd() % 3;       go("few");
    for (i = 0; i < N; i++) a[i] = i < N/2 ? i : N - i; go("organ");
    for (i = 0; i < N; i++) a[i] = i; a[N-1] = -1;  go("sorted+1");
    return 0;
}
""")
    rows = {}
    for line in out.split("\n"):
        parts = line.split()
        if len(parts) == 3 and parts[2] in ("ok", "BAD"):
            rows[parts[0]] = (int(parts[1]), parts[2])
    assert len(rows) == 8, out
    # Random input takes ~1.1 n log2 n; organ pipe (0 1 2 .. n/2 .. 2 1), a
    # weak case for median-of-three, takes ~2.3 n log2 n.  O(n^2) would be
    # ~500000.
    limit = 3 * 1000 * math.log2(1000)      # ~30000
    for name, (calls, status) in rows.items():
        assert status == "ok", name
        assert calls < limit, "%s: %d comparator calls" % (name, calls)
    # already in order: one pass of n-1 comparisons
    assert rows["sorted"][0] == 999
    assert rows["equal"][0] == 999
    assert rows["reversed"][0] == 999


def test_comparator_that_calls_qsort(tmp_path):
    """qsort saves its working storage, so a nested qsort is safe."""
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
static int inner[5];
static int bad;
static int up(const void *x, const void *y)
{ int p = *(const int *)x, q = *(const int *)y; return (p > q) - (p < q); }
static int nest(const void *x, const void *y)
{
    inner[0] = 50; inner[1] = 10; inner[2] = 40; inner[3] = 20; inner[4] = 30;
    qsort(inner, 5, sizeof(int), up);
    if (inner[0] != 10 || inner[4] != 50) bad = 1;
    return up(x, y);
}
int a[40];
int main(void)
{
    int i, ok = 1;
    for (i = 0; i < 40; i++) a[i] = (i * 17) % 40;
    qsort(a, 40, sizeof(int), nest);
    for (i = 0; i < 40; i++) if (a[i] != i) ok = 0;
    printf("nested %s %s\n", ok ? "sorted" : "UNSORTED", bad ? "INNER-BAD" : "inner-ok");
    return 0;
}
""")
    assert "nested sorted inner-ok" in out


def test_bsearch(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
static int up(const void *x, const void *y)
{ int p = *(const int *)x, q = *(const int *)y; return (p > q) - (p < q); }
static int ucmp(const void *x, const void *y)
{ return (int)*(const unsigned char *)x - (int)*(const unsigned char *)y; }
static int a[40];
static unsigned char big[40000];
int main(void)
{
    unsigned n, i, bad = 0, k;
    for (n = 0; n <= 40; n++) {
        for (i = 0; i < n; i++) a[i] = (int)(i * 3) - 20;
        for (i = 0; i < n; i++) {
            int key = (int)(i * 3) - 20, k1 = key + 1, k2 = key - 1;
            if (bsearch(&key, a, n, sizeof(int), up) != &a[i]) bad++;
            if (bsearch(&k1, a, n, sizeof(int), up)) bad++;
            if (bsearch(&k2, a, n, sizeof(int), up)) bad++;
        }
    }
    printf("small bad=%u\n", bad);
    bad = 0;
    for (i = 0; i < 40000u; i++) big[i] = (unsigned char)(i / 200u);
    for (k = 0; k < 201; k++) {
        unsigned char key = (unsigned char)k;
        unsigned char *p = bsearch(&key, big, 40000u, 1, ucmp);
        if (k < 200 ? (!p || *p != key) : p != 0) bad++;
    }
    printf("big bad=%u\n", bad);
    return 0;
}
""")
    assert "small bad=0" in out
    assert "big bad=0" in out


def test_memcmp_strcmp_compare_unsigned_chars(tmp_path):
    """C compares bytes as unsigned char: 0xFF > 0x00, 200 > 50."""
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <string.h>
int main(void)
{
    unsigned char a[2] = {200, 0}, b[2] = {50, 0}, c[2] = {255, 0}, d[2] = {1, 0};
    printf("%d %d %d %d\n", memcmp(a, b, 1), memcmp(b, a, 1), memcmp(c, d, 1), memcmp(d, c, 1));
    printf("%d %d\n", strcmp((char *)a, (char *)b), strcmp((char *)d, (char *)c));
    printf("%d %d\n", strncmp((char *)c, (char *)d, 1), strncmp((char *)b, (char *)a, 2));
    return 0;
}
""")
    assert "150 -150 254 -254" in out
    assert "150 -254" in out
    assert "254 -150" in out
