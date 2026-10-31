"""Family J step 2: the code of the sound-driver kernels and the loops.

  docs/char_return_mask.md   f(x) & mask counts as a byte use
  docs/param_reg.md          a byte parameter in a spare register,
                             dead register copies, RET cc
  docs/frame_drop.md         a frame without slots goes away
  docs/dead_test.md          if (c) ; with a pure c goes away
  docs/pair_compare.md       a register pair read directly in a compare
  docs/loop_rotation.md      the test of a loop at the bottom
  docs/fill_idiom.md         for (i = a; i < b; i++) x[i] = K is a fill
  docs/shl_inline.md         1 << n in line

Each program runs under cpmemu (with a timeout) and on the host compiler.
Both outputs must be the same.  The programs use only values that are the
same for a 16-bit and a 32-bit int.
"""

import re
import subprocess
import sys

import pytest

from .test_famj_startup import compile_c, host_expect, run_raw
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")


def same_as_host(tmp_path, src, *flags):
    expect = host_expect(tmp_path, src)
    mac = compile_c(tmp_path, src, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == expect
    return mac.read_text()


def ids(f):
    return " ".join(f) or "default"


# ---- 2a: f(x) & mask -----------------------------------------------------

MASK_SRC = r"""
#include <stdio.h>

static unsigned char status = 0;
static int calls;

static unsigned char in(unsigned char port)
{
    calls++;
    if (port == 4 && status) { status--; return 128; }
    return (unsigned char)(port * 3);
}

static signed char sgn(int x) { return (signed char)(x - 100); }
static char plain(int x) { return (char)(x * 7); }

static int wait_ready(void)
{
    int n = 0;
    status = 5;
    while (in(4) & 0x80)
        n++;
    return n;
}

static int use_all(int x)
{
    int a = in(x) & 0x0F;
    int b = in(x) | 1;
    int c = in(x) ^ 0x55;
    int d = sgn(x) & 0x7F;
    int e = sgn(x) | 3;
    int f = plain(x) & 255;
    int g = plain(x) ^ 1;
    int h = (sgn(x) & 0x80) != 0;
    if (in(x) & 0x40)
        a += 100;
    if (!(plain(x) & 1))
        b += 1000;
    return a + b + c + d + e + f + g + h;
}

int main(void)
{
    int i;
    long s = 0;
    printf("%d\n", wait_ready());
    for (i = 0; i < 40; i++)
        s += use_all(i * 5);
    printf("%ld %d\n", s, calls > 0);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-char-ret-mask",), ("-O0",), ("--no-register-cc",),
    ("--no-register-locals",), ("--no-param-reg",),
], ids=ids)
def test_char_return_mask(tmp_path, flags):
    same_as_host(tmp_path, MASK_SRC, *flags)


def test_char_return_mask_shape(tmp_path):
    src = r"""
extern int puts(const char *);
static unsigned char in(unsigned char p) { return p == 4 ? 128 : 0; }
static void wait(void) { while (in(4) & 0x80) ; }
int main(void) { wait(); puts("ok"); return 0; }
"""
    on = compile_c(tmp_path, src, "--no-inlining").read_text()
    off = compile_c(tmp_path, src, "--no-inlining", "--no-char-ret-mask",
                    name="o").read_text()

    def fn(text, name):
        seg = text[text.index(f"\n_{name}:"):]
        return seg[:seg.index(f"@{name}_ret:")]

    # the result of in() stays in A: the loop tests bit 7 of A
    assert "\tbit\t7,A" in fn(on, "wait") or "\tand\t128" in fn(on, "wait")
    # without the switch the function returns the byte in HL
    assert "\tld\tH,0" in fn(off, "in")
    assert "\tld\tH,0" not in fn(on, "in")


# ---- 2b: parameters in registers, RET cc ---------------------------------

PARAM_SRC = r"""
#include <stdio.h>
#include <stdint.h>

static uint8_t log_buf[64];
static uint16_t nwrites;
static uint8_t busy;

static void out(uint8_t port, uint8_t value)
{
    log_buf[(nwrites & 31) * 2] = port;
    log_buf[(nwrites & 31) * 2 + 1] = value;
    nwrites++;
    if ((port & 0xF8) == 0)
        busy = 2;
}

static uint8_t scale(uint8_t a, uint8_t b)
{
    uint8_t r = 0;
    if (a > b) {
        r = a;
        if (b & 1)
            r += b;
    } else {
        r = b;
    }
    while (a) {
        r ^= a;
        a >>= 1;
    }
    return r;
}

static int pick(uint8_t sel, int v)
{
    switch (sel & 3) {
    case 0: return v + 1;
    case 1: return v * 2;
    case 2: if (v > 5) return v - 5; break;
    }
    return -v;
}

static uint8_t assigned(uint8_t p, uint8_t q)
{
    p = p + q;
    if (p > 100)
        p = 100;
    q = p ^ 0x0F;
    return (uint8_t)(p + q);
}

static uint8_t with_call(uint8_t p, uint8_t q)
{
    out(p, q);
    out(q, p);
    return (uint8_t)(p * 3 + q);
}

int main(void)
{
    int i, s = 0;
    for (i = 0; i < 20; i++) {
        out((uint8_t)i, (uint8_t)(i * 9));
        s += scale((uint8_t)(i * 13), (uint8_t)(i * 7 + 3));
        s += pick((uint8_t)i, i);
        s += assigned((uint8_t)(i * 11), (uint8_t)(i * 5));
        s += with_call((uint8_t)i, (uint8_t)(i + 40));
    }
    for (i = 0; i < 64; i++)
        s += log_buf[i] * (i & 7);
    printf("%d %u %u\n", s, (unsigned)nwrites, (unsigned)busy);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-param-reg",), ("-O0",), ("--no-register-locals",),
    ("--no-register-cc",), ("--no-shared-storage",), ("--isr-safe",),
], ids=ids)
def test_param_reg(tmp_path, flags):
    same_as_host(tmp_path, PARAM_SRC, *flags)


