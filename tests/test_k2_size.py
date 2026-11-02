"""Family K step 2: smaller code from the code generator and the runtime.

Every test that runs code compiles one C program with uc80 and with the host
compiler, runs both (the host run and the cpmemu run have a timeout) and
compares the output.  Each change has a switch, and the tests run with the
switch on and off.  The pass tests at the end work on assembly text.
See docs/k2_code_size.md.
"""

import re
import shutil
import subprocess
import sys

import pytest

from uc80 import peep_flow, peep_local

from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO, _toolchain_available

needs_tools = pytest.mark.skipif(
    not _toolchain_available() or not shutil.which("cc"),
    reason="um80/ul80/cpmemu, the built libraries or cc are not available")


def run_both(tmp_path, source, *flags):
    """(host output, uc80 output, the .mac text) of one program."""
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


# ---------------------------------------------------------------------------
# K2-1: the address of a function or a string stored through a pointer
# ---------------------------------------------------------------------------

CONST_STORE = r"""
#include <stdio.h>
typedef int (*fn)(int);
struct op { const char *name; fn f; int n; };
static int a(int x) { return x + 1; }
static int b(int x) { return x * 2; }
static int c(int x) { return x - 3; }
struct op tab[6];
struct op *gp;
const char *names[4];
fn fns[4];
int g1, g2;
int *ptrs[3];
static void init(struct op *t, int k)
{
    t[1].name = "one"; t[1].f = a;
    t[k].name = "kth"; t[k].f = b;
    t[k + 1].f = c;
    gp->name = "head";
    gp[2].f = b;
    ((struct op *) gp + 3)->name = "three";
    names[k] = "n";
    names[0] = "zero";
    fns[k - 1] = a;
    fns[3] = c;
    ptrs[0] = &g1;
    ptrs[k - 1] = &g2;
}
int main(void)
{
    int i, k = 2;
    gp = tab;
    init(tab, k);
    for (i = 0; i < 6; i++)
        printf("%d %s %d\n", i, tab[i].name ? tab[i].name : "-",
               tab[i].f ? tab[i].f(10) : -1);
    for (i = 0; i < 4; i++)
        printf("%s %d\n", names[i] ? names[i] : "-", fns[i] ? fns[i](7) : -1);
    *ptrs[0] = 5; *ptrs[1] = 9;
    printf("%d %d\n", g1, g2);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-const-store",)])
def test_const_store_matches_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, CONST_STORE, *flags)
    assert got == want
    if not flags:
        assert re.search(r"\tld\t(DE|BC),@STR", text)


@needs_tools
def test_const_store_shrinks_the_code(tmp_path):
    def size(*flags):
        d = tmp_path / ("a" + "".join(flags))
        d.mkdir()
        _w, _g, text = run_both(d, CONST_STORE, *flags)
        return len(text)
    assert size() < size("--no-const-store")


# ---------------------------------------------------------------------------
# K2-3 (part): a counted for loop has its test at the end
# ---------------------------------------------------------------------------

FOR_PROG = r"""
#include <stdio.h>
static int s, cnt;
#define T(TYPE, INIT, OP, BOUND, UPD) { TYPE i; s = 0; cnt = 0; \
    for (i = INIT; i OP BOUND; UPD) { s += (int) i + 1; cnt++; if (cnt > 20) break; } \
    printf("%d %d\n", s, cnt); }
#define ALL(TYPE) \
    T(TYPE, 0, <, 0, i++) T(TYPE, 0, <, 1, i++) T(TYPE, 0, <, 4, i++) \
    T(TYPE, 0, <=, 4, i++) T(TYPE, 1, <, 8, i++) T(TYPE, 3, <=, 5, i += 2) \
    T(TYPE, 5, >, 2, i--) T(TYPE, 5, >=, 0, i--) T(TYPE, 4, >, 4, i--) \
    T(TYPE, 3, !=, 6, i++) T(TYPE, 0, !=, 3, i++) T(TYPE, 2, ==, 2, i += 5) \
    T(TYPE, 7, >, 0, i -= 3)
