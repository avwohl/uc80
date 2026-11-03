"""Family L step LB: code generation items (docs/family_l.md).  Each test
compares the output of uc80 under cpmemu with the host compiler, for the
default build and for the option that turns the change off."""

import re
import subprocess
import sys

import pytest

from ._faml import needs_toolchain, run_both, host_output, uc80_output
from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO

pytestmark = needs_toolchain


# ---------------------------------------------------------------------------
# L11: a compare of a register local with a word or byte in memory
# ---------------------------------------------------------------------------

CMP_SRC = r"""
#include <stdio.h>
volatile int nloop = 9, nouter = 3;
volatile unsigned char d8[64];
volatile signed char sc = 5;
static unsigned char tab[64];
unsigned lim_u = 7;
int lim_i = -3;
signed char lim_c = 4;

static int loop_uint(void)
{
    int s = 0;
    unsigned lim = lim_u + 3;
    unsigned i;
    for (i = 0; i < lim; i++)
        s += tab[i & 63];
    return s;
}
static int loop_int(int a)
{
    int s = 0, i;
    int lim = lim_i + a;
    for (i = -5; i < lim; i++)
        s += tab[(i + 5) & 63];
    return s;
}
static int loop_schar(signed char a)
{
    int s = 0;
    signed char i;
    signed char lim = (signed char) (lim_c + a);
    for (i = -7; i < lim; i++)
        s += tab[(i + 7) & 63];
    return s;
}
static int loop_down(unsigned a)
{
    int s = 0;
    unsigned i;
    for (i = a; i > lim_u; i--)
        s += tab[i & 63];
    return s;
}
static int cmp_ops(int x, int y)
{
    int r = 0;
    static int lim = 100;
    if (x < lim) r |= 1;
    if (x >= lim) r |= 2;
    if (x > lim) r |= 4;
    if (x <= lim) r |= 8;
    if (x == lim) r |= 16;
    if (x != lim) r |= 32;
    if (y < lim_i) r |= 64;
    return r + (unsigned) x % 3;
}
int main(void)
{
    int k;
    unsigned long u;
    for (k = 0; k < 64; k++)
        tab[k] = (unsigned char) (k * 7 + 3);
    printf("%d %d\n", loop_uint(), loop_int(20));
    printf("%d\n", loop_schar(10));
    printf("%d\n", loop_schar(100));
    printf("%d %d\n", loop_down(30), loop_down(5));
    for (k = -200; k <= 200; k += 37)
        printf("%d ", cmp_ops(k, k));
    printf("\n");
    for (u = 0; u < 70000u; u += 9999)
        printf("%d ", loop_down((unsigned) (u % 100)));
    printf("\n");
    return 0;
}
"""


def test_cmp_mem_matches_host(tmp_path):
    want, got, text = run_both(tmp_path, CMP_SRC)
    assert got == want


def test_cmp_mem_off_matches_host(tmp_path):
    want, got, text = run_both(tmp_path, CMP_SRC, "--no-cmp-mem")
    assert got == want


def test_cmp_mem_removes_the_push(tmp_path):
    _, _, on = run_both(tmp_path, CMP_SRC)
    _, _, off = run_both(tmp_path, CMP_SRC, "--no-cmp-mem")
    pat = re.compile(r"\tpush\t(DE|BC)\n\tld\t\1,\(\?\?AUTO\+\d+\)\n\tld\tA,\w\n"
                     r"\tsub\t\w\n")
    assert len(pat.findall(on)) < len(pat.findall(off))
    assert len(on) < len(off)


# ---------------------------------------------------------------------------
# L6: #pragma uc80 regcall, an external function in the register convention
# ---------------------------------------------------------------------------

RC_SRC = r"""
#include <stdio.h>
#if defined(__linux__) || defined(__APPLE__)
unsigned char out_count;
void out(unsigned char port, unsigned char value)
{
    putchar(port); putchar(value); out_count++;
}
unsigned char in(unsigned char port) { return (unsigned char) (port * 3 + 1); }
unsigned int mix(unsigned int a, unsigned char b)
{
    return a * 2 + b;
}
#else
void out(unsigned char port, unsigned char value);
unsigned char in(unsigned char port);
unsigned int mix(unsigned int a, unsigned char b);
#pragma uc80 regcall out in mix
#endif
static unsigned char seq[6] = { 65, 66, 67, 68, 69, 70 };
int main(void)
{
    unsigned char i, v = 0;
    for (i = 0; i < 6; i++) {
        v = in(i);
        out((unsigned char) (48 + i), seq[i]);
        out((unsigned char) (65 + v % 26), (unsigned char) (97 + v % 20));
    }
    printf("\n%u %u\n", mix(1000, 7), mix(30000u, 255));
    printf("%d\n", in(250) > 100);
    return 0;
}
"""

