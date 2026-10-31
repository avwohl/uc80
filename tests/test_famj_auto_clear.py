"""Family J step 5: the shared automatic storage is not cleared at start-up
(docs/startup_fast.md).

The tests change the generated text: before the BSS clear, they fill the
whole BSS with 5AH.  A static array must be 0 after the clear.  The value
of an automatic variable that nobody wrote shows what the clear did to the
area ??AUTO: 5AH when the start-up code skips the area, 0 when it clears
the area.
"""

import re

import pytest

from .test_famj_startup import compile_c, run_raw
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>

unsigned char zeros[40];
int counter;

static unsigned char probe(void)
{
    unsigned char a[8];
    return a[3];            /* nobody wrote the byte: the poison, or 0 */
}

static int work(int n)
{
    int a[6], i, s = 0;
    for (i = 0; i < 6; i++)
        a[i] = n + i;
    for (i = 0; i < 6; i++)
        s += a[i] * (i + 1);
    return s;
}

int main(void)
{
    int i, bad = 0;
    unsigned char p = probe();
    for (i = 0; i < 40; i++)
        bad += zeros[i];
    counter += work(3);
    printf("probe=%d bad=%d counter=%d\n", p, bad, counter);
    return 0;
}
"""

POISON = """\
\tEXTRN\t__bss_start
\tEXTRN\t__bss_end
@@POISON@@
"""

FILL = ("\tLD\tHL,__bss_start\n\tLD\tDE,__bss_start+1\n"
        "\tLD\tBC,__bss_end-__bss_start-1\n\tLD\t(HL),05AH\n\tLDIR\n")


def poisoned(tmp_path, *flags, src=SRC):
    mac = compile_c(tmp_path, src, *flags)
    text = mac.read_text()
    # the first instruction of the clear (the size of the BSS)
    m = re.search(r"^\tLD\tBC,__bss_end-(\?\?AUTOEND|__bss_start)-1\n", text,
                  re.M | re.I)
    assert m, "the start-up code has no BSS clear"
    text = text[:m.start()] + FILL + text[m.start():]
    text = "\tEXTRN\t__bss_start\n\tEXTRN\t__bss_end\n" + text
    mac.write_text(text)
    return mac, text


def test_auto_area_is_not_cleared(tmp_path):
    mac, text = poisoned(tmp_path)
    assert "??AUTOEND" in text
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out == "probe=90 bad=0 counter=" + str(sum((3 + i) * (i + 1)
                                                       for i in range(6))) \
        + "\n"


def test_switch_clears_the_area(tmp_path):
    mac, text = poisoned(tmp_path, "--no-skip-auto-clear")
    assert "??AUTOEND" not in text
    out = run_raw(tmp_path, mac).decode("latin-1").replace("\r", "")
    assert out.startswith("probe=0 bad=0 ")


def test_other_start_up_files_keep_the_layout(tmp_path):
    text = compile_c(tmp_path, SRC, "--no-fast-bss-clear").read_text()
    assert "??AUTOEND" not in text
    assert "/AUTO/" not in text


def test_area_comes_first_in_the_bss(tmp_path):
    import subprocess
    from .test_struct_return_abi import LIB_DIR
    mac = compile_c(tmp_path, SRC)
    rel, com, sym = (tmp_path / "t.rel", tmp_path / "t.com",
                     tmp_path / "t.sym")
    subprocess.run(["um80", "-g", str(mac), "-o", str(rel)], check=True,
                   capture_output=True, text=True, timeout=300)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com),
                    "-S", str(sym)], check=True, capture_output=True,
                   text=True, timeout=300)
    syms = {}
    for line in sym.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            syms[parts[1].upper()] = int(parts[0], 16)
    assert syms["??AUTO"] == syms["__BSS_START"]
    assert syms["??AUTOEND"] > syms["??AUTO"]
    for name in ("_ZEROS", "_COUNTER", "__EXIT_HOOK"):
        assert syms["??AUTOEND"] <= syms[name] < syms["__BSS_END"], name
