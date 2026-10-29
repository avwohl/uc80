"""Pair peephole rules and the discarded comma (docs/peephole_pairs.md).

Every program runs in cpmemu with the default options, with each switch off
(--no-peep-pairs, --no-discard-comma), with --no-register-locals and with
-O0.  All outputs must be equal.  Text checks show that a rule fires, that
a volatile access keeps its count and that an interrupt function is not
touched.
"""

import re

import pytest

from .test_register_cc import build_run, compile_mac, func, needs_run

VARIANTS = [(), ("--no-peep-pairs",), ("--no-discard-comma",),
            ("--no-peep-pairs", "--no-discard-comma"),
            ("--no-register-locals",), ("-O0",)]

WALK = r"""
#include <stdio.h>
struct S { unsigned char a, b, c; };
static struct S arr[10];
static unsigned char buf[40];
static int walk(int n) {
    int i; struct S *t; int s = 0;
    for (i = 0, t = arr; i < n; i++, t++) {
        t->a = (unsigned char)(i * 3);
        t->b = (unsigned char)(i ^ 5);
        t->c = (unsigned char)(t->a + t->b);
    }
    for (i = 0, t = arr; i < n; i++, t++) s += t->b + t->c;
    return s;
}
static unsigned char *fill(unsigned char *p, unsigned char v, int n) {
    unsigned char *q = p;
    while (n--) { *q = v; v += 3; q++; }
    return q;
}
static int sum(const unsigned char *p, int n) {
    int s = 0;
    while (n--) s += *p++;
    return s;
}
int main(void) {
    unsigned char *e;
    printf("%d\n", walk(10));
    e = fill(buf, 7, 33);
    printf("%d %d\n", (int)(e - buf), sum(buf, 33));
    return 0;
}
"""

ALIAS = r"""
#include <stdio.h>
static unsigned char buf[16];
static int run(void) {
    unsigned char *p = buf, *q = buf + 1;
    int i, s = 0;
    for (i = 0; i < 8; i++, p++, q++) {
        *p = (unsigned char)(i + 1);
        *q = (unsigned char)(*p + 10);
        s += *p * 3 + *q;
    }
    for (p = buf; p < buf + 9; p++) s += *p;
    return s;
}
int main(void) {
    printf("%d\n", run());
    return 0;
}
"""

SHIFT = r"""
#include <stdio.h>
static unsigned crc(unsigned x, int n) {
    int i, carries = 0;
    for (i = 0; i < n; i++) {
        unsigned y = x;
        x = x + x;
        if (x < y) carries++;
        if (x & 0x100) x ^= 0x1021;
        x += (unsigned)i;
    }
    return x ^ (unsigned)carries;
}
static unsigned shl2(unsigned x, int n) {
    int i;
    for (i = 0; i < n; i++) x = (x + x) + (x + x) + (unsigned)i;
    return x;
}
int main(void) {
    printf("%u %u %u\n", crc(0x1234, 40), crc(0xFFFF, 17), shl2(3, 9));
    return 0;
}
"""

COMMA = r"""
#include <stdio.h>
struct S { unsigned char a, b, c; };
static struct S arr[6] = {{1,2,3},{4,5,6},{7,8,9},{10,11,12},{13,14,15},{16,17,18}};
static char text[] = "abcdefghij";
static int value_context(void) {
    int i = 0, r;
    char *p = text;
    r = (i++, p++, *p);
    r += (i++, i * 2);
    r += (int)(i, p - text);
    (i++, p++);
    return r * 10 + i + (int)(p - text);
}
static int loops(void) {
    int i, s = 0;
    struct S *t;
    volatile unsigned char *vp = &arr[0].b;
    char *p;
    for (i = 0, t = arr; i < 6; i++, t++) s += t->c;
    for (i = 0, p = text; i < 10; i++, p++) s += *p;
    for (i = 0; i < 6; i++, vp += 3) s += *vp;
    return s;
}
int main(void) {
    printf("%d %d\n", value_context(), loops());
    return 0;
}
"""

# A frame function copies its register parameter into BC before the frame
# code, which destroys HL: the copy at the entry is not next to the body.
FRAME = r"""
#include <stdio.h>
int fib(int n) { int a, b; if (n < 2) return n; a = fib(n - 1); b = fib(n - 2); return a + b; }
int sumto(int n) { int s = 0, i; if (n > 100) return -1; for (i = 0; i <= n; i++) s += i; return s + sumto(n - 50 > 0 ? n - 50 : 101); }
int main(void) { printf("%d %d\n", fib(15), sumto(120)); return 0; }
"""