RC_ASM = """\
; out (A = port, E = value) writes the two characters; in (A = port).
; mix (HL = a, E = b) returns a * 2 + b.
\t.Z80
\tCSEG
\tPUBLIC\t_out
_out:\tPUSH\tDE
\tLD\tE,A
\tLD\tC,2
\tCALL\t5
\tPOP\tDE
\tLD\tC,2
\tCALL\t5
\tRET
\tPUBLIC\t_in
_in:\tLD\tL,A
\tADD\tA,A
\tADD\tA,L
\tINC\tA
\tRET
\tPUBLIC\t_mix
_mix:\tADD\tHL,HL
\tLD\tD,0
\tADD\tHL,DE
\tRET
\tEND
"""


def _regcall_run(tmp_path, *flags, asm=RC_ASM):
    src = tmp_path / "rc.c"
    src.write_text(RC_SRC)
    (tmp_path / "io.mac").write_text(asm)
    mac = tmp_path / "rc.mac"
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src),
                        str(tmp_path / "io.mac"), "-o", str(mac), *flags],
                       capture_output=True, text=True, cwd=str(REPO),
                       timeout=300)
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(tmp_path / "rc.rel")],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(["ul80", str(tmp_path / "rc.rel"),
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", str(tmp_path / "rc.com")],
                   check=True, capture_output=True, timeout=120)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "rc.com")],
                         capture_output=True, timeout=120)
    got = run.stdout.decode("latin-1").replace("\r", "")
    got = "\n".join(ln for ln in got.split("\n")
                    if not ln.startswith(("CPU mode", "Loaded ",
                                          "Program exit", "Note:")))
    return got.strip(), mac.read_text()


def test_regcall_matches_host(tmp_path):
    want = host_output(tmp_path, RC_SRC)
    got, text = _regcall_run(tmp_path)
    assert got == want
    main = text.split("_main:")[1].split("@main_ret")[0]
    assert "call\t_out" in main
    # no argument is popped after a call of out: none was pushed
    assert re.search(r"call\t_out\n\tpop\tDE", main) is None


def test_regcall_off_uses_the_stack(tmp_path):
    _, text = _regcall_run(tmp_path, "--no-regcall",
                           asm=RC_ASM)       # the asm does not match: only
    main = text.split("_main:")[1].split("@main_ret")[0]   # look at the text
    assert "\tpush\tHL" in main


def test_regcall_rejects_address_use(tmp_path):
    src = tmp_path / "bad.c"
    src.write_text("void out(unsigned char p, unsigned char v);\n"
                   "#pragma uc80 regcall out\n"
                   "void (*fp)(unsigned char, unsigned char) = out;\n"
                   "int main(void) { fp(1, 2); return 0; }\n")
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src), "-o",
                        str(tmp_path / "bad.mac")], capture_output=True,
                       text=True, cwd=str(REPO), timeout=120)
    assert r.returncode != 0
    assert "regcall" in r.stderr


# ---------------------------------------------------------------------------
# R4, R2, ternary: bytes in A, one multiply loop
# ---------------------------------------------------------------------------

