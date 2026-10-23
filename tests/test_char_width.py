"""Char width and signedness: integer promotion, ++/--, assignment values.

Each test builds a whole program, runs it under cpmemu and checks the
printed values, because every bug here gave a wrong VALUE while the code
looked plausible.  The expected values follow C with a 16-bit int (they
were checked against SDCC with --fsigned-char where SDCC gets them right;
SDCC 4.6.0 itself gets the value of an unsigned char ``u -= n`` wrong).

  (a) An unsigned char operand promotes to SIGNED int (C 6.3.1.1), so
      ``uc - 20`` is negative.  _get_expr_type made it unsigned int.
  (b) ++/-- on a 1-byte local read two bytes, so the value of ``c++``
      carried the neighbouring local in its high byte.
  (c) The same bug made ``a[i++]`` with a uint8_t local index read the
      wrong element.
  (d) The audit of the same class of bug found more: a prefix ++/-- or an
      assignment to a char (or bit-field) gave the value before it was
      narrowed; ``-uc``, ``k ? uc : -1`` and ``long / unsigned`` had the
      wrong signedness; the inliner dropped the conversion of arguments
      and of the return value to the declared types, and duplicated or
      dropped side effects of arguments; a compound assignment did not
      see that ``a[i++]`` has a side effect, and for a long or float
      target, or a long or float value, it evaluated the lvalue twice or
      used the wrong width; an integer branch of a ?: with a float
      result was not converted; the AST optimizer's ``x * 8UL`` ->
      ``x << 3`` and ``x / 8u`` -> ``x >> 3`` dropped the type of the
      literal, and its constant folding dropped L suffixes and ignored
      widths (``-256L >= 65280u``); a call through a table of function
      pointers did not convert its arguments to the parameter types.
"""

import os
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


def build_and_run(tmp_path, source, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text("#include <stdio.h>\n" + source)
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
                         timeout=60, env=env)
    return run.stdout.decode("latin-1").replace("\r", "")


def lines(out):
    return dict(l.split(" ", 1) for l in out.splitlines() if " " in l)


class TestPromotion:
    """(a) unsigned char promotes to signed int."""

    def test_unsigned_char_minus_constant(self, tmp_path):
        out = build_and_run(tmp_path, """
unsigned char gc = 10;
int main(void) {
  unsigned char c = 10;
  unsigned char d = 200;
  signed char s = -3;
  printf("a1 %d\\n", (c - 20) < 0);
  printf("a2 %d\\n", (c - 20) / 2);
  printf("a3 %d\\n", (gc - 20) < 0);
  printf("a4 %d\\n", (gc - 20) / 2);
  printf("a5 %d\\n", (c - d) % 7);
  printf("a6 %d\\n", (c - d) >> 1);
  printf("a7 %d\\n", (s + c) / 2);
  printf("a8 %ld\\n", (long)(c - 20));
  printf("a9 %d\\n", (c - 20) >= 0);
  printf("a10 %d\\n", c - 20 < 0 ? 1 : 2);
  printf("a11 %d\\n", (c & 0x0F) - 20 < 0);
  if (c - 20 < 0) printf("a12 neg\\n"); else printf("a12 pos\\n");
  return 0;
}
""")
        assert lines(out) == {
            "a1": "1", "a2": "-5", "a3": "1", "a4": "-5", "a5": "-1",
            "a6": "-95", "a7": "3", "a8": "-10", "a9": "0", "a10": "1",
            "a11": "1", "a12": "neg"}

    def test_same_rank_unsigned_still_wins(self, tmp_path):
        """uc - 300u is unsigned int: promotion does not undo 6.3.1.8."""
        out = build_and_run(tmp_path, """
int main(void) {
  unsigned char uc = 100;
  signed char sc = -1;
  long r;
  r = uc - 300u; printf("u1 %ld\\n", r);
  r = uc - 300; printf("u2 %ld\\n", r);
  r = sc + 0u; printf("u3 %ld\\n", r);
  return 0;
}
""")
        assert lines(out) == {"u1": "65336", "u2": "-200", "u3": "65535"}

    def test_unary_minus_and_complement(self, tmp_path):
        out = build_and_run(tmp_path, """
int main(void) {
  unsigned char uc = 100;
  long r;
  r = -uc; printf("n1 %ld\\n", r);
  r = ~uc; printf("n2 %ld\\n", r);
  printf("n3 %d\\n", -uc / 2);
  return 0;
}
""")
        assert lines(out) == {"n1": "-100", "n2": "-101", "n3": "-50"}

    def test_ternary_common_type(self, tmp_path):
        out = build_and_run(tmp_path, """
int main(void) {
  unsigned char uc = 100;
  signed char sc = -1;
  int k0 = 0, k1 = 1;
  unsigned long ul = 65537UL;
  long r;
  float f;
  r = k0 ? uc : -1; printf("t1 %ld\\n", r);
  r = k1 ? sc : 1u; printf("t2 %ld\\n", r);
  f = k0 ? 1.5f : 2; printf("t3 %d\\n", (int)(f * 10));
  f = k1 ? 3 : 1.5f; printf("t4 %d\\n", (int)(f * 10));
  printf("t5 %d\\n", (k0 ? -25911 : ul) > -127);
  printf("t6 %lu\\n", k1 ? -1 : ul);
  return 0;
}
""")
        assert lines(out) == {"t1": "-1", "t2": "65535", "t3": "20",
                              "t4": "30", "t5": "0", "t6": "4294967295"}

    def test_long_with_unsigned_int_is_signed(self, tmp_path):
        """long op unsigned int: the unsigned int converts to long."""
        out = build_and_run(tmp_path, """
int main(void) {
  long l = -100;
  unsigned int ui = 7;
  long r;
  r = l / ui; printf("l1 %ld\\n", r);
  r = l % ui; printf("l2 %ld\\n", r);
  r = l * ui; printf("l3 %ld\\n", r);
  r = (l < ui); printf("l4 %ld\\n", r);
  return 0;
}
""")
        assert lines(out) == {"l1": "-14", "l2": "-2", "l3": "-700",
                              "l4": "1"}

    def test_narrow_bitfield_promotes_to_int(self, tmp_path):
        out = build_and_run(tmp_path, """
struct B { unsigned int f:3; } b;
int main(void) {
  b.f = 0;
  printf("b1 %d\\n", (b.f - 1) < 0);
  printf("b2 %d\\n", (b.f - 1) / 2);
  return 0;
}
""")
        assert lines(out) == {"b1": "1", "b2": "0"}


