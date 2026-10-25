"""The ROM target: uc80 --target=rom, crt0_rom, libc_rom.lib, the snd-*
kernels as ROM images.  See docs/rom.md.

The tests compile, assemble and link with um80 and ul80 and run the image
in the bench/romrun Z80 runner.  They skip when a tool or a library is
missing.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bench"))
sys.path.insert(0, str(REPO / "bench" / "rom"))

import uc80  # noqa: E402

LIB = uc80.lib_dir()


def _ul80_has_rom_options():
    if not shutil.which("ul80"):
        return False
    r = subprocess.run(["ul80", "--help"], capture_output=True, text=True)
    return "--rom-data" in r.stdout + r.stderr


pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and _ul80_has_rom_options()),
    reason="needs um80 and a ul80 with ROM options")


def test_rom_libraries_are_built():
    """A missing library is a failure, not a skip (tests/conftest.py builds
    the libraries when the tools exist)."""
    for name in ("crt0_rom.rel", "libc_rom.lib", "runtime.lib"):
        assert (LIB / name).exists(), (
            name + " is missing; run 'uc80 --build-libs'")


from romrun import Machine  # noqa: E402


def uc80_cmd(*args):
    return [sys.executable, "-m", "uc80.main", *args]


def compile_rom(tmp_path, source, *extra, files=()):
    src = tmp_path / "t.c"
    src.write_text(source)
    return subprocess.run(
        uc80_cmd("--target=rom", str(src), *files, "-o", str(tmp_path / "t.mac"),
                 *extra),
        capture_output=True, text=True, cwd=str(REPO))


def link_rom(tmp_path, *, data="F800", ram_size="800", extra=()):
    r = subprocess.run(["um80", "t.mac", "-o", "t.rel"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run(
        ["ul80", "--bin", "-p", "0", "-d", data, "--rom-data",
         "--rom-size", "8000", "--ram-size", ram_size, "--min-stack", "40",
         "-o", "t.bin", "-S", "t.sym", "t.rel", str(LIB / "libc_rom.lib"),
         str(LIB / "runtime.lib"), *extra],
        cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return (tmp_path / "t.bin").read_bytes(), tmp_path / "t.sym"


def machine(tmp_path, ram=(0xF800, 0xFFFF)):
    m = Machine(ram=ram, banks=None, ram_fill="random", sp="poison", track=True)
    m.load_rom(str(tmp_path / "t.bin"))
    m.load_symbols(str(tmp_path / "t.sym"))
    return m


def symbols(tmp_path):
    out = {}
    for line in (tmp_path / "t.sym").read_text().splitlines():
        a, n = line.split()
        out[n.upper()] = int(a, 16)
    return out


PROGRAM = """
#include <string.h>
#include <stdlib.h>
volatile int out_len;
volatile int out_cmp;
volatile int out_sorted[6];
volatile int out_zero;
int v[6] = {9, 4, 7, 1, 8, 2};
char msg[] = "hello";
char buf[16];
static int cmp(const void *a, const void *b) { return *(const int *)a - *(const int *)b; }
int main(void)
{
    qsort(v, 6, sizeof v[0], cmp);
    for (int i = 0; i < 6; i++) out_sorted[i] = v[i];
    memcpy(buf, msg, strlen(msg) + 1);
    out_len = (int) strlen(buf);
    out_cmp = strcmp(buf, "hellp");
    out_zero = buf[10];
    return 0;
}
"""


@pytest.fixture
def program(tmp_path):
    r = compile_rom(tmp_path, PROGRAM)
    assert r.returncode == 0, r.stderr
    link_rom(tmp_path)
    return tmp_path


class TestRomImage:
    def test_vectors(self, program):
        img = (program / "t.bin").read_bytes()
        assert img[0:3] == b"\xf3\xed\x56"            # di, im 1
        assert img[3] == 0xC3                           # jp __start
        for a in (0x08, 0x10, 0x18, 0x20, 0x28, 0x30):
            assert img[a] == 0xC9, hex(a)               # ret
        assert img[0x38:0x3B] == b"\xfb\xed\x4d"        # ei, reti
        assert img[0x66:0x68] == b"\xed\x45"            # retn
        assert symbols(program)["__START"] == 0x68

    def test_runs_with_random_ram(self, program):
        m = machine(program)
        res = m.run(max_tstates=2_000_000)
        assert res.reason == "halt"
        assert m.rom_writes == []
        # out_sorted is in the BSS; find its six words by value.
        ram = m.dump_ram()
        words = [int.from_bytes(ram[i:i + 2], "little", signed=True)
                 for i in range(len(ram) - 1)]
        assert [1, 2, 4, 7, 8, 9] in [words[i:i + 12:2]
                                      for i in range(len(words) - 11)]
        assert b"hello\x00" in ram

    def test_data_copied_and_bss_cleared(self, tmp_path):
        r = compile_rom(tmp_path, """
