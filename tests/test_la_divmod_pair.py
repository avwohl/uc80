"""L4: x / y and x % y of the same operands divide once
(docs/family_l.md).  The tests run the program with uc80 and with the host
compiler and compare the results for many operand values, with all sign
combinations."""

from ._faml import needs_toolchain, run_both

pytestmark = needs_toolchain

HEAD = r"""
#include <stdio.h>
static int vs[] = { 0, 1, -1, 2, -2, 3, 7, -7, 10, -10, 99, 100, 255, 256,
    -256, 1000, -1000, 12345, -12345, 32767, -32767, -32768 };
static unsigned vu[] = { 0, 1, 2, 3, 7, 10, 99, 100, 255, 256, 1000,
    12345, 32767, 32768, 40000, 65535 };
#define NS ((int) (sizeof vs / sizeof vs[0]))
#define NU ((int) (sizeof vu / sizeof vu[0]))
static int a[4];
"""


def test_signed_pairs_match_host(tmp_path):
    src = HEAD + r"""
static int q, r;
/* remainder first, then quotient (the shape of davidly e.c) */
static int rem_first(int x, int n)
{
    a[1] = x % n;
    x = a[1] + x / n;
    return x;
}
/* quotient first */
static void quo_first(int x, int n)
{
    q = x / n;
    r = x % n;
}
/* statements between the two, none changes an operand */
static void between(int x, int n, int k)
{
    int t = x % n;
    k = k * 3 + 1;
    a[2] = k;
    q = t + x / n;
}
int main(void)
{
    int i, j;
    long sum = 0;
    for (i = 0; i < NS; i++)
        for (j = 0; j < NS; j++) {
            int x = vs[i], n = vs[j];
            if (n == 0 || (x == -32768 && n == -1)) continue;
            quo_first(x, n);
            sum += (long) q * 3 + r;
            sum += rem_first(x, n);
            between(x, n, i);
            sum += q;
            printf("%d %d %d %d\n", x, n, q, r);
        }
    printf("%ld\n", sum);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "call\t__sdivmod16" in text
    assert "call\t__sdiv16" not in text and "call\t__smod16" not in text


def test_unsigned_pairs_match_host(tmp_path):
    src = HEAD + r"""