BYTE_SRC = r"""
#include <stdio.h>
#include <stdint.h>
volatile uint16_t vw = 1234;
volatile uint8_t vb = 77;
volatile int8_t vs = -9;
static uint16_t mulb(uint16_t f, uint8_t b) { return (uint16_t) (f * b); }
static uint16_t mulb2(uint8_t b, uint16_t f) { return (uint16_t) (b * f); }
static int mulsb(int f, uint8_t b) { return f * b; }
static uint8_t bit(uint8_t n) { return (uint8_t) (1 << n); }
static uint8_t bit3(uint8_t n) { return (uint8_t) (3 << n); }
static uint8_t pick(uint8_t c, uint8_t a) { return (uint8_t) (c ? a + 1 : 7); }
static uint8_t pick2(int c) { return (uint8_t) (c < 5 ? 4 : 6); }
static void show(uint8_t v) { printf("%u ", v); }
int main(void)
{
    unsigned n;
    printf("%u %u %d\n", mulb(vw, vb), mulb2(vb, vw), mulsb(-300, vb));
    printf("%u %u\n", mulb(65535u, 255), mulb(0, 255));
    for (n = 0; n < 16; n++) {
        show(bit((uint8_t) n));
        show(bit3((uint8_t) n));
    }
    printf("\n");
    for (n = 0; n < 4; n++) {
        show(pick((uint8_t) n, 200));
        show(pick2((int) n * 3));
    }
    printf("\n%u\n", mulb(1000, (uint8_t) -vs));
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-mul8x16",), ("--no-byte-shl",),
                                   ("--no-byte-tern",), ("-O0",),
                                   ("--no-register-locals",),
                                   ("--int=32",)])
def test_byte_forms_match_host(tmp_path, flags):
    want = host_output(tmp_path, BYTE_SRC)
    got, text = uc80_output(tmp_path, BYTE_SRC, *flags)
    assert got == want


def test_mul8x16_is_called(tmp_path):
    _, _, on = run_both(tmp_path, BYTE_SRC)
    _, _, off = run_both(tmp_path, BYTE_SRC, "--no-mul8x16")
    assert "call\t__mul8x16" in on
    assert "call\t__mul16" not in on
    assert "call\t__mul16" in off


# ---------------------------------------------------------------------------
# A already holds the byte; xor A; the dead local reservation
# ---------------------------------------------------------------------------

AMEM_SRC = r"""
#include <stdio.h>
#include <stdarg.h>
static unsigned char vol, lim = 5, other;
volatile unsigned char hw = 3;
static void adj(unsigned char n)
{
    vol = n;
    if (vol > lim)
        vol -= lim;
    other = (unsigned char) (vol + 1);
    if (vol < 2)
        vol += 2;
    if (hw == 3 && hw == 3)
        other++;
    printf("%d %d ", vol, other);
}
static unsigned sum(const char *fmt, ...)
{
    va_list ap;
    unsigned total = 0;
    va_start(ap, fmt);
    for (; *fmt != 0; fmt++) {
        if (*fmt == 'u')
            total += va_arg(ap, unsigned);
        else
            total++;
    }
    va_end(ap);
    return total;
}
int main(void)
{
    unsigned char k;
    for (k = 0; k < 12; k++)
        adj(k);
    printf("\n%u %u\n", sum("uu.u", 100, 20, 3), sum("", 1));
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-a-mem",), ("--no-zero-xor",),
                                   ("--no-frame-drop",), ("-O0",),
                                   ("--no-flow-opt",)])
def test_a_mem_matches_host(tmp_path, flags):
    want = host_output(tmp_path, AMEM_SRC)
    got, text = uc80_output(tmp_path, AMEM_SRC, *flags)
    assert got == want


def test_a_mem_drops_a_load(tmp_path):
    _, _, on = run_both(tmp_path, AMEM_SRC)
    _, _, off = run_both(tmp_path, AMEM_SRC, "--no-a-mem")
    assert on.count("\tld\tA,(") < off.count("\tld\tA,(")


# ---------------------------------------------------------------------------
# L12: a char result in L, signed byte compares, HL reuse, dead sign extension
# ---------------------------------------------------------------------------

