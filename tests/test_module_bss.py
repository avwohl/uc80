"""One BSS block for each separately compiled module (stage B).  See
docs/rom.md ("Separate compilation").

With --no-whole-program each module has its zero-start data (uninitialised
globals, uninitialised statics, the shared storage of its locals) in one
named COMMON block, `COMMON /uc_<stem>_<hash>/`.  The linker gives each name
its own space, the start-up code clears the range __BSS_START to __BSS_END,
and a ROM image has no copy of the zero bytes.  Before this change the
bytes were in DSEG: in the image, and in a ROM copied from ROM to RAM.
"""

import re
import subprocess
import sys

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import (FLOWS, REPO, build_separate, machine, need_flow, run,
                        symbols, uc80_cmd)


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


A_C = """
#include <stdint.h>
volatile uint8_t buf_a[100];
static int hidden_a;
int cnt_a;
int tabl_a[4] = {1, 2, 3, 4};
static int work_a(int x)
{
    int t[3];
    t[0] = x; t[1] = x + 1; t[2] = x + 2;
    return t[0] + t[1] + t[2];
}
int fa(int n)
{
    hidden_a += n;
    cnt_a++;
    buf_a[n] = (uint8_t)work_a(n);
    return hidden_a + tabl_a[n & 3];
}
int peek_a(int n) { return buf_a[n]; }
"""

B_C = """
#include <stdint.h>
volatile uint8_t buf_b[50];
static int hidden_b;
int cnt_b;
volatile int res[8];
extern int cnt_a;
int fa(int n);
int peek_a(int n);
static int work_b(int x)
{
    int t[4];
    t[0] = x; t[1] = x * 2; t[2] = x * 3; t[3] = fa(x);
    return t[0] + t[1] + t[2] + t[3];
}
int main(void)
{
    int i;
    for (i = 0; i < 4; i++) {
        hidden_b += 10;
        buf_b[i] = (uint8_t)(100 + i);
        res[i] = work_b(i);
    }
    res[4] = cnt_a;
    res[5] = hidden_b;
    res[6] = peek_a(3) + buf_b[3];
    cnt_b = 5;
    res[7] = cnt_b;
    return 0;
}
"""

# hidden_a after calls with n = 0..3: 0, 1, 3, 6
EXPECT_RES = [
    0 + 0 + 0 + (0 + 1),                 # i=0: fa(0) = 0 + tabl[0]
    1 + 2 + 3 + (1 + 2),                 # i=1: fa(1) = 1 + tabl[1]
    2 + 4 + 6 + (3 + 3),                 # i=2
    3 + 6 + 9 + (6 + 4),                 # i=3
    4,                                   # cnt_a
    40,                                  # hidden_b
    (3 + 4 + 5) + 103,                   # peek_a(3) + buf_b[3]
    5,
]


def _res(m):
    return [int.from_bytes(m.peek(m.addr("_res") + 2 * i, 2), "little")
            for i in range(8)]


def _sdas_areas(rel):
    out = {}
    lines = rel.read_text().splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("A "):
            f = ln.split()
            out[f[1]] = int(f[3], 16)
    return out


def _build_old_area(tmp_path):
    d = tmp_path / "old"
    d.mkdir()
    (d / "a.c").write_text((tmp_path / "a.c").read_text())
    run(uc80_cmd("--target=rom", "--asm-syntax=sdas", "--no-whole-program",
                 "--no-module-bss", "-I", str(REPO / "lib" / "include"),
                 "a.c", "-o", "a.s"), d)
    run(["sdasz80", "-g", "-l", "-o", "a.s"], d)
    return _sdas_areas(d / "a.rel")


@pytest.mark.parametrize("flow", FLOWS)
def test_two_modules_each_have_a_cleared_bss_block(flow, tmp_path, libs):
    paths, errs, link = build_separate(
        tmp_path, {"b.c": B_C, "a.c": A_C}, flow, libs, crt0="plain")
    assert link.returncode == 0, link.stdout + link.stderr
    m = machine(paths)               # random RAM, poisoned SP
    assert m.run(max_tstates=2_000_000).reason == "halt"
    got = _res(m)
    assert got == EXPECT_RES, got
    if flow == "um80":
        sy = symbols(paths)
        assert sy["__BSS_START"] < sy["__BSS_END"]
        # only the initialised table is copied: no zero bytes in the image
        assert sy["__DATA_LEN"] == 8
        text = (tmp_path / "a.mac").read_text()
        blocks = re.findall(r"common\s+/(uc_a_[0-9a-f]{4})/", text)
        assert len(blocks) == 1
        assert "\tdseg" in text         # the initialised table stays there
        # the two modules have two different blocks
        other = re.findall(r"common\s+/(uc_b_[0-9a-f]{4})/",
                           (tmp_path / "b.mac").read_text())
        assert len(other) == 1 and other != blocks
    else:
        # zero data is in the RAM area _DATA (no bytes in the image); only
        # the table is in _INITIALIZED, which the start-up code copies
        areas = _sdas_areas(tmp_path / "a.rel")
        assert areas["_INITIALIZED"] == 8, areas
        assert areas["_DATA"] >= 100 + 2 + 2 + 6, areas
        old = _build_old_area(tmp_path)
        assert old["_INITIALIZED"] > areas["_INITIALIZED"] + 100, old


