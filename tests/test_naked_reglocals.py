"""Naked functions together with register locals (BC, DE, IY).

Rules (docs/register_locals.md, Naked functions):
- The pass never touches a naked function (no prologue, user assembly).
- A call to a naked function is a call like any other: the callee may
  change BC, DE, HL, A and IY, so a local in BC, DE or IY is saved around
  the call.  A naked function is never "IY clean".
The programs run in cpmemu.  The output must be the same with the pass on,
off, and with UC80_NO_IY=1.
"""

import os

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_register_locals import body_of

PROG = r"""
#include <stdio.h>
int clob(int a) __naked
{
    __asm
        ld hl,#2
        add hl,sp
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a
        ld bc,#0x1111
        ld de,#0x2222
        ld iy,#0x3333
        inc hl
        ret
    __endasm;
}
static int arr[8] = {1, 2, 3, 4, 5, 6, 7, 800};
static int work(int *p, int n) {
    int s = 0, t = 7;
    for (int i = 0; i < n; i++) {
        s += p[i];
        t += clob(i) & 3;
        s += *p;
    }
    return s * 10 + t;
}
struct P { int x; int y; struct P *next; };
static int walk(struct P *p) {
    int t = 0;
    while (p) { t += p->x + clob(p->y) - p->y; p = p->next; }
    return t;
}
int main(void) {
    struct P c = {5, 9, 0}, b = {-2, 4, &c}, a = {7, -1, &b};
    printf("%d\n", walk(&a));
    printf("%d\n", work(arr, 8));
    printf("%d\n", clob(41));
    return 0;
}
"""

def run(tmp_path, *flags, env=None):
    old = dict(os.environ)
    try:
        os.environ.pop("UC80_NO_IY", None)
        if env:
            os.environ.update(env)
        mac, path = compile_mac(tmp_path, PROG, *flags)
    finally:
        os.environ.clear()
        os.environ.update(old)
    return mac, run_mac(tmp_path, path).split()


@needs_run
def test_same_output_in_every_mode(tmp_path):
    base_mac, base = run(tmp_path, "--no-register-locals")
    assert base[2] == "42"
    for flags, env in [((), None), ((), {"UC80_NO_IY": "1"}),
                       (("--no-inlining",), None),
                       (("--no-register-cc",), None)]:
        _, out = run(tmp_path, *flags, env=env)
        assert out == base, (flags, env)


def test_naked_body_untouched_and_not_iy_clean(tmp_path):
    on = compile_mac(tmp_path, PROG)[0]
    off = compile_mac(tmp_path, PROG, "--no-register-locals")[0]
    assert body_of(on, "clob") == body_of(off, "clob")
    # the loop keeps its locals across the call: a push of a pair or IY
    # wraps the call, and the wrap is not pruned
    text = body_of(on, "work")
    if "work" in on and "call\t_clob" in text:
        i = text.index("call\t_clob")
        around = text[max(0, i - 200):i + 200]
        assert "push" in around and "pop" in around


@needs_run
def test_static_naked_functions_of_one_name_in_two_files(tmp_path):
    """Each file has a static naked h; the merge renames both (h__s0,
    h__s1), and each stays naked and is called from its own file."""
    a = tmp_path / "a.c"
    a.write_text("""
static int h(void) __naked { __asm
    ld hl,#11
    ret
    __endasm; }
int fa(void) { int r = h(); return r * 2; }
""")
    b = tmp_path / "b.c"
    b.write_text("""
#include <stdio.h>
int fa(void);
static int h(void) __naked { __asm
    ld hl,#31
    ret
    __endasm; }
int main(void) { int r = h(); printf("%d %d\\n", fa(), r); return 0; }
""")
    mac, path = compile_mac(tmp_path, a.read_text(), "-I", str(tmp_path),
                            files=[b])
    assert "h__s0" in mac and "h__s1" in mac
    assert run_mac(tmp_path, path).split() == ["22", "31"]


