"""A function returning an aggregate writes it where the caller says.

The result used to go into one static buffer, __sret_buf, that the whole
program shared.  Two consequences, both fixed by giving the caller the say:

  - the buffer was 256 bytes, so a larger aggregate could not be returned
    at all (the compiler refused it rather than writing past the end); and

  - only one result could be in flight.  ``arr[g().i] = f();`` computes the
    destination address *after* f returns, so g overwrote f's bytes on the
    way and the assignment stored g's result.  Nothing was reported.

The caller now passes the address to write to as a hidden first argument,
pushed last so it sits at IX+4 with the declared parameters starting at
IX+6, and the callee still leaves that address in HL so every reader of the
result is unchanged.  The destination is a slot in the caller's own frame,
one per call site, which is what makes both problems go away at once.

These tests run the programs -- the ABI is a two-sided agreement, and only
executing it checks that both sides read the same layout.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80")
            and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")


def build_and_run(tmp_path, source, flags=(), name="t"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(source)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *flags, "-o", str(mac)],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120)
    return run.stdout.decode("latin-1").replace("\r", "")


# Every optimization setting reaches a different storage decision: with
# shared storage the slots are ??AUTO-relative, without it they are
# IX-relative, and inlining changes which calls survive to have slots.
CONFIGS = [
    pytest.param((), id="default"),
    pytest.param(("--no-shared-storage",), id="stack-frames"),
    pytest.param(("--no-inlining", "--no-const-propagation"), id="no-inlining"),
    pytest.param(("-O0",), id="no-peephole"),
]


BIG = """#include <stdio.h>
struct Big { int v[200]; };            /* 400 bytes, over the old limit */
struct Big mk(int base) {
    struct Big b; int i;
    for (i = 0; i < 200; i++) b.v[i] = base + i;
    return b;
}
struct Big bump(struct Big b) {
    int i;
    for (i = 0; i < 200; i++) b.v[i] += 1000;
    return b;
}
int main(void) {
    struct Big x = bump(mk(1));
    printf("%d %d %d\\n", x.v[0], x.v[100], x.v[199]);
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_an_aggregate_larger_than_the_old_buffer_round_trips(tmp_path, flags):
    assert "1001 1101 1200\n" in build_and_run(tmp_path, BIG, flags)


ALIASING = """#include <stdio.h>
struct S { int a, b, c, d; };
struct S arr[3];
struct S f(int v) { struct S s; s.a=v; s.b=v+1; s.c=v+2; s.d=v+3; return s; }
struct S g(int v) { struct S s; s.a=v; s.b=v+10; s.c=v+20; s.d=v+30; return s; }
int main(void) {
    arr[g(1).a] = f(90);
    printf("%d %d %d %d\\n", arr[1].a, arr[1].b, arr[1].c, arr[1].d);
    return 0;
}
"""


@pytest.mark.parametrize("flags", CONFIGS)
def test_a_second_call_does_not_overwrite_the_first_result(tmp_path, flags):
    """``arr[g(1).a] = f(90)``: the destination address is computed after f
    returns, so with one shared buffer g's result was what got stored."""
    assert "90 91 92 93\n" in build_and_run(tmp_path, ALIASING, flags)


SHAPES = """#include <stdio.h>
struct P2 { char a, b; };                 /* 2 bytes -- comes back in HL */
struct S8 { int a, b, c, d; };
union  U8 { long l; int i[2]; };

struct P2 mkP2(int v) { struct P2 p; p.a=(char)v; p.b=(char)(v+1); return p; }
struct S8 mkS8(int v) { struct S8 s; s.a=v; s.b=v+1; s.c=v+2; s.d=v+3; return s; }
union  U8 mkU8(long v) { union U8 u; u.l = v; return u; }

/* the hidden pointer shifts the declared parameters along */
struct S8 combine(struct S8 x, int k, struct S8 y) {
    struct S8 r;
    r.a = x.a + y.a + k; r.b = x.b + y.b; r.c = x.c; r.d = y.d;
    return r;
}
/* a compound literal built from runtime values */
struct S8 mkc(int v) { return (struct S8){v, v * 2, v + 1, v - 1}; }
/* returning another function's aggregate result */
struct S8 wrap(int v) { return mkc(v); }
struct S8 recur(int n) {
    struct S8 s;
    if (n) { s = recur(n - 1); s.a += n; }
    else { s.a = 0; s.b = 1; s.c = 2; s.d = 3; }
    return s;
}
struct S8 (*fp)(int) = mkS8;
int noise(int x) { return x * 3; }

int main(void) {
    struct P2 p = mkP2(5);
    printf("%d %d\\n", p.a, p.b);
    printf("%d\\n", mkS8(40).c);
    struct S8 x = {1,2,3,4}, y = {10,20,30,40};
    struct S8 r = combine(x, 100, y);
    printf("%d %d %d %d\\n", r.a, r.b, r.c, r.d);
    struct S8 c = mkc(7);
    printf("%d %d %d %d\\n", c.a, c.b, c.c, c.d);
    struct S8 w = wrap(5);
    printf("%d %d %d %d\\n", w.a, w.b, w.c, w.d);
    union U8 u = mkU8(123456789L);
    printf("%ld\\n", u.l);
    struct S8 q = recur(4);
    printf("%d\\n", q.a);
    struct S8 v = fp(20);
    printf("%d %d\\n", v.a, v.d);
    printf("%d\\n", mkS8(3).a + noise(mkS8(4).b));
    return 0;
}
"""

SHAPES_EXPECTED = ("5 6\n"
                   "42\n"
                   "111 22 3 40\n"
                   "7 14 8 6\n"
                   "5 10 6 4\n"
                   "123456789\n"
                   "10\n"
                   "20 23\n"
                   "18\n")


@pytest.mark.parametrize("flags", CONFIGS)
def test_the_shapes_a_return_can_take(tmp_path, flags):
    out = build_and_run(tmp_path, SHAPES, flags + ("--printf", "long"))
    assert SHAPES_EXPECTED in out, out


def test_a_narrow_aggregate_still_comes_back_in_hl(tmp_path):
    """Two bytes or fewer are the value itself, with no hidden pointer.
    Both sides have to agree on that or the frame is off by a word."""
    out = build_and_run(tmp_path, """#include <stdio.h>
struct P { char a, b; };
struct P mk(int v) { struct P p; p.a=(char)v; p.b=(char)(v+1); return p; }
int add(struct P p, int k) { return p.a + p.b + k; }
int main(void) { printf("%d %d\\n", mk(5).b, add(mk(1), 100)); return 0; }
""")
    assert "6 103\n" in out, out


SEPARATE_CALLER = """#include <stdio.h>
#include "sep.h"
int main(void) {
    struct Pt p = mkpt(11);
    Vec v = mkvec(300);
    printf("%d %d %d %d %d %d\\n", p.x, p.y, p.z, p.w, v.v[0], v.v[99]);
    return 0;
}
"""

SEPARATE_CALLEE = """#include "sep.h"
struct Pt mkpt(int a) {
    struct Pt p; p.x=a; p.y=a+1; p.z=a+2; p.w=a+3; return p;
}
Vec mkvec(int base) {
    Vec v; int i;
    for (i = 0; i < 100; i++) v.v[i] = base + i;
    return v;
}
"""

SEPARATE_HEADER = """struct Pt { int x, y, z, w; };
typedef struct { int v[100]; } Vec;
struct Pt mkpt(int a);
Vec mkvec(int base);
"""


def test_the_two_sides_agree_across_translation_units(tmp_path):
    """Caller and callee work the layout out independently, from the same
    prototype.  A typedef'd aggregate exercises the name resolution both
    of them have to do."""
    (tmp_path / "sep.h").write_text(SEPARATE_HEADER)
    (tmp_path / "a.c").write_text(SEPARATE_CALLEE)
    (tmp_path / "b.c").write_text(SEPARATE_CALLER)
    for unit in ("a", "b"):
        r = subprocess.run(
            [sys.executable, "-m", "uc80.main", str(tmp_path / (unit + ".c")),
             "--no-whole-program", "--printf", "int",
             "-I", str(tmp_path), "-o", str(tmp_path / (unit + ".mac"))],
            capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(tmp_path / (unit + ".mac")),
                        "-o", str(tmp_path / (unit + ".rel"))],
                       check=True, capture_output=True, text=True)
    subprocess.run(
        ["ul80", str(LIB_DIR / "crt0.rel"), str(tmp_path / "b.rel"),
         str(tmp_path / "a.rel"), str(LIB_DIR / "libc.lib"),
         str(LIB_DIR / "runtime.lib"), "-o", str(tmp_path / "sep.com")],
        check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "sep.com")],
                         capture_output=True, timeout=120)
    out = run.stdout.decode("latin-1").replace("\r", "")
    assert "11 12 13 14 300 399\n" in out, out