int main(void)
{
    int j, k, total = 0;
    ALL(int) ALL(unsigned char) ALL(unsigned) ALL(long) ALL(short) ALL(char)
    ALL(unsigned long)
    /* nested, with continue and break */
    for (j = 0; j < 4; j++)
        for (k = 2; k <= 5; k++) {
            if (k == 3) continue;
            if (j == 2 && k == 5) break;
            total += j * 10 + k;
        }
    printf("%d\n", total);
    for (int q = 1; q < 3; q++) total += q;
    printf("%d\n", total);
    {
        volatile int v;
        int w = 0;
        for (v = 0; v < 3; v++) w += 2;
        printf("%d\n", w);
    }
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-for-entry",), ("-O0",),
                                   ("--int=32",)])
def test_for_entry_matches_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, FOR_PROG, *flags)
    assert got == want
    if not flags:
        assert "@FORB" in text
    if flags == ("--no-for-entry",):
        assert "@FORB" not in text


# ---------------------------------------------------------------------------
# K2-5: the compact __div16 and the shared signed divide and modulo
# ---------------------------------------------------------------------------

DIV_PROG = r"""
#include <stdio.h>
#include <stdint.h>
static uint16_t seed = 7;
static uint16_t rnd(void) { seed = seed * 25173u + 13849u; return seed; }
static uint16_t udivs[] = {1, 2, 3, 7, 10, 100, 127, 128, 129, 255, 256, 257,
                           300, 511, 512, 1000, 4095, 4096, 12345, 32767,
                           32768, 32769, 40000, 65535};
int main(void)
{
    int i, j;
    uint32_t h1 = 0, h2 = 0, h3 = 0, h4 = 0;
    for (i = 0; i < 24; i++) {
        uint16_t d = udivs[i];
        for (j = 0; j < 40; j++) {
            uint16_t x = rnd();
            if (j == 0) x = 0;
            if (j == 1) x = 65535;
            if (j == 2) x = d;
            if (j == 3) x = d - 1;
            h1 = h1 * 31 + (uint16_t) (x / d);
            h2 = h2 * 31 + (uint16_t) (x % d);
        }
    }
    for (i = 0; i < 24; i++) {
        int16_t d = (int16_t) udivs[i];
        if (d == -32768) continue;
        for (j = 0; j < 40; j++) {
            int16_t x = (int16_t) rnd();
            int16_t dd = (j & 1) ? -d : d;
            if (j == 0) x = -32768;
            if (j == 1) x = 32767;
            if (dd == 0) continue;
            h3 = h3 * 31 + (uint16_t) (x / dd);
            h4 = h4 * 31 + (uint16_t) (x % dd);
        }
    }
    printf("%lu %lu %lu %lu\n", (unsigned long) h1, (unsigned long) h2,
           (unsigned long) h3, (unsigned long) h4);
    return 0;
}
"""

SDM_PROG = r"""
#include <stdio.h>
#include <stdint.h>
static uint16_t seed = 99;
static int16_t rnd(void) { seed = seed * 25173u + 13849u; return (int16_t) seed; }
int main(void)
{
    int i;
    uint32_t h = 0;
    for (i = 0; i < 3000; i++) {
        int16_t x = rnd(), y = rnd();
        if ((i & 7) == 0) y >>= 8;
        if ((i & 15) == 1) y >>= 12;
        if ((i & 3) == 2) x >>= 4;
        if (y == 0) y = 1;
        if (x == -32768 && y == -1) y = 2;
        h = h * 31 + (uint16_t) (x / y);
        h = h * 17 + (uint16_t) (x % y);
    }
    printf("%lu\n", (unsigned long) h);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-div16-compact",)])
def test_div16_matches_host(tmp_path, flags):
    want, got, _text = run_both(tmp_path, DIV_PROG, *flags)
    assert got == want


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-sdivmod-merge",),
                                   ("--no-div16-compact",)])
def test_signed_divmod_matches_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, SDM_PROG, *flags)
    assert got == want
    if not flags:
        assert "__sdiv16m" in text and "__smod16m" in text
    if flags == ("--no-sdivmod-merge",):
        assert "__sdiv16m" not in text


@needs_tools
def test_signed_divmod_alone_keeps_the_single_routine(tmp_path):
    src = SDM_PROG.replace("h = h * 17 + (uint16_t) (x % y);", "")
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "__sdiv16m" not in text and "call\t__sdiv16" in text


@needs_tools
def test_div32_and_div16_together_match_host(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdint.h>
int main(void)
{
    uint32_t a = 4000000000UL, h = 0;
    uint16_t d, i;
    for (i = 1; i < 60; i++) {
        d = (uint16_t) (i * 1111u);
        h = h * 31 + a / d + (uint16_t) (a % d);
        h = h * 31 + (uint16_t) (60000u / d) + 60000u % d;
        h = h * 31 + (uint16_t) ((int16_t) (i * 77) / (int16_t) (d | 1));
        a += 123457UL;
    }
    printf("%lu\n", (unsigned long) h);
    return 0;
}
"""
    want, got, _text = run_both(tmp_path, src)
    assert got == want