def test_param_reg_shape(tmp_path):
    src = r"""
extern int puts(const char *);
unsigned char sink[8];
unsigned char n;
static void out(unsigned char port, unsigned char value)
{
    sink[n & 3] = port;
    sink[(n & 3) + 4] = value;
    n++;
    if ((port & 0xF8) == 0)
        n += 2;
}
int main(void) { out(1, 2); out(9, 4); puts("ok"); return 0; }
"""
    on = compile_c(tmp_path, src).read_text()
    off = compile_c(tmp_path, src, "--no-param-reg", name="o").read_text()
    f_on = on[on.index("\n_out:"):on.index("@out_ret:")]
    f_off = off[off.index("\n_out:"):off.index("@out_ret:")]
    assert "(??AUTO" in f_off
    assert "(??AUTO" not in f_on
    assert "\tret\tnz" in on or "\tret\tz" in on


# ---- 2c: the frame of a recursive function -------------------------------

FRAME_SRC = r"""
#include <stdio.h>

static int fib_calls;
static int fib(int n)
{
    fib_calls++;
    if (n < 2)
        return n;
    return fib(n - 1) + fib(n - 2);
}

static int ack(int m, int n)
{
    if (m == 0)
        return n + 1;
    if (n == 0)
        return ack(m - 1, 1);
    return ack(m - 1, ack(m, n - 1));
}

static int tak(int x, int y, int z)
{
    if (y >= x)
        return z;
    return tak(tak(x - 1, y, z), tak(y - 1, z, x), tak(z - 1, x, y));
}

static int is_odd(int n);
static int is_even(int n) { return n == 0 ? 1 : is_odd(n - 1); }
static int is_odd(int n) { return n == 0 ? 0 : is_even(n - 1); }

static int addr_of_local(int n)
{
    int local = n * 2;
    int *p = &local;
    if (n > 0)
        *p += addr_of_local(n - 1);
    return local;
}

static int deep(int n, int acc)
{
    int tmp[3];
    tmp[0] = n; tmp[1] = acc; tmp[2] = n + acc;
    if (n == 0)
        return tmp[2];
    return deep(n - 1, tmp[1] + tmp[0]);
}

int main(void)
{
    {
        int f14 = fib(14);
        printf("%d %d\n", f14, fib_calls);
    }
    printf("%d %d\n", ack(2, 3), ack(1, 7));
    printf("%d\n", tak(12, 6, 3));
    printf("%d %d\n", is_even(20), is_odd(7));
    printf("%d\n", addr_of_local(6));
    printf("%d\n", deep(10, 1));
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-frame-drop",), ("-O0",), ("--no-register-locals",),
    ("--no-register-cc",), ("--isr-safe",), ("--no-shared-storage",),
], ids=ids)
def test_frame_drop(tmp_path, flags):
    same_as_host(tmp_path, FRAME_SRC, *flags)


def test_frame_drop_shape(tmp_path):
    on = compile_c(tmp_path, FRAME_SRC).read_text()
    off = compile_c(tmp_path, FRAME_SRC, "--no-frame-drop",
                    name="o").read_text()
    fib_on = on[on.index("\n_fib:"):on.index("\n_ack:")] if "\n_fib:" in on and on.index("\n_fib:") < on.index("\n_ack:") else ""
    # the address-taking function keeps its frame in both builds
    seg = on[on.index("\n_addr_of_local:"):]
    seg = seg[:seg.index("@addr_of_local_ret:")]
    assert "IX" in seg
    assert len(on) < len(off) or on.count("push\tIX") < off.count("push\tIX")


def test_setjmp_keeps_frame(tmp_path):
    src = r"""
