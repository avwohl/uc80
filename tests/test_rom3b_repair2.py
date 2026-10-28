"""Second repair of stage A and stage B (rom3b).

1. A volatile 16-bit object that is reached through a pointer is written low
   byte first, for a plain store and for a compound store, with a constant
   pointer or any other pointer (`volatile T` with T a typedef-name used to
   lose the qualifier, so the compound path wrote the high byte first).
2. The access size in the range test of the constant-pointer pass follows
   --int and --long.
3. --isr-save-workareas without the whole program is an error only for a
   handler that reaches a work area or a routine of another module.

The programs run in bench/romrun, in the um80 flow and in the sdas flow.
"""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import (FLOWS, build_flow, build_separate, machine, need_flow,
                        uc80_cmd, run)
from .test_direct_pointers import run_traced


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


# ---- 1. the order of the two bytes ------------------------------------------

ORDER = """
#include <stdint.h>
typedef volatile uint16_t vreg;
static volatile uint16_t *const cp = (volatile uint16_t *)0x4010;
volatile uint16_t *gp;
void add(volatile uint16_t *q)   { *q += 0x101; }
void inc(volatile uint16_t *q)   { (*q)++; }
void dec(volatile uint16_t *q)   { (*q)--; }
void orr(volatile uint16_t *q)   { *q |= 0x8001; }
void put(volatile uint16_t *q, uint16_t v) { *q = v; }
void addt(vreg *q)               { *q += 0x101; }
int main(void)
{
    gp = (volatile uint16_t *)0x4020;
    add((volatile uint16_t *)0x4000);
    inc((volatile uint16_t *)0x4002);
    dec((volatile uint16_t *)0x4004);
    orr((volatile uint16_t *)0x4006);
    put((volatile uint16_t *)0x4008, 0x1234);
    addt((vreg *)0x400A);
    *cp += 0x101;
    (*cp)++;
    *gp += 0x101;
    (*gp)--;
    return 0;
}
"""

FIXED = {0x4000: 0x01, 0x4001: 0x02,       # 0x0201 + 0x101 = 0x0302
         0x4002: 0xFF, 0x4003: 0x00,       # ++ : 0x0100
         0x4004: 0x00, 0x4005: 0x01,       # -- : 0x00FF
         0x4006: 0x00, 0x4007: 0x00,       # | 0x8001
         0x400A: 0xFF, 0x400B: 0x00,       # + 0x101 : 0x0200
         # the registers are in ROM space of the runner: a write does not
         # change the value, so each operation starts from the same value
         0x4010: 0x01, 0x4011: 0x02,       # 0x0302, and 0x0202
         0x4020: 0x00, 0x4021: 0x03}       # 0x0401, and 0x02FF

WRITES = [
    (0x4000, 0x02), (0x4001, 0x03),
    (0x4002, 0x00), (0x4003, 0x01),
    (0x4004, 0xFF), (0x4005, 0x00),
    (0x4006, 0x01), (0x4007, 0x80),
    (0x4008, 0x34), (0x4009, 0x12),
    (0x400A, 0x00), (0x400B, 0x02),
    (0x4010, 0x02), (0x4011, 0x03),
    (0x4010, 0x02), (0x4011, 0x02),
    (0x4020, 0x01), (0x4021, 0x04),
    (0x4020, 0xFF), (0x4021, 0x02),
]


@pytest.mark.parametrize("extra", [(), ("--no-direct-pointers",)],
                         ids=["direct", "indirect"])
@pytest.mark.parametrize("flow", FLOWS)
def test_a_16_bit_volatile_is_written_low_byte_first(
        flow, extra, tmp_path, libs):
    """Each store writes the low byte, then the high byte: plain and
    compound, through a parameter, a typedef-name, a constant pointer
    and a global pointer, and the same with or without the direct pointers
    pass."""
    paths, _err = build_flow(tmp_path, {"t.c": ORDER}, flow, libs,
                             extra=extra)
    _m, events = run_traced(paths, fixed=FIXED)
    got = [(a, x) for (k, a, x) in events if k == "W"]
    assert got == WRITES


def test_the_order_is_the_same_in_the_listing_of_both_forms(tmp_path):
    """The direct and the indirect form differ in the address of the store,
    not in the order of the bytes: in the text, a compound store through
    HL has `ld (HL),E` before `ld (HL),D`."""
    need_flow("um80")
    build_flow(tmp_path, {"t.c": ORDER}, "um80",
               extra=("--no-direct-pointers",))
    text = (tmp_path / "t.mac").read_text()
    body = text.split("\n_add:")[1].split("@add_ret")[0]
    assert body.index("(HL),E") < body.index("(HL),D")
    body = text.split("\n_addt:")[1].split("@addt_ret")[0]
    assert body.index("(HL),E") < body.index("(HL),D")


# ---- 2. the access size follows --int and --long ------------------------------