volatile char a[8];
volatile char b = 5;
char tab[4] = {1, 2, 3, 4};
int main(void) { return 0; }
""")
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        m = machine(tmp_path)
        assert m.run(max_tstates=200_000).reason == "halt"
        sy = symbols(tmp_path)
        assert m.peek(sy["__DATA_RAM"], sy["__DATA_LEN"]) == bytes(
            (tmp_path / "t.bin").read_bytes()[sy["__DATA_ROM"]:
                                              sy["__DATA_ROM"] + sy["__DATA_LEN"]])
        bss = m.peek(sy["__BSS_START"], sy["__BSS_END"] - sy["__BSS_START"])
        assert bss == bytes(len(bss))

    def test_stack_starts_at_ram_end(self, program):
        m = machine(program)
        m.run(max_tstates=2_000_000)
        assert m.min_sp is not None and m.min_sp > 0xFF00

    def test_no_bdos_symbol(self, program):
        names = set(symbols(program))
        for bad in ("_EXIT", "_PUTCHAR", "_PRINTF", "_PUTS", "_MALLOC",
                    "__CONOUT", "_ABORT"):
            assert bad not in names
        # and no CALL 5 in the image
        img = (program / "t.bin").read_bytes()
        assert b"\xcd\x05\x00" not in img

    def test_bss_temporaries_are_not_in_the_rom_data(self, tmp_path):
        r = compile_rom(tmp_path, """
volatile long a = 100000, b = 7;
volatile long q, r;
int main(void) { q = a / b; r = a % b; return 0; }
""")
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        sy = symbols(tmp_path)
        # __div32_dvd and __rem32 live in the BSS, not in the copied data
        for name in ("__DIV32_DVD", "__REM32", "__TMP32"):
            if name in sy:
                assert sy["__BSS_START"] <= sy[name] < sy["__BSS_END"], name
        m = machine(tmp_path)
        assert m.run(max_tstates=2_000_000).reason == "halt"


class TestUserHandlers:
    def test_int_handler_vector_and_irq(self, tmp_path):
        (tmp_path / "isr.mac").write_text(
            "\t.Z80\n\tCSEG\n\tPUBLIC\t__int_handler\n"
            "__int_handler:\n\tPUSH\tAF\n\tLD\tA,(_isr_ticks)\n\tINC\tA\n"
            "\tLD\t(_isr_ticks),A\n\tPOP\tAF\n\tEI\n\tRETI\n"
            "\tPUBLIC\t_isr_ticks\n\tCOMMON\t//\n_isr_ticks:\tDS\t1\n\tEND\n")
        r = compile_rom(tmp_path, """
