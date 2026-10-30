"""Interactions of family I (LICM, value ranges, index narrowing,
noreturn, the new decimal conversion) with the bug-round-3 and integrate9
changes.  Every program runs in cpmemu and the output is compared with the
host C compiler, with 16-bit int, with --int 32 and with the family I
loop passes off.  The host runs have a timeout.
"""

import shutil
import subprocess

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401
from tests.test_register_cc import compile_mac, build_run

HOST_CC = shutil.which("cc")
LOOPS_OFF = ("--no-licm", "--no-range-cmp", "--no-narrow-index")
CONFIGS = [
    pytest.param((), id="default"),
    pytest.param(("--int", "32"), id="int32"),
    pytest.param(LOOPS_OFF, id="loops-off"),
    pytest.param(("-O0",), id="O0"),
    pytest.param(("--int", "32") + LOOPS_OFF, id="int32-loops-off"),
]


def host(tmp_path, src):
    c = tmp_path / "h.c"
    c.write_text(src)
    exe = tmp_path / "h"
    subprocess.run([HOST_CC, "-w", "-o", str(exe), str(c)], check=True,
                   capture_output=True, text=True, timeout=120)
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=60).stdout


# bit-field stores through 32-bit index helpers, static array + n, narrowing
NARROW = r'''#include <stdio.h>
struct S { unsigned f:7; unsigned g:9; } a[8];
struct Y { unsigned x:4; unsigned y:6; } yy[5];
static int nv[] = {1,2,3,4,5,6,7,8};
static long lv[] = {10L,20L,30L,40L};
static int *pn = nv + 2;
static long *pl = lv + 1;
unsigned char buf[64];
long idx(long k) { return k * 3 + 1; }
int main(void){
  long i; unsigned long u; int s = 0;
  for (i = 0; i < 40; i++) { a[i % 7].f += (unsigned)i; a[(i * 5) % 8].g ^= (unsigned)(i * 3); }
  for (i = 0; i < 8; i++) s += a[i].f * 3 + a[i].g;
  printf("A %d\n", s);
  for (i = 0; i < 30; i++) { yy[idx(i) % 5].x++; yy[(i + 2) % 5].y += (unsigned)(i & 7); }
  s = 0; for (i = 0; i < 5; i++) s += yy[i].x * 100 + yy[i].y;
  printf("B %d\n", s);
  for (u = 0; u < 60UL; u++) buf[u] = (unsigned char)(u * 7);
  s = 0; for (u = 0; u < 60UL; u += 3) s += buf[u] + buf[u + 1];
  printf("C %d\n", s);
  for (i = 0; i < 4; i++) s += pn[i] * 10 + (int)pl[i % 3] + nv[i + 2];
  printf("D %d\n", s);
  for (i = 7; i >= 0; i--) { a[i].f = (unsigned)(i * 11); a[i].g += a[(i + 1) % 8].f; }
  s = 0; for (i = 0; i < 8; i++) s += a[i].f + a[i].g;
  printf("E %d\n", s);
  return 0; }
'''

# LICM and ranges with bit-field RMW, block-scope enum, constexpr, volatile
LICM = r'''#include <stdio.h>
struct B { unsigned a:5; unsigned b:3; unsigned c:8; } bb;
static volatile int vv;
static int cnt;
static int tick(void) { return ++cnt; }
int main(void){
  int n = 9, k = 3, i, tot = 0; unsigned char ub;
  for (i = 0; i < n; i++) { bb.a += k * n; bb.b = bb.b + (n + k); bb.c ^= i; }
  printf("A %u %u %u\n", bb.a, bb.b, bb.c);
  { enum { E1 = 4, E2 = E1 * 3 };
    for (i = 0; i < E2; i++) tot += E1 * n + i; }
  printf("B %d\n", tot);
  { enum { E1 = 7 };
    for (i = 0; i < 5; i++) tot += E1 * k + i; }
  printf("C %d\n", tot);
  for (i = 0; i < 6; i++) { vv = n * k; tot += vv + (n - k); }
  printf("D %d\n", tot);
  for (i = 0; i < 5; i++) bb.c += tick();
  printf("E %d %u\n", cnt, bb.c);
  for (i = 0; i < 4; i++) bb.a = bb.a + tick() * tick();
  printf("F %d %u\n", cnt, bb.a);
  for (ub = 0; ub < 20; ub++) tot += ub * (n + k);
  printf("G %d\n", tot);
  { unsigned j; int t2 = 0; for (j = 0; j < 100; j++) { if (j > 50 && (j & 1)) t2 += (int)(j % 9); } printf("H %d\n", t2); }
  return 0; }
'''

NORET = r'''#include <stdio.h>
#include <stdlib.h>
[[noreturn]] static void bail(int c) { printf("bail %d\n", c); exit(c); }
int main(void){
  int i, t = 0;
  for (i = 0; i < 10; i++) { t += i * 3; if (t > 100) bail(2); if (i == 8) bail(3); }
  printf("not reached %d\n", t);
  return 0; }
'''