#include <stdio.h>
#include <setjmp.h>
static jmp_buf jb;
static int depth(int n)
{
    if (n == 0)
        longjmp(jb, 7);
    return depth(n - 1) + 1;
}
int main(void)
{
    int r = setjmp(jb);
    if (r == 0)
        depth(5);
    printf("%d\n", r);
    return 0;
}
"""
    same_as_host(tmp_path, src)


# ---- 2d: an if with nothing to do ----------------------------------------

DEAD_SRC = r"""
#include <stdio.h>

static unsigned char flags[40];
static int calls;
static int bump(void) { calls++; return calls & 1; }

int main(void)
{
    int i, n = 0;
    for (i = 0; i < 40; i++)
        flags[i] = (i % 3) == 0;
    for (i = 0; i < 40; i++)
        if (!flags[i]) ;
    for (i = 0; i < 40; i++)
        if (bump()) ;
    for (i = 0; i < 10; i++)
        if (n++ > 3) { }
    for (i = 0; i < 10; i++) {
        if (flags[i]) ;
        n += flags[i];
    }
    if (n) ; else ;
    printf("%d %d %d\n", i, calls, n);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-dead-test",), ("-O0",), ("--no-ast-optimize",),
], ids=ids)
def test_dead_test(tmp_path, flags):
    same_as_host(tmp_path, DEAD_SRC, *flags)


def test_dead_test_shape(tmp_path):
    src = r"""
volatile unsigned char port;
unsigned char flags[8];
int main(void)
{
    int i, n = 0;
    for (i = 0; i < 8; i++)
        if (!flags[i]) ;
    if (port) ;
    return n;
}
"""
    on = compile_c(tmp_path, src).read_text()
    off = compile_c(tmp_path, src, "--no-dead-test", name="o").read_text()
    def main_of(text):
        seg = text[text.index("\n_main:"):]
        return seg[:seg.index("@main_ret:")]

    assert "_flags" not in main_of(on)
    assert "_flags" in main_of(off)
    # the read of a volatile object stays
    assert "_port" in main_of(on)


# ---- 2e: pair compare ------------------------------------------------------

PAIR_SRC = r"""
#include <stdio.h>

unsigned char flags[300];
unsigned char other[300];

static int sieve(int size)
{
    int i, k, count = 0;
    for (i = 0; i < size; i++)
        flags[i] = 0;
    for (i = 2; i < size; i++) {
        if (!flags[i]) {
            count++;
            for (k = i + i; k < size; k += i)
                flags[k] = 1;
        }
    }
    return count;
}

static int signed_loop(int start, int step, int limit)
{
    int k, s = 0;
    for (k = start; k <= limit; k += step) {
        other[(k & 255)] = (unsigned char)k;
        s += k;
    }
    return s;
}

static unsigned unsigned_loop(unsigned start, unsigned step, unsigned limit)
{
    unsigned k, s = 0;
    for (k = start; k < limit; k += step)
        s += (unsigned char)k;
    return s;
}

int main(void)
{
    int i, t = 0;
    printf("%d %d\n", sieve(100), sieve(299));
    printf("%d %d\n", signed_loop(-20, 7, 100), signed_loop(3, 11, 200));
    printf("%d\n", signed_loop(-100, 50, -20));
    printf("%u %u\n", unsigned_loop(5, 3, 200), unsigned_loop(0, 17, 250));
    for (i = 0; i < 300; i++)
        t += other[i] + flags[i];
    printf("%d\n", t);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-pair-cmp",), ("-O0",), ("--no-register-locals",),
    ("--no-loop-rotate",), ("--no-licm", "--no-range-cmp", "--no-narrow-index"),
], ids=ids)
def test_pair_compare(tmp_path, flags):
    same_as_host(tmp_path, PAIR_SRC, *flags)


# ---- 2f: loop rotation ---------------------------------------------------------

LOOP_SRC = r"""
#include <stdio.h>

