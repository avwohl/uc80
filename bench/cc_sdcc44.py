"""SDCC 4.4.0 as a second SDCC compiler, label "sdcc-4.4".

4.4.0 is the SDCC version that ngdevkit pins
(github.com/dciabrin/ngdevkit-toolchain, SRC_SDCC=sdcc-src-4.4.0).
Built from the official sdcc-src-4.4.0.tar.bz2
(sha256 ae8c12165eb17680dff44b328d8879996306b7241efa3a83b2e3b2d2f7906a75)
with only the z80 port, no ucsim, no sdcdb, into
$UC80_BENCH_TOOLS/sdcc-4.4.0 (default ~/src/external/tools/sdcc-4.4.0).
Sdcc._bin takes sdasz80 from the same bin directory, and the sdcc driver
finds its own sdldz80 and share/sdcc/lib/z80, so 4.4 and 4.6 never mix.

The label has a dot.  z88dk-ticks picks CP/M mode by the first dot in
the path it gets, so run.py runs ticks in the image's directory with the
bare name "prog.com" (see run_ticks); no copy of the image is needed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from compilers import TOOLS, Sdcc  # noqa: E402

COMPILERS = [Sdcc(exe=str(TOOLS / "sdcc-4.4.0" / "bin" / "sdcc"),
                  label="sdcc-4.4")]
