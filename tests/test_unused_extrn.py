"""End-to-end tests for dropping unused EXTRNs.

uc80 declares every prototype in every included header as EXTRN.  um80
0.3.50 and later keep an unreferenced EXTRN, as MACRO-80 does, and ul80
then links the library module that defines it: puts("hello") linked to
9,088 bytes instead of 256.  main.py now drops every EXTRN nothing uses,
as its last step.  These tests build, link and run real programs, because
the failure modes are an oversized .com or an "Undefined symbol" from um80,
neither of which shows in the generated text alone.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "src" / "uc80" / "lib"
CPMEMU = Path(__file__).resolve().parent.parent.parent / "cpmemu" / "src" / "cpmemu"

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
         and (LIB_DIR / "libc.lib").exists()
         and (LIB_DIR / "runtime.lib").exists()),
    reason="um80/ul80/cpmemu or built libraries not available")


def build_and_run(tmp_path, files, *args):
    """Write `files` (name -> text), compile the .c/.mac ones, run it.

    Returns (com_size, stdout).
    """
    inputs = []
    for name, text in files.items():
        (tmp_path / name).write_text(text)
        if name.endswith((".c", ".mac")):
            inputs.append(str(tmp_path / name))
    mac = tmp_path / "out.mac"
    cc = subprocess.run([sys.executable, "-m", "uc80.main", *inputs, *args,
                         "-o", str(mac)], capture_output=True, text=True,
                        cwd=tmp_path)
    assert cc.returncode == 0, cc.stderr
    asm = subprocess.run(["um80", str(mac), "-o", str(tmp_path / "out.rel")],
                         capture_output=True, text=True, cwd=tmp_path)
    assert asm.returncode == 0, asm.stdout + asm.stderr
    com = tmp_path / "out.com"
    subprocess.run(["ul80", str(tmp_path / "out.rel"),
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", str(com)], check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         text=True, timeout=30)
    return com.stat().st_size, run.stdout


def test_hello_links_no_unused_libc(tmp_path):
    size, out = build_and_run(tmp_path, {"hello.c": (
        '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n'
        'int main(void) { puts("hello"); return 0; }\n')})
    assert "hello" in out
    assert size == 256


def test_appended_mac_keeps_header_extrn(tmp_path):
    """A .mac that jumps to a libc function C never calls still links.

    The .mac has no EXTRN of its own; the one string.h generates must
    survive, which means pruning has to run after the .mac is appended.
    """
    _, out = build_and_run(tmp_path, {
        "use.c": ('#include <stdio.h>\n#include <string.h>\n'
                  'int asmlen(const char *s);\n'
                  'int main(void) { printf("%d\\n", asmlen("hello"));'
                  ' return 0; }\n'),
        "use_a.mac": ("\t.z80\n\tpublic\t_asmlen\n\tcseg\n"
                      "_asmlen:\n\tjp\t_strlen\n\tend\n")})
    assert "5" in out


def test_asm_include_keeps_extrns(tmp_path):
    """An included asm file can use names this module cannot see."""
    _, out = build_and_run(tmp_path, {
        "helper.inc": "\tpublic\t_alen\n_alen:\n\tjp\t_strlen\n",
        "inc.c": ('#include <stdio.h>\n#include <string.h>\n'
                  'int alen(const char *s);\n'
                  '__asm__("\\tinclude\\thelper.inc");\n'
                  'int main(void) { printf("%d\\n", alen("hello"));'
                  ' return 0; }\n')})
    assert "5" in out


def test_asm_macro_concatenation_keeps_extrns(tmp_path):
    """A macro that builds `_strlen` from `_&NAME` still links."""
    _, out = build_and_run(tmp_path, {"macro.c": (
        '#include <stdio.h>\n#include <string.h>\n'
        'int alen(const char *s);\n'
        '__asm__("JPTO\\tMACRO\\tNAME\\n\\tjp\\t_&NAME\\n\\tENDM\\n'
        '\\tpublic\\t_alen\\n_alen:\\n\\tJPTO\\tstrlen");\n'
        'int main(void) { printf("%d\\n", alen("hello")); return 0; }\n')})
    assert "5" in out


def test_malloc_does_not_overwrite_globals(tmp_path):
    """The heap starts after the whole program, BSS included.

    It used to start at a word in lc_malloc's own DSEG, below the
    program's later data and BSS; the smaller links made that hit even
    malloc(300).
    """
    _, out = build_and_run(tmp_path, {"heap.c": (
        '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n'
        'int g;\nstatic int s = 77;\n'
        'int main(void) {\n'
        '    char *p; g = 1234;\n'
        '    p = malloc(1000);\n'
        '    if (p) memset(p, 0, 1000);\n'
        '    printf("g=%d s=%d ok=%d\\n", g, s, p != 0);\n'
        '    return 0;\n}\n')})
    assert "g=1234 s=77 ok=1" in out