ROOT_PROG = r"""
#include <stdio.h>
static int cnt;
static int step(int n) { int t = 0; for (int i = 0; i < n; i++) t += i & 3; return t; }
static void bump(void) { cnt += step(10); }
void unused_naked(void) __naked { __asm
    call _bump
    ret
    __endasm; }
void tick(void) __naked { __asm
    call _bump
    ld bc,#0
    ld de,#0
    ret
    __endasm; }
int main(void) {
    int a = 5, b = 6;
    for (int i = 0; i < 3; i++) { tick(); a += i; b += a; }
    printf("%d %d %d\n", cnt, a, b);
    return 0;
}
"""


@needs_run
@pytest.mark.parametrize("flags", [(), ("--no-register-locals",),
                                   ("--no-inlining",),
                                   ("--no-dead-elimination",)])
def test_naked_root_with_dead_code_and_inlining(tmp_path, flags):
    """An uncalled naked function stays (it is a root of the call graph),
    so does the helper that only its asm text calls; a naked function is
    never inlined; the locals of main survive a call that clobbers BC/DE."""
    mac, path = compile_mac(tmp_path, ROOT_PROG, *flags)
    assert "_unused_naked:" in mac and "_tick:" in mac
    assert "_bump:" in mac
    assert run_mac(tmp_path, path).split() == ["39", "8", "25"]


# ---- IY across a naked call -------------------------------------------------

IY_PROG = r"""
#include <stdio.h>
struct N { int a; int b; int c; struct N *next; };
struct N pool[3] = {{1, 2, 3, pool + 1}, {10, 20, 30, pool + 2}, {100, 200, 300, 0}};
void trash(void) __naked { __asm
    ld iy,#0x7777
    ld bc,#0x1234
    ld de,#0x4321
    ret
    __endasm; }
int walk(struct N *p) {
    int t = 0;
    while (p) {
        t += p->a + p->b;
        p->c = t;
        trash();
        t += p->c + p->a;
        p->b = t;
        p = p->next;
    }
    return t;
}
int main(void) {
    printf("%d\n", walk(pool));
    printf("%d %d %d\n", pool[0].b, pool[1].b, pool[2].b);
    return 0;
}
"""


@needs_run
def test_iy_pointer_survives_a_naked_call(tmp_path):
    """A pointer local in IY is saved around a call of a naked function
    that sets IY: the same output with the pass on and off."""
    old = dict(os.environ)
    try:
        os.environ.pop("UC80_NO_IY", None)
        mac_on, path = compile_mac(tmp_path, IY_PROG)
        on = run_mac(tmp_path, path)
        os.environ["UC80_NO_IY"] = "1"
        mac_noiy, path = compile_mac(tmp_path, IY_PROG)
        noiy = run_mac(tmp_path, path)
    finally:
        os.environ.clear()
        os.environ.update(old)
    mac_off, path = compile_mac(tmp_path, IY_PROG, "--no-register-locals")
    off = run_mac(tmp_path, path)
    assert on == noiy == off, (on, noiy, off)
    # the pointer is really in IY, and the call has a push IY / pop IY wrap
    text = body_of(mac_on, "walk")
    assert "IY" in text and "(IY+" in text, text
    i = text.index("call\t_trash")
    assert "push\tIY" in text[:i] and "pop\tIY" in text[i:], text
    assert "IY" not in body_of(mac_noiy, "walk")


def test_naked_mismatch_warning_of_a_static_names_its_own_file(tmp_path):
    """Both files have a static h; only a.c has a prototype that lacks
    __naked.  The warning names a.c and line 1, and the C name h."""
    import subprocess
    import sys
    (tmp_path / "a.c").write_text(
        "static int h(void);\n"
        "static int h(void) __naked { __asm\n ld hl,#1\n ret\n __endasm; }\n"
        "int fa(void) { return h(); }\n")
    (tmp_path / "b.c").write_text(
        "static int h(void) __naked { __asm\n ld hl,#2\n ret\n __endasm; }\n"
        "int fa(void);\nint main(void) { return h() + fa(); }\n")
    r = subprocess.run([sys.executable, "-m", "uc80.main", "a.c", "b.c",
                        "-o", "t.mac"], cwd=tmp_path, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr
    warns = [ln for ln in r.stderr.splitlines() if "naked" in ln]
    assert len(warns) == 1, r.stderr
    assert warns[0].startswith("uc80: warning: a.c:1: 'h' is naked in its "
                               "definition"), warns
    assert "__s" not in warns[0]
