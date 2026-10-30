"""Family J step 1: start-up, console and runtime shortcuts.

  docs/startup_fast.md       the BSS clear of crt0
  docs/void_putchar.md       putchar(c); as a statement
  docs/runtime_shortcuts.md  __div32 for a small dividend and a big divisor

Every program runs under cpmemu with a timeout.  The division programs also
run on the host compiler, and the two outputs must be the same.
"""

import re
import subprocess
import sys

import pytest

from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO, _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")


def compile_c(tmp_path, source, *flags, name="t"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(source)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *map(str, flags),
         "-o", str(mac)],
        capture_output=True, text=True, cwd=str(REPO), timeout=300)
    assert r.returncode == 0, r.stderr
    return mac


def run_raw(tmp_path, mac, name="t"):
    """Assemble, link and run; the output bytes, CR kept."""
    rel, com = tmp_path / (name + ".rel"), tmp_path / (name + ".com")
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True,
                   capture_output=True, text=True, timeout=300)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True, timeout=300)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120)
    out = run.stdout
    # drop the emulator's own banner and exit line
    out = re.sub(rb"^CPU mode: [^\n]*\n", b"", out)
    out = re.sub(rb"^Loaded [^\n]*\n", b"", out)
    out = re.sub(rb"Program exit[^\n]*\n?$", b"", out)
    return out


def host_expect(tmp_path, src):
    c_file = tmp_path / "host.c"
    c_file.write_text(src)
    exe = tmp_path / "host"
    r = subprocess.run(["cc", "-w", "-o", str(exe), str(c_file)],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        pytest.skip("no host compiler")
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=120).stdout


# ---- 1a: the BSS clear --------------------------------------------------

BSS_SRC = r"""
#include <stdio.h>
#ifndef N
#define N 300
#endif
extern unsigned char _bss_start[], _bss_end[];
unsigned char iffseen;
unsigned char arr[N];
int main(void)
{
    unsigned char *p;
    int bad = 0, nb = 0;
    for (p = _bss_start; p != _bss_end; p++) {
        nb++;
        if (*p && p != &iffseen)
            bad++;
    }
    for (p = _bss_end; p != _bss_end + 16; p++)
        if (*p != 0x5A)
            bad += 100;
    for (p = _bss_start - 16; p != _bss_start; p++)
        if (*p != 0x5A)
            bad += 1000;
    printf("bad=%d iff=%d big=%d\n", bad, iffseen, nb > 256);
    return 0;
}
"""


def poisoned_startup(tmp_path, crt0_name, ei):
    """A copy of the start-up code that fills the BSS and 16 bytes on each
    side with 5AH, sets or clears the interrupt flag first, and stores the
    interrupt flag (as 0 or 1) after the BSS clear."""
    text = (LIB_DIR / crt0_name).read_text()
    poison = (
        "\tDI\n"
        "\tLD\tHL,__bss_start-16\n"
        "\tLD\tDE,__bss_start-15\n"
        "\tLD\tBC,__bss_end-__bss_start+31\n"
        "\tLD\t(HL),5AH\n"
        "\tLDIR\n" + ("\tEI\n" if ei else ""))
    assert "\tLD\tSP,HL\n" in text and "__nobss:\n" in text
    text = text.replace("\tLD\tSP,HL\n", "\tLD\tSP,HL\n" + poison, 1)
    text = text.replace(
        "__nobss:\n",
        "__nobss:\n\tLD\tA,I\n\tLD\tA,0\n\tJP\tPO,__iffx\n\tINC\tA\n"
        "__iffx:\n\tLD\t(_iffseen),A\n", 1)
    path = tmp_path / "crt0_poison.mac"
    path.write_text(text)
    return path


# BSS size is N + 5 here; 250, 251 and 252 give 255, 256 and 257 bytes.
BSS_SIZES = [1, 2, 200, 250, 251, 252, 253, 300, 4095, 4097, 20000]


@pytest.mark.parametrize("crt0", ["crt0.mac", "crt0_small.mac", "crt0_slow.mac"])
@pytest.mark.parametrize("ei", [True, False])
@pytest.mark.parametrize("n", BSS_SIZES)
def test_bss_clear(tmp_path, crt0, ei, n):
    startup = poisoned_startup(tmp_path, crt0, ei)
    mac = compile_c(tmp_path, BSS_SRC, "--startup-lib", startup,
                    "--no-shared-storage", f"-DN={n}")
    out = run_raw(tmp_path, mac).decode("latin-1")
    big = 1 if n + 5 > 256 else 0
    assert out.strip() == f"bad=0 iff={1 if ei else 0} big={big}", out