static int calls;
static int cond(int x) { calls++; return x < 7; }

static int nested(int n, int m)
{
    int i, j, s = 0;
    for (i = 0; i < n; i++)
        for (j = 0; j < m; j++)
            s += i * 10 + j;
    return s;
}

static int with_break_continue(int n)
{
    int i, s = 0;
    for (i = 0; i < n; i++) {
        if (i == 3) continue;
        if (i == 11) break;
        s += i;
    }
    i = 0;
    while (i < n) {
        i++;
        if (i & 1) continue;
        if (i > 12) break;
        s += i * 100;
    }
    return s + i;
}

static int zero_trip(int n)
{
    int i, s = 5;
    for (i = 0; i < n; i++) s += 100;
    while (i < 0) { s += 1000; i++; }
    return s;
}

static int do_while(int n)
{
    int s = 0;
    do { s += n; n--; } while (n > 0);
    return s;
}

static int and_test(int n)
{
    int i = 0, s = 0;
    unsigned char c = 0;
    while (i < n && c < 50) {
        s += i;
        i++;
        c += 7;
    }
    return s * 100 + i;
}

static int or_test(int n)
{
    int i = 0, s = 0;
    while (i < 3 || i * 2 < n) {
        s += i;
        i++;
    }
    return s;
}

static int call_in_test(int n)
{
    int i = 0, s = 0;
    while (cond(i) && i < n) {
        s += i;
        i++;
    }
    return s;
}

static int goto_in_out(int n)
{
    int i = 0, s = 0;
    if (n > 5)
        goto inside;
    for (i = 0; i < n; i++) {
        s += 1;
    inside:
        s += 10;
        if (s > 90)
            goto out;
    }
out:
    return s * 10 + i;
}

static long wide(long a, long b)
{
    long s = 0;
    while (a < b) {
        s += a;
        a += 3;
    }
    return s;
}

static int signed_chars(int n)
{
    signed char c;
    int s = 0;
    for (c = -5; c < n; c++)
        s += c;
    return s;
}

static int shifted(unsigned v)
{
    int s = 0;
    while (v) {
        s += v & 1;
        v >>= 1;
    }
    return s;
}

static int sink_g;

/* the exit of the loop is the end of the function: a conditional RET */
static void tail_loop(int n)
{
    int i;
    for (i = 0; i < n; i++)
        sink_g += i;
}

static void tail_while(unsigned v)
{
    while (v) {
        sink_g ^= (int)v;
        v >>= 1;
    }
}

int main(void)
{
    tail_loop(7);
    tail_loop(0);
    tail_while(0x2C5u);
    tail_while(0);
    printf("%d\n", sink_g);
    printf("%d %d\n", nested(4, 5), nested(0, 3));
    printf("%d %d\n", with_break_continue(9), with_break_continue(30));
    printf("%d %d\n", zero_trip(0), zero_trip(4));
    printf("%d %d\n", do_while(5), do_while(0));
    printf("%d %d\n", and_test(3), and_test(30));
    printf("%d %d\n", or_test(2), or_test(20));
    {
        int c1 = call_in_test(4), c2 = call_in_test(30);
        printf("%d %d %d\n", c1, c2, calls);
    }
    printf("%d %d\n", goto_in_out(3), goto_in_out(8));
    printf("%ld %ld\n", wide(5L, 40L), wide(100L, 3L));
    printf("%d %d\n", signed_chars(9), signed_chars(-9));
    printf("%d %d\n", shifted(0x1234), shifted(0));
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-loop-rotate",), ("-O0",), ("--no-register-locals",),
    ("--no-pair-cmp",), ("--no-register-cc",), ("--int=32",),
    ("--no-licm", "--no-range-cmp", "--no-narrow-index"),
], ids=ids)
def test_loop_rotation(tmp_path, flags):
    same_as_host(tmp_path, LOOP_SRC, *flags)


