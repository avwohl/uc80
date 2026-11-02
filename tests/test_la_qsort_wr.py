"""L2: qsort of 2-byte elements calls __qsort_wr, and the comparator takes
its arguments in HL and DE (docs/family_l.md).  Each test runs a C program
with uc80 under cpmemu and with the host compiler and compares the output.
The comparators use a total order, so the sorted array is unique (qsort is
not stable)."""

from ._faml import needs_toolchain, run_both

pytestmark = needs_toolchain

COMMON = r"""
#include <stdio.h>
#include <stdlib.h>
#define MAXN 1100
static int a[MAXN + 4];
static unsigned short u[MAXN + 4];
static unsigned long seed = 12345;
static int rnd(void) { seed = seed * 1103515245UL + 12345UL; return (int)((seed >> 8) & 0x7fff); }
static int fails;
static long nsort;
static int value(unsigned i, unsigned n, int pat)
{
    switch (pat) {
    case 0: return rnd();
    case 1: return i;
    case 2: return n - i;
    case 3: return 7;
    case 4: return i < n / 2 ? i : n - i;
    case 5: return i % 5;
    case 6: return rnd() & 3;
    case 7: return (i & 1) ? i : n - i;
    case 8: return n - i + (rnd() & 1);
    default: return i + (rnd() & 1);
    }
}
"""

MAIN = r"""
static void check(unsigned n, int pat, int down)
{
    unsigned i;
    long s1 = 0, s2 = 0;
    unsigned short h = 0;
    a[0] = 0x1234; a[n + 1] = 0x4321;
    for (i = 0; i < n; i++) {
        int v = value(i, n, pat);
        a[1 + i] = v; s1 += v; s2 += (long) v * v;
    }
    SORT(&a[1], n);
    {
        long t1 = 0, t2 = 0;
        for (i = 0; i < n; i++) {
            int v = a[1 + i];
            t1 += v; t2 += (long) v * v;
            h = (unsigned short) (h * 31 + v);
            if (i && (down ? a[i] < v : a[i] > v)) { fails++; break; }
        }
        if (t1 != s1 || t2 != s2) fails++;
        if (a[0] != 0x1234 || a[n + 1] != 0x4321) fails++;
    }
    nsort++;
    if ((n & 7) == 0 && pat == 0) printf("%u %d %u\n", n, pat, (unsigned) h);
}
int main(void)
{
    unsigned n;
    int p;
    for (n = 0; n <= 48; n++)
        for (p = 0; p < 10; p++) check(n, p, DOWN);
    for (p = 0; p < 10; p++) {
        check(63, p, DOWN); check(64, p, DOWN); check(65, p, DOWN);
        check(127, p, DOWN); check(257, p, DOWN); check(700, p, DOWN);
    }
    printf("%s %ld\n", fails ? "FAILED" : "ALL OK", nsort);
    return 0;
}
"""


def program(cmp_src, sort_stmt, down=0):
    return (COMMON + cmp_src + "#define SORT(b, n) " + sort_stmt + "\n"
            + "#define DOWN " + str(down) + "\n" + MAIN)


ASC = r"""
static int cmp_int(const void *x, const void *y)
{ return *(const int *) x - *(const int *) y; }
"""

DESC = r"""
static int cmp_desc(const void *x, const void *y)
{
    int p = *(const int *) x, q = *(const int *) y;
    return p < q ? 1 : p > q ? -1 : 0;
}
"""

# a comparator with a call and local variables
HELPER = r"""
static int sgn(int d) { return d < 0 ? -1 : d > 0; }
static int cmp_helper(const void *x, const void *y)
{
    const int *p = x, *q = y;
    int d, e;
    d = *p;
    e = *q;
    return sgn(d - e) * 3;
}
"""


def test_ascending_matches_host(tmp_path):
    src = program(ASC, "qsort(b, n, sizeof(int), cmp_int)")
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert want.splitlines()[-1].startswith("ALL OK")
    assert "call\t__qsort_wr" in text
    assert "_qsort\n" not in text.replace("__qsort", "")
    assert "ld\tBC,_cmp_int" in text


def test_descending_matches_host(tmp_path):
    src = program(DESC, "qsort(b, n, sizeof(int), cmp_desc)", down=1)
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "call\t__qsort_wr" in text


def test_comparator_with_call_and_locals(tmp_path):
    src = program(HELPER, "qsort(b, n, sizeof(int), cmp_helper)")
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "call\t__qsort_wr" in text