def test_startup_variant_follows_the_bss_size(tmp_path):
    small = "int a[4]; int main(void) { return a[0]; }\n"
    big = "int a[400]; int main(void) { return a[0]; }\n"
    t_small = compile_c(tmp_path, small).read_text()
    t_big = compile_c(tmp_path, big, name="b").read_text()
    t_old = compile_c(tmp_path, big, "--no-fast-bss-clear",
                      name="o").read_text()
    assert "__bss_big" not in t_small and "LDIR" in t_small.upper()
    assert "__bss_big:" in t_big
    assert "__bss_big" not in t_old and "LDIR" in t_old.upper()


def test_bss_clear_normal_startup_runs(tmp_path):
    """The default start-up code, no poison: static data are zero."""
    src = r"""
#include <stdio.h>
static int tab[700];
static char c;
int main(void)
{
    int i, s = 0;
    for (i = 0; i < 700; i++)
        s |= tab[i];
    printf("%d %d\n", s, c);
    return 0;
}
"""
    for flags in ((), ("--no-fast-bss-clear",)):
        mac = compile_c(tmp_path, src, *flags)
        assert run_raw(tmp_path, mac).decode().strip() == "0 0"


# ---- 1b: putchar as a statement ------------------------------------------

PUT_SRC = r"""
#include <stdio.h>
static const char seq[] = "ab\n\r\ncd\r\n\n\nef\r";
char gc = 'g';
int gi = 'h';
unsigned char guc = 100;

static void fill(int n, char c) { while (n-- > 0) putchar(c); }

int main(void)
{
    int i, r;
    char lc = 'L';
    for (i = 0; seq[i]; i++)
        putchar(seq[i]);
    (void) putchar('\n');
    putchar(gc);
    putchar(gi);
    putchar(lc + 1);
    putchar(i > 5 ? 'Y' : 'N');
    putchar(guc);
    putchar(0x141);
    (void)(void) putchar('Z');
    fill(3, '.');
    fill(2, '\n');
    r = putchar('R');
    putchar(r);
    if (putchar('!') == '!')
        putchar('T');
    for (i = 0; i < 3; putchar('0' + i), i++)
        ;
    putchar('\n');
    return 0;
}
"""


def translate(data: bytes, crlf: bool) -> bytes:
    """What the console shows for the bytes the program writes."""
    out, last = bytearray(), 0
    for b in data:
        if b == 10 and crlf and last != 13:
            out.append(13)
        out.append(b)
        last = b
    return bytes(out)


def put_expected(crlf):
    seq = b"ab\n\r\ncd\r\n\n\nef\r"
    data = (seq + b"\n" + b"g" + b"h" + b"M" + b"Y" + bytes([100])
            + b"A" + b"Z" + b"..." + b"\n\n" + b"R" + b"R" + b"!" + b"T"
            + b"012" + b"\n")
    return translate(data, crlf)


@pytest.mark.parametrize("flags", [
    (), ("--no-void-putchar",), ("-O0",), ("--no-register-locals",),
    ("--no-shared-storage",), ("--int=32",), ("--no-register-cc",),
], ids=lambda f: " ".join(f) or "default")
def test_putchar_statement(tmp_path, flags):
    mac = compile_c(tmp_path, PUT_SRC, *flags)
    assert run_raw(tmp_path, mac) == put_expected(True)


def test_putchar_statement_no_crlf(tmp_path):
    mac = compile_c(tmp_path, PUT_SRC, "--no-crlf")
    assert run_raw(tmp_path, mac) == put_expected(False)


def test_putchar_statement_shape(tmp_path):
    on = compile_c(tmp_path, PUT_SRC).read_text()
    off = compile_c(tmp_path, PUT_SRC, "--no-void-putchar",
                    name="o").read_text()
    assert "call\t__putchar_v" in on
    assert "__putchar_v" not in off
    # the value of 'r = putchar(...)' and of the comparison is used
    assert "call\t_putchar" in on


def test_user_putchar_is_not_replaced(tmp_path):
    src = r"""
extern int puts(const char *);
static int count;
int putchar(int c) { count += c; return c; }
int main(void)
{
    putchar(1);
    putchar(2);
    return count == 3 ? 0 : 1;
}
"""
    text = compile_c(tmp_path, src).read_text()
    assert "__putchar_v" not in text


# ---- 1c: __div32 shortcut ----------------------------------------------------

