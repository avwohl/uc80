"""Every access to a volatile object happens as written.

C23 5.1.2.3 and 6.7.3: a volatile read or write is not elided, merged,
duplicated or reordered.  Four passes used to break that:

  * the AST optimizer (dead stores, folds of identical operands, loop
    unrolling) -- fixed in uc_core, tests in uc_core/tests/test_volatile.py;
  * the inliner, which dropped or duplicated a volatile argument;
  * the peephole pass, which sees assembly text only and fused
    `ld (_g),A / ld A,(_g)`, dropped the second of two equal reads and
    removed the store of `g = g`.  The code generator now brackets the code
    that may access a volatile object and main.py fences it with labels
    (volatile_fence.py);
  * the 32-bit ++ and += fast paths, which accessed the high word only on
    a carry.

These tests compile C and count the loads and stores of each volatile
symbol per function, and run one program end to end.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from uc80 import volatile_fence as vf

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

SOURCE = r"""
typedef volatile unsigned char vb;
volatile unsigned char g;
volatile unsigned int w;
volatile unsigned long L;
static volatile unsigned char sg;
extern volatile unsigned char eg;
vb tg;
volatile unsigned char arr[4];
struct S { volatile unsigned char a; volatile unsigned int b; } s;
volatile unsigned char *vp;
volatile unsigned int *vpw;
unsigned char * volatile qv;
unsigned char x, plain;
unsigned int y;
int r;
volatile int gi, gj;

