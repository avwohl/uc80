"""The ROM target: uc80 --target=rom, crt0_rom, libc_rom.lib, the snd-*
kernels as ROM images.  See docs/rom.md.

The tests compile, assemble and link with um80 and ul80 and run the image
in the bench/romrun Z80 runner.  They skip when a tool or a library is
missing.
"""

import re
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
                    "rt/rt_arith16", "rt/rt_float"):
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


CONST_PROGRAM = """
const int ctab[3] = {1, 2, 3};
const char *ptbl[] = {"a", "b"};
const char *const ctbl[] = {"c", "d"};
const char *pstr = "hello";
int wtab[2] = {5, 6};
static const unsigned char ktab[] = {1, 2, 3};
const struct { int a, b; } cstruct = {3, 4};
volatile const int vconst = 3;
volatile int sink;
int main(void)
{
    static const int stat_const[2] = {7, 8};
    static int stat_var[2] = {9, 10};
    sink = ctab[1] + ktab[2] + wtab[1] + cstruct.b + vconst + stat_const[1]
         + stat_var[1] + ((const int[]){11, 12})[1] + ctbl[1][0] + ptbl[1][0] + pstr[0];
    return 0;
}
"""


def segment_of(text, label):
    """CSEG or DSEG: the last segment directive before the label."""
    i = text.index("\n" + label)
    seg = re.findall(r"^\s*(CSEG|DSEG|COMMON)\b", text[:i], re.I | re.M)
    return seg[-1].upper() if seg else None