DIV_SRC = r"""
#include <stdio.h>
#include <stdint.h>

static const uint32_t U[] = {0UL, 1UL, 7UL, 255UL, 256UL, 65535UL, 65536UL,
    65537UL, 100000UL, 12345678UL, 0x7FFFFFFFUL, 0x80000000UL, 0xFFFFFFFFUL};
static const uint32_t D[] = {1UL, 2UL, 3UL, 255UL, 256UL, 65535UL, 65536UL,
    65537UL, 99999UL, 100000UL, 0xFFFFFFUL, 0x1000000UL, 0x1000001UL,
    0x7FFFFFFFUL, 0x80000000UL, 0xFFFFFFFFUL};
static const int32_t S[] = {0L, 1L, -1L, 7L, -7L, 65535L, -65535L, 65536L,
    -65536L, 70000L, -70000L, 100000L, -100000L, 2147483647L, -2147483647L};
static const int32_t T[] = {1L, -1L, 3L, -3L, 65535L, -65535L, 65536L,
    -65536L, 65537L, -65537L, 100000L, -100000L, 16777216L, -16777216L,
    2147483647L, -2147483647L};

int main(void)
{
    unsigned i, j;
    uint32_t a, b;
    int32_t x, y;
    unsigned long chk = 0;
    for (i = 0; i < sizeof U / sizeof U[0]; i++)
        for (j = 0; j < sizeof D / sizeof D[0]; j++) {
            a = U[i]; b = D[j];
            printf("%lu %lu %lu %lu\n", (unsigned long)a, (unsigned long)b,
                   (unsigned long)(a / b), (unsigned long)(a % b));
        }
    for (i = 0; i < sizeof S / sizeof S[0]; i++)
        for (j = 0; j < sizeof T / sizeof T[0]; j++) {
            x = S[i]; y = T[j];
            printf("%ld %ld %ld %ld\n", (long)x, (long)y, (long)(x / y),
                   (long)(x % y));
        }
    (void)chk;
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-rt-shortcuts",), ("-O0",), ("--no-whole-program",),
], ids=lambda f: " ".join(f) or "default")
def test_div32_against_host(tmp_path, flags):
    expect = host_expect(tmp_path, DIV_SRC)
    mac = compile_c(tmp_path, DIV_SRC, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == expect


DIV_PURE_SRC = r"""
extern int puts(const char *);
unsigned long a = 70000UL, b = 100000UL, c = 123456789UL;
int main(void)
{
    unsigned long q = a / b, r = a % b;
    unsigned long q2 = c / 70000UL;
    puts(q == 0 && r == 70000UL && q2 == 1763 ? "ok" : "bad");
    return 0;
}
"""


def test_div32_shortcut_text_and_option(tmp_path):
    on = compile_c(tmp_path, DIV_PURE_SRC).read_text()
    off = compile_c(tmp_path, DIV_PURE_SRC, "--no-rt-shortcuts",
                    name="o").read_text()
    assert "__div32:" in on and "__div32:" in off
    assert "rt-shortcut" in on
    assert "rt-shortcut" not in off
    for mac in (tmp_path / "t.mac", tmp_path / "o.mac"):
        assert run_raw(tmp_path, mac).decode().strip() == "ok"


# ---- locals that live across putchar statements ---------------------------

LIVE_SRC = r"""
#include <stdio.h>

static int walk(int n, int step)
{
    int i, s = 0;
    unsigned t = 1;
    for (i = 0; i < n; i += step) {
        putchar('a' + (i % 26));
        s += i;
        putchar('0' + (s % 10));
        t = (t * 3 + (unsigned)(s & 7)) & 0x7FFF;
        if ((t & 3) == 0)
            putchar('\n');
    }
    return s + (int)t;
}

static void digits(unsigned v, int width)
{
    char buf[8];
    int n = 0;
    do { buf[n++] = (char)('0' + v % 10); v /= 10; } while (v && n < 8);
    while (width-- > n)
        putchar(' ');
    while (n > 0)
        putchar(buf[--n]);
}

int main(void)
{
    int r = walk(40, 3);
    putchar('\n');
    digits((unsigned)r, 8);
    putchar('\n');
    digits(12345u, 3);
    putchar('\n');
    return 0;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-void-putchar",), ("-O0",), ("--no-register-locals",),
    ("--no-loop-rotate", "--no-pair-cmp"),
], ids=lambda f: " ".join(f) or "default")
def test_locals_survive_putchar_statements(tmp_path, flags):
    expect = host_expect(tmp_path, LIVE_SRC)
    mac = compile_c(tmp_path, LIVE_SRC, *flags)
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == expect
