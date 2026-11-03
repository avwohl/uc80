"""Merge of family L into the integrate14 repair tree: interactions that
RUN.  The expected output is the output of the host compiler.

1. A divide pair (L4, __sdivmod16 and the unsigned form) in a loop or in a
   function other than main keeps the fast __div16 (no compact __div16).

The regcall pragma with the bug round 7 pragma handling is covered by
tests/test_lb_codegen.py (a normal compile) and tests/test_bugfix7_core.py.
"""

import re

import pytest

from .test_integ14_famk_bug6 import host, uc

PAIR_S = r"""
#include <stdio.h>
int a[8];
int main(void)
{
    int n = 7, x = 31000, i;
    for (i = 0; i < 6; i++) {
        a[i] = x % n;
        x = 10 * a[i] + x / n + i;
    }
    printf("%d %d %d\n", x, a[0], a[5]);
    return 0;
}
"""
PAIR_U = PAIR_S.replace("int a[8]", "unsigned a[8]").replace(
    "int n = 7, x = 31000, i;", "unsigned n = 700, x = 61000; int i;").replace(
    "%d %d %d", "%u %u %u")


@pytest.mark.parametrize("src", [PAIR_S, PAIR_U], ids=["signed", "unsigned"])
def test_divmod_pair_in_loop_keeps_fast_div16(tmp_path, src):
    want = host(tmp_path, src)
    out, mac = uc(tmp_path, src)
    assert out == want
    assert re.search(r"EXTRN\s+__dv8w", mac)
    out, _mac = uc(tmp_path, src, "--no-divmod-pair")
    assert out == want


IDX_SRC = r"""
#include <stdio.h>
int main(void)
{
    int a[200], x = 0, n, N = 200;
    for (n = 1; n < 200; n++) a[n] = 1;
    a[0] = 0;
    while (N > 190) {
        n = N--;
        while (--n) {
            a[n] = x % n;
            x = 10 * a[n - 1] + x / n;
        }
        printf("%d ", x);
    }
    printf("\n");
    return 0;
}
"""


def test_divisor_used_as_array_index_is_small(tmp_path):
    """The divisor n indexes a[200]: under 256, so a pair in a loop may use
    the compact __div16 (no __dv8w).  The output stays right."""
    want = host(tmp_path, IDX_SRC)
    out, mac = uc(tmp_path, IDX_SRC)
    assert out == want
    assert re.search(r"EXTRN\s+__dv8w", mac) is None
    out, mac = uc(tmp_path, IDX_SRC, "--no-div16-compact")
    assert out == want


# ---- repair of integrate15 ---------------------------------------------

import subprocess
import sys

from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO


def _compile(tmp_path, text, *flags, extra=(), name="r"):
    src = tmp_path / (name + ".c")
    src.write_text(text)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src),
                        *map(str, extra), "-o", str(mac), *flags],
                       capture_output=True, text=True, cwd=str(REPO),
                       timeout=300)
    return r, mac


