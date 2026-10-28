"""Repair tests for stage B (src/uc80/ptrabs.py): a local that starts from a
read through a constant pointer, an access outside the address space, and
the pass under several option sets.  Each program runs in romrun in both
flows.  The read order is checked with a breakpoint on each read."""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import FLOWS, build_flow, machine, need_flow
from .test_direct_pointers import run_traced, HW_LO, HW_HI


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


HEAD = """
#include <stdint.h>
struct chip { volatile uint8_t status; volatile uint8_t data;
              volatile uint16_t w; volatile uint8_t a[2]; };
struct two { volatile uint8_t x; volatile uint8_t y; };
static struct chip *const ch = (struct chip *)0x4100;
static volatile uint8_t *const p = (volatile uint8_t *)0x4010;
static volatile uint8_t *q;
volatile uint8_t out[8];
"""

INIT = HEAD + """
int main(void)
{
    q = (volatile uint8_t *)0x4020;
    uint8_t s = ch->status;          /* 4100 */
    uint8_t a = *p;                  /* 4010 */
    uint8_t b = p[1];                /* 4011 */
    uint8_t t[2] = { *p, p[1] };     /* 4010 4011 */
    struct two st = { *q, q[1] };    /* 4020 4021 */
    uint16_t w = ch->w;              /* 4102 */
    for (uint8_t i = *p; i < 3; i++) /* 4010 */
        out[7] = i;
    out[0] = s; out[1] = a; out[2] = b; out[3] = t[0] + t[1];
    out[4] = st.x + st.y; out[5] = (uint8_t)w;
    ch->data = s;
    return 0;
}
"""
INIT_SCRIPT = {0x4100: [1], 0x4010: [2, 3, 4, 5], 0x4011: [10, 20],
               0x4020: [6], 0x4021: [7], 0x4102: [0x105]}
INIT_READS = [("R", 0x4100, 1), ("R", 0x4010, 1), ("R", 0x4011, 1),
              ("R", 0x4010, 1), ("R", 0x4011, 1),
              ("R", 0x4020, 1), ("R", 0x4021, 1), ("R", 0x4102, 2),
              ("R", 0x4010, 1)]

VARIANTS = [(), ("-O0",), ("--no-inlining",), ("--no-register-locals",),
            ("--isr-safe",)]


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("extra", VARIANTS, ids=lambda e: "".join(e) or "def")
def test_a_local_starting_from_a_constant_pointer_links_and_reads_in_order(
        flow, extra, tmp_path, libs):
    paths, _e = build_flow(tmp_path, {"t.c": INIT}, flow, libs, extra=extra)
    m, ev = run_traced(paths, INIT_SCRIPT)
    reads = [e for e in ev if e[0] == "R" and HW_LO <= e[1] < HW_HI]
    assert reads == INIT_READS
    assert list(m.peek("_out", 6)) == [1, 2, 10, 3 + 20, 13, 5]
    assert [e for e in ev if e[0] == "W"] == [("W", 0x4101, 1)]


def test_the_init_program_has_no_pointer_left(tmp_path):
    need_flow("um80")
    build_flow(tmp_path, {"t.c": INIT}, "um80")
    text = (tmp_path / "t.mac").read_text()
    for n in ("_ch", "_p", "_q"):
        assert "(%s)" % n not in text and "EXTRN\t%s\n" % n not in text
        assert "HL,%s\n" % n not in text


EDGE = """
#include <stdint.h>
static volatile uint16_t *const w = (volatile uint16_t *)0xFFFF;
static volatile uint8_t *const p = (volatile uint8_t *)0x0002;
static volatile uint8_t *const r = (volatile uint8_t *)0x4000;
volatile uint8_t out[4];
int main(void)
{
    out[0] = (uint8_t)*w;
    out[1] = p[-3];
    out[2] = r[1];
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_an_access_outside_the_address_space_still_links(flow, tmp_path,
                                                         libs):
    res = {}
    for tag, extra in (("new", ()), ("old", ("--no-direct-pointers",))):
        d = tmp_path / tag
        d.mkdir()
        paths, _e = build_flow(d, {"t.c": EDGE}, flow, libs, extra=extra)
        m = machine(paths)
        m.cpu.mem[0x4001] = 0x77
        m.run(max_tstates=3_000_000)
        res[tag] = list(m.peek("_out", 3))
    assert res["new"] == res["old"]
    assert res["new"][2] == 0x77


COMPOUND = HEAD + """
int main(void)
{
    ch->w += 0x101;
    ch->w++;
    out[0] = ch->status;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_a_compound_write_to_a_16_bit_register_ends_with_the_value(
        flow, tmp_path, libs):
    paths, _e = build_flow(tmp_path, {"t.c": COMPOUND}, flow, libs)
    # the register returns what the test says, as a device would
    m, ev = run_traced(paths, {0x4102: [0x2010, 0x2111]})
    last = {}
    for (_t, a, v, _pc) in m.rom_writes:
        last[a] = v
    assert last[0x4102] | (last[0x4103] << 8) == 0x2112