@pytest.mark.parametrize("flow", FLOWS)
def test_a_module_without_zero_data_has_no_block(flow, tmp_path, libs):
    src = "int t[3] = {1, 2, 3};\nint f(void) { return t[1]; }\n"
    paths, errs, link = build_separate(
        tmp_path, {"t.c": src, "m.c": "int f(void);\nvolatile int r;\n"
                   "int main(void) { r = f(); return 0; }\n"}, flow, libs,
        crt0="plain")
    assert link.returncode == 0, link.stdout + link.stderr
    if flow == "um80":
        assert "common" not in (tmp_path / "t.mac").read_text().lower() \
            .replace("; common", "")
    m = machine(paths)
    assert m.run(max_tstates=200_000).reason == "halt"
    assert m.peek16("_r") == 2


@pytest.mark.parametrize("flow", FLOWS)
def test_same_file_name_in_two_directories_does_not_share_a_block(
        flow, tmp_path):
    need_flow(flow)
    names = []
    for d in ("d1", "d2"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "util.c").write_text("char big[40];\n")
        out = tmp_path / d / "util.mac"
        run(uc80_cmd("--target=rom", "--no-whole-program",
                     str(tmp_path / d / "util.c"), "-o", str(out)), REPO)
        names += re.findall(r"common\s+/(\w+)/", out.read_text())
    assert len(names) == 2 and names[0] != names[1], names


def test_cpm_two_modules_share_nothing_and_the_com_file_is_smaller(tmp_path):
    from tests.test_struct_return_abi import CPMEMU, LIB_DIR
    if not (CPMEMU.exists() and (LIB_DIR / "libc.lib").exists()):
        pytest.skip("needs cpmemu and the libraries")
    (tmp_path / "a.c").write_text("""
int ga[300];
int fa(void) { int i; for (i = 0; i < 300; i++) ga[i] = i; return ga[299]; }
""")
    (tmp_path / "b.c").write_text("""
#include <stdio.h>
int gb[300];
int fa(void);
int main(void)
{
    int i, s = 0;
    for (i = 0; i < 300; i++) gb[i] = 1000 + i;
    s = fa();
    printf("%d %d %d\\n", s, gb[0], gb[299]);
    return 0;
}
""")
    sizes = {}
    for tag, extra in (("new", []), ("old", ["--no-module-bss"])):
        d = tmp_path / tag
        d.mkdir()
        for u in ("a", "b"):
            r = subprocess.run(
                [sys.executable, "-m", "uc80.main", str(tmp_path / (u + ".c")),
                 "--no-whole-program", *extra, "-o", str(d / (u + ".mac"))],
                capture_output=True, text=True, cwd=str(REPO))
            assert r.returncode == 0, r.stderr
            subprocess.run(["um80", str(d / (u + ".mac")), "-o",
                            str(d / (u + ".rel"))], check=True,
                           capture_output=True)
        subprocess.run(["ul80", str(LIB_DIR / "crt0.rel"), str(d / "b.rel"),
                        str(d / "a.rel"), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o",
                        str(d / "p.com"), "-S", str(d / "p.sym")],
                       check=True, capture_output=True)
        if tag == "new":
            # the range that crt0 clears holds both arrays
            sy = {}
            for ln in (d / "p.sym").read_text().splitlines():
                f = ln.split()
                if len(f) == 2:
                    sy[f[1].lower()] = int(f[0], 16)
            lo, hi = sy["__bss_start"], sy["__bss_end"]
            assert lo <= sy["_ga"] < hi and lo <= sy["_gb"] < hi, sy
            assert hi - lo >= 1200
        out = subprocess.run([str(CPMEMU), str(d / "p.com")],
                             capture_output=True, timeout=60)
        assert out.stdout.decode("latin-1").replace("\r", "") == \
            "299 1000 1299\n"
        sizes[tag] = (d / "p.com").stat().st_size
    assert sizes["old"] - sizes["new"] >= 1100, sizes