def _link_run(tmp_path, mac, name="r"):
    subprocess.run(["um80", str(mac), "-o", str(tmp_path / (name + ".rel"))],
                   check=True, capture_output=True, timeout=120)
    r = subprocess.run(["ul80", str(tmp_path / (name + ".rel")),
                        str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"),
                        "-o", str(tmp_path / (name + ".com"))],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    run = subprocess.run([str(CPMEMU), str(tmp_path / (name + ".com"))],
                         capture_output=True, timeout=120)
    got = run.stdout.decode("latin-1").replace("\r", "")
    return "\n".join(ln for ln in got.split("\n")
                     if not ln.startswith(("CPU mode", "Loaded ",
                                           "Program exit", "Note:"))).strip()


VOL_SRC = r"""
#include <stdio.h>
#if defined(__linux__) || defined(__APPLE__)
static volatile unsigned char fake[2];
#define REG fake[0]
#else
#define REG (*(volatile unsigned char *) 0xE000)
#endif
volatile unsigned char status;
struct dev { volatile unsigned char st; };
static void ack(void) { status; }
static void ack2(void) { (void) status; }
static void ack3(struct dev *d) { d->st; }
static void ack4(volatile unsigned char *p) { *p; }
static void ack5(void) { REG; }
static void plain(void) { 1; }
int main(void)
{
    struct dev d;
    ack(); ack2(); ack3(&d); ack4(&status); ack5(); plain();
    printf("ok\n");
    return 0;
}
"""


def test_empty_call_keeps_a_volatile_read_in_the_body(tmp_path):
    r, mac = _compile(tmp_path, VOL_SRC)
    assert r.returncode == 0, r.stderr
    text = mac.read_text()
    main = text.split("_main:")[1].split("@main_ret")[0]
    # the calls of the five functions that read a device register stay
    # (or are inlined with the read); only plain() goes
    assert main.count("call\t_ack") + main.count("(_status)") >= 4
    assert "call\t_plain" not in main
    assert _link_run(tmp_path, mac) == "ok"
    r2, mac2 = _compile(tmp_path, VOL_SRC, "--no-empty-call", name="r2")
    assert "call\t_ack" in mac2.read_text() or "(_status)" in mac2.read_text()


ASM_CALLS = """\
\t.Z80
\tCSEG
\tEXTRN\t_hook
\tEXTRN\t_hook2
\tPUBLIC\t_viaasm
_viaasm:\tLD\tHL,1
\tPUSH\tHL
\tCALL\t_hook2
\tPOP\tHL
\tCALL\t_hook
\tRET
\tEND
"""


def test_empty_function_called_by_an_assembly_file_links(tmp_path):
    (tmp_path / "ea_asm.mac").write_text(ASM_CALLS)
    text = r"""
#include <stdio.h>
void hook(void) { }
void hook2(int x) { }
void viaasm(void);
int main(void) { hook(); hook2(3); viaasm(); printf("ok\n"); return 0; }
"""
    r, mac = _compile(tmp_path, text, extra=[tmp_path / "ea_asm.mac"])
    assert r.returncode == 0, r.stderr
    assert _link_run(tmp_path, mac) == "ok"


def test_empty_regcall_definition_is_still_an_error(tmp_path):
    text = ("void out(unsigned char, unsigned char);\n"
            "#pragma uc80 regcall out\n"
            "void out(unsigned char p, unsigned char v) { }\n"
            "int main(void) { out(1, 2); return 0; }\n")
    r, _mac = _compile(tmp_path, text)
    assert r.returncode != 0
    assert "regcall" in r.stderr


def test_regcall_pragma_survives_E_and_reaches_codegen(tmp_path):
    from .test_lb_codegen import RC_SRC, RC_ASM
    from ._faml import host_output
    src = tmp_path / "rc.c"
    src.write_text(RC_SRC)
    pre = subprocess.run([sys.executable, "-m", "uc80.main", "-E", str(src)],
                         capture_output=True, text=True, cwd=str(REPO),
                         timeout=120)
    assert pre.returncode == 0, pre.stderr
    assert "#pragma uc80 regcall out in mix" in pre.stdout
    (tmp_path / "rc2.c").write_text(pre.stdout)
    (tmp_path / "io.mac").write_text(RC_ASM)
    r = subprocess.run([sys.executable, "-m", "uc80.main",
                        str(tmp_path / "rc2.c"), str(tmp_path / "io.mac"),
                        "-o", str(tmp_path / "rc2.mac")],
                       capture_output=True, text=True, cwd=str(REPO),
                       timeout=300)
    assert r.returncode == 0, r.stderr
    main = (tmp_path / "rc2.mac").read_text().split("_main:")[1].split(
        "@main_ret")[0]
    assert "call\t_out" in main
    assert re.search(r"call\t_out\n\tpop\tDE", main) is None
    assert _link_run(tmp_path, tmp_path / "rc2.mac", "rc2") == \
        host_output(tmp_path, RC_SRC).strip()


TD_SRC = r"""
#include <stdio.h>
#include <stdint.h>
typedef unsigned int UT;
UT a2(UT x, UT n) { UT a, b; a = x / n; b = x % n; return a * 3 + b; }
uint16_t a3(uint16_t x, uint16_t n)
{
    uint16_t a, b; a = x / n; b = x % n; return a * 3 + b;
}
int16_t a4(int16_t x, int16_t n)
{
    int16_t a, b; a = x / n; b = x % n; return a * 3 + b;
}
int main(void)
{
    printf("%u %u %d\n", a2(60000u, 7), a3(50000u, 300), a4(-30000, 77));
    return 0;
}
"""


def test_divide_pair_through_typedef_names(tmp_path):
    want = host(tmp_path, TD_SRC)
    out, mac = uc(tmp_path, TD_SRC)
    assert out == want
    calls = len(re.findall(r"call\t__(?:div16|sdiv16|dv8w|sdivmod16|"
                           r"udivmod16)", mac, re.I))
    out2, mac2 = uc(tmp_path, TD_SRC, "--no-divmod-pair")
    assert out2 == want
    calls2 = len(re.findall(r"call\t__(?:div16|sdiv16|dv8w|sdivmod16|"
                            r"udivmod16)", mac2, re.I))
    assert calls < calls2


IDX_BIG = r"""
#include <stdio.h>
int main(void)
{
    int a[10], x = 50000 / 3, n, i, t = 0;
    unsigned ux = 60000u, un = 300;
    for (i = 0; i < 10; i++) a[i] = i;
    for (i = 1; i < 10; i++) {
        n = a[i];
        t += a[n];
        n = n * 100 + 300;              /* the divisor is now 400..1200 */
        a[i] = x % n;
        x = x / n + 1000 + i;
        ux = ux / un + ux % un + 60000u;
    }
    printf("%d %d %u\n", t, x, ux);
    return 0;
}
"""


def test_divisor_index_name_that_grows_past_255_is_still_right(tmp_path):
    want = host(tmp_path, IDX_BIG)
    out, _mac = uc(tmp_path, IDX_BIG)
    assert out == want