CHAR_SRC = r"""
#include <stdio.h>
typedef signed char sc;
typedef char (*pf_t)(void);
static signed char board[9];
signed char gs[4] = { -128, 127, 0, -1 };
unsigned char gu[4] = { 0, 255, 128, 7 };
static sc score(int k) { return (sc) (k * 37 - 100); }
static unsigned char uscore(int k) { return (unsigned char) (k * 53 + 9); }
static char rec(char n) { if (n <= 0) return 3; return rec(n - 1); }
static sc rec2(sc n) { if (n <= -3) return n; return rec2(n - 1); }
static char w0(void) { return board[0] == board[1] ? board[0] : 0; }
static char w1(void) { register int x = board[1]; return x == board[2] ? x : -5; }
static pf_t tab[2] = { w0, w1 };
static int widen(int k) { int v = score(k); return v * 2; }
static int widen_u(int k) { int v = uscore(k); return v + 1000; }
static long wide_l(int k) { return score(k); }
int main(void)
{
    int i, t = 0;
    int cmp = 0;
    signed char a, b;
    for (i = 0; i < 9; i++)
        board[i] = (signed char) (i & 1 ? -9 : 4);
    board[1] = board[2] = -9;
    for (i = 0; i < 12; i++)
        printf("%d %d %d %d ", score(i), uscore(i), widen(i), widen_u(i));
    printf("\n%d %d %d %d\n", rec(5), rec2(4), tab[0](), tab[1]());
    printf("%ld %ld\n", wide_l(1), wide_l(7));
    for (i = 0; i < 12; i++) {
        if (score(i)) t++;
        if (score(i) > 5) t += 2;
        if (uscore(i) > 100) t += 4;
        if (score(i) == -63) t += 8;
    }
    printf("%d\n", t);
    for (i = 0; i < 4; i++)
        for (a = -128; ; a += 51) {
            for (b = 0; b < 4; b++) {
                if (a < gs[b]) cmp += 1;
                if (a >= gs[b]) cmp += 2;
                if (a > gs[b]) cmp += 4;
                if (a <= gs[b]) cmp += 8;
                if ((unsigned char) a < gu[b]) cmp += 16;
                if (a == gs[b]) cmp += 32;
            }
            if (a > 100) break;
        }
    printf("%d\n", cmp);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-char-noext",),
                                   ("--no-cmp-mem",), ("--no-flow-opt",),
                                   ("-O0",), ("--no-register-locals",),
                                   ("--no-register-cc",), ("--int=32",),
                                   ("--no-hl-reuse", "--no-dead-alu",
                                    "--no-pop-cleanup")])
def test_char_results_match_host(tmp_path, flags):
    want = host_output(tmp_path, CHAR_SRC)
    got, text = uc80_output(tmp_path, CHAR_SRC, *flags)
    assert got == want


def test_char_result_is_not_extended_in_the_callee(tmp_path):
    _, _, on = run_both(tmp_path, CHAR_SRC)
    _, _, off = run_both(tmp_path, CHAR_SRC, "--no-char-noext")
    assert ";@CHARL" in on and ";@CHARL" not in off
    f = on.split("_w0:")[1].split("@w0_ret")[0] if "_w0:" in on else ""
    assert "sbc\tA,A" not in f


BOARD_SRC = r"""
#include <stdio.h>
static unsigned char g_board[9];
static int g_n;
static int visit(int depth)
{
    int i, found = 0;
    g_n++;
    if (depth >= 3)
        return 1;
    for (i = 0; i < 9; i++) {
        if (g_board[i] == 0) {
            g_board[i] = (unsigned char) (1 + (depth & 1));
            found += visit(depth + 1);
            g_board[i] = 0;
        }
    }
    return found;
}
static void pop_args(int a, int b, int c)
{
    g_n += a + b + c;
}
int main(void)
{
    g_board[4] = 7;
    int v;
    pop_args(1, 2, 3);
    v = visit(0);
    printf("%d %d\n", v, g_n);
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-hl-reuse",), ("--no-dead-alu",),
                                   ("--no-pop-cleanup",), ("-O0",),
                                   ("--no-flow-opt",)])
def test_board_matches_host(tmp_path, flags):
    want = host_output(tmp_path, BOARD_SRC)
    got, text = uc80_output(tmp_path, BOARD_SRC, *flags)
    assert got == want


def test_hl_is_loaded_once(tmp_path):
    _, _, on = run_both(tmp_path, BOARD_SRC)
    _, _, off = run_both(tmp_path, BOARD_SRC, "--no-hl-reuse")
    assert on.count("ld\tHL,_g_board") < off.count("ld\tHL,_g_board")


# ---------------------------------------------------------------------------
# L7, L9, L13: chained stores, a big-endian word, ld DE,(X), call reads
# ---------------------------------------------------------------------------

WORD_SRC = r"""
#include <stdio.h>
typedef unsigned char u8;
typedef unsigned short u16;
static u8 mem[300];
static u16 tbl[40];
struct S { u16 a, b, c, d, e; } st;
static u16 keep;
static u8 *getp(int n) { return mem + n; }
static void load(int n)
{
    u8 *p = getp(n);
    st.a = (((u16) p[0]) << 8) | ((u16) p[1]); p += 2;
    st.b = (((u16) p[0]) << 8) | ((u16) p[1]); p += 2;
    st.c = (((u16) p[0]) << 8) | ((u16) p[1]); p += 2;
    st.d = (((u16) p[0]) << 8) | ((u16) p[1]); p += 2;
    st.e = (((u16) p[0]) << 8) | ((u16) p[1]);
}
static void setup(u16 *t)
{
    t[0] = 0x1234; t[1] = 0x5678; t[2] = 9; t[4] = 0xBEEF; t[5] = 1;
    t[6] = 0x7777; t[9] = 12345; t[10] = 23; t[11] = 0xFFFF;
}
static void setup_b(u8 *t)
{
    t[3] = 7; t[4] = 8; t[5] = 9; t[8] = 200; t[9] = 255; t[12] = 1;
}
static u16 swap_load(void) { return keep; }
int main(void)
{
    int i;
    u16 sum = 0;
    for (i = 0; i < 300; i++)
        mem[i] = (u8) (i * 37 + 11);
    load(5);
    printf("%04x %04x %04x %04x %04x\n", st.a, st.b, st.c, st.d, st.e);
    load(200);
    printf("%04x %04x %04x %04x %04x\n", st.a, st.b, st.c, st.d, st.e);
    setup(tbl);
    setup_b(mem + 100);
    for (i = 0; i < 40; i++)
        sum = (u16) (sum * 3 + tbl[i]);
    for (i = 100; i < 114; i++)
        sum = (u16) (sum * 5 + mem[i]);
    keep = sum;
    printf("%u %u\n", sum, swap_load());
    return 0;
}
"""


