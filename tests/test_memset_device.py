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
    ("const_plus", "memset((unsigned char *) 0x4B00 + 2, 0x41, 9);",
     [(0x4B02, 9, 0x41)]),
    ("const_plus_macro_style", "memset((void *) (REG_BASE + 0x10), 0x42, 8);",
     [(0x4C10, 8, 0x42)]),
    ("const_minus", "memset((unsigned char *) 0x4B20 - 4, 0x43, 7);",
     [(0x4B1C, 7, 0x43)]),
    ("const_int_plus_ptr", "memset(3 + (unsigned char *) 0x4B30, 0x44, 7);",
     [(0x4B33, 7, 0x44)]),
    ("const_member_addr", "memset(&((struct dev *) 0x4D00)->buf[2], 0x45, 9);",
     [(0x4D04, 9, 0x45)]),
    ("const_array_member", "memset(((struct dev *) 0x4D40)->buf, 0x46, 10);",
     [(0x4D42, 10, 0x46)]),
    ("const_element_addr", "memset(&((unsigned char *) 0x4D80)[5], 0x47, 8);",
     [(0x4D85, 8, 0x47)]),
    # a constant base with an offset that is not constant, and a volatile
    # pointee before the conversion to void *
    ("const_var_offset",
     "memset((unsigned char *) 0x4F00 + (unsigned char) (c * 3), c, c + 1);",
     [(0x4F99, 52, 0x33)]),
    ("const_var_offset_macro", "memset((void *) (REG_BASE + i * 16 + j), v, n);",
     [(0x4C23, 33, 0x3C)]),
    ("const_var_offset_left", "memset(i * 2 + (unsigned char *) 0x5100, v, 8);",
     [(0x5104, 8, 0x3C)]),
    ("const_var_index", "memset(&((unsigned char *) 0x5200)[c], 0x49, 7);",
     [(0x5233, 7, 0x49)]),
    ("const_member_var_index",
     "memset(&((struct dev *) 0x5300)->buf[i], v, 4);",
     [(0x5304, 4, 0x3C)]),
    ("volatile_void_cast", "memset((void *) (dev + i * 16 + j), v, n);",
     [(0x5023, 33, 0x3C)]),
    ("volatile_void_cast_small", "memset((void *) (dev + j), v, 3);",
     [(0x5003, 3, 0x3C)]),
    ("volatile_void_cast_var_n", "memset((void *) (dev + i), v, c + 1);",
     [(0x5002, 52, 0x3C)]),
    ("volatile_void_cast_plain", "memset((void *) vp, 1, 20);",
     [(0x4800, 20, 1)]),
    ("volatile_offset", "memset(vq + i * 16 + j, v, 12);",
     [(0x4923, 12, 0x3C)]),
    ("const_plus_var_n", "memset((unsigned char *) 0x4E00 + 1, 0x48, n);",
     [(0x4E01, 33, 0x48)]),
    # a member or element of a volatile structure is volatile
    ("vstruct_arrow_index", "memset((void *) &vsd->buf[i], v, 5);",
     [(0x5703, 5, 0x3C)]),
    ("vstruct_array_member", "memset((void *) vsd->buf, v, 5);",
     [(0x5701, 5, 0x3C)]),
    ("vstruct_array_plus_var", "memset((void *) (vsd->buf + c), v, 5);",
     [(0x5734, 5, 0x3C)]),
    ("vstruct_nested", "memset((void *) &vsd->in.q[i], v, 3);",
     [(0x5715, 3, 0x3C)]),
    ("cond_of_constants",
     "memset(sel ? (unsigned char *) 0x4F00 : (unsigned char *) 0x4F80, v, 2);",
     [(0x4F00, 2, 0x3C)]),
    ("cond_in_void_cast",
     "memset((void *) (sel ? (unsigned char *) 0x4F40 : dev), v, 2);",
     [(0x4F40, 2, 0x3C)]),
    # a cast to another pointer type is looked through
    ("volatile_char_cast", "memset((unsigned char *) dev + i, v, 2);",
     [(0x5002, 2, 0x3C)]),
]