void store3(void) { g = 1; g = 2; g = 3; }
void static2(void) { sg = 1; sg = 2; }
void extern2(void) { eg = 1; eg = 2; }
void typedef2(void) { tg = 1; tg = 2; }
void read2(void) { x = g; x = g; }
void wread2(void) { y = w; y = w; }
void selfassign(void) { g = g; }
void rmw3(void) { g |= 1; g |= 1; g |= 1; }
void wrmw(void) { w += 1; w += 1; }
void winc3(void) { w++; w++; w++; }
void ginc3(void) { g++; g++; g++; }
unsigned char wr_rd(void) { g = 5; return g; }
unsigned int wwr_rd(void) { w = 7; return w; }
void and_and(void) { x = g && g; }
void cmp_after_store(void) { g = 5; if (g == 5) x = 1; }
int eq_self(void) { return gi == gi; }
int le_self(void) { return gi <= gi; }
int or_self(void) { return gi | gi; }
int or_nested(void) { return (gi | gj) | gj; }
int times_two(void) { return gi * 2; }
void mod_one(void) { r = gi % 1; }
void dead_read(void) { r = gi; r = 1; }
void dead_read_arr(void) { r = arr[1]; r = 2; }
void arr2(void) { x = arr[2]; x = arr[2]; }
void member2(void) { x = s.a; x = s.a; y = s.b; y = s.b; }
void ptr_store2(void) { *vp = 1; *vp = 2; }
void ptr_read2(void) { x = *vp; x = *vp; }
void ptr_selfassign(void) { *vp = *vp; }
void ptr_word2(void) { y = *vpw; y = *vpw; }
void cast_store2(void) { *(volatile unsigned char *)0x40 = 1; *(volatile unsigned char *)0x40 = 1; }
void qv_store2(void) { *qv = 1; *qv = 2; }
void local_rw(void) { volatile unsigned char l; l = 1; l = 2; x = l; x = l; }
void local_init(void) { volatile int l = 3; y = l + l; }
void plain2(void) { plain = 1; plain = 2; }
const char *tt(void) { const char *t = "x" "y"; g = 1; return t; }
void long_inc(void) { L++; }
void long_add(void) { L += 5; }
void long_selfadd(void) { L = L + 1; }
void long_or(void) { L |= 0xFF; }
void unrolled(void) { for (volatile int i = 0; i < 3; i++) r += 1; }
void tdunrolled(void) { for (vb i = 0; i < 3; i++) r += 1; }
int main(void) { return 0; }
"""

# function: (loads, stores) of the symbols named in SYMS[function]
# (default: g).  A word or long counts once per ld of a word.
EXPECT = {
    "store3": ("_g", 0, 3),
    "static2": ("_sg", 0, 2),
    "extern2": ("_eg", 0, 2),
    "typedef2": ("_tg", 0, 2),
    "read2": ("_g", 2, 0),
    "wread2": ("_w", 2, 0),
    "selfassign": ("_g", 1, 1),
    "rmw3": ("_g", 3, 3),
    "wrmw": ("_w", 2, 2),
    "winc3": ("_w", 3, 3),
    "wr_rd": ("_g", 1, 1),
    "wwr_rd": ("_w", 1, 1),
    "and_and": ("_g", 2, 0),
    "cmp_after_store": ("_g", 1, 1),
    "eq_self": ("_gi", 2, 0),
    "le_self": ("_gi", 2, 0),
    "or_self": ("_gi", 2, 0),
    "or_nested": ("_gj", 2, 0),
    "times_two": ("_gi", 1, 0),
    "mod_one": ("_gi", 1, 0),
    "dead_read": ("_gi", 1, 0),
    "dead_read_arr": ("_arr", 1, 0),
    "arr2": ("_arr", 2, 0),
    "member2": ("_s", 4, 0),
    "ptr_store2": ("(HL)", 0, 2),
    "ptr_read2": ("(HL)", 2, 0),
    "ptr_selfassign": ("(HL)", 1, 1),
    "ptr_word2": ("(HL)", 4, 0),        # a word read is two byte reads
    "cast_store2": ("(HL)", 0, 2),
    "qv_store2": ("_qv", 2, 0),         # the volatile pointer is read twice
    "local_rw": ("AUTO", 2, 2),
    "local_init": ("AUTO", 2, 1),
    "long_inc": ("_L", 2, 2),
    "long_add": ("_L", 2, 2),
    "long_selfadd": ("_L", 2, 2),
    "long_or": ("_L", 2, 2),
}

_MEM = r"\((?:{})(?:\+\d+)?\)"


def _function(mac: str, name: str) -> list[str]:
    m = re.search(rf"^_{name}:\n(.*?)^@{name}_ret:", mac, re.M | re.S)
    assert m, name
    return [l.split(";")[0].strip() for l in m.group(1).splitlines()
            if l.startswith("\t")]


def _count(lines: list[str], sym: str) -> tuple[int, int]:
    """(loads, stores) of ``sym`` in ``lines``.  ``(HL)`` counts the loads
    from and stores to an address held in HL, ``AUTO`` those of a local."""
    if sym == "(HL)":
        loads = [l for l in lines if re.match(r"ld\s+[A-Z]+,\(HL\)$", l)]
        stores = [l for l in lines if re.match(r"ld\s+\(HL\),", l)]
        return len(loads), len(stores)
    if sym == "AUTO":
        pat = r"\(\?\?AUTO(?:\+\d+)?\)"
    else:
        pat = _MEM.format(re.escape(sym))
    loads = [l for l in lines if re.match(rf"ld\s+[A-Z]+,{pat}$", l)]
    stores = [l for l in lines if re.match(rf"ld\s+{pat},", l)]
    return len(loads), len(stores)


def _compile(tmp_path, source, *flags) -> str:
    c = tmp_path / "t.c"
    c.write_text(source)
    mac = tmp_path / "t.mac"
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c), "-o", str(mac),
         "--no-whole-program", "--no-dead-elimination", "--no-embed-runtime",
         "--no-embed-startup", *flags],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return mac.read_text()


@pytest.fixture(scope="module")
def mac(tmp_path_factory):
    return _compile(tmp_path_factory.mktemp("vol"), SOURCE, "--no-inlining")


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_accesses_happen_as_written(mac, name):
    sym, loads, stores = EXPECT[name]
    assert _count(_function(mac, name), sym) == (loads, stores)


def test_char_increments_are_three_read_modify_writes(mac):
    body = _function(mac, "ginc3")
    assert sum(1 for l in body if l == "inc\t(HL)") == 3
    assert sum(1 for l in body if l == "ld\tHL,_g") == 3


def test_plain_stores_are_still_merged(mac):
    assert _count(_function(mac, "plain2"), "_plain") == (0, 1)


@pytest.mark.parametrize("name", ["unrolled", "tdunrolled"])
def test_loop_over_a_volatile_variable_is_kept(mac, name):
    assert re.search(r"^@FOR\d+:", "\n".join(
        l for l in mac.split("\n")), re.M)
    body = _function(mac, name)
    assert any(l.startswith("jr") or l.startswith("jp") for l in body)


def test_no_marker_or_fence_label_reaches_the_assembler(mac):
    assert "__UC80_VOL" not in mac
    assert "??VOL" not in mac


@pytest.mark.parametrize("flags", [("--no-optimize",), ("--no-ast-optimize",)])
def test_accesses_happen_as_written_without_optimization(tmp_path, flags):
    text = _compile(tmp_path, SOURCE, "--no-inlining", *flags)
    for name in ("store3", "rmw3", "wr_rd", "long_inc"):
        sym, loads, stores = EXPECT[name]
        assert _count(_function(text, name), sym) == (loads, stores)
        assert "__UC80_VOL" not in text and "??VOL" not in text


INLINE_SOURCE = r"""
volatile unsigned char g; volatile unsigned int w; unsigned char x;
static unsigned char twice(unsigned char v) { return v + v; }
static unsigned char ign(unsigned char v) { return 0; }
static unsigned char second(unsigned char a, unsigned int b) { return b; }
static unsigned char rd(void) { return g; }
static unsigned char once(unsigned char v) { return v + 1; }
static unsigned char cond(unsigned char c, unsigned char v) { return c ? v : 0; }
void e_once(void) { x = once(g); }
void e_cond(void) { x = cond(1, g); }
void e_twice(void) { x = twice(g); }
void e_ignored(void) { x = ign(g); }
void e_stmt(void) { ign(g); }
void e_second(void) { x = second(g, w); }
void e_rd2(void) { x = rd() + rd(); }
int main(void) { return 0; }
"""


@pytest.mark.parametrize("name,loads", [
    ("e_twice", 1), ("e_ignored", 1), ("e_stmt", 1), ("e_rd2", 2)])
def test_inlining_keeps_the_number_of_reads(tmp_path, name, loads):
    text = _compile(tmp_path, INLINE_SOURCE)
    assert _count(_function(text, name), "_g")[0] == loads


def test_inlining_is_kept_for_a_volatile_argument_used_once(tmp_path):
    text = _compile(tmp_path, INLINE_SOURCE)
    body = _function(text, "e_once")
    assert _count(body, "_g")[0] == 1
    assert not any(l.startswith("call") for l in body)


def test_inlining_is_refused_for_a_volatile_argument_used_conditionally(tmp_path):
    text = _compile(tmp_path, INLINE_SOURCE)
    body = _function(text, "e_cond")
    assert _count(body, "_g")[0] == 1
    assert any(l.startswith("call") for l in body)


def test_inlining_second_argument_is_read(tmp_path):
    text = _compile(tmp_path, INLINE_SOURCE)
    body = _function(text, "e_second")
    assert _count(body, "_g")[0] == 1 and _count(body, "_w")[0] == 1


# ---------------------------------------------------------------------
# The fence pass on its own.
# ---------------------------------------------------------------------

REGION = "\n".join([
    "_f:",
    "\tld\tHL,5",
    vf.VOL_BEGIN,
    "\tld\tA,1",
    "\tld\t(_g),A",
    "\tld\tA,(_g)",
    "\tld\tL,A",
    vf.VOL_END,
    "\tld\tA,(_x)",
    "\tret",
])


def test_fence_puts_labels_around_memory_accesses_inside_a_region_only():
    out = vf.fence_regions(REGION).split("\n")
    stores = out.index("\tld\t(_g),A")
    assert out[stores - 1].startswith("??VOL") and out[stores + 1].startswith("??VOL")
    # one label between two adjacent accesses
    assert out[stores + 1] != out[stores + 3] or out[stores + 2] == "\tld\tA,(_g)"
    n = sum(1 for l in out if l.startswith("??VOL"))
    assert n == 3                       # before the store, between, after
    # nothing outside the region
    assert out[out.index("\tld\tA,(_x)") - 1] == vf.VOL_END


def test_strip_removes_exactly_what_fence_added():
    plain = "\n".join(l for l in REGION.split("\n")
                      if l not in (vf.VOL_BEGIN, vf.VOL_END))
    assert vf.strip(vf.fence_regions(REGION)) == plain


def test_text_without_a_region_is_returned_unchanged():
    text = "_f:\n\tld\tA,(_x)\n\tret\n"
    assert vf.fence_regions(text) is text
    assert vf.strip(text) is text


# ---------------------------------------------------------------------
# End to end.
# ---------------------------------------------------------------------

needs_toolchain = pytest.mark.skipif(
    not (CPMEMU.exists() and shutil.which("um80") and shutil.which("ul80")
         and (LIB_DIR / "libc.lib").exists()),
    reason="needs cpmemu, um80, ul80 and the built libraries")

E2E = r"""
#include <stdio.h>
volatile unsigned char g;
volatile unsigned int w;
volatile unsigned long L;
volatile unsigned char arr[4];
volatile unsigned char *vp = arr;
unsigned char x;
unsigned int y;

