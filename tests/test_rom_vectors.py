"""RST vectors and IM 2 of the ROM target.  See docs/rom_hardware.md.

Each program is built with um80 and ul80, and again with
`--asm-syntax=sdas`, sdasz80 and sdldz80 (when the tools exist).  The
images run in bench/romrun with real interrupts: an IM 2 interrupt carries
the data bus byte, an IM 0 interrupt carries an RST opcode.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import HAVE_SDAS

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bench"))
sys.path.insert(0, str(REPO / "bench" / "rom"))

import sndrom  # noqa: E402
import uc80  # noqa: E402
from romrun import Machine  # noqa: E402

HAVE_UM80 = bool(shutil.which("um80") and shutil.which("ul80"))
LIB = uc80.lib_dir()
INC = str(LIB / "include")

pytestmark = pytest.mark.skipif(
    not (HAVE_UM80 or HAVE_SDAS), reason="needs um80 and ul80, or sdas")

FLOWS = ["um80", "sdas"]


def uc80_cmd(*args):
    return [sys.executable, "-m", "uc80.main", *args]


def build(tmp_path, source, flow, rom_libs=None, rom_size=0x8000):
    """Build t.c; return (bin, sym) paths."""
    if flow == "um80" and not HAVE_UM80:
        pytest.skip("needs um80 and ul80")
    if flow == "sdas" and not HAVE_SDAS:
        pytest.skip("needs the SDCC sdas tools")
    (tmp_path / "t.c").write_text(source)
    if flow == "sdas":
        from uc80 import sdas
        steps = [uc80_cmd("--target=rom", "--asm-syntax=sdas", "-I", INC,
                          "--rom-size", "%X" % rom_size, "t.c", "-o", "t.s"),
                 ["sdasz80", "-g", "-l", "-o", "t.s"]]
        for cmd in steps:
            _run(cmd, tmp_path)
        origin = sdas.code_origin_of((tmp_path / "t.s").read_text()) or 0x68
        _run(["sdldz80", *sndrom.sdas_link_flags(origin=origin), "-i", "-j",
              "t.ihx", "t.rel", "-l", str(rom_libs / "uc80_lc.lib"),
              "-l", str(rom_libs / "uc80_rt.lib")], tmp_path)
        _run(["sdobjcopy", "-I", "ihex", "-O", "binary", "t.ihx", "t.bin"],
             tmp_path)
        sndrom.noi_to_sym(tmp_path / "t.noi", tmp_path / "t.sym")
    else:
        _run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac"), tmp_path)
        _run(["um80", "t.mac", "-o", "t.rel"], tmp_path)
        _run(["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
              "--rom-size", "%X" % rom_size, "--ram-size", "800",
              "--min-stack", "40",
              "-o", "t.bin", "-S", "t.sym", "t.rel",
              str(LIB / "libc_rom.lib"), str(LIB / "runtime.lib")], tmp_path)
    return tmp_path / "t.bin", tmp_path / "t.sym"


def _run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, " ".join(map(str, cmd)) + "\n" + r.stdout + r.stderr
    return r


def machine(paths, rom_size=0x8000):
    m = Machine(rom_size=rom_size, banks=None, ram_fill="random",
                sp="poison", track=True)
    m.load_rom(str(paths[0]))
    m.load_symbols(str(paths[1]))
    return m


@pytest.fixture
def libs(request):
    """uc80_lc.lib and uc80_rt.lib for the sdas flow (built once), or None
    when the sdas tools are missing."""
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


IM2_PROGRAM = """
#include <stdint.h>
volatile uint8_t count_a, count_b, other;

#pragma uc80 im2 3 on_a
void on_a(void) { count_a++; }

#pragma uc80 im2 10 on_b critical
void on_b(void) { count_b += 10; }

