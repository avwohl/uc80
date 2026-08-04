"""An appended .mac reaches the assembler byte for byte.

Inline asm() was bracketed with the optimizer-barrier markers; a .mac file
passed on the command line was not, even though it is the same thing --
hand-written assembly whose author has already chosen the instructions.
The peephole therefore rewrote it, three ways:

  - it fused `LD A,(HL)` + `LD C,A` into `LD C,(HL)` with A still live
    afterwards, giving a silently wrong answer from a clean build;
  - when a label sat between the two fused instructions the label went
    with them, so any jump to it failed to assemble -- turning a working
    helper into a hard um80 error that only -O0 avoided;
  - ORG and ASEG were discarded, relocating a helper written for a fixed
    address into CSEG with no diagnostic.

All three are the same omission, so all three are covered here.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

# A C program with BSS, so the splice placement is exercised too.
MAIN_C = """#include <stdio.h>
extern unsigned int helper(void);
static int big[64];
int main(void){ big[0] = 1; printf("v=%u\\n", helper()); return 0; }
"""

# LD A,(HL) / LD C,A is the pattern the peephole fuses.  A is read again
# afterwards, so fusing changes the result.
FUSE_MAC = """\t.z80
\tCSEG
\tPUBLIC\t_helper
_helper:
\tLD\tHL,tbl
\tLD\tA,(HL)
\tLD\tC,A
\tLD\tB,0
\tLD\tHL,200
\tADD\tHL,BC
\tRET
tbl:\tDB\t27
\tEND
"""

# The same fusion with a label between the two instructions.
LABEL_MAC = """\t.z80
\tCSEG
\tPUBLIC\t_helper
_helper:
\tLD\tHL,4660
kloop:\tLD\tA,(HL)
\tLD\tC,A
\tLD\tHL,4660
\tRET
\tJP\tkloop
\tEND
"""

ORG_MAC = """\t.z80
\tASEG
\tORG\t0F000H
\tPUBLIC\t_athigh
_athigh:
\tLD\tHL,1234
\tRET
\tCSEG
\tPUBLIC\t_helper
_helper:
\tLD\tHL,5678
\tRET
\tEND
"""


def compile_with_mac(tmp_path, c_src, mac_src, *extra_args, name="prog"):
    (tmp_path / (name + ".c")).write_text(c_src)
    (tmp_path / "helper.mac").write_text(mac_src)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(tmp_path / (name + ".c")),
         str(tmp_path / "helper.mac"), "--printf", "int",
         "-o", str(mac), *extra_args],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return mac.read_text()


class TestTextIsUntouched:
    def test_fusable_pair_survives(self, tmp_path):
        code = compile_with_mac(tmp_path, MAIN_C, FUSE_MAC)
        assert "LD\tA,(HL)" in code
        assert "LD\tC,A" in code
        assert "ld c,(hl)" not in code

    def test_label_between_the_pair_survives(self, tmp_path):
        code = compile_with_mac(tmp_path, MAIN_C, LABEL_MAC)
        assert "kloop:" in code

    def test_org_and_aseg_survive(self, tmp_path):
        code = compile_with_mac(tmp_path, MAIN_C, ORG_MAC)
        upper = code.upper()
        assert "ASEG" in upper
        assert "ORG\t0F000H" in upper

    def test_survives_asm_dce_too(self, tmp_path):
        """asm_dce honours the same markers; check it, not just the peephole."""
        code = compile_with_mac(tmp_path, MAIN_C, LABEL_MAC)
        assert "kloop:" in code
        code = compile_with_mac(tmp_path, MAIN_C, LABEL_MAC, "--no-asm-dce")
        assert "kloop:" in code


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestOnEmulator:
    def build_and_run(self, tmp_path, mac_src, *extra_args):
        compile_with_mac(tmp_path, MAIN_C, mac_src, *extra_args)
        mac, rel, com = (tmp_path / ("prog" + e)
                         for e in (".mac", ".rel", ".com"))
        r = subprocess.run(["um80", str(mac), "-o", str(rel)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             timeout=60, env=env)
        return run.stdout.decode("latin-1").replace("\r", "")

    def test_fused_pair_gave_a_wrong_answer(self, tmp_path):
        """200 + 27; the fusion dropped the load into A and returned 200."""
        assert self.build_and_run(tmp_path, FUSE_MAC) == "v=227\n"

    def test_same_answer_at_O0(self, tmp_path):
        """-O0 was the workaround, so both must now agree."""
        assert (self.build_and_run(tmp_path, FUSE_MAC)
                == self.build_and_run(tmp_path, FUSE_MAC, "-O0"))

    def test_label_case_assembles_and_runs(self, tmp_path):
        assert self.build_and_run(tmp_path, LABEL_MAC) == "v=4660\n"