def source(stmt):
    return f"""
#include <string.h>
#define REG_BASE 0x4C00
struct dev {{ unsigned char ctl, st, buf[16]; }};
volatile unsigned char *vp = (volatile unsigned char *) 0x4800;
volatile unsigned char *vq = (volatile unsigned char *) 0x4900;
volatile unsigned char *dev = (volatile unsigned char *) 0x5000;
struct dev2 {{ unsigned char ctl; unsigned char buf[16];
    struct {{ unsigned char p[2]; unsigned char q[8]; }} in; }};
volatile struct dev2 *vsd = (volatile struct dev2 *) 0x5700;
volatile int sel = 1;
volatile void *r;
volatile unsigned char sink;
int main(void)
{{
    int c = 0x33;
    unsigned n = 33;
    int i = 2, j = 3;
    unsigned char v = 0x3C;
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


RAM_CASES = [
    ("ram_var_offset", "memset(ram + c, 9, 20);"),
    ("ram_void_cast", "memset((void *) (ram + c), 9, 20);"),
    ("ram_ptr_var_offset", "memset((void *) (rp + i * 4 + j), v, 40);"),
    ("ram_const_size_var_value", "memset(ram + i, v, 30);"),
]


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("name,stmt", RAM_CASES, ids=[c[0] for c in RAM_CASES])
def test_a_ram_fill_with_a_variable_offset_keeps_ldir(
        name, stmt, flow, tmp_path, libs):
    """No constant address and no volatile pointee: a RAM fill.  The fill
    runs (the RAM bytes are right) and the code has an LDIR."""
    src = """
#include <string.h>
unsigned char ram[128];
unsigned char *rp = ram;
volatile unsigned char sink;
int main(void)
{
    int c = 5, i = 2, j = 3;
    unsigned char v = 0x3C;
    %s
    sink = ram[0];
    return 0;
}
""" % stmt
    paths, _err = build_flow(tmp_path, {"t.c": src}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    assert m.rom_writes == []
    start = {"ram_var_offset": 5, "ram_void_cast": 5,
             "ram_ptr_var_offset": 11, "ram_const_size_var_value": 2}[name]
    cnt = {"ram_var_offset": 20, "ram_void_cast": 20,
           "ram_ptr_var_offset": 40, "ram_const_size_var_value": 30}[name]
    val = 9 if name in ("ram_var_offset", "ram_void_cast") else 0x3C
    got = m.peek("_ram", 128)
    assert all(b == val for b in got[start:start + cnt])
    from .rom_flows import run, uc80_cmd
    run(uc80_cmd("--target=rom", "t.c", "-o", "t2.mac"), tmp_path)
    text = (tmp_path / "t2.mac").read_text().lower()
    assert "ldir" in text and "memset_dev" not in text


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


NO_BLOCKOPS = CASES[:11:5] + [c for c in CASES if c[0] in (
    "const_var_offset", "const_var_offset_macro", "volatile_void_cast",
    "volatile_void_cast_var_n", "volatile_offset", "const_var_index",
    "vstruct_arrow_index", "vstruct_nested", "cond_of_constants",
    "volatile_char_cast")]


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("name,stmt,want", NO_BLOCKOPS,
                         ids=[c[0] for c in NO_BLOCKOPS])
def test_no_blockops_still_fills_a_device_with_stores(
        name, stmt, want, flow, tmp_path, libs):
    """--no-blockops keeps the library calls for RAM, but a fill of device
    memory must not read the destination: the call goes to __memset_dev."""
    got, _d = run_case(tmp_path, stmt, flow, libs, extra=("--no-blockops",))
    exp = [(a + i, v) for (a, n, v) in want for i in range(n)]
    assert got == exp