def test_unsigned_short_elements(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdlib.h>
static unsigned short v[300];
static int cmpu(const void *x, const void *y)
{
    unsigned short p = *(const unsigned short *) x, q = *(const unsigned short *) y;
    return p < q ? -1 : p > q;
}
int main(void)
{
    unsigned i, n;
    unsigned short h;
    unsigned long s = 99;
    for (n = 0; n < 300; n += 37) {
        for (i = 0; i < n; i++) {
            s = (s * 1103515245UL + 12345UL) & 0xffffffffUL;
            v[i] = (unsigned short) (s >> 9);
        }
        qsort(v, n, sizeof v[0], cmpu);
        for (h = 0, i = 0; i < n; i++) h = (unsigned short) (h * 31 + v[i]);
        printf("%u %u\n", n, (unsigned) h);
    }
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "call\t__qsort_wr" in text


def test_option_keeps_stack_comparator(tmp_path):
    src = program(ASC, "qsort(b, n, sizeof(int), cmp_int)")
    want, got, text = run_both(tmp_path, src, "--no-qsort-wr")
    assert got == want
    assert "__qsort_wr" not in text


def test_no_register_cc_keeps_generic_qsort(tmp_path):
    src = program(ASC, "qsort(b, n, sizeof(int), cmp_int)")
    want, got, text = run_both(tmp_path, src, "--no-register-cc")
    assert got == want
    assert "__qsort_wr" not in text


def test_comparator_that_calls_qsort_keeps_the_stack_convention(tmp_path):
    # the comparator reaches qsort: the stack convention and the generic
    # qsort (reentrant) stay
    src = r"""
#include <stdio.h>
#include <stdlib.h>
static int w[40], t[5], inner;
static int cmp_in(const void *x, const void *y)
{ return *(const int *) x - *(const int *) y; }
static int cmp_out(const void *x, const void *y)
{
    int i;
    t[0] = 5; t[1] = 3; t[2] = 4; t[3] = 1; t[4] = 2;
    qsort(t, 5, sizeof(int), cmp_in);
    for (i = 1; i < 5; i++) if (t[i - 1] > t[i]) inner += 100;
    inner++;
    return *(const int *) x - *(const int *) y;
}
int main(void)
{
    int i;
    for (i = 0; i < 40; i++) w[i] = (i * 17) % 41;
    qsort(w, 40, sizeof(int), cmp_out);
    for (i = 0; i < 40; i++) printf("%d ", w[i]);
    printf("\n%d\n", inner > 0 && inner < 100);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "ld\tBC,_cmp_out" not in text       # cmp_out reaches qsort
    assert "ld\tBC,_cmp_in" in text            # cmp_in reaches nothing


def test_comparator_used_elsewhere_keeps_stack_convention(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdlib.h>
static int w[30];
static int cmp(const void *x, const void *y)
{ return *(const int *) x - *(const int *) y; }
int main(void)
{
    int i;
    int (*f)(const void *, const void *) = cmp;
    for (i = 0; i < 30; i++) w[i] = (i * 13) % 31;
    qsort(w, 30, sizeof(int), cmp);
    printf("%d %d\n", f(&w[0], &w[1]), f(&w[5], &w[1]) > 0);
    for (i = 0; i < 30; i++) printf("%d ", w[i]);
    printf("\n");
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "__qsort_wr" not in text


def test_other_element_sizes_keep_the_old_paths(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdlib.h>
static long w[50];
static int cmpl(const void *x, const void *y)
{
    long p = *(const long *) x, q = *(const long *) y;
    return p < q ? -1 : p > q;
}
int main(void)
{
    int i;
    for (i = 0; i < 50; i++) w[i] = ((long) i * 7919L) % 101L - 50;
    qsort(w, 50, sizeof(long), cmpl);
    for (i = 0; i < 50; i++) printf("%ld ", w[i]);
    printf("\n");
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "__qsort_wr" not in text


def test_with_int32_uses_the_old_path(tmp_path):
    src = program(ASC, "qsort(b, n, sizeof(int), cmp_int)")
    # --int=32: sizeof(int) is 4, so the generic path serves the array
    want, got, text = run_both(tmp_path, src, "--int=32")
    assert got == want
    assert "__qsort_wr" not in text


def test_comparison_count_is_n_log_n(tmp_path):
    """No O(n^2) case in __qsort_wr: the same patterns as the test of the
    generic qsort (tests/test_qsort.py), 1000 elements."""
    src = r"""
#include <stdio.h>
#include <stdlib.h>
#define N 1000
static int a[N];
static unsigned long calls;
static int up(const void *x, const void *y)
{ int p = *(const int *) x, q = *(const int *) y; calls++; return (p > q) - (p < q); }
static unsigned seed = 1;
static int rnd(void) { seed = seed * 25173u + 13849u; return (int) (seed >> 1); }
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
    for (i = 0; i < N; i++) a[i] = i < N/2 ? N/2 - i : i - N/2; go("valley");
    for (i = 0; i < N; i++) a[i] = (i * 7) % 10;    go("sawtooth");
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert "call\t__qsort_wr" in text
    rows = {}
    for line in got.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in ("ok", "BAD"):
            rows[parts[0]] = (int(parts[1]), parts[2])
    assert len(rows) == 10, got
    import math
    limit = 3 * 1000 * math.log2(1000)
    for name, (calls, status) in rows.items():
        assert status == "ok", name
        assert calls < limit, "%s: %d comparator calls" % (name, calls)
    assert rows["sorted"][0] == 999 and rows["reversed"][0] == 999