def test_loop_rotation_shape(tmp_path):
    on = compile_c(tmp_path, LOOP_SRC).read_text()
    off = compile_c(tmp_path, LOOP_SRC, "--no-loop-rotate",
                    name="o").read_text()
    assert re.search(r"^@RB\d+:", on, re.M)
    assert not re.search(r"^@RB\d+:", off, re.M)
    # a loop that ends the function is rotated too (the exit is ret cc)
    seg = on[on.index("\n_tail_loop:"):]
    seg = seg[:seg.index("@tail_loop_ret:")]
    assert "@RB" in seg and "\tret\t" in seg


# ---- 2g: the fill idiom ------------------------------------------------------

FILL_SRC = r"""
#include <stdio.h>

unsigned char big[400];
signed char sg[20];
char txt[16];
int words[10];
unsigned char guard[4];
volatile unsigned char vol[10];
static unsigned char lo[12];

int main(void)
{
    int i, j, s = 0;
    unsigned u;
    for (i = 0; i < 400; i++) big[i] = 0x5A;
    s += i;
    for (i = 3; i <= 17; i++) sg[i] = -2;
    s += i;
    for (u = 0; u < 16; ++u) txt[u] = 'x';
    s += (int)u;
    for (i = 0; i < 10; i += 1) words[i] = 7;
    s += i;
    for (i = 0; i < 2; i++) guard[i] = 9;
    s += i;
    for (i = 5; i < 5; i++) lo[i] = 1;
    s += i;
    for (i = 0; i < 10; i++) vol[i] = 3;
    for (j = 0; j < 12; j++) lo[j] = 0x80 | j;
    for (j = 0; j < 12; j++) s += lo[j];
    for (i = 0; i < 400; i++) s += big[i] & 1;
    for (i = 0; i < 20; i++) s += sg[i];
    for (i = 0; i < 16; i++) s += txt[i];
    for (i = 0; i < 10; i++) s += words[i] + vol[i];
    for (i = 0; i < 4; i++) s += guard[i];
    printf("%d\n", s);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-fill-idiom",), ("-O0",), ("--no-blockops",),
    ("--no-register-locals",), ("--int=32",),
], ids=ids)
def test_fill_idiom(tmp_path, flags):
    same_as_host(tmp_path, FILL_SRC, *flags)


def test_fill_idiom_shape(tmp_path):
    on = compile_c(tmp_path, FILL_SRC).read_text()
    off = compile_c(tmp_path, FILL_SRC, "--no-fill-idiom",
                    name="o").read_text()
    assert on.count("ldir") > off.count("ldir")
    # the volatile array and the int array are not filled
    m = on[on.index("_main:"):]
    assert "_vol" in m


# ---- 2h: 1 << n ------------------------------------------------------------

SHL_SRC = r"""
#include <stdio.h>

static int bit(int n) { return 1 << n; }
static unsigned ubit(unsigned n) { return 1u << n; }

int main(void)
{
    int i, s = 0, t = 0;
    unsigned char c = 0xA5;
    for (i = 0; i < 15; i++) {
        s += bit(i) & 0x7FFF;
        t += (c & (1 << i)) != 0;
        s ^= ubit((unsigned)i) >> 1;
    }
    for (i = 7; i >= 0; i--)
        printf("%c", (c & (1 << i)) ? '1' : '0');
    printf("\n%d %d\n", s, t);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-shl-inline",), ("-O0",), ("--no-register-locals",),
], ids=ids)
def test_shl_inline(tmp_path, flags):
    same_as_host(tmp_path, SHL_SRC, *flags)


# ---- inline assembly stays as written ---------------------------------------

def test_dead_copy_pass_leaves_inline_asm_alone(tmp_path):
    src = r"""
extern int puts(const char *);
unsigned char sink;
static void f(unsigned char a)
{
    sink = a;
    __asm__("\tld\tc,l\n\tld\tb,a\n");
    sink++;
}
int main(void) { f(3); puts("ok"); return 0; }
"""
    text = compile_c(tmp_path, src, "--no-inlining").read_text()
    seg = text[text.index("\n_f:"):]
    seg = seg[:seg.index("@f_ret:")]
    assert re.search(r"ld\s+c,l", seg, re.I)
    assert re.search(r"ld\s+b,a", seg, re.I)
