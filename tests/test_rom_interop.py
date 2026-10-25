"""uc80 next to SDCC tools and nullsound (docs/rom_interop.md).

Each ROM here is built with `uc80 --asm-syntax=sdas`, sdasz80 and sdldz80
and runs in bench/romrun.  The tests skip when the sdas tools are missing.
The nullsound test needs the nullsound sources (bench/_cache/ngdevkit, or
a network connection) and skips without them.
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

import isrproof  # noqa: E402
import nullsound  # noqa: E402
import sndrom  # noqa: E402
from romrun import Machine  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS and shutil.which("uc80")),
    reason="needs uc80 and the SDCC sdas tools")

HAVE_UM80 = bool(shutil.which("um80") and shutil.which("ul80"))


@pytest.mark.parametrize("kernel", sorted(sndrom.KERNELS))
@pytest.mark.parametrize("layout", ["neogeo"])
def test_snd_kernel_sdas_build_equals_the_golden_output(
        kernel, layout, rom_libs, tmp_path):
    info = sndrom.build_sdas(kernel, tmp_path, layout, rom_libs)
    assert info["ok"], info["log"]
    r = sndrom.execute(info, kernel)
    assert r["text"] == sndrom.golden(kernel)
    assert r["reason"].startswith("port:")


@pytest.mark.skipif(not HAVE_UM80, reason="needs um80 and ul80")
@pytest.mark.parametrize("kernel", ["snd-cmdq", "snd-fnum"])
def test_snd_kernel_sdas_image_is_the_um80_image(kernel, rom_libs, tmp_path):
    """Same bytes, same sizes, same T-states as the um80 and ul80 build."""
    a = tmp_path / "ul"
    b = tmp_path / "sd"
    a.mkdir()
    b.mkdir()
    ia = sndrom.build(kernel, a, "neogeo")
    ib = sndrom.build_sdas(kernel, b, "neogeo", rom_libs)
    assert ia["ok"] and ib["ok"], (ia.get("log"), ib.get("log"))
    img_a = Path(ia["bin"]).read_bytes()
    img_b = Path(ib["bin"]).read_bytes()
    assert img_a[:len(img_b)] == img_b
    assert set(img_a[len(img_b):]) <= {0xFF}      # ul80 pads with FFh
    assert (ia["rom"], ia["ram"], ia["data"], ia["bss"]) == (
        ib["rom"], ib["ram"], ib["data"], ib["bss"])
    ra, rb = sndrom.execute(ia, kernel), sndrom.execute(ib, kernel)
    assert ra["text"] == rb["text"]
    assert ra["timed_t"] == rb["timed_t"] and ra["stack"] == rb["stack"]


@pytest.fixture
def sdas_isr_proofs(rom_libs):
    isrproof.SDAS_LIBS = rom_libs
    yield
    isrproof.SDAS_LIBS = None


def test_isr_proofs_with_sdas(sdas_isr_proofs, tmp_path):
    """The NMI command queue, the YM2610 timer interrupt and the bank
    select run from an sdldz80 image with the same port logs.  The vector
    at 66h is a JP to a C function here, so the code starts at 69h."""
    r = isrproof.prove_nmi(tmp_path)
    assert r["ok"] and r["nmis"] == 300 and r["isr_min"] == 667
    r = isrproof.prove_seq(tmp_path)
    assert r["ok"] and r["irqs"] == 500 and r["got"] == r["expected"] == 7934
    r = isrproof.prove_bank(tmp_path)
    assert r["ok"] and r["bank_ports"] == [(b << 8) | 0x0B
                                           for b in range(2, 8)]


ASM_PROGRAM = r"""
#include <z80.h>
unsigned char r;
static const unsigned char tab[3] = { 7, 8, 9 };

void f(void)
{
    __asm
        ld a,#0x5A
        ld (_r),a
        out (0x10),a
        ld b,#3
    1$: ld a,b
        out (0x11),a
        djnz 1$
        ld hl,#_r
        ld a,(hl)
        add a,#1
        out (0x12),a
        jr 2$
        .db 0x11,0x22
        .dw 0x3344
    2$: ld hl,#_tab+1
        ld a,(hl)
        out (0x13),a
        ld a,#'A'
        out (0x14),a
    __endasm;
}

