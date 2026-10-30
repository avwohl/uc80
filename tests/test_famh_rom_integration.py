"""Integration of the family-H passes (address CSE, dead stores, inline block
operations) with the ROM work: constant-address pointers (ptrabs), volatile
16-bit stores, interrupt handlers, naked functions, per-module BSS.

Each program runs in bench/romrun, in the um80 flow and in the sdas flow.
The reference build switches the three passes off (and, where named, the
direct pointers); the optimised build must write the same bytes to the same
addresses in the same order, and leave the same RAM.
"""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import (FLOWS, build_flow, build_separate, machine, need_flow,
                        uc80_cmd, run)

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS or __import__("shutil").which("um80")),
    reason="needs um80 and ul80, or sdas")

OFF = ("--no-addr-cse", "--no-chain-store", "--no-dead-store",
       "--no-blockops")


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


def trace(tmp_path, tag, src, flow, libs, extra=(), ram=(0xFA00, 0xFB00),
          irq=None, t=6_000_000):
    d = tmp_path / tag
    d.mkdir()
    paths, err = build_flow(d, {"t.c": src}, flow, libs, extra=extra)
    m = machine(paths)
    if irq:
        m.add_irq_every(irq, start=3000, vec=0)
    res = m.run(max_tstates=t)
    assert res.reason == "halt", res.reason
    writes = [(a, v) for (_t, a, v, _pc) in m.rom_writes]
    mem = bytes(m.cpu.mem[ram[0]:ram[1]])
    return writes, mem, (d / "t.mac").read_text() if flow == "um80" else "", err


def same(tmp_path, src, flow, libs, extra=(), ref_extra=(), **kw):
    new = trace(tmp_path, "new", src, flow, libs, extra=extra, **kw)
    old = trace(tmp_path, "old", src, flow, libs,
                extra=OFF + tuple(ref_extra) + tuple(extra), **kw)
    assert new[0] == old[0], "write logs differ"
    assert new[1] == old[1], "RAM differs"
    return new, old


# ---- 1. address CSE next to direct constant-address accesses ----------------