INCDEC = """
typedef unsigned char uint8_t;
int arr[8] = {10, 11, 12, 13, 14, 15, 16, 17};
int g(void) {             /* shared storage (not recursive) */
  unsigned char c = 5;
  unsigned char d = 0x77;
  int x = c++;
  return x + d;
}
int f(int k) {            /* recursive: an IX frame */
  unsigned char c = 5;
  unsigned char d = 0x77;
  int x;
  if (k > 100) return f(k - 1);
  x = ++c;
  x += d;
  return x;
}
int idx_shared(void) {
  uint8_t pad = 0x55;
  uint8_t i = 2;
  int s = 0;
  s += arr[i++];
  s += arr[i++];
  s += arr[--i];
  s += arr[i--];
  return s * 100 + i + pad - 0x55;
}
int idx_ix(int k) {
  uint8_t pad = 0x55;
  uint8_t i = 2;
  int s = 0;
  if (k > 100) return idx_ix(k - 1);
  s += arr[i++];
  s += arr[i++];
  s += arr[--i];
  s += arr[i--];
  return s * 100 + i + pad - 0x55;
}
"""


class TestIncDecLocalChar:
    """(b) and (c): the value of ++/-- on a 1-byte local."""

    def test_postfix_and_prefix_value(self, tmp_path):
        out = build_and_run(tmp_path, INCDEC + """
int main(void) {
  printf("g %d\\n", g());
  printf("f %d\\n", f(1));
  return 0;
}
""")
        assert lines(out) == {"g": "124", "f": "125"}

    def test_uint8_index(self, tmp_path):
        out = build_and_run(tmp_path, INCDEC + """
int main(void) {
  printf("s %d\\n", idx_shared());
  printf("x %d\\n", idx_ix(1));
  return 0;
}
""")
        assert lines(out) == {"s": "5102", "x": "5102"}

    def test_wraparound_value(self, tmp_path):
        """The value of ++c is the char after narrowing: 255+1 is 0."""
        out = build_and_run(tmp_path, """
unsigned char gu = 255;
signed char gs = 127;
unsigned char garr[4] = {255, 127, 128, 0};
signed char gsarr[4] = {127, -128, -1, 0};
_Bool ba[2];
int wrap_shared(void) {
  unsigned char u = 255;
  signed char s = 127;
  signed char m = -128;
  unsigned char z = 0;
  int a = ++u, b = ++s, c = --m, d = --z, e = u++, h = s--;
  printf("ws %d %d %d %d %d %d %d %d\\n", a, b, c, d, e, h, u, s);
  return 0;
}
int wrap_ix(int k) {
  unsigned char u = 255;
  signed char s = 127;
  signed char m = -128;
  unsigned char z = 0;
  int a, b, c, d, e, h;
  if (k > 100) return wrap_ix(k - 1);
  a = ++u; b = ++s; c = --m; d = --z; e = u++; h = s--;
  printf("wi %d %d %d %d %d %d %d %d\\n", a, b, c, d, e, h, u, s);
  return 0;
}
int main(void) {
  int a, b, c, d;
  wrap_shared();
  wrap_ix(1);
  a = ++gu; b = ++gs; c = gu--; d = gs--;
  printf("gl %d %d %d %d %d %d\\n", a, b, c, d, gu, gs);
  a = ++garr[0]; b = ++garr[1]; c = --garr[3]; d = garr[2]++;
  printf("ga %d %d %d %d\\n", a, b, c, d);
  a = ++gsarr[0]; b = --gsarr[1]; c = ++gsarr[2]; d = gsarr[3]--;
  printf("gsa %d %d %d %d\\n", a, b, c, d);
  a = ++ba[0]; b = ++ba[0]; c = ba[0]; d = --ba[1];
  printf("bool %d %d %d %d %d\\n", a, b, c, d, ba[1]);
  return 0;
}
""")
        assert lines(out) == {
            "ws": "0 -128 127 255 0 -128 1 127",
            "wi": "0 -128 127 255 0 -128 1 127",
            "gl": "0 -128 0 -128 255 127",
            "ga": "0 128 255 128",
            "gsa": "-128 127 0 0",
            "bool": "1 1 1 1 1"}

    def test_char_local_beyond_ix_reach(self, tmp_path):
        """A char local more than 128 bytes down the frame."""
        out = build_and_run(tmp_path, """
int far(int k) {
  unsigned char c = 5;
  char big[200];
  signed char s = -7;
  int x;
  big[0] = 1;
  if (k > 100) return far(k - 1);
  x = c + s + big[0];
  return x * 100 + c++ + s--;
}
int main(void) { printf("far %d\\n", far(1)); return 0; }
""")
        assert out == "far -102\n"