class TestConstInRom:
    def test_const_objects_are_in_the_rom_and_the_rest_in_ram(self, tmp_path):
        r = compile_rom(tmp_path, CONST_PROGRAM)
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        sy = symbols(tmp_path)
        for name in ("_CTAB", "_CTBL", "_CSTRUCT"):
            assert sy[name] < sy["__DATA_ROM"], name        # ROM
        # a pointer to const data is itself a writable object, and so is
        # an array of such pointers; a volatile const object stays in RAM
        for name in ("_PTBL", "_PSTR", "_WTAB", "_VCONST"):
            assert sy[name] >= 0xF800, name
        assert sy["__DATA_LEN"] == sy["__DATA_END"] - sy["__DATA_RAM"]
        text = (tmp_path / "t.mac").read_text()
        for label in ("_ctab:", "_ctbl:", "_cstruct:", "_ktab:", "@STR1:"):
            assert segment_of(text, label) == "CSEG", label
        for label in ("_ptbl:", "_wtab:", "_pstr:", "_vconst:"):
            assert segment_of(text, label) == "DSEG", label
        assert segment_of(text, "__main_S0:") == "CSEG"      # stat_const
        assert segment_of(text, "__main_S1:") == "DSEG"      # stat_var
        m = machine(tmp_path)
        assert m.run(max_tstates=500_000).reason == "halt"
        assert m.rom_writes == []

    def test_ram_data_is_smaller_than_the_data_in_the_rom_image(self, tmp_path):
        r = compile_rom(tmp_path, CONST_PROGRAM)
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        sy = symbols(tmp_path)
        # only ptbl (4), pstr (2), wtab (4), vconst (2), stat_var (4) and
        # the string pointers are copied; the const tables are not
        assert sy["__DATA_LEN"] <= 20

    def test_string_literal_is_in_the_rom(self, tmp_path):
        r = compile_rom(tmp_path, """
volatile char c;
int main(void) { const char *s = "abc"; c = s[1]; return 0; }
""")
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        sy = symbols(tmp_path)
        assert sy["__DATA_LEN"] == 0
        assert b"abc\x00" in (tmp_path / "t.bin").read_bytes()

    def test_write_through_a_cast_hits_the_rom_write_fault(self, tmp_path):
        r = compile_rom(tmp_path, """
const int ctab[3] = {1, 2, 3};
int main(void)
{
    int *volatile p = (int *) ctab;     /* undefined behavior */
    p[1] = 99;
    return 0;
}
""")
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        m = Machine(ram=(0xF800, 0xFFFF), banks=None, ram_fill="random",
                    sp="poison", track=True, rom_write="fault")
        m.load_rom(str(tmp_path / "t.bin"))
        m.load_symbols(str(tmp_path / "t.sym"))
        res = m.run(max_tstates=500_000)
        assert res.reason == "rom-write"
        assert m.rom_writes and m.rom_writes[0][1] == symbols(tmp_path)["_CTAB"] + 2

    def test_cpm_output_keeps_const_data_in_dseg(self, tmp_path):
        src = tmp_path / "t.c"
        src.write_text(CONST_PROGRAM)
        r = subprocess.run(uc80_cmd(str(src), "-o", str(tmp_path / "t.mac")),
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        text = (tmp_path / "t.mac").read_text()
        assert "__UC80_ROMDATA__" not in text
        assert "_ctab:" in text.split("DSEG")[1]


class TestRootsAndVectors:
    def test_entry_without_main(self, tmp_path):
        r = compile_rom(tmp_path, """
volatile int out;
static int twice(int x) { return x * 2; }
void run(void) { out = twice(21); for (;;) { } }
int dead(void) { return 1; }
""", "--entry", "run")
        assert r.returncode == 0, r.stderr
        text = (tmp_path / "t.mac").read_text()
        assert "CALL\t_run" in text and "_dead:" not in text
        link_rom(tmp_path)
        m = machine(tmp_path)
        res = m.run(max_tstates=100_000)
        assert res.reason in ("budget", "idle")
        assert m.peek16("_out") == 42

    def test_no_main_and_no_entry_is_an_error(self, tmp_path):
        r = compile_rom(tmp_path, "void run(void) { }\n")
        assert r.returncode == 1
        assert "no main()" in r.stderr and "--entry" in r.stderr

    def test_unknown_entry_is_an_error(self, tmp_path):
        r = compile_rom(tmp_path, "int main(void) { return 0; }\n",
                        "--entry", "nothere")
        assert r.returncode == 1
        assert "nothere" in r.stderr

    def test_keep_pragma_keeps_an_unused_function(self, tmp_path):
        r = compile_rom(tmp_path, """
#pragma uc80 keep spare
static int spare(void) { return 7; }
int unused(void) { return 8; }
int main(void) { return 0; }
""")
        assert r.returncode == 0, r.stderr
        text = (tmp_path / "t.mac").read_text()
        assert "_spare:" in text and "_unused:" not in text

    def test_vector_pragma_jumps_to_the_c_function(self, tmp_path):
        r = compile_rom(tmp_path, """
#pragma uc80 vector 38h on_int
#pragma uc80 vector 66h on_nmi
void on_int(void) { }
void on_nmi(void) { }
int main(void) { return 0; }
""")
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        img = (tmp_path / "t.bin").read_bytes()
        sy = symbols(tmp_path)
        assert img[0x38:0x3B] == bytes([0xC3, sy["_ON_INT"] & 255, sy["_ON_INT"] >> 8])
        assert img[0x66:0x69] == bytes([0xC3, sy["_ON_NMI"] & 255, sy["_ON_NMI"] >> 8])

    @pytest.mark.parametrize("pragma", [
        "#pragma uc80 vector 40h f",
        "#pragma uc80 vector 38h",
        "#pragma uc80 keep",
        "#pragma uc80 frobnicate x",
    ])
    def test_bad_pragma_is_an_error(self, tmp_path, pragma):
        r = compile_rom(tmp_path, pragma + "\nvoid f(void) { }\nint main(void) { return 0; }\n")
        assert r.returncode == 1
        assert "#pragma uc80" in r.stderr

    def test_vector_and_assembly_handler_conflict(self, tmp_path):
        (tmp_path / "isr.mac").write_text(
            "\t.Z80\n\tCSEG\n\tPUBLIC\t__int_handler\n__int_handler:\n\tRETI\n\tEND\n")
        r = compile_rom(tmp_path, """
#pragma uc80 vector 38h f
void f(void) { }
int main(void) { return 0; }
""", files=[str(tmp_path / "isr.mac")])
        assert r.returncode == 1
        assert "same vector" in r.stderr

    def test_public_pragma_exports_a_static_and_an_unused_bss_object(self, tmp_path):
        r = compile_rom(tmp_path, """
#pragma uc80 public counter, table, spare
volatile unsigned char counter;
static unsigned char table[4] = {1, 2, 3, 4};
static int spare(void) { return 1; }
int main(void) { return 0; }
""")
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        sy = symbols(tmp_path)
        assert sy["_COUNTER"] >= sy["__BSS_START"]
        assert "_TABLE" in sy and "_SPARE" in sy

    def test_bss_object_is_named_in_the_symbol_file(self, tmp_path):
        r = compile_rom(tmp_path, """
volatile unsigned char ticks;
int main(void) { ticks = 1; return 0; }
""")
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        assert "_TICKS" in symbols(tmp_path)

    def test_assembly_isr_uses_a_c_global_and_a_rom_table(self, tmp_path):
        (tmp_path / "isr.mac").write_text(
            "\t.Z80\n\tCSEG\n\tPUBLIC\t__int_handler\n\tEXTRN\t_counter\n"
            "__int_handler:\n\tPUSH\tAF\n\tLD\tA,(_counter)\n\tINC\tA\n"
            "\tLD\t(_counter),A\n\tPOP\tAF\n\tEI\n\tRETI\n\tEND\n")
        r = compile_rom(tmp_path, """
#pragma uc80 public counter
const int tab[4] = {1, 2, 3, 4};
volatile unsigned char counter;
volatile int sink;
int main(void)
{
    asm("\\tei");
    while (counter < 3)
        ;
    asm("\\tdi");
    sink = tab[2];
    return 0;
}
""", files=[str(tmp_path / "isr.mac")])
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        m = machine(tmp_path)
        m.add_irq_every(3000, start=3000)
        assert m.run(max_tstates=500_000).reason == "halt"
        assert m.peek8("_counter") == 3
        assert m.peek16("_sink") == 3
        assert symbols(tmp_path)["_TAB"] < 0x8000