# ---------------------------------------------------------------------------
# K2-5: __puts_s
# ---------------------------------------------------------------------------

PUTS_PROG = r"""
#include <stdio.h>
int main(void)
{
    const char *p = "from a pointer";
    puts("one");
    puts("");
    puts(p);
    printf("two\n");
    printf("%d\n", puts("three") >= 0);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-puts-small",)])
def test_puts_matches_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, PUTS_PROG, *flags)
    assert got == want
    assert ("__puts_s" in text) == (not flags)


@needs_tools
def test_puts_defined_by_the_program_is_kept(tmp_path):
    src = r"""
#include <stdio.h>
int puts(const char *s) { putchar('<'); while (*s) putchar(*s++); putchar('>'); putchar('\n'); return 0; }
int main(void) { puts("mine"); return 0; }
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert "__puts_s" not in text


# ---------------------------------------------------------------------------
# The shared start and end of the arms of an if/else
# ---------------------------------------------------------------------------

BRANCH_PROG = r"""
#include <stdio.h>
#include <stdint.h>
static uint32_t crc32_bit(const uint8_t *p, int len)
{
    uint32_t crc = 0xFFFFFFFFUL;
    int k;
    while (len--) {
        crc ^= *p++;
        for (k = 0; k < 8; k++) {
            if (crc & 1u)
                crc = (crc >> 1) ^ 0xEDB88320UL;
            else
                crc >>= 1;
        }
    }
    return crc ^ 0xFFFFFFFFUL;
}
static uint16_t arms(uint16_t x, uint16_t y)
{
    uint16_t r;
    if (x & 4) { r = y + 3; r = r * 2; r = r + 1; }
    else { r = y + 3; r = r * 2; }
    if (y > x) { x = y; x ^= 5; r += x; } else { x = y; r += x; }
    return r + x;
}
static int twoway(int a, int b)
{
    int r;
    if (a > b) { r = a - b; r <<= 1; } else { r = b - a; r <<= 1; }
    return r;
}
int main(void)
{
    uint8_t buf[40];
    int i;
    uint16_t h = 0;
    for (i = 0; i < 40; i++) buf[i] = (uint8_t) (i * 37 + 11);
    printf("%lu\n", (unsigned long) crc32_bit(buf, 40));
    for (i = 0; i < 200; i++) h = h * 31 + arms((uint16_t) (i * 41), (uint16_t) (i * 17 + 3));
    for (i = -50; i < 50; i += 7) h = h * 31 + (uint16_t) twoway(i, 10 - i);
    printf("%lu\n", (unsigned long) h);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-branch-merge",), ("-O0",)])
def test_branch_merge_matches_host(tmp_path, flags):
    want, got, _text = run_both(tmp_path, BRANCH_PROG, *flags)
    assert got == want


def merge(text):
    old = (peep_local.BRANCH_MERGE, peep_local.RET_JMP)
    peep_local.BRANCH_MERGE = True
    peep_local.RET_JMP = False
    try:
        return peep_local.optimize(text)
    finally:
        peep_local.BRANCH_MERGE, peep_local.RET_JMP = old


ARMS = """_f:
	ld	A,(_x)
	and	1
	jr z,@ELSE
	ld	HL,(_a)
	ld	DE,(_b)
	srl	D
	rr	E
	ld	A,L
	xor	32
	ld	L,A
	ld	(_a),HL
	ld	(_b),DE
	jr @END
@ELSE:
	ld	HL,(_a)
	ld	DE,(_b)
	srl	D
	rr	E
	ld	(_a),HL
	ld	(_b),DE
@END:
	inc	C
	ret