int main(void)
{
    __asm__("\\tei");
    while (count_a < 4 || count_b < 60)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_two_handlers_count_their_own_interrupts(flow, tmp_path, libs):
    paths = build(tmp_path, IM2_PROGRAM, flow, libs)
    img = paths[0].read_bytes()
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    # reset: DI, IM 2
    assert img[0:3] == b"\xf3\xed\x5e"
    table = sy["__IM2_TABLE"]
    assert table == 0x100
    # slot 3 and slot 10 hold the handler addresses, the rest the default
    word = lambda i: int.from_bytes(img[table + 2 * i:table + 2 * i + 2],
                                    "little")
    assert word(3) == sy["_ON_A"] and word(10) == sy["_ON_B"]
    assert {word(i) for i in range(128) if i not in (3, 10)} == {
        sy["__IM2_DEFAULT"]}
    m = machine(paths)
    # interleave: slot 3 (bus byte 06h), slot 10 (14h), an unbound slot (40h)
    t = 4000
    for k in range(4):
        m.add_irq_at(t, 0x06)
        m.add_irq_at(t + 2500, 0x14)
        m.add_irq_at(t + 5000, 0x40)
        t += 9000
    m.add_irq_at(t, 0x14)
    m.add_irq_at(t + 2500, 0x14)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt", res
    assert m.peek8("_count_a") == 4
    assert m.peek8("_count_b") == 60
    assert m.rom_writes == []
    # I holds the high byte of the table
    assert m.cpu.get()["I"] == 0x01


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_unbound_slot_runs_the_default_handler(flow, tmp_path, libs):
    paths = build(tmp_path, IM2_PROGRAM, flow, libs)
    m = machine(paths)
    for k in range(5):
        m.add_irq_at(5000 + 1000 * k, 0x7E)      # slot 63: no handler
    m.add_irq_at(30000, 0x06)
    m.add_irq_at(31000, 0x06)
    m.add_irq_at(32000, 0x06)
    m.add_irq_at(33000, 0x06)
    for k in range(6):
        m.add_irq_at(34000 + 1000 * k, 0x14)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt"
    assert len(m.accepted) == 15
    assert m.peek8("_count_a") == 4 and m.peek8("_count_b") == 60


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_table_address_is_free_to_choose(flow, tmp_path, libs):
    src = "#pragma uc80 im2table 0x0400\n" + IM2_PROGRAM
    paths = build(tmp_path, src, flow, libs)
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    assert sy["__IM2_TABLE"] == 0x400
    m = machine(paths)
    for k in range(4):
        m.add_irq_at(4000 + 3000 * k, 0x06)
        m.add_irq_at(5000 + 3000 * k, 0x14)
    m.add_irq_at(20000, 0x14)
    m.add_irq_at(21000, 0x14)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt"
    assert m.cpu.get()["I"] == 0x04
    assert m.peek8("_count_a") == 4 and m.peek8("_count_b") == 60


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("table", [0x4000, 0xA000])
def test_im2_table_address_with_a_hex_digit_first(flow, table, tmp_path, libs):
    """0A000H needs its leading 0: um80 reads A000H as a name and sdas
    refuses ORG A000H.  The ROM is large enough for the table."""
    src = f"#pragma uc80 im2table {table:#x}\n" + IM2_PROGRAM
    paths = build(tmp_path, src, flow, libs, rom_size=0xC000)
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    assert sy["__IM2_TABLE"] == table
    assert sy["_MAIN"] >= table + 0x100
    m = machine(paths, rom_size=0xC000)
    for k in range(4):
        m.add_irq_at(4000 + 3000 * k, 0x06)
        m.add_irq_at(5000 + 3000 * k, 0x14)
    m.add_irq_at(20000, 0x14)
    m.add_irq_at(21000, 0x14)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt"
    assert m.cpu.get()["I"] == table >> 8
    assert m.peek8("_count_a") == 4 and m.peek8("_count_b") == 60


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_table_at_the_top_of_a_64k_rom_builds(flow, tmp_path, libs):
    """FE00h is the highest table: the code starts at FF00h."""
    src = "#pragma uc80 im2table 0xFE00\n" + IM2_PROGRAM
    paths = build(tmp_path, src, flow, libs, rom_size=0x10000)
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    assert sy["__IM2_TABLE"] == 0xFE00
    assert 0xFF00 <= sy["_MAIN"] < 0x10000
    assert 0xFF00 < len(paths[0].read_bytes()) <= 0x10000


def test_hex_constants_of_the_start_up_text_start_with_a_digit():
    from uc80 import rom
    assert rom.hex_const(0xA000) == "0A000H"
    assert rom.hex_const(0xA0, 2) == "0A0H"
    assert rom.hex_const(0x4000) == "4000H"
    assert rom.hex_const(0xFF, 2) == "0FFH"
    assert rom.hex_const(0x100) == "0100H"
    text = "\n".join(rom.startup_text(
        LIB / "crt0_rom.mac", None, None, None, im2=({3: "_h"}, 0xF000)))
    assert "LD\tA,0F0H" in text and "ORG\t0F000H" in text
    assert "ORG\t0F100H" in text and ";@RELOC 0F100H" in text


def sdas_compile(tmp_path, source, *flags):
    (tmp_path / "t.c").write_text(source)
    return subprocess.run(
        uc80_cmd("--target=rom", "--asm-syntax=sdas", *flags, "-I", INC,
                 "t.c", "-o", "t.s"), cwd=tmp_path, capture_output=True,
        text=True)


@pytest.mark.parametrize("table", [0x7F00, 0x8000, 0xA000])
def test_sdas_flow_reports_rom_overflow_of_a_high_table(table, tmp_path):
    """ul80 stops with ROM overflow; the sdas flow now stops too."""
    r = sdas_compile(tmp_path, f"#pragma uc80 im2table {table:#x}\n"
                     + IM2_PROGRAM)
    assert r.returncode == 1, r.stderr
    assert "uc80: error: ROM overflow" in r.stderr
    assert f"starts at {table + 0x100:04X}h" in r.stderr
    assert "--rom-size" in r.stderr
    assert not (tmp_path / "t.s").exists()


def test_sdas_flow_accepts_a_high_table_with_a_larger_rom(tmp_path):
    r = sdas_compile(tmp_path, "#pragma uc80 im2table 0x8000\n"
                     + IM2_PROGRAM, "--rom-size", "C000")
    assert r.returncode == 0, r.stderr
    r = sdas_compile(tmp_path, "#pragma uc80 im2table 0x7E00\n"
                     + IM2_PROGRAM)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("size", ["20000", "1FFFF", "0"])
def test_rom_size_above_the_address_space_is_an_error(size, tmp_path):
    r = sdas_compile(tmp_path, IM2_PROGRAM, "--rom-size", size)
    assert r.returncode == 1
    assert f"--rom-size {size}: expected a size in hex, 1 to 10000h" \
        in r.stderr


def test_rom_size_reaches_the_ul80_line_that_verbose_prints(tmp_path):
    (tmp_path / "t.c").write_text("int main(void) { return 0; }\n")
    r = subprocess.run(uc80_cmd("--target=rom", "-v", "--rom-size", "4000",
                                "t.c", "-o", "t.mac"), cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "--rom-size 4000 " in r.stdout and "--rom-size 8000" \
        not in r.stdout


def test_rom_size_is_checked_in_the_um80_flow_too(tmp_path):
    (tmp_path / "t.c").write_text("int main(void) { return 0; }\n")
    r = subprocess.run(uc80_cmd("--target=rom", "--rom-size", "20000",
                                "t.c", "-o", "t.mac"), cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 1 and "--rom-size 20000" in r.stderr


def test_rom_size_must_be_hex(tmp_path):
    r = sdas_compile(tmp_path, IM2_PROGRAM, "--rom-size", "zz")
    assert r.returncode == 1 and "--rom-size zz" in r.stderr


RST_PROGRAM = """
#include <stdint.h>
volatile uint8_t calls, last;

#pragma uc80 rst 10h on_rst
void on_rst(void) { calls++; }

#pragma uc80 rst 0x18 tick interrupt
void tick(void) { last++; }

int main(void)
{
    __asm__("\\trst 10h");
    __asm__("\\trst 10h");
    __asm__("\\trst 10h");
    __asm__("\\tim 0");
    __asm__("\\tei");
    while (last < 3)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_rst_software_call_and_interrupt_handler(flow, tmp_path, libs):
    paths = build(tmp_path, RST_PROGRAM, flow, libs)
    img = paths[0].read_bytes()
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    assert img[0x10] == 0xC3
    assert int.from_bytes(img[0x11:0x13], "little") == sy["_ON_RST"]
    assert img[0x18] == 0xC3
    assert int.from_bytes(img[0x19:0x1B], "little") == sy["_TICK"]
    assert img[0x08] == 0xC9 and img[0x20] == 0xC9      # still RET
    assert img[0:3] == b"\xf3\xed\x56"                  # IM 1 at reset
    m = machine(paths)
    for k in range(3):
        m.add_irq_at(20000 + 2000 * k, 0xDF)            # RST 18h opcode
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt"
    assert m.peek8("_calls") == 3
    assert m.peek8("_last") == 3
    # the interrupt handler ends with RETI (EI first, five pushes)
    a = sy["_TICK"]
    assert img[a] == 0xFB and img[a + 1:a + 6] == b"\xf5\xc5\xd5\xe5\xfd"


IM1_PROGRAM = """
#include <stdint.h>
volatile uint8_t ticks;
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
int main(void)
{
    __asm__("\\tei");
    while (ticks < 5)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_im1_still_works_without_im2(flow, tmp_path, libs):
    paths = build(tmp_path, IM1_PROGRAM, flow, libs)
    img = paths[0].read_bytes()
    assert img[0:3] == b"\xf3\xed\x56"
    assert "__im2_table" not in {n.lower() for n in _syms(paths[1])}
    m = machine(paths)
    m.add_irq_every(2000, start=2000)
    assert m.run(max_tstates=200_000).reason == "halt"
    assert m.peek8("_ticks") == 5


def test_a_program_with_no_vectors_has_no_im2_or_rst_code(tmp_path):
    """No pragma: the crt0 text has no IM 2, no table and a RET at each RST
    vector, so the image is the same as before this feature."""
    from uc80 import rom
    plain = rom.startup_text(LIB / "crt0_rom.mac", None, None, None)
    again = rom.startup_text(LIB / "crt0_rom.mac", None, None, None,
                             rst={}, im2=None)
    assert plain == again
    text = "\n".join(plain)
    assert "IM\t2" not in text and "__im2" not in text
    assert "\n\tLD\tI,A" not in text
    assert text.count("\tRET\n") >= 6
    for a in (0x08, 0x10, 0x18, 0x20, 0x28, 0x30):
        assert f"__vec_rst{a:02x}:\n\tRET" in text


def test_the_program_code_starts_after_the_table_only_with_im2(tmp_path):
    from uc80 import rom
    t = "\n".join(rom.startup_text(LIB / "crt0_rom.mac", None, None, None,
                                   im2=({}, 0x100)))
    assert "\tIM\t2" in t and "\tLD\tI,A" in t and "LD\tA,01H" in t
    assert "__im2_table:" in t and "ORG\t0200H" in t


def _syms(sym):
    out = {}
    for line in Path(sym).read_text().splitlines():
        a, n = line.split()
        out[n] = int(a, 16)
    return out


# ---- errors ---------------------------------------------------------------

def _compile_error(tmp_path, source, text):
    (tmp_path / "e.c").write_text(source)
    r = subprocess.run(uc80_cmd("--target=rom", "e.c", "-o", "e.mac"),
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1, r.stderr
    assert "uc80: error:" in r.stderr and text in r.stderr, r.stderr
    assert "Traceback" not in r.stderr


F = "void f(void) { }\nvoid g(void) { }\nint main(void) { return 0; }\n"


@pytest.mark.parametrize("pragma,text", [
    ("rst 00h f", "the RST address must be"),
    ("rst 38h f", "use '#pragma uc80 interrupt NAME'"),
    ("rst 0x40 f", "the RST address must be"),
    ("rst 0Ch f", "the RST address must be"),
    ("rst 10h f\n#pragma uc80 rst 10h g", "RST 10h vector is already set to f"),
    ("rst 10h f interrupt\n#pragma uc80 rst 10h g interrupt",
     "RST 10h vector is already set"),
    ("rst 10h f\n#pragma uc80 vector 10h g", "RST 10h vector is already"),
    ("rst 10h f bogus", "expected '#pragma uc80 rst"),
    ("rst 10h nothere", "'nothere' is named"),
    ("im2 128 f", "the IM 2 slot must be 0 to 127"),
    ("im2 -1 f", "the IM 2 slot must be 0 to 127"),
    ("im2 5 f\n#pragma uc80 im2 5 g", "IM 2 slot 5 is already set to f"),
    ("im2 5 f critical\n#pragma uc80 im2 6 f", "interrupt function of another"),
    ("im2table 0x180", "a multiple of 100h"),
    ("im2table 0x0", "a multiple of 100h"),
    ("im2table 0xFF00", "from 0100h to FE00h"),
    ("im2table 0x10000", "from 0100h to FE00h"),
    ("im2table 0x200\n#pragma uc80 im2table 0x300", "already at 0200h"),
    ("im2 5 nothere", "'nothere' is named"),
])
def test_vector_errors(pragma, text, tmp_path):
    _compile_error(tmp_path, f"#pragma uc80 {pragma}\n" + F, text)


def test_im2_handler_with_a_parameter_is_an_error(tmp_path):
    _compile_error(tmp_path, "#pragma uc80 im2 1 f\nvoid f(int x) { }\n"
                   "int main(void) { return 0; }\n", "parameter")


def test_im2_handler_called_from_c_is_an_error(tmp_path):
    _compile_error(tmp_path, "#pragma uc80 im2 1 f\nvoid f(void) { }\n"
                   "int main(void) { f(); return 0; }\n", "call")


def test_rst_plain_and_vector_pragma_alias(tmp_path):
    """'#pragma uc80 vector 08h NAME' is the same as 'rst 08h NAME'."""
    paths = build(tmp_path, """
volatile unsigned char n;
#pragma uc80 vector 08h h
void h(void) { n++; }
int main(void)
{
    __asm__("\\trst 08h");
    __asm__("\\trst 08h");
    return 0;
}
""", "um80")
    m = machine(paths)
    m.run(max_tstates=100_000)
    assert m.peek8("_n") == 2


# ---- handlers written in assembly -----------------------------------------

ASM_HANDLER = """\tPUBLIC\t_ih
\textrn\t_asm_count
_ih:
\tpush\taf
\tld\ta,(_asm_count)
\tinc\ta
\tld\t(_asm_count),a
\tpop\taf
\tei
\treti
\tdseg
\tpublic\t_asm_count
_asm_count:\tdb\t0
\tcseg
"""

ASM_C = """
extern void ih(void);
#pragma uc80 im2 3 ih
int main(void)
{
    __asm__("\\tei");
    for (;;) {
        __asm__("\\thalt");
    }
}
"""


@pytest.mark.skipif(not HAVE_UM80, reason="needs um80 and ul80")
def test_im2_slot_can_name_a_handler_in_an_assembly_file(tmp_path):
    (tmp_path / "t.c").write_text(ASM_C)
    (tmp_path / "a.mac").write_text(ASM_HANDLER.replace(
        "\textrn\t_asm_count\n", "").replace("\tdseg", "\tdseg"))
    _run(uc80_cmd("--target=rom", "t.c", "a.mac", "-o", "t.mac"), tmp_path)
    _run(["um80", "t.mac", "-o", "t.rel"], tmp_path)
    _run(["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
          "--rom-size", "8000", "--ram-size", "800", "--min-stack", "40",
          "-o", "t.bin", "-S", "t.sym", "t.rel",
          str(LIB / "libc_rom.lib"), str(LIB / "runtime.lib")], tmp_path)
    img = (tmp_path / "t.bin").read_bytes()
    sy = {n.upper(): a for n, a in _syms(tmp_path / "t.sym").items()}
    assert int.from_bytes(img[0x100 + 6:0x100 + 8], "little") == sy["_IH"]
    m = machine((tmp_path / "t.bin", tmp_path / "t.sym"))
    for k in range(3):
        m.add_irq_at(3000 + 3000 * k, 0x06)
    m.run(max_tstates=30_000)
    assert m.peek8("_asm_count") == 3


def test_im2_slot_with_no_definition_anywhere_is_an_error(tmp_path):
    _compile_error(tmp_path, "extern void ih(void);\n"
                   "#pragma uc80 im2 3 ih\nint main(void) { return 0; }\n",
                   "no assembly file defines")


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_level_source_carries_a_data_bus_byte(flow, tmp_path, libs):
    """A level-triggered source (set_irq_level) with vec 14h reaches slot
    10 only; slot 3 never runs."""
    paths = build(tmp_path, IM2_PROGRAM, flow, libs)
    m = machine(paths)
    src = object()
    m.at(3000, lambda mm, t: mm.set_irq_level(True, src, 0x14), kind="call")
    m.at(9000, lambda mm, t: mm.set_irq_level(False, src), kind="call")
    m.run(max_tstates=20_000)
    assert m.peek8("_count_a") == 0
    assert m.peek8("_count_b") >= 10


WORST_CASE = """
#include <z80.h>
volatile unsigned char n, k;
void _watchdog_kick(void) { z80_out(0x7F, 0xA5); }
#pragma uc80 public _watchdog_kick
#pragma uc80 interrupt on_int
void on_int(void) { n++; }
#pragma uc80 nmi on_nmi
void on_nmi(void) { k++; }
#pragma uc80 im2 0 on_a
void on_a(void) { n++; }
int main(void) { return 0; }
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_im2_start_up_code_fits_below_the_table(flow, tmp_path, libs):
    """With the watchdog, a user INT and a user NMI handler (the most
    start-up code), __im2_default, the last label before the table, is still
    below the table: the table ORG never goes backward."""
    paths = build(tmp_path, WORST_CASE, flow, libs)
    sy = {n.upper(): a for n, a in _syms(paths[1]).items()}
    assert sy["__IM2_DEFAULT"] + 2 <= sy["__IM2_TABLE"] == 0x100


# ---- names in more than one file ---------------------------------------------

def _build_files(tmp_path, files, flow="um80", rom_libs=None):
    """Build several C files in one program (um80/ul80 or the sdas flow)."""
    if flow == "sdas" and not HAVE_SDAS:
        pytest.skip("needs the SDCC sdas tools")
    if flow == "um80" and not HAVE_UM80:
        pytest.skip("needs um80 and ul80")
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    if flow == "sdas":
        from uc80 import sdas
        _run(uc80_cmd("--target=rom", "--asm-syntax=sdas", "-I", INC,
                      "--rom-size", "8000", *files, "-o", "t.s"), tmp_path)
        _run(["sdasz80", "-g", "-l", "-o", "t.s"], tmp_path)
        origin = sdas.code_origin_of((tmp_path / "t.s").read_text()) or 0x68
        _run(["sdldz80", *sndrom.sdas_link_flags(origin=origin), "-i", "-j",
              "t.ihx", "t.rel", "-l", str(rom_libs / "uc80_lc.lib"),
              "-l", str(rom_libs / "uc80_rt.lib")], tmp_path)
        _run(["sdobjcopy", "-I", "ihex", "-O", "binary", "t.ihx", "t.bin"],
             tmp_path)
        sndrom.noi_to_sym(tmp_path / "t.noi", tmp_path / "t.sym")
        return tmp_path / "t.bin", tmp_path / "t.sym"
    _run(uc80_cmd("--target=rom", *files, "-o", "t.mac"), tmp_path)
    _run(["um80", "t.mac", "-o", "t.rel"], tmp_path)
    _run(["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
          "--rom-size", "8000", "--ram-size", "800", "--min-stack", "40",
          "-o", "t.bin", "-S", "t.sym", "t.rel",
          str(LIB / "libc_rom.lib"), str(LIB / "runtime.lib")], tmp_path)
    return tmp_path / "t.bin", tmp_path / "t.sym"


A_C = """
#pragma uc80 %s 1 h
volatile unsigned char x;
static void h(void) { x++; }
"""
B_C = """
#pragma uc80 %s 2 h
extern volatile unsigned char x;
volatile unsigned char y;
static void h(void) { y += 2; }
int main(void)
{
    __asm__("\\tei");
    while (x < 4 || y < 8)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_two_static_handlers_of_one_name_each_bind_in_their_own_file(
        flow, tmp_path, libs):
    """A pragma resolves in its own file: slot 1 is the h of a.c (h__s0),
    slot 2 is the h of b.c (h__s1).  The code runs: x and y both count."""
    paths = _build_files(tmp_path, {"a.c": A_C % "im2", "b.c": B_C % "im2"},
                         flow, libs)
    m = machine(paths)
    for k in range(4):
        m.add_irq_at(4000 + 2000 * k, 0x02)      # slot 1: h of a.c
        m.add_irq_at(5000 + 2000 * k, 0x04)      # slot 2: h of b.c
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt", res
    assert m.peek8("_x") == 4 and m.peek8("_y") == 8


@pytest.mark.parametrize("flow", FLOWS)
def test_the_interrupt_pragma_binds_to_the_static_of_its_own_file(
        flow, tmp_path, libs):
    a = ("#pragma uc80 interrupt h\nvolatile unsigned char x;\n"
         "static void h(void) { x++; }\n")
    b = ("extern volatile unsigned char x;\nvolatile unsigned char y;\n"
         "static void h(void) { y += 5; }\n"
         "int main(void)\n{\n    __asm__(\"\\tim 1\\n\\tei\");\n"
         "    while (x < 3)\n        ;\n    __asm__(\"\\tdi\");\n"
         "    return y;\n}\n")
    paths = _build_files(tmp_path, {"a.c": a, "b.c": b}, flow, libs)
    m = machine(paths)
    for k in range(3):
        m.add_irq_at(4000 + 2000 * k, 0xFF)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt", res
    assert m.peek8("_x") == 3 and m.peek8("_y") == 0   # b.c h never ran


def test_two_external_definitions_are_still_ambiguous(tmp_path):
    """No file owns the name: the pragma cannot tell which function."""
    (tmp_path / "a.c").write_text(
        "#pragma uc80 interrupt h\nvoid h(void) { }\n")
    (tmp_path / "b.c").write_text(
        "void h(void) { }\nint main(void) { return 0; }\n")
    r = subprocess.run(uc80_cmd("--target=rom", "a.c", "b.c", "-o", "t.mac"),
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 1
    assert "a.c:1: #pragma uc80 interrupt: 'h' is defined in more than one " \
           "file" in r.stderr, r.stderr


@pytest.mark.skipif(not HAVE_UM80, reason="needs um80 and ul80")
def test_handlers_with_different_names_in_two_files_run(tmp_path):
    a = (A_C % "im2").replace("h(", "ha(").replace(" h\n", " ha\n")
    b = (B_C % "im2").replace("h(", "hb(").replace(" h\n", " hb\n")
    paths = _build_files(tmp_path, {"a.c": a, "b.c": b})
    m = machine(paths)
    for k in range(4):
        m.add_irq_at(4000 + 2000 * k, 0x02)      # slot 1: ha
        m.add_irq_at(5000 + 2000 * k, 0x04)      # slot 2: hb
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt", res
    assert m.peek8("_x") == 4 and m.peek8("_y") == 8


# ---- the pragmas in a CP/M program ---------------------------------------------

@pytest.mark.parametrize("pragma,text", [
    ("im2 3 h", "the IM 2 vector table is not written: only --target=rom"),
    ("im2table 0x4000", "the IM 2 vector table is not written"),
    ("rst 10h h", "the RST 10h vector is not set to 'h': only "
                  "--target=rom writes the vectors"),
    ("interrupt h", "the INT (38h) vector is not set to 'h': only "
                    "--target=rom writes the vectors"),
    ("nmi h", "the NMI (66h) vector is not set to 'h': only "
              "--target=rom writes the vectors"),
    ("vector 38h h", "the INT (38h) vector is not set to 'h'"),
])
def test_vector_pragmas_warn_when_the_target_is_cpm(pragma, text, tmp_path):
    (tmp_path / "c.c").write_text(
        f"#pragma uc80 {pragma}\nvoid h(void) {{ }}\n"
        "int main(void) { return 0; }\n")
    r = subprocess.run(uc80_cmd("c.c", "-o", "c.mac"), cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert f"uc80: warning: c.c:1: {text}" in r.stderr, r.stderr


def test_a_cpm_program_without_the_pragmas_has_no_warning(tmp_path):
    (tmp_path / "c.c").write_text("int main(void) { return 0; }\n")
    r = subprocess.run(uc80_cmd("c.c", "-o", "c.mac"), cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0 and "warning" not in r.stderr


# ---- a naked handler ---------------------------------------------------------

NAKED_HANDLER = """
volatile unsigned char n;
#pragma uc80 im2 3 h
%s
{
    __asm__("\\tpush af\\n\\tld a,(_n)\\n\\tinc a\\n\\tld (_n),a\\n"
            "\\tpop af\\n\\tei\\n\\treti\\n");
}
int main(void)
{
    __asm__("\\tei");
    while (n < 3)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("spelling", [
    "void h(void) __attribute__((naked))",
    "void h(void) __naked",
    "__attribute__((naked)) void h(void)",
    "[[uc80::naked]] void h(void)"])
def test_a_naked_im2_handler_has_no_prologue(flow, spelling, tmp_path, libs):
    paths = build(tmp_path, NAKED_HANDLER % spelling, flow, libs)
    sy = {k.upper(): v for k, v in _syms(paths[1]).items()}
    img = paths[0].read_bytes()
    assert img[sy["_H"]] == 0xF5            # PUSH AF, not PUSH IX
    m = machine(paths)
    for k in range(3):
        m.add_irq_at(4000 + 2000 * k, 0x06)
    res = m.run(max_tstates=300_000)
    assert res.reason == "halt", res
    assert m.peek8("_n") == 3


# ---- rst n is a call to an unknown routine (register locals) ---------------------

RST_CLOBBER = """
volatile unsigned r;
#pragma uc80 rst 10h soft
void soft(void) __naked { __asm__("\\tld bc,0\\n\\tld de,0\\n\\tld iy,0\\n\\tret\\n"); }
static void f(void) { __asm__("\\trst 10h"); }
struct P { unsigned x; struct P *next; };
static unsigned walk(struct P *p)
{
    unsigned t = 0;
    while (p) { t += p->x; f(); p = p->next; }
    return t;
}
int main(void)
{
    struct P c = {300, 0}, b = {20, &c}, a = {1, &b};
    unsigned i, s = 0;
    for (i = 0; i < 5; i++) { f(); s += i; }
    r = s * 1000 + walk(&a);
    return 0;
}
"""

RST_CLOBBER_C = """
volatile unsigned r;
static void helper(void) __naked { __asm__("\\tld bc,0\\n\\tld de,0\\n\\tret\\n"); }
#pragma uc80 rst 10h soft
void soft(void) { helper(); }
static void f(void) { __asm__("\\trst 10h"); }
int main(void)
{
    unsigned i, s = 0;
    for (i = 0; i < 5; i++) { f(); s += i; }
    r = s;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
@pytest.mark.parametrize("src,want", [(RST_CLOBBER, 10321), (RST_CLOBBER_C, 10)],
                         ids=["naked", "c-handler"])
def test_rst_in_asm_clobbers_like_a_call(flow, src, want, tmp_path, libs,
                                         monkeypatch):
    """rst n reaches a handler that may change BC, DE and IY: the register
    locals of the callers are saved around the call of f."""
    for iy in ("0", "1"):
        monkeypatch.setenv("UC80_NO_IY", iy)
        d = tmp_path / ("iy" + iy)
        d.mkdir()
        paths = build(d, src, flow, libs)
        m = machine(paths)
        res = m.run(max_tstates=400_000)
        assert res.reason == "halt", res
        assert m.peek16("_r") == want % 65536


# ---- user assembly names a static of its own file ------------------------------

ASM_A = """
volatile unsigned char out_a, out_b;
static unsigned char cnt;
static void helper(void) { out_a = 11; }
void nk_call(void) __naked { __asm__("\\tcall _helper\\n\\tret\\n"); }
void bump(void) { __asm__("\\tld a,(_cnt)\\n\\tinc a\\n\\tld (_cnt),a\\n"); }
int geta(void) { return cnt; }
"""
ASM_B = """
extern volatile unsigned char out_a, out_b;
static unsigned char cnt;
static void helper(void) { out_b = 22; }
void nk_call(void);
void bump(void);
int geta(void);
void nk_b(void) { helper(); }
volatile int r;
int main(void)
{
    nk_call(); nk_b(); bump(); bump(); bump(); cnt = 40;
    r = geta() * 100 + cnt;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_asm_text_reaches_the_static_of_its_own_file(flow, tmp_path, libs):
    """Two files each define static helper and static cnt.  The asm text
    of a.c says _helper and _cnt: both mean the objects of a.c."""
    paths = _build_files(tmp_path, {"a.c": ASM_A, "b.c": ASM_B}, flow, libs)
    m = machine(paths)
    res = m.run(max_tstates=400_000)
    assert res.reason == "halt", res
    assert m.peek8("_out_a") == 11 and m.peek8("_out_b") == 22
    assert m.peek16("_r") == 340


# ---- messages of the pragmas with several files --------------------------------

def _compile_files(tmp_path, files, *flags):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return subprocess.run(uc80_cmd("--target=rom", *flags, *files, "-o",
                                   "t.mac"), cwd=tmp_path,
                          capture_output=True, text=True)


def test_a_pragma_cannot_name_a_static_of_another_file(tmp_path):
    r = _compile_files(tmp_path, {
        "a.c": "#pragma uc80 im2 1 h\nint main(void) { return 0; }\n",
        "c.c": "static void h(void) { }\nvoid c_call(void) { h(); }\n"})
    assert r.returncode == 1
    assert "a.c:1: #pragma uc80 im2: 'h' is not defined in this file" \
        in r.stderr, r.stderr
    assert "c_call" not in r.stderr


def test_messages_show_the_c_name_of_a_renamed_static(tmp_path):
    r = _compile_files(tmp_path, {
        "a.c": "#pragma uc80 vector 38h h\nstatic void h(void) { }\n",
        "b.c": "#pragma uc80 vector 38h h\nstatic void h(void) { }\n"
               "int main(void) { return 0; }\n"})
    assert r.returncode == 1
    assert "the INT (38h) vector is already set to h" in r.stderr, r.stderr
    assert "__s" not in r.stderr, r.stderr


def test_entry_names_a_static_of_two_files_is_ambiguous(tmp_path):
    r = _compile_files(tmp_path, {
        "a.c": "static void h(void) { }\n",
        "b.c": "static void h(void) { }\nint main(void) { return 0; }\n"},
        "--entry", "h")
    assert r.returncode == 1
    assert "'h' is a static function or variable in more than one file" \
        in r.stderr, r.stderr


@pytest.mark.parametrize("flow", FLOWS)
def test_asm_text_with_c_style_hex_builds_in_both_flows(flow, tmp_path, libs):
    src = """
volatile unsigned r;
void setter(void) __naked { __asm__("\\tld hl,0x5A5A\\n\\tld (_r),hl\\n\\tret\\n"); }
int main(void) { setter(); return 0; }
"""
    paths = build(tmp_path, src, flow, libs)
    m = machine(paths)
    res = m.run(max_tstates=100_000)
    assert res.reason == "halt", res
    assert m.peek16("_r") == 0x5A5A