static unsigned q, r;
static void quo_first(unsigned x, unsigned n)
{
    q = x / n;
    r = x % n;
}
static void rem_first(unsigned x, unsigned n)
{
    r = x % n;
    q = x / n;
}
static void mixed(int x, unsigned n)
{
    r = x % n;
    q = x / n;
}
int main(void)
{
    int i, j;
    unsigned long sum = 0;
    for (i = 0; i < NU; i++)
        for (j = 1; j < NU; j++) {
            quo_first(vu[i], vu[j]);
            printf("%u %u %u %u ", vu[i], vu[j], q, r);
            rem_first(vu[i], vu[j]);
            sum += (unsigned long) q + r;
            mixed((int) (vu[i] & 0x7fff), vu[j]);
            printf("%u %u\n", q, r);
        }
    printf("%lu\n", sum);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "call\t__div16" in text
    assert "call\t__mod16" not in text


def test_constant_divisors(tmp_path):
    src = HEAD + r"""
static unsigned q, r;
static int sq, sr;
static void digit(unsigned x)
{
    r = x % 10;
    q = x / 10;
}
static void digit_s(int x)
{
    sr = x % 10;
    sq = x / 10;
}
static void p2(unsigned x, int y)
{
    r = x % 8;
    q = x / 8;
    sr = y % 4;
    sq = y / 4;
}
static void big(unsigned x)
{
    r = x % 1000;
    q = x / 1000;
}
int main(void)
{
    int i;
    for (i = 0; i < NU; i++) {
        digit(vu[i]); printf("%u %u ", q, r);
        big(vu[i]); printf("%u %u ", q, r);
        p2(vu[i], vs[i]); printf("%u %u %d %d ", q, r, sq, sr);
        digit_s(vs[i]); printf("%d %d\n", sq, sr);
    }
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want


def test_no_pair_when_operand_changes(tmp_path):
    src = HEAD + r"""
static int q, r;
static void changes(int x, int n)
{
    r = x % n;
    x = x / 2 + 1;
    q = x / n;
}
static void changes_n(int x, int n)
{
    r = x % n;
    n++;
    q = x / n;
}
static void same_statement(int x, int n)
{
    q = (x = x - 1) % n + x / n;
    r = x;
}
static int cond(int x, int n)
{
    int t = (n > 0 && x % n == 1);
    return t + x / n;
}
static void loopy(int x, int n)
{
    int i;
    q = 0;
    for (i = 0; i < 3; i++) {
        r = x % n;
        x = x / n;
        q += r;
    }
    r = x;
}
int main(void)
{
    int i, j;
    for (i = 0; i < NS; i++)
        for (j = 0; j < NS; j++) {
            int x = vs[i], n = vs[j];
            if (n == 0 || n == -1 || (x == -32768)) continue;
            changes(x, n); printf("%d %d ", q, r);
            changes_n(x, n == -1 ? 5 : n); printf("%d %d ", q, r);
            same_statement(x, n); printf("%d %d ", q, r);
            printf("%d ", cond(x, n));
            loopy(x, n); printf("%d %d\n", q, r);
        }
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want


def test_global_operand_and_call_between(tmp_path):
    src = HEAD + r"""
static int gx = 100, gn = 7, q, r;
static void touch(void) { gx += 5; }
static void with_call(void)
{
    r = gx % gn;
    touch();
    q = gx / gn;
}
static void without_call(void)
{
    r = gx % gn;
    q = gx / gn;
}
int main(void)
{
    with_call(); printf("%d %d %d\n", q, r, gx);
    without_call(); printf("%d %d %d\n", q, r, gx);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want


def test_option_divides_twice(tmp_path):
    src = HEAD + r"""
static int q, r;
static void both(int x, int n)
{
    q = x / n;
    r = x % n;
}
int main(void)
{
    both(100, 7); printf("%d %d\n", q, r);
    both(-100, 7); printf("%d %d\n", q, r);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src, "--no-divmod-pair")
    assert got == want
    assert "call\t__sdivmod16" not in text
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "call\t__sdivmod16" in text


def test_with_int32(tmp_path):
    src = HEAD + r"""
static int q, r;
static void both(int x, int n)
{
    q = x / n;
    r = x % n;
}
int main(void)
{
    both(100000, 7); printf("%d %d\n", q, r);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src, "--int=32")
    assert got == want


def test_static_local_changed_by_recursion(tmp_path):
    # a static local is shared by the recursive calls: a call between the
    # two operations can change it
    src = r"""
#include <stdio.h>
static int depth_q, depth_r;
static int walk(int level, int n)
{
    static int x = 100;
    int r;
    r = x % n;
    if (level > 0)
        walk(level - 1, n);
    else
        x = x + 11;
    depth_q = x / n;
    depth_r = r;
    return depth_q;
}
int main(void)
{
    printf("%d\n", walk(2, 7));
    printf("%d %d\n", depth_q, depth_r);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want


def test_inline_byte_divide_leaves_the_second_operation(tmp_path):
    # the dividend is known to be 0..199: the divide is inline code with no
    # call that returns both results, so the pair stays two operations
    src = r"""
#include <stdio.h>
int main(void)
{
    unsigned x, r, q;
    unsigned long sum = 0;
    for (x = 0; x < 200; x++) {
        r = x % 10;
        q = x / 10;
        sum += q * 100 + r;
    }
    printf("%lu\n", sum);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want


def test_signed_pair_next_to_a_lone_divide_stays_two_operations(tmp_path):
    # __sdivmod16 would link next to __sdiv16: the image would grow
    src = HEAD + r"""
static int q, r;
static void both(int x, int n)
{
    q = x / n;
    r = x % n;
}
static int alone(int x, int n) { return x / n; }
int main(void)
{
    both(100, 7); printf("%d %d %d\n", q, r, alone(55, 4));
    both(-100, 7); printf("%d %d\n", q, r);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "call\t__sdivmod16" not in text


def test_signed_pair_in_main_outside_loops_stays_two_operations(tmp_path):
    src = HEAD + r"""
int main(void)
{
    int x = 100, n = 7, q, r;
    q = x / n;
    r = x % n;
    printf("%d %d\n", q, r);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "14 2"
    assert "call\t__sdivmod16" not in text


def test_unsigned_pair_in_main_outside_loops_is_paired(tmp_path):
    src = HEAD + r"""
int main(void)
{
    unsigned x = 1000, n = 7, q, r;
    q = x / n;
    r = x % n;
    printf("%u %u\n", q, r);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "142 6"
    assert "call\t__div16" in text and "call\t__mod16" not in text