"""


def test_branch_merge_moves_the_common_start_and_end():
    out = merge(ARMS)
    assert out.count("srl\tD") == 1
    assert out.count("ld\t(_a),HL") == 1
    assert out.index("srl\tD") < out.index("ld\tA,(_x)")
    assert "@ELSES:" in out


def test_branch_merge_keeps_a_test_that_the_start_changes():
    # the start writes A, which the test reads: no hoist
    text = ARMS.replace("ld\tA,(_x)\n\tand\t1",
                        "and\t1").replace("srl\tD", "ld\tA,E")
    out = merge(text)
    assert out.index("ld\tA,E") > out.index("and\t1")


def test_branch_merge_does_not_share_a_jump():
    text = """_f:
	ld	A,(_x)
	or	A
	jr z,@E
	ld	HL,1
	jr @_f_ret
	jr @END
@E:
	ld	HL,0
	jr @_f_ret
@END:
@_f_ret:
	ret
"""
    out = merge(text)
    assert out.count("jr @_f_ret") + out.count("jr\t@_f_ret") == 2


def test_branch_merge_needs_flags_dead_after_the_start():
    # the start changes the flags and the first instruction after it reads
    # the carry: no hoist
    text = ARMS.replace("ld\tA,L\n\txor\t32\n\tld\tL,A",
                        "adc\tA,L\n\tld\tL,A")
    out = merge(text)
    assert out.count("srl\tD") == 2


# ---------------------------------------------------------------------------
# The signed test x < 1
# ---------------------------------------------------------------------------

SIGN_PROG = r"""
#include <stdio.h>
int n;
static int gt(void) { return n > 0; }
static int le(void) { return n <= 0; }
static int lt1(void) { if (n < 1) return 7; return 8; }
static int ge1(void) { if (n >= 1) return 7; return 8; }
int main(void)
{
    int v, k, h = 0;
    static const int vals[] = {-32768, -32767, -300, -2, -1, 0, 1, 2, 255, 256,
                               257, 32766, 32767};
    for (k = 0; k < 13; k++) {
        n = vals[k];
        h = (h * 3 + gt()) & 0xFFF; h = (h * 3 + le()) & 0xFFF; h = (h * 3 + lt1()) & 0xFFF; h = (h * 3 + ge1()) & 0xFFF;
        printf("%d %d %d %d %d\n", n, gt(), le(), lt1(), ge1());
    }
    v = 5;
    while (v > 0) v -= 2;
    n = v;
    k = 0;
    while (n < 3) { n++; k++; }
    printf("%d %d %d\n", v, k, h & 255);
    for (v = 40; v > 0; v -= 7) h++;
    printf("%d\n", h);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-sign-one",)])
def test_sign_one_matches_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, SIGN_PROG, *flags)
    assert got == want
    if not flags:
        assert "\tbit\t7,H" in text


# ---------------------------------------------------------------------------
# The liveness pass
# ---------------------------------------------------------------------------

FLOW_PROG = r"""
#include <stdio.h>
#include <stdint.h>
#include <string.h>
static unsigned char tab[16];
static uint16_t sum(const unsigned char *p, uint16_t len)
{
    uint16_t s = 0;
    while (len--)
        s = s * 3 + *p++;
    return s;
}
static int cmp2(const char *a, const char *b, int k)
{
    return (strcmp(a, b) > 0) - (strcmp(a, b) < 0) + k;
}
static uint16_t crc16(const unsigned char *p, uint16_t len)
{
    uint16_t crc = 0xFFFF;
    unsigned char k;
    while (len--) {
        crc ^= (uint16_t) ((uint16_t) *p++ << 8);
        for (k = 0; k < 8; k++)
            crc = (uint16_t) ((crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1);
    }
    return crc;
}
int main(void)
{
    int i, t = 0;
    for (i = 0; i < 16; i++) tab[i] = (unsigned char) (i * 29 + 3);
    printf("%lu %lu\n", (unsigned long) sum(tab, 16), (unsigned long) sum(tab, 0));
    printf("%lu\n", (unsigned long) crc16(tab, 16));
    for (i = 0; i < 5; i++)
        t += cmp2("abc", i & 1 ? "abd" : "abc", i);
    printf("%d\n", t);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-flow-opt",)])
def test_flow_opt_matches_host(tmp_path, flags):
    want, got, _text = run_both(tmp_path, FLOW_PROG, *flags)
    assert got == want


def flow(text):
    old = peep_flow.ENABLED
    peep_flow.ENABLED = True
    try:
        return peep_flow.optimize(text)
    finally:
        peep_flow.ENABLED = old


def test_flow_removes_a_copy_chain():
    text = """_f:
	ld	H,B
	ld	L,H
	ld	A,L
	ld	E,A
	xor	E
	ld	L,A
	ld	H,0
	ret