int main(void)
{
    f();
    z80_out(0xFE, 0);
    return 0;
}
"""
ASM_EXPECT = [(0x10, 0x5A), (0x11, 3), (0x11, 2), (0x11, 1), (0x12, 0x5B),
              (0x13, 8), (0x14, 0x41), (0xFE, 0)]


def _ports(m):
    return [(p & 0xFF, v) for (_t, k, p, v) in m.port_log if k == "OUT"]


def _run_rom(path, sym):
    m = Machine(nmi_gate=False, ram_fill="random", sp="poison")
    m.load_rom(str(path))
    m.load_symbols(str(sym))
    m.magic_ports.add(0xFE)
    m.run(max_tstates=2_000_000)
    return m


@pytest.mark.parametrize("flow", ["sdas", "um80"])
def test_asm_block_runs_in_a_rom(flow, rom_libs, tmp_path):
    """An SDCC __asm block, in sdas syntax, gives the expected port
    writes in an image of the um80 flow and of the sdas flow."""
    if flow == "um80" and not HAVE_UM80:
        pytest.skip("needs um80 and ul80")
    (tmp_path / "a.c").write_text(ASM_PROGRAM)
    inc = str(REPO / "src" / "uc80" / "lib" / "include")
    if flow == "sdas":
        steps = [
            [sys.executable, "-m", "uc80.main", "--target=rom",
             "--asm-syntax=sdas", "-I", inc, "a.c", "-o", "a.s"],
            ["sdasz80", "-g", "-l", "-o", "a.s"],
            ["sdldz80", *sndrom.sdas_link_flags(), "-i", "-j", "a.ihx",
             "a.rel", "-l", str(rom_libs / "uc80_lc.lib"),
             "-l", str(rom_libs / "uc80_rt.lib")],
            ["sdobjcopy", "-I", "ihex", "-O", "binary", "a.ihx", "a.bin"],
        ]
    else:
        lib = Path(subprocess.run(["uc80", "--print-lib-dir"],
                                  capture_output=True, text=True).stdout.strip())
        steps = [
            [sys.executable, "-m", "uc80.main", "--target=rom", "a.c",
             "-o", "a.mac"],
            ["um80", "a.mac", "-o", "a.rel"],
            ["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
             "--rom-size", "8000", "--ram-size", "800", "--min-stack", "100",
             "-o", "a.bin", "-S", "a.sym", "a.rel",
             str(lib / "libc_rom.lib"), str(lib / "runtime.lib")],
        ]
    for cmd in steps:
        r = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 0, " ".join(cmd) + "\n" + r.stdout + r.stderr
    if flow == "sdas":
        sndrom.noi_to_sym(tmp_path / "a.noi", tmp_path / "a.sym")
    m = _run_rom(tmp_path / "a.bin", tmp_path / "a.sym")
    assert _ports(m) == ASM_EXPECT


def test_cli_prints_the_sdas_commands(tmp_path):
    (tmp_path / "a.c").write_text("int main(void) { return 0; }\n")
    r = subprocess.run([sys.executable, "-m", "uc80.main", "--target=rom",
                        "--asm-syntax=sdas", "-v", "a.c", "-o", "a.s"],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "sdasz80 -g -l -o a.s" in r.stdout
    assert "sdldz80 -n -b _CODE=0x68" in r.stdout
    assert "-g __BSS_END=s__DATA+l__DATA" in r.stdout


# ---- SDCC ---------------------------------------------------------------

SDCC_MAIN = r"""
extern unsigned int n;            /* defined in the uc80 module */
extern unsigned int result;
void uc_work(void);
__sfr __at(0x30) PORT_SDCC;
unsigned int ticks = 100;         /* initial data of SDCC */

void sd_tick(void)                /* called from the uc80 module */
{
    ticks++;
}

void main(void)
{
    n = 1000;
    uc_work();
    PORT_SDCC = (unsigned char)(result & 0xFF);
    PORT_SDCC = (unsigned char)(result >> 8);
    PORT_SDCC = (unsigned char)ticks;
    for (;;) { }
}
"""

UC_MODULE = r"""
#include <z80.h>
unsigned int n;
unsigned int result;
unsigned int base = 5;                 /* initial data of uc80 */
const unsigned char tag[4] = { 1, 2, 3, 4 };
extern unsigned int ticks;
extern void sd_tick(void);

static unsigned int step(unsigned int x)    /* x is at (IX+4) */
{
    sd_tick();                              /* SDCC code runs here */
    return x * 7 / 3;
}