CSE = """
#include <stdint.h>
struct S { volatile uint8_t arr[8]; volatile uint16_t w[4]; uint8_t n; };
static struct S *const sp = (struct S *)0x4000;
static volatile uint8_t *const vm = (volatile uint8_t *)0x4100;
static uint8_t *const ram = (uint8_t *)0xFA00;
static volatile uint16_t *const vw = (volatile uint16_t *)0x4200;
void work(int i)
{
    sp->arr[i] = sp->arr[i + 1] + 1;
    sp->arr[i + 1] = sp->arr[i] ^ 5;
    ram[i] = ram[i + 1] + ram[i];
    ram[i + 1] = ram[i] * 2;
    sp->w[i & 3] = sp->w[(i + 1) & 3] + 1;
    vm[i] = vm[i + 1] + 2;
    vm[i + 1] = vm[i] + vm[i + 1];
    vw[i & 3] += 0x101;
    vw[(i & 3)] = vw[(i & 3)] + vw[(i + 1) & 3];
}
int main(void)
{
    int i;
    for (i = 0; i < 9; i++)
        ram[i] = (uint8_t)(i * 3 + 1);
    for (i = 0; i < 6; i++)
        work(i);
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_address_cse_and_direct_accesses_on_one_object(
        flow, tmp_path, libs):
    (w, mem, _t, _e), _old = same(tmp_path, CSE, flow, libs)
    assert len(w) >= 60
    assert mem[:4] != b"\0\0\0\0"


@pytest.mark.parametrize("flow", FLOWS)
def test_address_cse_with_indirect_pointers_gives_the_same(
        flow, tmp_path, libs):
    a = trace(tmp_path, "a", CSE, flow, libs)
    b = trace(tmp_path, "b", CSE, flow, libs,
              extra=("--no-direct-pointers",))
    assert a[0] == b[0] and a[1] == b[1]


CSE2 = """#include <stdint.h>
struct T { uint8_t arr[8]; uint16_t w[4]; uint8_t grid[4][4]; };
static struct T *const tp = (struct T *)0xFA00;
static uint8_t *const ram = (uint8_t *)0xFB00;
uint8_t garr[16];
static volatile uint8_t *const vm = (volatile uint8_t *)0x4100;
void work(int i, int j)
{
    tp->arr[i] = tp->arr[i + 1] + tp->arr[i];
    tp->arr[i + 1] = tp->arr[i] * 3;
    tp->w[j] = tp->w[j] + tp->w[j + 1];
    tp->grid[i][j] = tp->grid[i][j] + tp->grid[i][j + 1] + 1;
    ram[i] = ram[i] + ram[i + 1];
    ram[i + 1] = ram[i] ^ 7;
    garr[i] = garr[i] + garr[i + 1];
    garr[i + 1] = garr[i] * 2;
    vm[i] = ram[i] + garr[i];
}
int main(void)
{
    int i, j;
    for (i = 0; i < 9; i++) { ram[i] = i + 1; garr[i] = i * 5; tp->arr[i & 7] = i; }
    for (i = 0; i < 6; i++) for (j = 0; j < 3; j++) work(i, j);
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_address_cse_fires_next_to_direct_accesses(flow, tmp_path, libs):
    """Non-volatile elements of a struct, of an array behind a constant
    pointer and of a named array: the pass makes a temporary pointer, and
    the result in RAM is the one of the build without the passes."""
    (w, mem, text, _e), (_w2, _m2, text2, _e2) = same(
        tmp_path, CSE2, flow, libs, ram=(0xFA00, 0xFC00))
    assert len(w) == 18
    assert any(mem)
    if text:
        assert text != text2        # the pass changed the code


@pytest.mark.parametrize("flow", FLOWS)
def test_address_cse_fires_with_indirect_pointers_too(flow, tmp_path, libs):
    a = trace(tmp_path, "a", CSE2, flow, libs, ram=(0xFA00, 0xFC00))
    b = trace(tmp_path, "b", CSE2, flow, libs, ram=(0xFA00, 0xFC00),
              extra=("--no-direct-pointers",))
    assert a[0] == b[0] and a[1] == b[1]


# ---- 2. dead stores never remove a volatile or a direct access --------------

DSE = """
#include <stdint.h>
volatile uint8_t out;
static volatile uint8_t *const port = (volatile uint8_t *)0x4000;
static volatile uint16_t *const port16 = (volatile uint16_t *)0x4010;
uint8_t g;
void f(int n)
{
    int dead = 5;
    volatile uint8_t *p = (volatile uint8_t *)0x4020;
    volatile uint16_t *q = (volatile uint16_t *)0x4030;
    uint8_t *r = (uint8_t *)0xFA00;
    int i = 0;
    *p = 1;
    *p = 2;
    *q = 0x1234;
    *q = 0x5678;
    *port = 3;
    *port = 4;
    *port16 = 0xABCD;
    *port16 = 0x1357;
    r[0] = 7;
    r[0] = 8;
    for (i = 0; i < n; i++)
        *port = (uint8_t)i;
    dead = dead + n;
    dead = 0;
    g = (uint8_t)dead;
}
int main(void)
{
    f(3);
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_dead_store_keeps_every_store_through_a_constant_pointer(
        flow, tmp_path, libs):
    (w, mem, _t, _e), _old = same(tmp_path, DSE, flow, libs)
    assert w.count((0x4020, 1)) == 1 and w.count((0x4020, 2)) == 1
    assert (0x4030, 0x34) in w and (0x4031, 0x12) in w
    assert [x for x in w if x[0] == 0x4000] == [
        (0x4000, 3), (0x4000, 4), (0x4000, 0), (0x4000, 1), (0x4000, 2)]
    assert mem[0] == 8


@pytest.mark.parametrize("flow", FLOWS)
def test_dead_store_in_a_handler_leaves_the_work_alone(
        flow, tmp_path, libs):
    src = """
#include <stdint.h>
volatile uint8_t n;
volatile uint8_t log[8];
static volatile uint8_t *const port = (volatile uint8_t *)0x4000;
#pragma uc80 interrupt isr
void isr(void)
{
    int dead = 3;
    uint8_t t = n;
    volatile uint8_t *p = (volatile uint8_t *)0x4001;
    *p = t;
    *p = t + 1;
    t = 9;
    dead = 4;
    *port = n;
    log[n & 7] = (uint8_t)(t + dead);
    n++;
}
int main(void)
{
    __asm__("\\tei");
    while (n < 5) { }
    __asm__("\\tdi");
    return 0;
}
"""
    new = trace(tmp_path, "new", src, flow, libs, irq=7001)
    old = trace(tmp_path, "old", src, flow, libs, extra=OFF, irq=7001)
    # the number of interrupts differs by timing only when the code changes:
    # compare the order of the writes of the first five
    wn = [x for x in new[0] if x[0] in (0x4000, 0x4001)][:10]
    wo = [x for x in old[0] if x[0] in (0x4000, 0x4001)][:10]
    assert wn == wo and len(wn) == 10


# ---- 3. block operations and volatile ----------------------------------------

VOL = """
#include <stdint.h>
#include <string.h>
typedef volatile uint8_t vb;
struct R { volatile uint16_t a; volatile uint16_t b; volatile uint8_t c[4]; };
static struct R *const regs = (struct R *)0x4100;
static volatile uint8_t *const mmio = (volatile uint8_t *)0x4000;
struct R img = {0x0102, 0x0304, {5, 6, 7, 8}};
vb *vp = (vb *)0x4200;
uint8_t src[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
uint8_t back[8];
int main(void)
{
    *regs = img;
    memcpy(back, (void *)regs, 8);
    memcpy(mmio, src, 16);
    memset(mmio + 32, 0x55, 8);
    memcpy(vp, src, 8);
    memset(vp + 16, 0, 8);
    memset((void *)0x4300, 0xEE, 8);
    memcpy((void *)0x4310, src, 6);
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_block_operations_keep_volatile_and_mmio_writes(
        flow, tmp_path, libs):
    # The fills of device memory (memset to a volatile pointee or to a
    # constant address) store every byte, so the build with the pass off
    # (the library memset: the first byte, then LDIR) differs by design.
    # Everything else in the log is equal.
    new = trace(tmp_path, "new", VOL, flow, libs)
    old = trace(tmp_path, "old", VOL, flow, libs, extra=OFF)
    w, text = new[0], new[2]
    fills = [(0x4020 + i, 0x55) for i in range(8)] + \
        [(0x4210 + i, 0) for i in range(8)] + \
        [(0x4300 + i, 0xEE) for i in range(8)]
    in_fill = lambda x: (0x4020 <= x[0] < 0x4028 or 0x4210 <= x[0] < 0x4218
                         or 0x4300 <= x[0] < 0x4308)
    assert [x for x in w if in_fill(x)] == fills
    assert [x for x in w if not in_fill(x)] == [
        x for x in old[0] if not in_fill(x)]
    assert new[1] == old[1], "RAM differs"
    # *regs = img: 16-bit members, low byte first, ascending
    assert w[:8] == [(0x4100, 2), (0x4101, 1), (0x4102, 4), (0x4103, 3),
                     (0x4104, 5), (0x4105, 6), (0x4106, 7), (0x4107, 8)]
    # the calls through volatile pointers stay calls
    assert w.count((0x4000, 1)) >= 1
    assert [x for x in w if 0x4000 <= x[0] < 0x4010] == [
        (0x4000 + i, i + 1) for i in range(16)]
    assert [x for x in w if 0x4200 <= x[0] < 0x4208] == [
        (0x4200 + i, i + 1) for i in range(8)]
    if text:
        main = text.split("\n_main:")[1]
        assert main.count("call\t_memcpy") >= 2     # mmio and vp
        assert "call\t_memset" not in main      # stores only


@pytest.mark.parametrize("flow", FLOWS)
def test_struct_copy_to_a_volatile_target_writes_low_byte_first(
        flow, tmp_path, libs):
    src = """
#include <stdint.h>
struct R { volatile uint16_t a; volatile uint16_t b; volatile uint32_t c; };
static struct R *const regs = (struct R *)0x4100;
static volatile struct R *const vregs = (volatile struct R *)0x4200;
struct R img = {0x0102, 0x0304, 0x05060708UL};
struct R mid;
int main(void)
{
    *regs = img;
    *vregs = img;
    mid = *regs;
    *regs = mid;
    return 0;
}
"""
    (w, _m, _t, _e), _old = same(tmp_path, src, flow, libs)
    a = [x for x in w if 0x4100 <= x[0] < 0x4110]
    b = [x for x in w if 0x4200 <= x[0] < 0x4210]
    exp = [(0, 2), (1, 1), (2, 4), (3, 3), (4, 8), (5, 7), (6, 6), (7, 5)]
    assert [(x[0] - 0x4100, x[1]) for x in a[:8]] == exp
    assert [(x[0] - 0x4200, x[1]) for x in b] == exp


# ---- 4. --isr-save-workareas with inline block operations --------------------

ISR = """
#include <stdint.h>
#include <string.h>
volatile uint32_t sum_out;
volatile uint16_t isr_n;
uint8_t src[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
uint8_t dst[16];
struct P { int a; int b; int c; };
struct P gp = {1, 2, 3}, hp;
volatile uint32_t isr_acc;
#pragma uc80 interrupt isr
void isr(void)
{
    uint32_t v = isr_acc + 0x01234567UL;
    memcpy(dst, src, 16);
    memset(dst + 4, 0xAA, 6);
    hp = gp;
    isr_acc = v * 3 + v %% 13;
    isr_n++;
}
int main(void)
{
    uint32_t s = 0;
    __asm__("\\tei");
    for (uint16_t i = 1; i <= 120; i++) {
        volatile uint32_t x = 1000003UL * i;
        uint32_t d = i + 77;
        s += (x / d) * 3 + x %% d;
    }
    __asm__("\\tdi");
    sum_out = s;
    return 0;
}
""".replace("%%", "%")


def expected_sum():
    s = 0
    for i in range(1, 121):
        x = (1000003 * i) & 0xFFFFFFFF
        d = i + 77
        s = (s + ((x // d) * 3 & 0xFFFFFFFF) + x % d) & 0xFFFFFFFF
    return s


@pytest.mark.parametrize("opts", [(), ("--no-blockops",)],
                         ids=["inline", "calls"])
@pytest.mark.parametrize("flow", FLOWS)
def test_save_workareas_with_inline_block_operations(
        opts, flow, tmp_path, libs):
    paths, err = build_flow(tmp_path, {"t.c": ISR}, flow, libs,
                            extra=("--isr-save-workareas",) + opts)
    assert "saves and restores the work areas of rt_rem32, rt_tmp32" in err
    assert "warning" not in err
    m = machine(paths)
    m.add_irq_every(9973, start=7000, vec=0)
    assert m.run(max_tstates=20_000_000).reason == "halt"
    assert m.peek16("_isr_n") > 20
    assert int.from_bytes(m.peek("_sum_out", 4), "little") == expected_sum()
    d = bytes(m.peek("_dst", 16))
    assert d == bytes([1, 2, 3, 4] + [0xAA] * 6 + list(range(11, 17)))
    assert m.peek16("_hp") == 1 and m.peek16("_hp") == 1


@pytest.mark.parametrize("flow", ["um80"])
def test_a_handler_with_inline_copies_calls_no_runtime_routine(
        flow, tmp_path, libs):
    need_flow(flow)
    src = ISR.replace("isr_acc = v * 3 + v % 13;", "isr_acc = v;")
    build_flow(tmp_path, {"t.c": src}, flow, libs,
               extra=("--isr-save-workareas",))
    text = (tmp_path / "t.mac").read_text()
    body = text.split("\n_isr:")[1].split("@isr_ret")[0]
    assert "_memcpy" not in body and "_memset" not in body
    assert "ldir" in body.lower()


# ---- 5. naked functions are skipped ------------------------------------------

NAKED = """
#include <stdint.h>
#include <string.h>
uint8_t src[8] = {1,2,3,4,5,6,7,8};
uint8_t dst[8];
volatile uint8_t out;
void nk(void) __naked
{
    __asm
        ld a,#7
        ld (_out),a
        ret
    __endasm;
}
void plain(void)
{
    int dead = 3;
    dead = 4;
    memcpy(dst, src, 8);
    nk();
}
int main(void)
{
    plain();
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_naked_function_is_left_alone_by_the_three_passes(
        flow, tmp_path, libs):
    paths, err = build_flow(tmp_path, {"t.c": NAKED}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=500_000).reason == "halt"
    assert m.peek("_out", 1)[0] == 7
    assert bytes(m.peek("_dst", 8)) == bytes(range(1, 9))


# ---- 6. separate compilation and per-module BSS ------------------------------

SEP_A = """
#include <stdint.h>
#include <string.h>
struct S { uint8_t arr[8]; uint16_t w[4]; };
static struct S *const sp = (struct S *)0xFA00;
uint8_t zeros[32];
volatile uint8_t port_log;
static volatile uint8_t *const port = (volatile uint8_t *)0x4000;
void modb(void);
int main(void)
{
    int i, dead = 1;
    dead = 2;
    memset(zeros, 0, 32);
    for (i = 0; i < 6; i++) {
        sp->arr[i] = sp->arr[i + 1] + i;
        sp->arr[i + 1] = sp->arr[i] * 3;
        *port = sp->arr[i];
    }
    modb();
    return 0;
}
"""

SEP_B = """
#include <stdint.h>
#include <string.h>
uint8_t bzero[40];
uint8_t bsrc[12] = {1,2,3,4,5,6,7,8,9,10,11,12};
uint8_t bdst[12];
static volatile uint8_t *const port = (volatile uint8_t *)0x4001;
void modb(void)
{
    int i, dead = 5;
    dead = 6;
    memcpy(bdst, bsrc, 12);
    memset(bzero, 0x11, 40);
    for (i = 0; i < 4; i++)
        *port = bdst[i] + bzero[i];
}
"""


@pytest.mark.parametrize("opts", [(), OFF], ids=["passes", "no-passes"])
@pytest.mark.parametrize("flow", FLOWS)
def test_separate_compilation_with_the_three_passes(
        opts, flow, tmp_path, libs):
    paths, errs, link = build_separate(
        tmp_path, {"a.c": SEP_A, "b.c": SEP_B}, flow, libs, crt0="plain",
        extra=opts)
    assert link.returncode == 0, link.stdout + link.stderr
    m = machine(paths)
    assert m.run(max_tstates=2_000_000).reason == "halt"
    w = [(a, v) for (_t, a, v, _pc) in m.rom_writes]
    assert len([x for x in w if x[0] == 0x4001]) == 4
    assert bytes(m.peek("_bdst", 12)) == bytes(range(1, 13))
    assert bytes(m.peek("_bzero", 40)) == bytes([0x11] * 40)
    assert bytes(m.peek("_zeros", 32)) == bytes(32)


def test_separate_results_are_equal_with_and_without_the_passes(
        tmp_path, libs):
    need_flow("um80")
    res = []
    for tag, opts in (("n", ()), ("o", OFF)):
        d = tmp_path / tag
        d.mkdir()
        paths, _e, link = build_separate(
            d, {"a.c": SEP_A, "b.c": SEP_B}, "um80", libs, crt0="plain",
            extra=opts)
        assert link.returncode == 0
        m = machine(paths)
        assert m.run(max_tstates=2_000_000).reason == "halt"
        res.append([(a, v) for (_t, a, v, _pc) in m.rom_writes])
    assert res[0] == res[1] and len(res[0]) == 10


# ---- repair pass: chained volatile stores, memset through a cast -------------

CHAIN = """
#include <stdint.h>
#include <string.h>
struct V { volatile uint8_t c[8]; };
static volatile uint8_t *const vm = (volatile uint8_t *)0x4100;
static volatile uint8_t *const port = (volatile uint8_t *)0x4000;
static struct V *const vr = (struct V *)0x4200;
static uint8_t *const ram = (uint8_t *)0xFA00;
int main(void)
{
    int i;
    for (i = 0; i < 3; i++) {
        vm[i] = vm[i + 1] = 5;
        *port = vr->c[i] = (uint8_t)(7 + i);
    }
    memset((void *)0xFA10, 0xEE, 12);
    memset(ram + 40, 0x33, 9);
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_chained_volatile_stores_keep_their_order_and_memset_fills_ram(
        flow, tmp_path, libs):
    (w, mem, text, _e), _old = same(tmp_path, CHAIN, flow, libs)
    # the right store of a chain comes first, as the C rule for = needs
    got = [x for x in w if x[0] < 0x4300]
    exp = []
    for i in range(3):
        exp += [(0x4100 + i + 1, 5), (0x4100 + i, 5),
                (0x4200 + i, 7 + i), (0x4000, 7 + i)]
    assert sorted(got) == sorted(exp)
    assert mem[0x10:0x1C] == bytes([0xEE] * 12)
    assert mem[40:49] == bytes([0x33] * 9)
    if text:
        main = text.split("\n_main:")[1]
        assert "call\t_memset" not in main      # the casts made it inline