"""
    out = flow(text)
    # H, L and A are written again before ret reads them: the chain stays
    # only in the form ld E,B
    assert "ld\tE,B" in out
    assert "ld\tH,B" not in out
    assert "ld\tL,H" not in out


def test_flow_keeps_a_write_that_a_call_reads():
    text = """_f:
	ld	E,A
	ld	D,0
	call	_g
	ret
"""
    assert flow(text) == text


def test_flow_keeps_a_write_read_on_the_other_path():
    text = """_f:
	ld	E,5
	or	A
	jr	z,@a
	ld	E,6
@a:
	ld	A,E
	ret
"""
    assert flow(text) == text


def test_flow_does_not_store_to_memory_through_c():
    text = """_f:
	ld	C,A
	ld	A,5
	ld	(_x),A
	ld	A,C
	ld	(_y),A
	ret
"""
    out = flow(text)
    assert "ld\t(_y),C" not in out


def test_flow_cleans_up_arguments_with_pop_af():
    text = """_f:
	push	BC
	push	HL
	call	_g
	inc	SP
	inc	SP
	inc	SP
	inc	SP
	pop	BC
	ld	A,H
	or	L
	ret
"""
    out = flow(text)
    assert out.count("pop\tAF") == 2
    assert "inc\tSP" not in out


def test_flow_keeps_inc_sp_when_a_is_live():
    text = """_f:
	push	HL
	call	_g
	inc	SP
	inc	SP
	ld	(_x),A
	ret
"""
    out = flow(text)
    assert out.count("inc\tSP") == 2


def test_flow_post_decrement_test():
    text = """_f:
	ld	HL,(_n)
	push	HL
	dec	HL
	ld	(_n),HL
	pop	HL
	ld	A,H
	or	L
	jr	z,@e
	ld	H,B
	ld	L,C
@e:
	ret
"""
    out = flow(text)
    assert "push\tHL" not in out
    assert out.index("or\tL") < out.index("dec\tHL")


def test_flow_post_decrement_keeps_hl_when_live():
    text = """_f:
	ld	HL,(_n)
	push	HL
	dec	HL
	ld	(_n),HL
	pop	HL
	ld	A,H
	or	L
	jr	z,@e
	ld	(_m),HL
@e:
	ret
"""
    out = flow(text)
    assert "push\tHL" not in out
    assert "inc\tHL" in out


def test_flow_removes_dead_code_after_a_jump():
    text = """_f:
	jr	@a
	jr	@b
@a:
	jr	@c
@c:
	ret
"""
    out = flow(text)
    assert "@b" not in out
    assert "jr" not in out


def test_flow_leaves_a_run_with_exx_alone():
    text = """_f:
	ld	E,A
	exx
	ret
"""
    assert flow(text) == text


def test_flow_forwards_a_word_that_a_pair_holds():
    text = """_f:
	ld	HL,(_a)
	ld	DE,(_a+2)
	ld	(??AUTO+0),HL
	ld	(??AUTO+2),DE
	ld	HL,(??AUTO+0)
	ld	DE,(??AUTO+2)
	ld	(_b),HL
	ld	BC,(??AUTO+0)
	ld	(HL),A
	ld	HL,(??AUTO+0)
	ret
"""
    out = flow(text)
    assert "ld\tHL,(??AUTO+0)" in out          # after the store through HL
    assert out.count("ld\tHL,(??AUTO+0)") == 1
    assert "ld\tDE,(??AUTO+2)" not in out
    assert "ld\tB,H\n\tld\tC,L" in out         # BC from the pair HL


def test_flow_forwarding_stops_at_a_label_a_call_and_an_overlap():
    for stop in ("@x:", "\tcall\t_g", "\tld\t(??AUTO+1),A",
                 "\tld\t(??AUTO+0),A", "\tldir"):
        text = f"""_f:
	ld	(??AUTO+0),HL
{stop}
	ld	HL,(??AUTO+0)
	ret
"""
        out = flow(text)
        assert "ld\tHL,(??AUTO+0)" in out, stop


def test_flow_forwarding_ignores_other_labels_in_a_store():
    text = """_f:
	ld	(??AUTO+0),HL
	ld	(_x),A
	ld	HL,(??AUTO+0)
	ret