int main(void)
{
    g = 1; g = 2; g = 3;
    printf("g=%u\n", g);
    g |= 4; g |= 8; g ^= 1;
    printf("g=%u\n", g);
    w = 7; y = w; w++; w++; w += 3;
    printf("w=%u y=%u\n", w, y);
    L = 0xFFFE; L++; L++; L += 0x10000UL;
    printf("L=%lu\n", L);
    vp[1] = 9; vp[2] = vp[1]; *vp = vp[2] + 1;
    printf("arr=%u %u %u\n", arr[0], arr[1], arr[2]);
    g = 5; x = g && g;
    printf("x=%u\n", x);
    for (volatile int i = 0; i < 3; i++) y += i;
    printf("y=%u\n", y);
    return 0;
}
"""


@needs_toolchain
def test_end_to_end(tmp_path):
    c = tmp_path / "prog.c"
    c.write_text(E2E)
    mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c),
                        "--printf", "long", "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    assert "??VOL" not in mac.read_text()
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120, env=dict(os.environ))
    out = [l.strip() for l in run.stdout.decode("latin-1").replace("\r", "")
           .splitlines() if l.strip() and not l.startswith(
               ("CPU mode", "Loaded", "Program exit"))]
    assert out == ["g=3", "g=14", "w=12 y=7", "L=131072", "arr=10 9 9",
                   "x=1", "y=10"]
