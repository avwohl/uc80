"""memset to device memory never reads the destination (docs/blockops.md,
"Constant addresses, volatile and the work areas").

The library memset stores the first byte and copies it along with LDIR.
LDIR reads each byte it copies, so a device register that returns another
value on a read gets that value written to the next address.  When the
destination is volatile memory, or a constant address, the compiler emits
stores only: an unrolled run, a store loop, or a call of __memset_dev.

The programs run in bench/romrun, in the um80 flow and in the sdas flow.
The addresses 4000h and up are in the ROM area of the runner: a write there
is logged with its address and value and changes nothing, and a read returns
the byte of the image (FFh), as a device that ignores writes does.  So an
LDIR fill writes FFh after the first byte, and the log shows the error.
"""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import FLOWS, build_flow, machine

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS or __import__("shutil").which("um80")),
    reason="needs um80 and ul80, or sdas")


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


# name, C statement, the expected writes as (start, count, value)
CASES = [
    ("const_small", "memset((void *) 0x4300, 0x5A, 3);", [(0x4300, 3, 0x5A)]),
    ("const_mid", "memset((void *) 0x4310, 0x5A, 6);", [(0x4310, 6, 0x5A)]),
    ("const_loop", "memset((void *) 0x4320, 0x6B, 10);", [(0x4320, 10, 0x6B)]),
    ("const_zero", "memset((void *) 0x4340, 0, 40);", [(0x4340, 40, 0)]),
    ("const_var_value", "memset((void *) 0x4400, c, 300);",
     [(0x4400, 300, 0x33)]),
    ("const_256", "memset((void *) 0x4600, 0x77, 256);",
     [(0x4600, 256, 0x77)]),
    ("const_255", "memset((void *) 0x4700, 0x78, 255);",
     [(0x4700, 255, 0x78)]),
    ("volatile_ptr", "memset(vp, 1, 20);", [(0x4800, 20, 1)]),
    ("volatile_ptr_var_n", "memset(vq, c, n);", [(0x4900, 33, 0x33)]),
    ("const_var_n", "memset((void *) 0x4A00, 0x21, n);",
     [(0x4A00, 33, 0x21)]),
    ("result_used", "r = memset(vp, 7, 9);", [(0x4800, 9, 7)]),
]


def source(stmt):
    return f"""
#include <string.h>
volatile unsigned char *vp = (volatile unsigned char *) 0x4800;
volatile unsigned char *vq = (volatile unsigned char *) 0x4900;
volatile void *r;
volatile unsigned char sink;
int main(void)
{{
    int c = 0x33;
    unsigned n = 33;
    {stmt}
    sink = (unsigned char) (c + n);
    return 0;
}}
"""


def run_case(tmp_path, stmt, flow, libs, extra=()):
    paths, err = build_flow(tmp_path, {"t.c": source(stmt)}, flow, libs,
                            extra=extra)
    m = machine(paths)
    res = m.run(max_tstates=3_000_000)
    assert res.reason == "halt", res.reason
    return [(a, v) for (_t, a, v, _pc) in m.rom_writes], tmp_path


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("name,stmt,want", CASES,
                         ids=[c[0] for c in CASES])
def test_the_fill_writes_the_value_to_every_byte(
        name, stmt, want, flow, tmp_path, libs):
    got, _d = run_case(tmp_path, stmt, flow, libs)
    exp = [(a + i, v) for (a, n, v) in want for i in range(n)]
    assert got == exp


def test_the_ram_fill_keeps_ldir(tmp_path):
    """A fill of RAM is not a device: it stays an LDIR."""
    (tmp_path / "t.c").write_text("""
#include <string.h>
unsigned char ram[100];
volatile unsigned char sink;
int main(void) { memset(ram, 9, 100); sink = ram[99]; return 0; }
""")
    from .rom_flows import run, uc80_cmd
    run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac"), tmp_path)
    text = (tmp_path / "t.mac").read_text().lower()
    assert "ldir" in text
    assert "memset_dev" not in text


def test_the_device_fill_has_no_ldir(tmp_path):
    from .rom_flows import run, uc80_cmd
    for name, stmt, _want in CASES:
        d = tmp_path / name
        d.mkdir()
        (d / "t.c").write_text(source(stmt))
        run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac"), d)
        text = (d / "t.mac").read_text().lower()
        body = text.split("\n_main:")[1]
        assert "ldir" not in body and "_memset\n" not in body, name


def test_a_user_memset_keeps_its_calls(tmp_path):
    from .rom_flows import run, uc80_cmd
    (tmp_path / "t.c").write_text("""
#include <string.h>
void *memset(void *s, int c, size_t n) { return s; }
int main(void) { memset((void *) 0x4300, 1, 40); return 0; }
""")
    run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac"), tmp_path)
    text = (tmp_path / "t.mac").read_text().lower()
    assert "memset_dev" not in text


@pytest.mark.parametrize("flow", FLOWS)
def test_locals_in_registers_survive_the_store_loop(flow, tmp_path, libs):
    """The loop uses A, B, C and HL: a local that the register pass keeps in
    BC or DE around the fill must come back unchanged."""
    src = """
#include <string.h>
volatile unsigned char *dev = (volatile unsigned char *) 0x4C00;
volatile unsigned sum;
static unsigned work(unsigned a, unsigned b)
{
    unsigned t = 0;
    for (unsigned i = 0; i < 5; i++) {
        memset((void *) 0x4D00, (unsigned char) (a + i), 20 + i);
        memset(dev, (unsigned char) (b + i), 300);
        t += a * 3 + b * 5 + i;
    }
    return t;
}
int main(void) { sum = work(7, 11); return 0; }
"""
    paths, _err = build_flow(tmp_path, {"t.c": src}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    assert m.peek16("_sum") == sum(7 * 3 + 11 * 5 + i for i in range(5))
    exp = []
    for i in range(5):
        exp += [(0x4D00 + k, (7 + i) & 255) for k in range(20 + i)]
        exp += [(0x4C00 + k, (11 + i) & 255) for k in range(300)]
    assert [(a, v) for (_t, a, v, _pc) in m.rom_writes] == exp