"""
    out = flow(text)
    assert "ld\tHL,(??AUTO+0)" not in out


def test_flow_forwarding_does_not_track_fixed_addresses():
    text = """_f:
	ld	(___abs4010us),HL
	ld	(___abs4010uc),A
	ld	HL,(___abs4010us)
	ret
"""
    assert "ld\tHL,(___abs4010us)" in flow(text)


def test_flow_keeps_a_jump_into_halt_loop():
    text = """__halt:
	HALT
	JR	__halt
"""
    assert flow(text) == text


def test_flow_conditional_return_falls_through():
    text = """_f:
	ld	E,1
	ret	nz
	ld	E,2
	ld	A,E
	ret
"""
    out = flow(text)
    assert "ld\tE,1" in out and "ld\tE,2" in out


def test_peep_jump_to_ret_becomes_ret():
    text = """_f:
	ld	A,B
	or	A
	jr	z,@L1
	ld	HL,1
	jr	@f_ret
@L1:
	ld	HL,0
@f_ret:
	ret
"""
    old = peep_local.RET_JMP
    try:
        peep_local.RET_JMP = True
        out = peep_local.optimize(text)
        peep_local.RET_JMP = False
        out_off = peep_local.optimize(text)
    finally:
        peep_local.RET_JMP = old
    assert "jr\t@f_ret" not in out and "\tret\n@L1:" in out
    assert "jr\t@f_ret" in out_off


def test_peep_cp_zero_becomes_or_a_only_for_zero_and_carry_flags():
    on = """_f:
	ld	A,H
	cp	0
	ret	nz
	ld	A,L
	cp	0
	jp	po,@x
@x:
	ret
"""
    old = peep_local.CP_ZERO
    try:
        peep_local.CP_ZERO = True
        out = peep_local.optimize(on)
        peep_local.CP_ZERO = False
        out_off = peep_local.optimize(on)
    finally:
        peep_local.CP_ZERO = old
    assert out.count("cp\t0") == 1            # the one before jp po stays
    assert out.count("or\tA") == 1
    assert out_off.count("cp\t0") == 2


RET_PROG = r"""
#include <stdio.h>
static int f(int x)
{
    if (x > 5) return 1;
    if (x < -5) return 2;
    if (x == 0) return 3;
    return 4;
}
static unsigned char g(unsigned char c)
{
    if (c == 0) return 10;
    if (c > 200) return 11;
    return c;
}
static int cmp0(unsigned x, unsigned y)
{
    if ((x >> 8) != 0) return 1;
    if ((y & 255) == 0) return 2;
    return 0;
}
int main(void)
{
    int i, h = 0;
    for (i = -9; i < 12; i++) h = (h * 7 + f(i)) & 0x3FFF;
    for (i = 0; i < 256; i += 5) h = (h * 7 + g((unsigned char) i)) & 0x3FFF;
    for (i = 0; i < 40; i++) h = (h * 7 + cmp0((unsigned) (i * 60), (unsigned) (i * 17))) & 0x3FFF;
    printf("%d\n", h);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-ret-jump", "--no-cp-zero")])
def test_ret_jump_and_cp_zero_match_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, RET_PROG, *flags)
    assert got == want
    if not flags:
        assert "or\tA" in text


# ---------------------------------------------------------------------------
# A counted loop of at most 4 passes is unrolled
# ---------------------------------------------------------------------------

