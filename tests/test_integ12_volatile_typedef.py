"""Volatile through a typedef name or a struct type (integrate12 repair).

uc_core dropped the volatile qualifier of a cast or declaration type that
names a typedef or a struct.  The dead store pass, the empty-if pass and
the device memset then treated the access as plain RAM.  Found by a
verifier on integrate12: ``u8 x = *(volatile u8 *) 0x4300;`` lost the
read, and ``memset`` to a member array of a ``volatile S_t`` object used
LDIR.  Fixed in uc_core (resolved_to_legacy keeps volatile).
"""

import re

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import FLOWS, build_flow, machine, run, uc80_cmd

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS or __import__("shutil").which("um80")),
    reason="needs um80 and ul80, or sdas")


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


HDR = r'''
typedef unsigned char u8;
struct S1 { u8 ctl; u8 buf[40]; };
typedef struct S1 S1_t;
typedef struct { u8 ctl; u8 buf[40]; } S2_t;
typedef volatile struct S1 VS1;
typedef volatile S2_t VS2;
volatile struct S1 *p1 = (volatile struct S1 *) 0x5700;
volatile S1_t *p2 = (volatile S1_t *) 0x5800;
volatile S2_t *p3 = (volatile S2_t *) 0x5900;
VS1 *p4 = (VS1 *) 0x5A00;
VS2 *p5 = (VS2 *) 0x5B00;
volatile u8 *vb = (volatile u8 *) 0x5C00;
volatile u8 sink;
'''

MEMSETS = [
    ("struct", "memset((void *) p1->buf, 0x41, 20);", 0x5701, 0x41),
    ("typedef_struct", "memset((void *) p2->buf, 0x42, 20);", 0x5801, 0x42),
    ("anon_typedef", "memset((void *) p3->buf, 0x43, 20);", 0x5901, 0x43),
    ("typedef_volatile_struct", "memset((void *) p4->buf, 0x44, 20);",
     0x5A01, 0x44),
    ("typedef_volatile_typedef", "memset((void *) p5->buf, 0x45, 20);",
     0x5B01, 0x45),
    ("address_of_element", "memset((void *) &p3->buf[2], 0x46, 20);",
     0x5903, 0x46),
    ("plus", "memset((void *) (p3->buf + 3), 0x47, 20);", 0x5904, 0x47),
    ("typedef_byte_ptr", "memset((void *) (vb + 2), 0x49, 20);", 0x5C02,
     0x49),
]


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("name,stmt,base,val", MEMSETS,
                         ids=[c[0] for c in MEMSETS])
def test_memset_to_a_volatile_typedef_struct_writes_each_byte_once(
        name, stmt, base, val, flow, tmp_path, libs):
    src = ("#include <string.h>\n" + HDR
           + "int main(void)\n{\n    " + stmt + "\n    sink = 1;\n"
           "    return 0;\n}\n")
    paths, _e = build_flow(tmp_path, {"t.c": src}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    got = [(a, v) for (_t, a, v, _pc) in m.rom_writes]
    assert got == [(base + i, val) for i in range(20)]


# a read of a device through a volatile typedef: the load must stay
READS = [
    ("dead_local_init", "u8 x = *(volatile u8 *) 0x4300;"),
    ("dead_local_assign", "u8 x; x = *(volatile u8 *) 0x4300;"),
    ("dead_local_struct", "u8 x = ((volatile S2_t *) 0x4300)->ctl;"),
    ("empty_if", "if (*(volatile u8 *) 0x4300) ;"),
    ("empty_if_eq", "if ((*(volatile u8 *) 0x4300) == 3) ;"),
    ("empty_if_struct", "if (((volatile S2_t *) 0x4300)->ctl) ;"),
    ("empty_if_struct_tag", "if (((volatile struct S1 *) 0x4300)->ctl) ;"),
    ("empty_if_u16", "if (*(volatile unsigned short *) 0x4300) ;"),
    ("empty_if_ternary", "if (g ? *(volatile u8 *) 0x4300 : 0) ;"),
]


@pytest.mark.parametrize("opt", [(), ("-O0",)], ids=["O1", "O0"])
@pytest.mark.parametrize("name,stmt", READS, ids=[c[0] for c in READS])
def test_a_read_through_a_volatile_typedef_cast_stays(name, stmt, opt,
                                                      tmp_path):
    src = (HDR + "int g;\nvoid f(void) { " + stmt + " }\n"
           "int main(void) { f(); return 0; }\n")
    (tmp_path / "t.c").write_text(src)
    run(uc80_cmd("--target=rom", *opt, "t.c", "-o", "t.mac"), tmp_path)
    mac = (tmp_path / "t.mac").read_text().lower()
    body = mac.split("\n_f:")[1].split("\n_main:")[0]
    assert re.search(r"ld\s+\w+,\(\w+\)", body), body


# A volatile object declared through a typedef name must be read at each
# use.  The event lowers n while the loop runs; the program must stop.
# (This test passed before the repair too: it guards the loop pass.)
LOOP_SRC = """
typedef unsigned short u16;
volatile u16 n = 60000;
volatile unsigned char done;
int main(void)
{
    u16 i = 0;
    while (i < n)
        i++;
    done = 1;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_a_volatile_typedef_bound_is_read_in_each_pass(flow, tmp_path, libs):
    paths, _e = build_flow(tmp_path, {"t.c": LOOP_SRC}, flow, libs)
    m = machine(paths)
    addr = m.addr("n")

    def lower(mach, _t):
        mach.cpu.mem[addr] = 10
        mach.cpu.mem[addr + 1] = 0

    m.at(200_000, lower)
    assert m.run(max_tstates=900_000).reason == "halt"
    assert m.peek8("done") == 1


def test_a_byte_cast_of_a_volatile_typedef_word_reads_the_word(tmp_path):
    """``(unsigned char) vw`` with ``volatile u16 vw`` is a 16-bit access.
    The narrowing pass made it a one-byte read when the type came from a
    typedef name (bench program micro)."""
    src = ("typedef unsigned short u16;\nvolatile u16 vw;\n"
           "volatile unsigned char vb;\n"
           "int main(void) { vb = (unsigned char) vw; return 0; }\n")
    (tmp_path / "t.c").write_text(src)
    run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac"), tmp_path)
    body = (tmp_path / "t.mac").read_text().lower().split("\n_main:")[1]
    assert "ld\thl,(_vw)" in body, body