void uc_work(void)
{
    unsigned char i;
    unsigned int acc = 0;
    for (i = 0; i < 3; i++)
        acc += step(n + i);
    result = acc + base + tag[2];
    z80_out(0x20, (unsigned char)(ticks & 0xFF));
}
"""


@pytest.mark.skipif(not shutil.which("sdcc"), reason="needs sdcc")
def test_sdcc_main_with_a_uc80_module(tmp_path):
    """A main() compiled by SDCC with its own crt0 calls a function that
    uc80 compiled, and the uc80 code calls an SDCC function.  The initial
    data of both modules is copied by the SDCC start-up code from
    _INITIALIZER to _INITIALIZED, and the uc80 function keeps IX."""
    (tmp_path / "main.c").write_text(SDCC_MAIN)
    (tmp_path / "uc.c").write_text(UC_MODULE)
    inc = str(REPO / "src" / "uc80" / "lib" / "include")
    steps = [
        [sys.executable, "-m", "uc80.main", "--target=rom",
         "--asm-syntax=sdas", "--no-embed-startup", "--entry", "uc_work",
         "-I", inc, "uc.c", "-o", "uc.s"],
        ["sdasz80", "-g", "-l", "-o", "uc.s"],
        ["sdcc", "-mz80", "-c", "main.c"],
        ["sdcc", "-mz80", "--code-loc", "0x200", "--data-loc", "0xF800",
         "main.rel", "uc.rel", "-o", "prog.ihx"],
        ["sdobjcopy", "-I", "ihex", "-O", "binary", "prog.ihx", "prog.bin"],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 0, " ".join(cmd) + "\n" + r.stdout + r.stderr
    m = Machine(ram_fill="random", rom_write="fault")
    m.load_rom(str(tmp_path / "prog.bin"))
    m.run(max_tstates=3_000_000)
    want = (1000 * 7 // 3 + 1001 * 7 // 3 + 1002 * 7 // 3 + 5 + 3) & 0xFFFF
    assert _ports(m) == [(0x20, 103), (0x30, want & 0xFF),
                         (0x30, want >> 8), (0x30, 103)]
    assert len(m.rom_writes) == 0


# ---- nullsound ----------------------------------------------------------

@pytest.fixture(scope="module")
def nullsound_sources():
    try:
        return nullsound.fetch()
    except nullsound.Unavailable as e:
        pytest.skip(f"nullsound sources not available: {e}")


@pytest.fixture(scope="module")
def nullsound_rom(nullsound_sources, tmp_path_factory):
    work = tmp_path_factory.mktemp("nullsound")
    lib = nullsound.build_nullsound(nullsound_sources, work)
    nullsound.build_user(nullsound_sources, work)
    nullsound.link(lib, work)
    return nullsound_sources, work


def test_nullsound_builds_with_c_commands_and_keeps_its_ram_ranges(
        nullsound_rom):
    src, work = nullsound_rom
    assert (work / "ns.bin").stat().st_size > 8000
    # nullsound/check-bounds.py: no state range crosses 256 bytes
    nullsound.check_bounds(src, work)
    sym = nullsound.load_syms(work / "ns.sym")
    # the vectors are nullsound's, the table is the user's
    assert sym["CMD_JMPTABLE"] > 0x100
    assert sym["_CMD_KEY_ON"] > sym["CMD_JMPTABLE"]
    image = (work / "ns.bin").read_bytes()
    assert image[0] == 0xF3 and image[0x66] == 0x08   # di; ex af,af' at the NMI
    # entry 04h and 05h of the table jump to the C functions
    t = sym["CMD_JMPTABLE"]
    for n, name in ((2, "_CMD_INIT"), (4, "_CMD_KEY_ON"),
                    (5, "_CMD_KEY_OFF")):
        e = image[t + 3 * n:t + 3 * n + 3]
        assert e[0] == 0xC3 and e[1] | e[2] << 8 == sym[name]


@pytest.mark.parametrize("fill,seed", [(None, 1), ("random", 1),
                                       ("random", 7)])
def test_nullsound_calls_c_commands_that_write_the_ym2610(
        nullsound_rom, fill, seed):
    """The 68k sends command 02h (start the driver; the hook runs a C
    function), 04h three times (C: key on, with a note from a const
    table and the runtime division) and 05h (C: key off).  The C code
    writes the YM2610 through nullsound's ym2610_write_port_a and replies
    on port 0Ch.  The register writes are the expected sequence."""
    _src, work = nullsound_rom
    info = nullsound.execute(work, ram_fill=fill, seed=seed)
    assert nullsound.check(info) == []
    m = info["machine"]
    sym = nullsound.load_syms(work / "ns.sym")
    assert m.peek8(sym["_KEY_COUNT"]) == 3     # three key-on commands
    assert len(m.rom_writes) == 0
    # the stack stayed in the RAM and above the nullsound data
    assert m.min_sp is not None and m.min_sp > 0xFF9B


def test_nullsound_sources_are_pinned():
    assert len(nullsound.COMMIT) == 40
    assert all(len(h) == 64 for h in nullsound.FILES.values())