UNROLL_PROG = r"""
#include <stdio.h>
#include <stdint.h>
int gi;
int tab[4][16];
unsigned char ub[8];
static int seen_i(void) { return gi * 10; }
static void bump(void) { gi++; }
static int total;
#define LOOP(TYPE, A, OP, B, S) { TYPE v; int s = 0; \
    for (v = A; v OP B; v += S) { s = s * 3 + (int) v + 1; } \
    printf("%d %d\n", s, (int) v); }
#define SET(TYPE) \
    LOOP(TYPE, 0, <, 1, 1) LOOP(TYPE, 0, <=, 0, 1) LOOP(TYPE, 0, <, 4, 1) \
    LOOP(TYPE, 1, <=, 4, 1) LOOP(TYPE, 2, <, 9, 3) LOOP(TYPE, 0, <, 0, 1) \
    LOOP(TYPE, 3, <=, 5, 2) LOOP(TYPE, 5, <, 5, 1)
int main(void)
{
    int i, j, k, n = 0;
    uint16_t u;
    SET(int) SET(unsigned) SET(unsigned char) SET(short) SET(long)
    SET(unsigned short)
    /* a nest that folds to constant addresses */
    for (i = 0; i <= 3; i++) for (j = 0; j <= 1; j++) for (k = 0; k <= 0; k++)
        tab[0][i + 8 * (j + 8 * k)] = 1 + i;
    for (i = 0; i <= 1; i++) for (j = 0; j <= 0; j++) for (k = 0; k <= 3; k++)
        tab[1][i + 2 * (j + 2 * k)] = 5 + k;
    for (i = 0; i < 4; i++) { n += tab[0][i] + tab[0][i + 8] + tab[1][i]; }
    printf("%d %d %d %d\n", n, i, j, k);
    /* the counter is a global that a function reads */
    for (gi = 0; gi < 3; gi++) total += seen_i();
    printf("%d %d\n", total, gi);
    /* a call in the body changes the global counter */
    n = 0;
    for (gi = 0; gi < 3; gi++) { bump(); n += gi; }
    printf("%d %d\n", n, gi);
    /* the counter is unsigned char and the body adds it */
    for (u = 1; u < 4; u++) ub[u] = (unsigned char) (u * 100);
    printf("%d %d %d %d %d\n", ub[1], ub[2], ub[3], ub[0], (int) u);
    /* the counter is changed in the body or has its address taken */
    for (i = 0; i < 4; i++) { n++; if (i == 1) i++; }
    printf("%d %d\n", n, i);
    {
        int q, *pq = &q, sum = 0;
        for (q = 0; q < 3; q++) { sum += *pq; *pq += 0; }
        printf("%d %d\n", sum, q);
    }
    /* sizeof of the counter must not change */
    {
        unsigned char c8;
        int sz = 0;
        for (c8 = 0; c8 < 3; c8++) sz += (int) sizeof(c8) + (int) sizeof c8;
        printf("%d %d\n", sz, c8);
    }
    /* continue and break keep the loop */
    for (i = 0; i < 4; i++) { if (i == 2) continue; n += i; }
    for (i = 0; i < 4; i++) { if (i == 3) break; n += 7; }
    printf("%d %d\n", n, i);
    /* a loop in a loop with a call in the inner body */
    for (i = 0; i < 3; i++) for (j = 0; j < 2; j++) n += seen_i() + i + j;
    printf("%d %d %d\n", n, i, j);
    return 0;
}
"""


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-loop-unroll",), ("-O0",),
                                   ("--int=32",)])
def test_loop_unroll_matches_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, UNROLL_PROG, *flags)
    assert got == want


@needs_tools
def test_loop_unroll_shrinks_a_nest_of_constant_stores(tmp_path):
    src = r"""
int p[512];
int main(void)
{
    int i, j, k;
    for (i = 0; i <= 3; i++) for (j = 0; j <= 1; j++) for (k = 0; k <= 0; k++)
        p[i + 8 * (j + 8 * k)] = 1;
    return p[9];
}
"""
    want, got, on = run_both(tmp_path, src)
    d = tmp_path / "off"
    d.mkdir()
    _w, _g, off = run_both(d, src, "--no-loop-unroll")
    assert "@FORB" not in on and "ld\t(_p+18),HL" in on
    assert len(on) < len(off)


def test_flow_conditional_call_does_not_stop_the_pass():
    text = """_f:
	ld	H,B
	ld	L,H
	ld	A,L
	bit	7,D
	call	nz,_g
	ld	E,A
	ret
"""
    out = flow(text)
    assert "ld\tA,B" in out                 # the copy chain was folded


def test_flow_conditional_call_keeps_what_the_callee_may_read():
    text = """_f:
	ld	A,5
	or	D
	call	nz,_g
	ld	A,2
	ret
"""
    out = flow(text)
    assert "ld\tA,5" in out


def test_flow_exx_leaves_only_its_own_function_alone():
    text = """_a:
	ld	B,5
	exx
	ld	B,7
	exx
	ld	A,B
	ret
_b:
	ld	H,B
	ld	L,H
	ld	A,L
	ld	E,A
	ret
"""
    out = flow(text)
    assert out.split("_b:")[0] == text.split("_b:")[0]
    assert "ld\tL,B" in out.split("_b:")[1]


def test_flow_keeps_a_load_that_sets_the_flags():
    text = """_f:
	ld	A,I
	ld	A,0
	jp	po,@x
	inc	A
@x:
	ld	(_y),A
	ret
"""
    assert flow(text) == text