@pytest.mark.parametrize("flags", [(), ("--no-be16",), ("--no-hl-reuse",),
                                   ("--no-ex-load",), ("--no-call-reads",),
                                   ("-O0",), ("--no-register-locals",),
                                   ("--no-flow-opt",)])
def test_word_forms_match_host(tmp_path, flags):
    want = host_output(tmp_path, WORD_SRC)
    got, text = uc80_output(tmp_path, WORD_SRC, *flags)
    assert got == want


def test_big_endian_word_is_two_loads(tmp_path):
    _, _, on = run_both(tmp_path, WORD_SRC)
    _, _, off = run_both(tmp_path, WORD_SRC, "--no-be16")
    assert on.count("\tld\tD,(HL)\n\tinc\tHL\n\tld\tE,(HL)\n\tex\tDE,HL") >= 3
    assert off.count("\tld\tD,(HL)\n\tinc\tHL\n\tld\tE,(HL)\n\tex\tDE,HL") == 0
    assert len(on) < len(off)


def test_stores_through_a_base_pointer_use_inc():
    """The liveness pass turns ``ld HL,N / add HL,BC`` after a store into
    ``inc HL`` when HL holds BC plus a number that is at most three away."""
    from uc80 import peep_flow
    text = "\n".join([
        "\tld\tHL,10", "\tadd\tHL,BC", "\tld\tDE,4660", "\tld\t(HL),E",
        "\tinc\tHL", "\tld\t(HL),D",
        "\tld\tHL,13", "\tadd\tHL,BC", "\tld\tDE,9", "\tld\t(HL),E",
        "\tinc\tHL", "\tld\t(HL),D",
        "\tld\tHL,40", "\tadd\tHL,BC", "\tld\t(HL),E", "\tret", ""])
    out = peep_flow.optimize(text)
    assert out.count("add\tHL,BC") == 2      # the second add is gone
    assert out.count("\tinc\tHL") == 4      # two stores and two steps
    peep_flow.HL_SUM = False
    try:
        assert peep_flow.optimize(text).count("add\tHL,BC") == 3
    finally:
        peep_flow.HL_SUM = True


def test_char_l_return_mask_ends_with_the_function():
    """The mark ;@CHARL says that the return of one function reads only L.
    The code after the epilogue (the embedded runtime) is another function:
    its return reads every register.  A first version kept the mark and
    deleted the write of H before the return of a runtime helper."""
    from uc80 import peep_flow
    text = "\n".join([
        "; Function f", ";@CHARL", "_f:", "\tld\tL,A", "@f_ret:", "\tret",
        "", "__helper:", "\tld\tH,B", "\tret", ""])
    out = peep_flow.optimize(text)
    assert "\tld\tH,B" in out
    inner = "\n".join(["; Function g", ";@CHARL", "_g:", "\tld\tH,B",
                       "\tld\tL,A", "@g_ret:", "\tret", ""])
    assert "\tld\tH,B" not in peep_flow.optimize(inner)


def test_char_l_mark_needs_no_function_comment():
    """A pass can remove the comment line `; Function g`.  The mark of one
    function then must not reach the next function (ccfuzz seeds 628 and
    673): a function that returns a long keeps the write of E."""
    from uc80 import peep_flow
    text = "\n".join([
        ";@CHARL", "_f:", "\tld\tL,A", "@f_ret:", "\tret",
        "_g:", "\tld\tE,A", "\tld\tD,A", "@g_ret:", "\tret", ""])
    out = peep_flow.optimize(text)
    assert "\tld\tE,A" in out
