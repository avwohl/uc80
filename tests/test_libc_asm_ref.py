"""Assembly modules against the C versions that they replaced.

The scanf engine (lc_scan_core), the integer reader (lc_strtoint) and
`_m_pack` / `_m_unpack` are assembly.  The C versions are in tests/libref/.
Each test compiles the C version and the library version into one program
and compares the results on random arguments.  A fixed seed makes the run
repeatable.
"""

import os
import random
import subprocess
import sys
from pathlib import Path

from ._clib import CPMEMU, LIB_DIR, REPO, build_and_run, skip_without_tools

pytestmark = skip_without_tools

REF = Path(__file__).resolve().parent / "libref"
CSRC = REPO / "src" / "uc80" / "lib" / "csrc"


def build_with_text(tmp_path, source):
    """Like build_and_run, but links lc_scan_text first.  The program calls
    _scan_run itself, so the compiler adds no reference to the module, and
    the linker would take the default module lc_scan_tdefault."""
    (tmp_path / "prog.c").write_text(source)
    r = subprocess.run([sys.executable, "-m", "uc80.main",
                        str(tmp_path / "prog.c"), "-o", str(tmp_path / "prog.mac")],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    for name, mac in (("prog", tmp_path / "prog.mac"),
                      ("text", LIB_DIR / "lc" / "lc_scan_text.mac")):
        subprocess.run(["um80", str(mac), "-o", str(tmp_path / (name + ".rel"))],
                       check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(tmp_path / "prog.rel"), str(tmp_path / "text.rel"),
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", str(tmp_path / "prog.com")],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "prog.com")],
                         capture_output=True, timeout=120, cwd=str(tmp_path),
                         env=dict(os.environ, PYTHONHASHSEED="0"))
    return run.stdout.decode("latin-1").replace("\r", "")


def cstr(s):
    return '"' + (s.replace("\\", "\\\\").replace("\n", "\\n")
                  .replace("\t", "\\t").replace('"', '\\"')) + '"'


SCAN_HELPERS = r'''
static int ref_muladd(unsigned char *r, int nb, int mul, int add)
{
    int i; unsigned t; unsigned c = add;
    for (i = 0; i < nb; i++) {
        t = (unsigned)r[i] * mul + c; r[i] = (unsigned char)t; c = t >> 8;
    }
    return c;
}
static void ref_negate(unsigned char *r, int nb)
{
    int i; unsigned c = 1, t;
    for (i = 0; i < nb; i++) {
        t = (unsigned char)~r[i] + c; r[i] = (unsigned char)t; c = t >> 8;
    }
}
int ref_float(struct sc *in, int width, void *dst) { return 0; }
'''


def scan_cases(rng, n):
    convs = "diouxXpibcs[n%%dduxsc"
    mods = ["", "", "", "h", "hh", "l", "ll", "j", "z", "t", "L", "q"]
    alpha = "0123456789abcdefxXbBzZ  \t\n+-%,:[]^-_.AFG"

    def fmt():
        out = ""
        for _ in range(rng.randint(1, 5)):
            k = rng.random()
            if k < 0.1:
                out += rng.choice([" ", "\t", "  ", "\n"])
            elif k < 0.25:
                out += rng.choice("abc,-:x0 ")
            else:
                sp = "%"
                if rng.random() < 0.15:
                    sp += "*"
                if rng.random() < 0.3:
                    sp += str(rng.choice([1, 2, 3, 4, 5, 8, 10, 40]))
                sp += rng.choice(mods)
                c = rng.choice(convs)
                if c == "[":
                    body = rng.choice(["a-z", "^a-z", "]abc", "^]x", "0-9",
                                      "^ ", "a-", "-a", "a-c-e", "xyz",
                                      "^0-9a-f", "A-Za-z0-9", ""])
                    sp += "[" + body + ("]" if rng.random() < 0.93 else "")
                else:
                    sp += c
                out += sp
        if rng.random() < 0.15:
            out += "%n"
        return out

    def inp():
        k = rng.random()
        if k < 0.1:
            return rng.choice(["", " ", "0", "0x", "0xg", "-", "+", "  -12 34",
                               "0b1", "0b2", "0x1F zz", "12abc", "abc def",
                               "[x]", "%d"])
        s = ""
        if rng.random() < 0.4:
            s += rng.choice(["", " ", "\t", "  \n"])
        for _ in range(rng.randint(0, 18)):
            s += rng.choice(alpha[:rng.choice([10, 20, 30, len(alpha)])])
        return s

    return [(fmt(), inp()) for _ in range(n)]


def test_scanf_engine(tmp_path):
    ref = (REF / "lc_scan_core.c").read_text()
    ref = (ref.replace('#include "scan.h"', "")
           .replace("_scan_run", "ref_scan_run")
           .replace("_sc_getc", "ref_getc").replace("_sc_ungetc", "ref_ungetc")
           .replace("_sc_float", "ref_float").replace("_sc_muladd", "ref_muladd")
           .replace("_sc_negate", "ref_negate"))
    ref = ref.replace("extern int ref_muladd(unsigned char *r, int nb, int mul, int add);", "")
    ref = ref.replace("extern void ref_negate(unsigned char *r, int nb);", "")
    ref = ref.replace("sc_", "rsc_")
    scanh = ((REF / "scan.h").read_text()
             .replace("_sc_getc", "ref_getc").replace("_sc_ungetc", "ref_ungetc")
             .replace("_scan_run", "ref_scan_run").replace("_sc_float", "ref_float")
             .replace("#ifndef SCAN_H", "#ifndef REF_SCAN_H")
             .replace("#define SCAN_H", "#define REF_SCAN_H"))
    cases = scan_cases(random.Random(7), 150)
    table = ",\n".join("{%s,%s}" % (cstr(f), cstr(s)) for f, s in cases)
    src = scanh + SCAN_HELPERS + ref + r'''
#include <stdio.h>
#include <string.h>
struct c { const char *f, *s; };
static struct c cs[] = {
''' + table + r'''};
static const char *pos;
static int g(struct sc *in) { if (*pos == 0) return -1; return (unsigned char)*pos++; }
static void u(struct sc *in, int c) { pos--; }
extern int _scan_run(struct sc *in, const char *fmt, char *ap);
static unsigned char bufs[10][24];
static void *args[10];
static void setup(void)
{
    int i;
    for (i = 0; i < 10; i++) { memset(bufs[i], 0xA5 - i, 24); args[i] = bufs[i]; }
}
int main(void)
{
    int i, bad = 0, r1, r2, n1, n2; const char *p1;
    static unsigned char sv[10][24]; struct sc in;
    for (i = 0; i < sizeof(cs)/sizeof(cs[0]); i++) {
        in.get = g; in.unget = u; in.s = 0; in.fp = 0;
        setup(); pos = cs[i].s;
        r1 = ref_scan_run(&in, cs[i].f, (char *)args); p1 = pos; n1 = in.n;
        memcpy(sv, bufs, sizeof(sv));
        setup(); pos = cs[i].s;
        r2 = _scan_run(&in, cs[i].f, (char *)args); n2 = in.n;
        if (r1 != r2 || p1 != pos || n1 != n2 || memcmp(sv, bufs, sizeof(sv))) {
            bad++;
            if (bad < 10)
                printf("format [%s] input [%s]: C %d %d %d, asm %d %d %d\n",
                       cs[i].f, cs[i].s, r1, (int)(p1 - cs[i].s), n1, r2,
                       (int)(pos - cs[i].s), n2);
        }
    }
    printf("bad=%d of %d\n", bad, (int)(sizeof(cs)/sizeof(cs[0])));
    return 0;
}
'''
    out = build_with_text(tmp_path, src)
    assert out.strip().endswith("bad=0 of 150"), out


def strtoint_cases(rng, n):
    alpha = "0123456789abcdefghijklmnopqrstuvwxyzABCXYZ  \t\n+-:@/`{[_"
    fixed = ["", " ", "0", "0x", "0X1f", "0b101", "0B2", "-0x", "+0b", "  -12",
             "0x7fffffff", "-0x80000000", "0x80000000", "4294967295",
             "4294967296", "-2147483648", "2147483647", "2147483648",
             "-2147483649", "9223372036854775807", "9223372036854775808",
             "-9223372036854775808", "18446744073709551615",
             "18446744073709551616", "0xffffffffffffffff",
             "0x10000000000000000", "-0", "00", "08", "0x", "0xg", "0b", "0b2",
             "0bz", "z", "-z", "1_000", "99999999999999999999999"]

    def text():
        if rng.random() < 0.15:
            return rng.choice(fixed)
        s = ""
        if rng.random() < 0.5:
            s += rng.choice(["", " ", "\t", " \n "])
        if rng.random() < 0.4:
            s += rng.choice("+-")
        if rng.random() < 0.3:
            s += rng.choice(["0x", "0X", "0b", "0B", "0"])
        for _ in range(rng.randint(0, 14)):
            s += rng.choice(alpha[:rng.choice([10, 16, 36, 50, len(alpha)])])
        return s

    return [(text(),
             rng.choice([0, 0, 2, 8, 10, 10, 16, 16, 36, 1, 37, 3, 7, 12, 20,
                         35, -1, 100]),
             rng.choice([4, 8]), rng.choice([0, 1])) for _ in range(n)]


def test_strtoint(tmp_path):
    ref = (REF / "lc_strtoint.c").read_text()
    ref = (ref.replace("_strtoint", "ref_strtoint").replace("digit_value", "ref_dv")
           .replace("_sc_muladd", "ref_muladd").replace("_sc_negate", "ref_negate"))
    ref = ref.replace("extern int ref_muladd(unsigned char *r, int nb, int mul, int add);", "")
    ref = ref.replace("extern void ref_negate(unsigned char *r, int nb);", "")
    cases = strtoint_cases(random.Random(3), 400)
    table = ",\n".join("{%s,%d,%d,%d}" % (cstr(s), b, nb, sg)
                       for s, b, nb, sg in cases)
    src = ('#include <stdio.h>\n#include <string.h>\n#include <errno.h>\n'
           'extern void _strtoint(const char *, char **, int, unsigned char *, '
           'int, int);\n' + SCAN_HELPERS.split("int ref_float")[0] + ref + r'''
struct c { const char *s; int base, nb, sg; };
static struct c cs[] = {
''' + table + r'''};
int main(void)
{
    int i, bad = 0, e1, e2; unsigned char r1[8], r2[8]; char *p1, *p2;
    for (i = 0; i < sizeof(cs)/sizeof(cs[0]); i++) {
        errno = 0; p1 = p2 = 0; memset(r1, 0x55, 8); memset(r2, 0x55, 8);
        ref_strtoint(cs[i].s, &p1, cs[i].base, r1, cs[i].nb, cs[i].sg); e1 = errno;
        errno = 0;
        _strtoint(cs[i].s, &p2, cs[i].base, r2, cs[i].nb, cs[i].sg); e2 = errno;
        if (p1 != p2 || e1 != e2 || memcmp(r1, r2, 8)) {
            bad++;
            if (bad < 10)
                printf("[%s] base %d nb %d sg %d: C end %d errno %d, asm end %d errno %d\n",
                       cs[i].s, cs[i].base, cs[i].nb, cs[i].sg, (int)(p1 - cs[i].s),
                       e1, (int)(p2 - cs[i].s), e2);
        }
    }
    for (i = 0; i < sizeof(cs)/sizeof(cs[0]); i++)      /* a NULL end pointer */
        _strtoint(cs[i].s, 0, cs[i].base, r2, cs[i].nb, cs[i].sg);
    printf("bad=%d of %d\n", bad, (int)(sizeof(cs)/sizeof(cs[0])));
    return 0;
}
''')
    out = build_and_run(tmp_path, src)
    assert out.strip().endswith("bad=0 of 400"), out


def test_pack_unpack(tmp_path):
    rng = random.Random(5)
    pack = (REF / "lc_m_pack.c").read_text().replace("_m_pack", "ref_pack")
    unp = (REF / "lc_m_unpack.c").read_text().replace("_m_unpack", "ref_unpack")
    pack = pack.replace('#include "m.h"', "")
    unp = unp.replace('#include "m.h"', "")
    hdr = (CSRC / "m.h").read_text()
    cases = []
    for e in list(range(-30, 5)) + [253, 254, 255, 256, 300, -255, -256, -257,
                                    -1000, 1000]:
        for m in (0x800000, 0x800001, 0xFFFFFF, 0x1000000, 0xC00000, 0x800002):
            for neg in (0, 1):
                cases.append((neg, m, e))
    for _ in range(500):
        cases.append((rng.getrandbits(1),
                      rng.choice([0, rng.getrandbits(24) | 0x800000, 0x1000000]),
                      rng.randint(-30, 260)))
    xs = ([0, 0x80000000, 1, 0x7FFFFF, 0x400000, 0x800000, 0x3F800000,
           0x7F7FFFFF, 0x7F800000, 0x7FC00000, 0xFF800001, 0x80000001,
           0x00123456, 0x00000100]
          + [rng.getrandbits(32) for _ in range(150)]
          + [rng.getrandbits(23) for _ in range(150)])
    src = hdr + pack + unp + r'''
#include <stdio.h>
struct c { int neg; unsigned long m; int e; };
static struct c cs[] = {
''' + ",\n".join("{%d,%dUL,%d}" % c for c in cases) + r''' };
static unsigned long xs[] = {
''' + ",\n".join("0x%XUL" % v for v in xs) + r''' };
extern float _m_pack(int, unsigned long, int);
extern unsigned long _m_unpack(float, int *);
int main(void)
{
    int i, bad = 0, e1, e2; m_u a, b; unsigned long m1, m2;
    for (i = 0; i < sizeof(cs)/sizeof(cs[0]); i++) {
        a.f = ref_pack(cs[i].neg, cs[i].m, cs[i].e);
        b.f = _m_pack(cs[i].neg, cs[i].m, cs[i].e);
        if (a.u != b.u) { bad++; if (bad < 8) printf("pack %d %lx %d: C %lx asm %lx\n", cs[i].neg, cs[i].m, cs[i].e, a.u, b.u); }
    }
    for (i = 0; i < sizeof(xs)/sizeof(xs[0]); i++) {
        a.u = xs[i];
        m1 = ref_unpack(a.f, &e1);
        m2 = _m_unpack(a.f, &e2);
        if (m1 != m2 || e1 != e2) { bad++; if (bad < 8) printf("unpack %lx: C %lx %d asm %lx %d\n", xs[i], m1, e1, m2, e2); }
    }
    printf("bad=%d\n", bad);
    return 0;
}
'''
    out = build_and_run(tmp_path, src)
    assert out.strip().endswith("bad=0"), out