@pytest.mark.parametrize("flags", CONFIGS)
@pytest.mark.parametrize("name,src", [("narrow", NARROW), ("licm", LICM),
                                      ("noret", NORET)])
def test_program_matches_host(tmp_path, flags, name, src):
    want = host(tmp_path, src)
    got = build_and_run(tmp_path, src, flags=flags)
    assert got == want


def test_narrowing_turns_itself_off_with_int32(tmp_path):
    def mac_of(tag, *flags):
        d = tmp_path / tag
        d.mkdir()
        return compile_mac(d, NARROW, *flags)[0]
    # int 16: the pass changes the code; int 32: the code is the same
    assert mac_of("a", ) != mac_of("b", "--no-narrow-index")
    assert (mac_of("c", "--int", "32")
            == mac_of("d", "--int", "32", "--no-narrow-index"))


MULTI_A = r'''#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
extern void fatal(int code);
extern uint16_t scan(uint16_t n);
int main(void){ printf("%u\n", scan(300)); fatal(4); printf("no\n"); return 0; }
'''
MULTI_B = r'''#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
[[noreturn]] void fatal(int code) { printf("fatal %d\n", code); exit(code); }
static uint8_t cnt;
uint16_t scan(uint16_t n){
  uint16_t i, a = 1, b = 2, c = 3;
  for (i = 0; i < n; i++) { a += i; b ^= a; c += b; cnt += (uint8_t)(n + 3); }
  return a + b + c + cnt; }
'''
MULTI_B_SEM = MULTI_B + "\nint e = 3;\nstatic int q = e;\n"


def test_noreturn_multi_file_build(tmp_path):
    b = tmp_path / "b.c"
    b.write_text(MULTI_B)
    out = build_run(tmp_path, MULTI_A, files=[b])
    host_dir = tmp_path / "hh"
    host_dir.mkdir()
    (host_dir / "a.c").write_text(MULTI_A)
    (host_dir / "b.c").write_text(MULTI_B)
    exe = host_dir / "h"
    subprocess.run([HOST_CC, "-w", "-std=c2x", "-o", str(exe), str(host_dir / "a.c"),
                    str(host_dir / "b.c")], check=True, capture_output=True,
                   text=True, timeout=120)
    want = subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=60).stdout
    assert out.split() == want.split()


def test_semcheck_error_in_second_file_is_reported_with_noreturn(tmp_path):
    b = tmp_path / "b.c"
    b.write_text(MULTI_B_SEM)
    a = tmp_path / "a.c"
    a.write_text(MULTI_A)
    import sys
    from tests.test_struct_return_abi import REPO
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(a), str(b),
                        "-o", str(tmp_path / "t.mac")], capture_output=True,
                       text=True, cwd=str(REPO), timeout=120)
    assert r.returncode != 0
    assert "b.c:" in r.stderr


# ---- loop passes with volatile accesses and an interrupt handler ----------

from tests.test_famh_rom_integration import same, libs  # noqa: E402,F401
from tests.rom_flows import FLOWS  # noqa: E402

ISR = """
#include <stdint.h>
volatile uint8_t n;
volatile uint8_t log[8];
static volatile uint8_t *const port = (volatile uint8_t *)0x4000;
struct B { unsigned a:4; unsigned b:4; } bf;
uint8_t ram[16];
#pragma uc80 interrupt isr
void isr(void)
{
    uint8_t i;
    int k = n * 3;
    for (i = 0; i < 4; i++) {
        *port = (uint8_t)(k + 1);
        bf.a += (unsigned)(k & 3);
        log[i] = (uint8_t)(n + k);
    }
    n++;
}
int main(void)
{
    int i, j = 5;
    __asm__("\\tei");
    for (i = 0; i < 12; i++) { ram[i] = (uint8_t)(j * 7 + i); *port = (uint8_t)(j * 7); }
    while (n < 5) { }
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_loop_passes_keep_volatile_accesses_in_a_handler(flow, tmp_path, libs):
    new = None
    from tests.test_famh_rom_integration import trace
    a = trace(tmp_path, "new", ISR, flow, libs, irq=7001)
    b = trace(tmp_path, "old", ISR, flow, libs, extra=LOOPS_OFF, irq=7001)
    wa = [x for x in a[0] if x[0] == 0x4000]
    wb = [x for x in b[0] if x[0] == 0x4000]
    # The interrupt lands in another place of the main loop when the code
    # of the two builds has another speed.  main writes 35, the handler
    # writes k + 1 four times for each interrupt.  The two builds must
    # give the same handler writes and the same number of main writes.
    ha = [x for x in wa if x[1] != 35][:16]
    hb = [x for x in wb if x[1] != 35][:16]
    assert ha == hb and len(ha) == 16
    assert len([x for x in wa if x[1] == 35]) == len(
        [x for x in wb if x[1] == 35]) == 12
    assert a[1][:16] == b[1][:16]
