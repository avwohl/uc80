"""z88dk 80cc as a benchmark compiler, label "z88dk-80cc".

80cc is the optimising C compiler of z88dk v2.5 (zcc -compiler=80cc).  It
uses the sccz80 calling convention and the classic CP/M library, so the
Z88dk adapter of compilers.py serves unchanged, with these differences:

  * z88dk 2.4 (the tag, ~/src/external/tools/z88dk-2.4) has no 80cc.  This
    adapter uses the official macOS nightly zip
        http://nightly.z88dk.org/z88dk-osx-latest.zip
    unpacked to ~/src/external/tools/z88dk-nightly/z88dk (fetched
    2026-10-24, sha256 8302c127dd46ac104eb466329fe45b26773a8f97ed16835f7f6c907382cf45be).
    The directory has PROVENANCE.txt.  Set Z88DK80CC=<dir> to use another
    tree.  The nightly is never used for the other z88dk labels, so their
    numbers stay those of 2.4.
  * Flags: the z88dk wiki 80cc lines, -O3 for speed.  Size mode adds
    --opt-code-size.  Locals come from the stack pointer (the 80cc
    default); -fframe-pointer is not used.
  * Math: from v2.5, -lm is math32 (IEEE 4 byte), the library of the
    wiki's 80cc float rows.  A program that uses no float links nothing.
  * 80cc does not support struct assignment; programs that need a switch
    for that use NOSTRUCTASSIGN (see bench/programs.d/z88dk_int.toml).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compilers import TOOLS, Z88dk  # noqa: E402


class Z88dk80cc(Z88dk):
    def __init__(self):
        super().__init__("sccz80")
        self.backend = "80cc"
        self.name = "z88dk-80cc"
        self.root = Path(os.environ.get(
            "Z88DK80CC", TOOLS / "z88dk-nightly" / "z88dk"))

    def available(self) -> bool:
        return ((self.root / "bin" / "zcc").exists()
                and (self.root / "bin" / "z88dk-80cc").exists())

    def version(self) -> str:
        return "z88dk nightly (v2.5 dev), 80cc"

    def _flags(self, mode) -> list[str]:
        flags = ["-compiler=80cc", "-O3"]
        return flags + (["--opt-code-size"] if mode == "size" else [])


COMPILERS = [Z88dk80cc()]