extern volatile unsigned char isr_ticks;
int main(void)
{
    asm("\\tei");
    while (isr_ticks < 3)
        ;
    asm("\\tdi");
    return 0;
}
""", files=[str(tmp_path / "isr.mac")])
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        img = (tmp_path / "t.bin").read_bytes()
        assert img[0x38] == 0xC3                       # jp __int_handler
        m = machine(tmp_path)
        m.add_irq_every(3000, start=3000)
        res = m.run(max_tstates=500_000)
        assert res.reason == "halt"
        assert m.peek8("_isr_ticks") == 3

    def test_nmi_handler_vector(self, tmp_path):
        (tmp_path / "nmi.mac").write_text(
            "\t.Z80\n\tCSEG\n\tPUBLIC\t__nmi_handler\n"
            "__nmi_handler:\n\tRETN\n\tEND\n")
        r = compile_rom(tmp_path, "int main(void) { return 0; }\n",
                        files=[str(tmp_path / "nmi.mac")])
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        img = (tmp_path / "t.bin").read_bytes()
        assert img[0x66] == 0xC3
        assert img[0x38:0x3B] == b"\xfb\xed\x4d"       # INT stays the default

    def test_stack_top_option(self, tmp_path):
        r = compile_rom(tmp_path, "int main(void) { return 0; }\n",
                        "--stack-top", "0FF00H")
        assert r.returncode == 0, r.stderr
        assert "LD\tSP,0FF00H" in (tmp_path / "t.mac").read_text()


class TestDiagnostics:
    @pytest.mark.parametrize("body,name", [
        ('#include <stdio.h>\nint main(void) { puts("x"); return 0; }', "puts"),
        ('#include <stdio.h>\nint main(void) { printf("%d", 1); return 0; }', "printf"),
        ('#include <stdlib.h>\nint main(void) { exit(1); return 0; }', "exit"),
        ('#include <stdlib.h>\nint main(void) { abort(); return 0; }', "abort"),
        ('#include <stdio.h>\nint main(void) { return getchar(); }', "getchar"),
        ('#include <stdio.h>\nint main(void) { putchar(65); return 0; }', "putchar"),
        ('#include <stdio.h>\nint main(void) { return fopen("a", "r") != 0; }', "fopen"),
        ('#include <stdlib.h>\nint main(void) { return malloc(4) != 0; }', "malloc"),
    ])
    def test_cpm_only_call_is_refused(self, tmp_path, body, name):
        r = compile_rom(tmp_path, body)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "uc80: error: --target=rom" in r.stderr
        assert name in r.stderr
        assert "internal error" not in r.stderr

    def test_main_with_arguments_is_refused(self, tmp_path):
        r = compile_rom(tmp_path, "int main(int argc, char **argv) { return argc; }")
        assert r.returncode == 1
        assert "main() has parameters" in r.stderr

    def test_ffreestanding_is_the_same_target(self, tmp_path):
        src = tmp_path / "t.c"
        src.write_text('#include <stdio.h>\nint main(void) { puts("x"); return 0; }')
        r = subprocess.run(uc80_cmd("-ffreestanding", str(src), "-o",
                                    str(tmp_path / "t.mac")),
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 1
        assert "--target=rom" in r.stderr

    def test_cpm_target_is_unchanged(self, tmp_path):
        src = tmp_path / "t.c"
        src.write_text('#include <stdio.h>\nint main(void) { puts("x"); return 0; }')
        r = subprocess.run(uc80_cmd(str(src), "-o", str(tmp_path / "t.mac")),
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        assert "crt0.mac - C Runtime Startup for CP/M" in (tmp_path / "t.mac").read_text()

    def test_stack_top_needs_rom_target(self, tmp_path):
        src = tmp_path / "t.c"
        src.write_text("int main(void) { return 0; }")
        r = subprocess.run(uc80_cmd("--stack-top", "100", str(src)),
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 2


class TestLibcRom:
    def test_library_has_no_console_or_process_modules(self):
        from uc80 import rom
        safe, unsafe = rom.rom_modules(LIB)
        for key in ("lc/lc_strlen", "lc/lc_memcpy", "lc/lc_qsort",
                    "lc/lc_strtol", "lc/lc_rand", "lc/lc_bsearch",
                    "rt/rt_mul16", "rt/rt_fadd"):
            assert key in safe, key
        for key in ("lc/lc_puts", "lc/lc_printf_core", "lc/lc_exit",
                    "lc/lc_malloc", "lc/lc_fopen", "lc/lc_getchar",
                    "lc/lc_abort"):
            assert key in unsafe, key

    def test_every_library_module_is_free_of_bdos_symbols(self):
        out = subprocess.run(["ulib80", "-l", str(LIB / "libc_rom.lib")],
                             capture_output=True, text=True)
        text = out.stdout + out.stderr
        for bad in ("_puts", "_putchar", "_printf", "_exit", "_malloc",
                    "__conout"):
            assert bad not in text.split(), bad


class TestLibcInRom:
    """sscanf, the strto* functions and the math library are in
    libc_rom.lib, and they work with the ROM start-up code (data copied
    from ROM, BSS cleared)."""

    SOURCE = """
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
volatile struct {
    unsigned long magic;
    int i[4];
    long l[2];
    float f[8];
} out = {0xC0FFEE11UL};
int main(void)
{
    int a = 0, n = 0, r;
    float f = 0;
    char *e;
    r = sscanf("  42 %", "%d %%", &a);
    out.i[0] = r;
    out.i[1] = a;
    r = sscanf("7 2.5", "%d %f%n", &a, &f, &n);
    out.i[2] = r * 100 + a;
    out.i[3] = n;
    out.f[0] = f;
    out.l[0] = strtol("-0x1f", &e, 0);
    out.l[1] = e != 0 && *e == 0;
    out.f[1] = strtod("1e3", 0);
    out.f[2] = sqrt(2.0f);
    out.f[3] = sin(1.0f);
    out.f[4] = pow(2.0f, 0.5f);
    out.f[5] = floor(-2.5f);
    out.f[6] = (float) strtol("123456", 0, 10);
    return 0;
}
"""

    def test_scanf_strtol_and_math_run_in_a_rom(self, tmp_path):
        import struct
        r = compile_rom(tmp_path, self.SOURCE)
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path, ram_size="800")
        m = machine(tmp_path)
        assert m.run(max_tstates=60_000_000).reason == "halt"
        ram = m.peek(0xF800, 0x800)
        at = ram.index(struct.pack("<L", 0xC0FFEE11))
        ints = struct.unpack_from("<4h", ram, at + 4)
        longs = struct.unpack_from("<2l", ram, at + 12)
        floats = struct.unpack_from("<8f", ram, at + 20)
        assert ints == (1, 42, 207, 5)
        assert longs == (-31, 1)
        assert floats[0] == 2.5 and floats[1] == 1000.0
        assert abs(floats[2] - 2 ** 0.5) < 2e-7
        assert abs(floats[3] - 0.8414709848) < 2e-7
        assert abs(floats[4] - 2 ** 0.5) < 2e-7
        assert floats[5] == -3.0 and floats[6] == 123456.0


@pytest.mark.skipif(not shutil.which("uc80"), reason="needs the uc80 console script")
class TestSndKernel:
    def test_cmdq_rom_matches_the_cpm_golden_output(self, tmp_path):
        import sndrom
        info = sndrom.build("snd-cmdq", tmp_path, "dev")
        assert info["ok"], info["log"]
        r = sndrom.execute(info, "snd-cmdq")
        assert r["reason"] == "port:FF"
        assert r["text"] == sndrom.golden("snd-cmdq")
        assert r["rom_writes"] == 0

    def test_cmdq_fits_the_neo_geo_ram(self, tmp_path):
        import sndrom
        info = sndrom.build("snd-cmdq", tmp_path, "neogeo")
        assert info["ok"], info["log"]
        assert info["ram"] + 0x100 <= 0x800
        r = sndrom.execute(info, "snd-cmdq")
        assert r["text"] == sndrom.golden("snd-cmdq")