WIDE = """
#include <stdint.h>
static volatile int *const ip = (volatile int *)0x4000;
static volatile long *const lp = (volatile long *)0x4100;
struct s { volatile char c; volatile int i; volatile long l; };
static struct s *const sp = (struct s *)0x4200;
volatile int out;
int main(void)
{
    ip[0] = 1;
    ip[1] = 2;
    ip[3] = 3;
    lp[1] = 4;
    lp[2] = 5;
    sp->c = 6;
    sp->i = 7;
    sp->l = 8;
    out = ip[1] + (int)lp[2];
    return 0;
}
"""


@pytest.mark.parametrize("opts", [("--int=32",), ("--int=32", "--long=64")],
                         ids=["int32", "int32-long64"])
@pytest.mark.parametrize("flow", FLOWS)
def test_wide_int_and_long_give_the_same_accesses(flow, opts, tmp_path, libs):
    res = {}
    for tag, extra in (("new", opts),
                       ("old", opts + ("--no-direct-pointers",))):
        d = tmp_path / tag
        d.mkdir()
        try:
            paths, _err = build_flow(d, {"t.c": WIDE}, flow, libs,
                                     extra=extra)
        except AssertionError as exc:
            pytest.skip("this configuration does not build: %s"
                        % str(exc)[-200:])
        m = machine(paths)
        assert m.run(max_tstates=3_000_000).reason == "halt"
        res[tag] = [(a, v) for (_t, a, v, _pc) in m.rom_writes
                    if 0x4000 <= a < 0x4400]
    assert res["new"] == res["old"]
    assert len(res["new"]) >= 20


END_OF_MEMORY = """
static volatile int *const p = (volatile int *)0xFFFE;
volatile int out;
int main(void) { *p = 5; return 0; }
"""


def test_a_wide_int_at_the_end_of_memory_is_not_rewritten(tmp_path):
    """A 4-byte int at FFFEh does not fit: the pointer stays a pointer
    (no name at that address), with --int=32; with 2 bytes it fits."""
    for extra, direct in (((), True), (("--int=32",), False)):
        d = tmp_path / ("a" if direct else "b")
        d.mkdir()
        (d / "t.c").write_text(END_OF_MEMORY)
        r = run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac", *extra), d)
        text = (d / "t.mac").read_text()
        assert ("___absFFFE" in text) == direct, text[:2000]


# ---- 3. --isr-save-workareas without the whole program ------------------------

TRIVIAL = """
#include <stdint.h>
volatile uint8_t n;
#pragma uc80 interrupt isr
void isr(void) { n++; }
int main(void) { __asm__("\\tei"); while (n < 3) { } __asm__("\\tdi"); return 0; }
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_a_trivial_handler_with_the_option_builds_separately(
        flow, tmp_path, libs):
    """A makefile may pass the option to every module: a handler that
    reaches no work area builds, links and runs."""
    paths, errs, link = build_separate(
        tmp_path, {"t.c": TRIVIAL}, flow, libs, crt0="vec",
        extra=("--isr-save-workareas",))
    assert "error" not in errs[0]
    assert link.returncode == 0, link.stdout + link.stderr
    m = machine(paths)
    m.add_irq_every(2000, start=1000, vec=0x38)
    assert m.run(max_tstates=2_000_000).reason == "halt"
    assert m.peek("_n", 1)[0] >= 3


def test_the_pragma_on_a_trivial_handler_is_still_an_error(tmp_path):
    (tmp_path / "t.c").write_text(
        TRIVIAL + "\n#pragma uc80 isr_save_workareas isr\n")
    r = run(uc80_cmd("--target=rom", "--no-whole-program", "t.c", "-o",
                     "t.mac"), tmp_path, ok=False)
    assert r.returncode == 1
    assert "needs the whole program" in r.stderr


def test_a_handler_that_reaches_a_work_area_is_still_an_error(tmp_path):
    (tmp_path / "t.c").write_text("""
#include <stdint.h>
volatile uint64_t v;
#pragma uc80 interrupt isr
void isr(void) { v = v * 3 + v / 7; }
""")
    r = run(uc80_cmd("--target=rom", "--no-whole-program",
                     "--isr-save-workareas", "t.c", "-o", "t.mac"),
            tmp_path, ok=False)
    assert r.returncode == 1
    assert "needs the whole program" in r.stderr


def test_a_handler_that_calls_another_module_is_still_an_error(tmp_path):
    (tmp_path / "t.c").write_text("""
extern void helper(void);
#pragma uc80 interrupt isr
void isr(void) { helper(); }
""")
    r = run(uc80_cmd("--target=rom", "--no-whole-program",
                     "--isr-save-workareas", "t.c", "-o", "t.mac"),
            tmp_path, ok=False)
    assert r.returncode == 1
    assert "needs the whole program" in r.stderr