class TestAssignmentValue:
    """(d) the value of an assignment is the narrowed, stored value."""

    def test_char_assignment_and_compound(self, tmp_path):
        out = build_and_run(tmp_path, """
unsigned char gu = 100;
signed char gs = 100;
unsigned char gua[3] = {100, 200, 50};
signed char gsa[3] = {100, -100, 50};
struct S { unsigned char u; signed char s; int i; } gst = {100, 100, 7};
int sh(void) {
  unsigned char u = 100;
  signed char s = 100;
  int a = (u += 200), b = (s += 100), c = (u = 300), d = (s = 200);
  int e = (u -= 50), f = (s *= 3), g = (u <<= 2), h = (s >>= 1);
  printf("sh %d %d %d %d %d %d %d %d\\n", a, b, c, d, e, f, g, h);
  return 0;
}
int ix(int k) {
  unsigned char u = 100;
  signed char s = 100;
  int a, b, c, d, e, f, g, h;
  if (k > 100) return ix(k - 1);
  a = (u += 200); b = (s += 100); c = (u = 300); d = (s = 200);
  e = (u -= 50); f = (s *= 3); g = (u <<= 2); h = (s >>= 1);
  printf("ix %d %d %d %d %d %d %d %d\\n", a, b, c, d, e, f, g, h);
  return 0;
}
int main(void) {
  int a, b, c, d;
  struct S *p = &gst;
  sh(); ix(1);
  a = (gu += 200); b = (gs += 100); c = (gu = 300); d = (gs = 200);
  printf("gl %d %d %d %d\\n", a, b, c, d);
  a = (gua[0] += 200); b = (gsa[0] += 100); c = (gua[1] = 300);
  d = (gsa[1] -= 100);
  printf("ga %d %d %d %d\\n", a, b, c, d);
  a = (p->u += 200); b = (p->s += 100); c = (gst.u = 300); d = (gst.s = 200);
  printf("st %d %d %d %d\\n", a, b, c, d);
  return 0;
}
""")
        assert lines(out) == {
            "sh": "44 -56 44 -56 250 88 232 44",
            "ix": "44 -56 44 -56 250 88 232 44",
            "gl": "44 -56 44 -56",
            "ga": "44 -56 44 56",
            "st": "44 -56 44 -56"}

    def test_bitfield_assignment_value(self, tmp_path):
        out = build_and_run(tmp_path, """
struct B { unsigned int f:3; signed int g:4; unsigned int h:9; } b;
int main(void) {
  int x, y, z;
  x = (b.f = 9); y = (b.g = 9); z = (b.h = 600);
  printf("bf %d %d %d %d %d %d\\n", x, y, z, b.f, b.g, b.h);
  b.f = 7; x = ++b.f; b.g = 7; y = ++b.g; z = b.g++;
  printf("bf2 %d %d %d %d %d\\n", x, y, z, b.f, b.g);
  return 0;
}
""")
        assert lines(out) == {"bf": "1 -7 88 1 -7 88",
                              "bf2": "0 -8 -8 0 -7"}

    def test_compound_with_side_effect_in_lvalue(self, tmp_path):
        """a[i++] op= v evaluates i++ once and narrows the value."""
        out = build_and_run(tmp_path, """
unsigned char ua[6] = {100, 200, 50, 7, 9, 11};
signed char sa[6] = {100, -100, 50, 7, 9, 11};
_Bool ba[4];
int ia[4] = {1, 2, 3, 4};
int k = 0;
int nx(void) { return k++; }
int main(void) {
  int a, b, c, d, e, i;
  a = (ua[nx()] += 200);
  b = (sa[nx()] -= 100);
  c = (ua[nx()] *= 6);
  d = (sa[nx()] <<= 5);
  e = (ua[nx()] -= 10);
  printf("safe %d %d %d %d %d %d\\n", a, b, c, d, e, k);
  a = (ba[nx() - 5] += 2);
  printf("bool %d %d\\n", a, ba[0]);
  i = 0;
  ia[i++] += 10;
  printf("ia %d %d %d\\n", ia[0], ia[1], i);
  return 0;
}
""")
        assert lines(out) == {"safe": "44 56 44 -32 255 5", "bool": "1 1",
                              "ia": "11 2 1"}


    def test_compound_any_width_lvalue_once(self, tmp_path):
        """long, float, pointer and _Bool targets; long and float values."""
        out = build_and_run(tmp_path, """
long la[4] = {1, 2, 3, 4};
unsigned long ula[2] = {4000000000UL, 5};
float fa[3] = {1.5f, 2.5f, 3.5f};
int ia[4] = {10, 20, 30, 40};
unsigned char ca[3] = {250, 7, 9};
int *pa[3] = {&ia[0], &ia[1], &ia[2]};
_Bool ba[2];
int k;
int nx(void) { return k++; }
int main(void) {
  int i = 0; long r; float f;
  r = (la[i++] += 70000L); printf("a %ld %ld %d\\n", r, la[0], i);
  i = 1; r = (la[i++] *= -3); printf("b %ld %ld %d\\n", r, la[1], i);
  i = 0; ula[i++] /= 4u; printf("c %lu %d\\n", ula[0], i);
  i = 0; fa[i++] += 1; printf("d %d %d\\n", (int)(fa[0] * 10), i);
  i = 1; f = (fa[i++] *= 2.0f);
  printf("e %d %d %d\\n", (int)(f * 10), (int)(fa[1] * 10), i);
  i = 0; ia[i++] += 1.5f; printf("f %d %d\\n", ia[0], i);
  i = 0; ia[i++] /= 2L; printf("g %d %d\\n", ia[0], i);
  i = 0; ca[i++] += 10L; printf("h %d %d\\n", ca[0], i);
  i = 0; pa[i++] += 1; printf("p %d %d\\n", *pa[0], i);
  i = 2; la[i++] -= 0.5f; printf("q %ld %d\\n", la[2], i);
  i = 0; ba[i++] += 7L; printf("bb %d %d\\n", ba[0], i);
  la[nx()] <<= 2; printf("s %ld %d\\n", la[0], k);
  la[nx()] >>= 1; printf("t %ld %d\\n", la[1], k);
  return 0;
}
""")
        assert lines(out) == {
            "a": "70001 70001 1", "b": "-6 -6 2", "c": "1000000000 1",
            "d": "25 1", "e": "50 50 2", "f": "11 1", "g": "5 1",
            "h": "4 1", "p": "20 1", "q": "2 3", "bb": "1 1",
            "s": "280004 1", "t": "-3 2"}

