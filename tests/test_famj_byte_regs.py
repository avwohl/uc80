"""Family J step 4: byte locals in B, C, D and E (docs/byte_regs.md).

Each program runs under cpmemu (with a timeout) and on the host compiler.
Both outputs must be the same.  The programs use only values that are the
same for a 16-bit and a 32-bit int.  The shape tests read the generated
text: with the pass on, four byte locals share BC and DE and the address
sums use A only; with --no-byte-regs the text is the old one.
"""

import re

import pytest

from .test_famj_kernels import ids, same_as_host
from .test_famj_startup import compile_c
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

FLAGS = [
    (), ("--no-byte-regs",), ("-O0",), ("--no-register-locals",),
    ("--no-licm-load",), ("--no-pair-cmp",), ("--no-register-cc",),
    ("--isr-safe",), ("--no-skip-auto-clear",), ("--no-rl-addr-taken",),
    ("--no-range-byte-cmp",),
]

# The shape of the 8 queens search: four byte locals, three arrays that
# the index sums reach, a recursive call.
QUEENS_SRC = r"""
#include <stdio.h>

#define NMAX 8
unsigned char n = 6;
unsigned char col[NMAX], diag1[2 * NMAX - 1], diag2[2 * NMAX - 1];
unsigned char pos[NMAX];
unsigned int solutions, sum;

static void place(unsigned char row)
{
    unsigned char c;
    if (row == n) {
        unsigned char i;
        unsigned int h = 0;
        solutions++;
        for (i = 0; i < n; i++)
            h = (unsigned int)((h * 9u + pos[i]) & 0xFFFFu);
        sum = (unsigned int)((sum + h) & 0xFFFFu);
        return;
    }
    for (c = 0; c < n; c++) {
        unsigned char d1 = (unsigned char)(row + c);
        unsigned char d2 = (unsigned char)(row - c + n - 1);
        if (!col[c] && !diag1[d1] && !diag2[d2]) {
            col[c] = diag1[d1] = diag2[d2] = 1;
            pos[row] = c;
            place((unsigned char)(row + 1));
            col[c] = diag1[d1] = diag2[d2] = 0;
        }
    }
}

int main(void)
{
    int k;
    for (k = 4; k <= 7; k++) {
        n = (unsigned char)k;
        solutions = 0;
        sum = 0;
        place(0);
        printf("%d %u %u\n", k, solutions, sum);
    }
    return 0;
}
"""

# Byte locals that live across calls to functions with the register
# convention and with the stack convention, and a word local beside them.
CALLS_SRC = r"""
#include <stdio.h>

unsigned char tab[16];
int total;

static unsigned char mix(unsigned char a, unsigned char b)
{
    total = (total * 3 + a - b) & 0x3FFF;
    return (unsigned char)(a * 5 + b);
}

static int wide(int a, int b, int c)
{
    total = (total + (a ^ b ^ c)) & 0x3FFF;
    return (a + b + c) & 0x3FFF;
}

static int walk(unsigned char x, unsigned char y, int w)
{
    unsigned char i, j;
    int s = 0;
    for (i = 0; i < 6; i++) {
        j = mix(x, i);
        tab[(i + y) & 15] = j;
        s += wide(i, j, w) & 255;
        x = (unsigned char)(x + j);
        w = (w + i) & 0x3FFF;
    }
    return s + x + y;
}

int main(void)
{
    int k, s = 0;
    for (k = 0; k < 20; k++)
        s = (s + walk((unsigned char)(k * 7), (unsigned char)(k + 3), k)) & 0x7FFF;
    printf("%d %d", s, total);
    for (k = 0; k < 16; k++)
        printf(" %d", tab[k]);
    printf("\n");
    return 0;
}
"""

# Signed and unsigned char compares of two locals, a local and a member,
# a local and an array element.
CMP_SRC = r"""
#include <stdio.h>

struct pr { signed char a; unsigned char b; };
signed char sv[8] = {-128, -100, -1, 0, 1, 50, 127, 5};
unsigned char uv[8] = {0, 1, 100, 127, 128, 200, 254, 255};

static int cmp_all(signed char x, signed char y, struct pr *p, int i)
{
    signed char v = sv[i & 7];
    unsigned char u = uv[i & 7];
    int r = 0;
    r += (x < y) + 2 * (x > y) + 4 * (x <= y) + 8 * (x >= y);
    r += 16 * (x < v) + 32 * (v > p->a);
    r += 64 * (u < p->b) + 128 * (u >= uv[(i + 3) & 7]);
    r += 256 * (v <= sv[(i + 1) & 7]);
    return r;
}

int main(void)
{
    int i, j, h = 0;
    struct pr q;
    for (i = 0; i < 8; i++) {
        for (j = 0; j < 8; j++) {
            q.a = sv[j];
            q.b = uv[i];
            h = (h * 5 + cmp_all(sv[i], sv[j], &q, i + j)) & 0x7FFF;
        }
        printf("%d\n", h);
    }
    return 0;
}
"""

# A 16-bit counter beside byte locals: word index sums, k += step, a signed
# compare with a constant, negative counters.
WORD_SRC = r"""
#include <stdio.h>

int words[40];
char bytes[64];

static int sweep(int start, int step, unsigned char tag)
{
    int k, cnt = 0;
    unsigned char t = tag;
    for (k = start; k < 60; k += step) {
        bytes[k] = (char)(t + 1);
        t = (unsigned char)(t + step);
        cnt++;
    }
    for (k = -3; k < 3; k++)
        cnt += (k < 0) + 2 * (k >= 2);
    return cnt + t;
}

static int idx(int a, int b, unsigned char m)
{
    int s = 0, i;
    unsigned char z = m;
    for (i = a; i < b; i++) {
        words[i] = words[i] + i + z;
        z = (unsigned char)(z ^ i);
        s += words[i] & 15;
    }
    return s + z;
}

int main(void)
{
    int i, h = 0;
    for (i = 0; i < 40; i++)
        words[i] = i * 3;
    for (i = 1; i < 8; i++)
        h = (h + sweep(i, i + 1, (unsigned char)(i * 9)) + idx(i, 30 + i, (unsigned char)i)) & 0x7FFF;
    printf("%d", h);
    for (i = 0; i < 64; i += 7)
        printf(" %d", bytes[i]);
    printf("\n");
    return 0;
}
"""