PROGRAMS = {"frame": FRAME, "walk": WALK, "alias": ALIAS, "shift": SHIFT, "comma": COMMA}


@needs_run
@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_every_option_set_gives_the_same_output(tmp_path, name):
    outs = []
    for i, flags in enumerate(VARIANTS):
        d = tmp_path / f"v{i}"
        d.mkdir()
        outs.append(build_run(d, PROGRAMS[name], "--no-inlining", *flags))
    assert outs[0].strip(), "no output"
    assert all(o == outs[0] for o in outs), list(zip(VARIANTS, outs))


EXPECT = {"comma": None}


@needs_run
def test_comma_values_are_right(tmp_path):
    """value_context: r = 'b' (98) + 4 + 1... computed by hand."""
    out = build_run(tmp_path, COMMA)
    # i=0,p=text: r=(i=1,p=1)->'b'=98; r+=(i=2,2*2)=4 ->102; r+=(p-text)=1 ->103;
    # then (i++,p++) -> i=3,p=2: 103*10 + 3 + 2 = 1035
    assert out.split()[0] == "1035"


def test_discarded_comma_does_not_save_the_old_value(tmp_path):
    src = ("struct S { char a, b, c; }; struct S arr[8];\n"
           "int f(int n) { int i; struct S *t; int s = 0;\n"
           "  for (i = 0, t = arr; i < n; i++, t++) s += t->b;\n"
           "  return s; }\n"
           "int main(void) { return f(5); }\n")
    on, _ = compile_mac(tmp_path, src)
    off, _ = compile_mac(tmp_path, src, "--no-discard-comma")
    assert len(func(on, "f")) < len(func(off, "f"))
    assert "push\tHL" in func(off, "f")


def test_pair_rules_fire_on_the_ymreg_kernel(tmp_path):
    """frame_update keeps the table pointer in BC: the load through a copy
    of BC becomes ld A,(BC), and the copy-and-reload of out() goes."""
    from pathlib import Path
    bench = Path(__file__).resolve().parent.parent / "bench" / "programs"
    src = (bench / "snd-ymreg" / "ymreg.c").read_text()
    (tmp_path / "snd").mkdir()
    (tmp_path / "snd" / "snd.h").write_text((bench / "snd" / "snd.h").read_text())
    on, _ = compile_mac(tmp_path, src, "-I", str(tmp_path / "snd"))
    off, _ = compile_mac(tmp_path, src, "--no-peep-pairs", "-I",
                         str(tmp_path / "snd"))
    assert re.search(r"ld\s+A,\(BC\)", on, re.I)
    assert not re.search(r"ld\s+A,\(BC\)", off, re.I)
    assert len(on) < len(off)


def test_a_volatile_read_keeps_its_count(tmp_path):
    src = ("static unsigned char buf[40];\n"
           "int sum(volatile unsigned char *p, int n) {\n"
           "  int s = 0;\n"
           "  while (n--) { s += *p; s += *p; p++; }\n"
           "  return s; }\n"
           "int main(void) { return sum(buf, 33); }\n")
    for flags in ((), ("--no-peep-pairs",)):
        mac, _ = compile_mac(tmp_path, src, *flags)
        body = func(mac, "sum")
        reads = re.findall(r"ld\s+A,\((?:HL|BC|DE)\)|ld\s+[CDEL],\(HL\)", body,
                           re.I)
        assert len(reads) == 2, (flags, body)


ISR = r"""
volatile unsigned char n, m;
unsigned char tab[8];
#pragma uc80 interrupt isr
void isr(void) {
    unsigned char *p = tab;
    int i;
    for (i = 0, p = tab; i < 8; i++, p++) { *p = n; n++; m += *p; }
}
int main(void) { return 0; }
"""


def test_an_interrupt_function_keeps_its_code(tmp_path):
    texts = []
    for flags in ((), ("--no-peep-pairs", "--no-slot-fold")):
        d = tmp_path / ("x" + str(len(texts)))
        d.mkdir()
        mac, _ = compile_mac(d, ISR, "--target=rom", *flags)
        texts.append(func(mac, "isr"))
    assert texts[0] == texts[1]