class TestInlinedCalls:
    """(d) a call the inliner expands keeps the declared types."""

    def test_return_and_parameter_types(self, tmp_path):
        out = build_and_run(tmp_path, """
unsigned char ret_u(int x) { return x; }
long take_ul(unsigned char c) { return c; }
long ret_l_from_s(signed char c) { return c; }
unsigned uf(int x) { return x; }
long lmul(long x) { return x * x; }
int main(void) {
  printf("argl %ld %ld %ld\\n", take_ul(-1), ret_l_from_s(-1),
         ret_l_from_s(300));
  printf("u %d\\n", uf(-1) > 0);
  printf("lm %ld\\n", lmul(1000));
  printf("r %d\\n", ret_u(300) + 1);
  return 0;
}
""")
        assert lines(out) == {"argl": "255 -1 44", "u": "1", "lm": "1000000",
                              "r": "45"}

    def test_argument_side_effects_run_once(self, tmp_path):
        out = build_and_run(tmp_path, """
int cnt;
int sq(int x) { return x * x; }
int nx(void) { cnt++; return cnt; }
int five(int x) { return 5; }
int main(void) {
  int k = 3, r;
  r = sq(k++); printf("a %d %d\\n", r, k);
  r = sq(nx()); printf("b %d %d\\n", r, cnt);
  r = five(nx()); printf("c %d %d\\n", r, cnt);
  return 0;
}
""")
        assert lines(out) == {"a": "9 4", "b": "1 1", "c": "5 2"}