# Recursion with stack arguments and a char result: byte locals across the
# call and the cleanup of the arguments.
REC_SRC = r"""
#include <stdio.h>

int calls;
unsigned char board[9];

static signed char score(signed char alpha, signed char beta, signed char depth,
                         signed char move)
{
    signed char piece, best;
    int p, value;
    calls++;
    if (depth >= 3)
        return (signed char)((move * 3 + depth) % 7 - 3);
    value = (depth & 1) ? -20 : 20;
    piece = (depth & 1) ? 1 : 2;
    best = alpha;
    for (p = 0; p < 4; p++) {
        if (board[p] == 0) {
            signed char sc;
            board[p] = (unsigned char)piece;
            sc = score(alpha, beta, (signed char)(depth + 1), (signed char)p);
            board[p] = 0;
            if (depth & 1) {
                if (sc > value) {
                    value = sc;
                    if (value >= beta)
                        return (signed char)value;
                    if (value > alpha)
                        alpha = (signed char)value;
                }
            } else {
                if (sc < value) {
                    value = sc;
                    if (value <= alpha)
                        return (signed char)value;
                    if (value < beta)
                        beta = (signed char)value;
                }
            }
            best = (signed char)(best + sc);
        }
    }
    return (signed char)(value + (best & 1));
}

int main(void)
{
    int a;
    for (a = -3; a <= 2; a++) {
        int r;
        calls = 0;
        r = score((signed char)a, 3, 0, 0);
        printf("%d %d %d\n", a, r, calls);
    }
    return 0;
}
"""

SOURCES = {"queens": QUEENS_SRC, "calls": CALLS_SRC, "cmp": CMP_SRC,
           "word": WORD_SRC, "rec": REC_SRC}


@pytest.mark.parametrize("flags", FLAGS, ids=ids)
@pytest.mark.parametrize("name", sorted(SOURCES))
def test_byte_regs_against_host(tmp_path, name, flags):
    same_as_host(tmp_path, SOURCES[name], *flags)


def func_text(mac, name):
    a = mac.index(f"\n_{name}:")
    b = mac.index(f"@{name}_ret:", a)
    return mac[a:b]


def test_queens_shape(tmp_path):
    on = compile_c(tmp_path, QUEENS_SRC).read_text()
    off = compile_c(tmp_path, QUEENS_SRC, "--no-byte-regs",
                    name="o").read_text()
    f_on = func_text(on, "place")
    f_off = func_text(off, "place")
    # the address sums use A only, and no push of BC for them
    assert f_on.count("LOW (") >= 4
    assert "LOW (" not in f_off
    # the four byte locals live in B, C, D and E
    for r in "BCDE":
        assert re.search(rf"\tinc\t{r}\n", f_on) or re.search(
            rf"\tld\t{r},A\n", f_on)
    # fewer slot accesses than before
    assert f_on.count("(IX") < f_off.count("(IX")


def test_option_off_has_no_byte_local_in_b_or_d(tmp_path):
    off = compile_c(tmp_path, QUEENS_SRC, "--no-byte-regs").read_text()
    f_off = func_text(off, "place")
    assert not re.search(r"\tinc\tB\n", f_off)
    assert not re.search(r"\tinc\tD\n", f_off)


# A function whose address is taken: its char local sits in a register, and
# the sign extension that only the old slot needed goes away.
PTR_SRC = r"""
#include <stdio.h>

char board[9];

char win0(void)
{
    register int x = board[0];
    if ((x == board[1] && x == board[2]) || (x == board[3] && x == board[6]))
        return (char)x;
    return 0;
}

char win4(void)
{
    register int x = board[4];
    if ((x == board[0] && x == board[8]) || (x == board[2] && x == board[6]))
        return (char)x;
    return 0;
}

char (*fns[2])(void) = {win0, win4};

int main(void)
{
    int i, s = 0, k;
    for (k = 0; k < 40; k++) {
        for (i = 0; i < 9; i++)
            board[i] = (char)(((k >> (i & 3)) + i * (k + 1)) % 3);
        board[1] = board[0];
        board[2] = board[0];
        s = (s * 3 + fns[k & 1]()) & 0x3FFF;
        s = (s + (*fns[(k + 1) & 1])() * 5) & 0x3FFF;
    }
    printf("%d\n", s);
    return 0;
}
"""


@pytest.mark.parametrize("flags", FLAGS, ids=ids)
def test_byte_regs_function_pointer_against_host(tmp_path, flags):
    same_as_host(tmp_path, PTR_SRC, *flags)


def test_address_taken_function_gets_a_register_local(tmp_path):
    on = compile_c(tmp_path, PTR_SRC).read_text()
    off = compile_c(tmp_path, PTR_SRC, "--no-rl-addr-taken",
                    name="o").read_text()
    f_on, f_off = func_text(on, "win0"), func_text(off, "win0")
    assert "(??AUTO" in f_off
    assert "(??AUTO" not in f_on
    # no dead sign extension in front of the first compare
    assert f_on.count("rlca") <= f_off.count("rlca") - 1, (f_on, f_off)