def test_flow_keeps_the_load_of_an_address():
    text = """_f:
	ld	HL,__sc_float_on
	ld	HL,0
	ret
"""
    assert "__sc_float_on" in flow(text)


def test_flow_folds_a_constant_offset():
    text = """_f:
	ld	HL,4
	add	HL,BC
	inc	HL
	inc	HL
	ld	A,(HL)
	ret
"""
    out = flow(text)
    assert "ld\tHL,6" in out and "inc\tHL" not in out


def test_flow_folds_an_offset_through_a_register_that_dies():
    text = """_f:
	ld	DE,4151
	add	HL,DE
	inc	HL
	inc	HL
	ld	E,(HL)
	inc	HL
	ld	D,(HL)
	ret
"""
    out = flow(text)
    assert "ld\tDE,4153" in out


def test_flow_keeps_an_offset_when_the_carry_is_read():
    text = """_f:
	ld	HL,4
	add	HL,BC
	inc	HL
	jr	c,@x
@x:
	ret
"""
    assert flow(text) == text


def test_flow_forgets_a_constant_after_a_write_and_a_label():
    text = """_f:
	ld	HL,1
	ld	(_a),HL
	inc	HL
	ld	HL,1
	ld	(_b),HL
@x:
	ld	HL,1
	ld	(_c),HL
	ret
"""
    out = flow(text)
    assert out.count("ld\tHL,1") == 3


def test_flow_keeps_the_second_load_of_a_constant_after_a_call():
    text = """_f:
	ld	HL,1
	call	_g
	ld	HL,1
	ret
"""
    assert flow(text).count("ld\tHL,1") == 2


# ---------------------------------------------------------------------------
# __mul16c: the multiply of 25 bytes
# ---------------------------------------------------------------------------

MUL_MAIN = r"""
#include <stdio.h>
#include <stdint.h>
static int16_t vals[] = {0, 1, 2, 3, 5, 7, 10, 100, 127, 128, 255, 256, 257,
                         1000, 4096, 12345, 32767, -1, -2, -3, -128, -255,
                         -256, -1000, -12345, -32768};
int main(void)
{
    int i, j;
    uint32_t h = 0;
    int16_t a, b;
    for (i = 0; i < 26; i++) {
        a = vals[i];
        b = vals[(i * 7 + 3) % 26];
        h = h * 31 + (uint16_t) (a * b);
        b = vals[(i * 11 + 5) % 26];
        h = h * 31 + (uint16_t) (a * b);
    }
    printf("%lu\n", (unsigned long) h);
    return 0;
}
"""

MUL_NESTED = MUL_MAIN.replace(
    "    for (i = 0; i < 26; i++) {",
    "    for (j = 0; j < 2; j++)\n    for (i = 0; i < 26; i++) {")


@needs_tools
@pytest.mark.parametrize("flags", [(), ("--no-mul16c",)])
def test_mul16c_matches_host(tmp_path, flags):
    want, got, text = run_both(tmp_path, MUL_MAIN, *flags)
    assert got == want
    assert ("__mul16c" in text) == (not flags)


@needs_tools
def test_mul16c_is_not_used_in_a_loop_nest_or_outside_main(tmp_path):
    want, got, text = run_both(tmp_path, MUL_NESTED)
    assert got == want
    assert "__mul16c" not in text and "__mul16" in text
    src = r"""
#include <stdio.h>
static int mul(int a, int b) { return a * b; }
static int (*volatile pm)(int, int) = mul;
int main(void)
{
    int i, s = 0;
    for (i = 0; i < 20; i++) s += pm(i, i + 3);
    printf("%d\n", s);
    return 0;
}
"""
    d = tmp_path / "f"
    d.mkdir()
    want, got, text = run_both(d, src)
    assert got == want
    assert "__mul16c" not in text


@needs_tools
def test_mul16c_all_small_products_match_host(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdint.h>
int main(void)
{
    uint16_t a, b;
    uint32_t h = 0;
    for (a = 0; a < 300; a += 7)
        for (b = 0; b < 300; b += 5) h = h * 31 + (uint16_t) (a * b);
    for (a = 65000; a != 64990; a++) h = h * 31 + (uint16_t) (a * 3u);
    printf("%lu\n", (unsigned long) h);
    return 0;
}
"""
    want, got, _text = run_both(tmp_path, src, "--no-loop-unroll")
    assert got == want
