"""Family J fill idiom against the device rules of bug rounds 4 and 5.

A loop that stores a constant to consecutive bytes of volatile memory or of
a constant address must stay a store loop.  It must not become an LDIR
fill, because LDIR reads the device.  The ROM runner area from 4000h up
ignores writes and reads back FFh: an LDIR fill writes FFh after the first
byte and the write log shows the error.
"""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import FLOWS, build_flow, machine, run, uc80_cmd

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS or __import__("shutil").which("um80")),
    reason="needs um80 and ul80, or sdas")


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


# name, statement, expected writes as (start, count, value)
CASES = [
    ("const_index",
     "for (i = 0; i < 40; i++) ((volatile unsigned char *) 0x4300)[i] = 0x5A;",
     [(0x4300, 40, 0x5A)]),
    ("const_index_le",
     "for (i = 0; i <= 39; i++) ((unsigned char *) 0x4400)[i] = 0x6B;",
     [(0x4400, 40, 0x6B)]),
    ("volatile_ptr_index",
     "for (i = 0; i < 40; i++) vp[i] = 0x11;", [(0x4800, 40, 0x11)]),
    ("volatile_ptr_walk",
     "{ volatile unsigned char *q = vp; for (i = 0; i < 40; i++) *q++ = 0x12; }",
     [(0x4800, 40, 0x12)]),
    ("const_struct_member",
     "for (i = 0; i < 16; i++) ((struct dev *) 0x4D00)->buf[i] = 0x13;",
     [(0x4D02, 16, 0x13)]),
    ("volatile_struct_member",
     "for (i = 0; i < 16; i++) vsd->buf[i] = 0x14;", [(0x5701, 16, 0x14)]),
    ("while_const",
     "i = 0; while (i < 40) { ((unsigned char *) 0x4E00)[i] = 0x15; i++; }",
     [(0x4E00, 40, 0x15)]),
    ("big_const", "for (i = 0; i < 300; i++) ((unsigned char *) 0x5100)[i] = 0x16;",
     [(0x5100, 300, 0x16)]),
]

SWITCHES = [(), ("--no-fill-idiom",), ("-O0",), ("--no-loop-rotate",),
            ("--no-licm", "--no-licm-load", "--no-byte-regs"),
            ("--int=32",)]


def source(stmt):
    return f"""
struct dev {{ unsigned char ctl, st, buf[16]; }};
volatile unsigned char *vp = (volatile unsigned char *) 0x4800;
struct dev2 {{ unsigned char ctl; unsigned char buf[16]; }};
volatile struct dev2 *vsd = (volatile struct dev2 *) 0x5700;
volatile unsigned char sink;
int main(void)
{{
    int i;
    {stmt}
    sink = (unsigned char) i;
    return 0;
}}
"""


@pytest.mark.parametrize("sw", SWITCHES, ids=lambda s: "+".join(s) or "default")
@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("name,stmt,want", CASES, ids=[c[0] for c in CASES])
def test_a_device_store_loop_writes_each_byte_once(
        name, stmt, want, flow, sw, tmp_path, libs):
    paths, _e = build_flow(tmp_path, {"t.c": source(stmt)}, flow, libs,
                           extra=sw)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    got = [(a, v) for (_t, a, v, _pc) in m.rom_writes]
    assert got == [(a + i, v) for (a, n, v) in want for i in range(n)]


def test_no_ldir_in_the_device_loops(tmp_path):
    for name, stmt, _w in CASES:
        d = tmp_path / name
        d.mkdir()
        (d / "t.c").write_text(source(stmt))
        run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac"), d)
        body = (d / "t.mac").read_text().lower().split("\n_main:")[1]
        assert "ldir" not in body, name


@pytest.mark.parametrize("flow", FLOWS)
def test_a_volatile_ram_array_loop_is_not_a_fill(flow, tmp_path, libs):
    src = """
volatile unsigned char vbuf[200];
unsigned char buf[200];
volatile unsigned sink;
int main(void)
{
    int i;
    for (i = 0; i < 200; i++) vbuf[i] = 0x21;
    for (i = 0; i < 200; i++) buf[i] = 0x22;
    sink = vbuf[199] + buf[199] + i;
    return 0;
}
"""
    paths, _e = build_flow(tmp_path, {"t.c": src}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    assert all(b == 0x21 for b in m.peek("_vbuf", 200))
    assert all(b == 0x22 for b in m.peek("_buf", 200))