class TestLiteralTypeKept:
    """(d) x * 2^n, x / 2^n and x % 2^n keep the type of a U or L literal.

    uc_core's optimizer rewrote them as shifts and masks, which have the
    type of the left operand only.  uc80 now leaves them to codegen.
    """

    def test_unsigned_and_long_power_of_two(self, tmp_path):
        out = build_and_run(tmp_path, """
int x = -8;
int big = 10000;
unsigned u = 40000u;
unsigned char g13 = 2;
long l = -8;
int main(void) {
  printf("d1 %u\\n", x / 8u);
  printf("d2 %u\\n", x % 8u);
  printf("d3 %d\\n", (x * 8u) > 0);
  printf("d4 %d\\n", (x * 2u) > 0);
  printf("d5 %u\\n", u / 8u);
  printf("d6 %ld\\n", l / 8u);
  printf("d7 %lu\\n", (unsigned long)(x / 8UL));
  printf("d8 %lu\\n", (unsigned long)(x % 8UL));
  printf("d9 %lu\\n", big * 8UL);
  printf("d10 %lu\\n", (g13 | 4096) * 8UL);
  printf("d11 %lu\\n", (g13 + 0) * 65536L);
  printf("d12 %u\\n", u % 4096u);
  printf("d13 %u\\n", u / 512u);
  printf("d14 %ld\\n", (long)(u ^ (-2L)));
  printf("d15 %ld\\n", u + (-2L) - 10);
  printf("d16 %d\\n", (-256L) >= 65280u);
  printf("d17 %ld\\n", 2L + 3L);
  printf("d18 %d\\n", -1 < 0u);
  return 0;
}
""")
        assert lines(out) == {
            "d1": "8191", "d2": "0", "d3": "1", "d4": "1", "d5": "5000",
            "d6": "-1", "d7": "536870911", "d8": "0", "d9": "80000",
            "d10": "32784", "d11": "131072", "d12": "3136", "d13": "78",
            "d14": "-40002", "d15": "39988", "d16": "0", "d17": "5",
            "d18": "0"}


class TestIndirectCallArguments:
    """(d) a call through a table of function pointers converts each
    argument to its parameter type, as a direct call does.

    A 2-byte argument for an unsigned long parameter moved every later
    parameter, so the callee's ``p ^= 2`` on its char parameter wrote
    into the caller's pending argument for another call.
    """

    def test_table_call_long_parameter(self, tmp_path):
        out = build_and_run(tmp_path, """
static unsigned char g12 = 32;
char g15 = 89;
static unsigned char f33(unsigned long p30, unsigned char p31, signed char p32)
{
    unsigned int l35 = 8;
    l35 = p32++;
    return l35;
}
static unsigned char f46(unsigned long p43, unsigned char p44, signed char p45)
{
    p45 ^= 2u;
    return g12 + p43 + p44;
}
static unsigned char (*const tb48[2])(unsigned long, unsigned char, signed char) = {f46, f46};
long lsum(long a, long b) { return a + b; }
long (*lp)(long, long) = lsum;
int main(void) {
    unsigned int l68 = 0;
    unsigned char i71 = 0;
    g15 = f33(1, tb48[0](g12, l68, i71), 0);
    printf("t1 %d\\n", g15);
    printf("t2 %d\\n", tb48[1](3, 4, 5));
    printf("t3 %ld\\n", (*lp)(70000, 5));
    return 0;
}
""")
        assert lines(out) == {"t1": "0", "t2": "39", "t3": "70005"}
