"""Wide integers, _Bool conversion and the places that convert to them.

Each program in tests/wide_int/ is plain C that the host compiler also
builds (-DHOST selects 16-bit and 32-bit stand-ins for uc80's int and
long); NAME.out is what the host prints (fold16 needs a 16-bit int: its
output is written by hand).  Every program is built with
uc80 under each flag set below and must print the same text.

  bool_return_param   a _Bool result or parameter is 0 or 1, for a value
                      whose low byte is zero ((_Bool)0x100), of every width
  bool_init_assign    _Bool objects initialised or assigned from wide values:
                      scalars, arrays, structs, compound literals, statics
  tern_ll             ?: with a 64-bit arm (the other arm is widened), and
                      xor with (c ? 0x8000000000000000ULL : 0)
  init_ll, init_ll2   long long elements and members in local initialisers
  arg_ll_conv         a long, int or char argument for a long long parameter
                      keeps its sign
  switch_incdec_ll    switch on a long long; ++/-- values of 64-bit members
  compound_ll_lvalue  op= on a long long lvalue with a side effect (a[i++] += 5)
                      evaluates the lvalue once
  unary_ll            ~ and - of a long long leave the low 32 bits in DEHL
  long_from_ll        a long initialised from a long long keeps 32 bits
  ll_const_arg        long long constants above 32767 as arguments
  ll_literals         integer literal types (0xffffffffU is an unsigned long)
                      and constants as long long arguments and values
  ll_mixed            conversions, comparisons, shifts, arithmetic and calls
                      that mix long long with narrower types
  bool_misc           _Bool from every width, in calls, returns and casts
  fold16              (x op c1) op c2 with constants of different widths is
                      not folded to x op c3
  incdec_ll_binary    ++ and -- on a long long as an operand of a binary
                      operator (x++ * 2, a++ + g()) keep the other operand
  inline_narrow_param a wide argument for a short or char parameter of an
                      inlined function is cut to the parameter type
  inline_narrow_result a wider body for a result of a 16-bit typedef is cut
  bool_static_addr    _Bool objects initialised from address constants
  cse_cast_types      casts of one variable to different types are not one
                      common subexpression (uc_core CSE key)
  cast_ll_literal_arg a long long literal under a narrowing cast as an
                      argument keeps only the bits of the narrow type
  ccfuzz_ll126        seed 126 of bench/ccfuzz.py --ll
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CASES = Path(__file__).resolve().parent / "wide_int"
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

FLAGS = [[], ["-O0"], ["--no-inlining"], ["--no-register-cc"],
         ["--no-inlining", "--no-register-cc"]]


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")


def build_and_run(tmp_path, c_file, flags):
    mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)] + flags,
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    r = subprocess.run(["um80", str(mac), "-o", str(rel)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                       text=True, cwd=str(tmp_path), timeout=60,
                       stdin=subprocess.DEVNULL)
    return r.stdout.replace("\r", "")


@pytest.mark.parametrize("flags", FLAGS, ids=lambda f: " ".join(f) or "default")
@pytest.mark.parametrize("src", sorted(CASES.glob("*.c")),
                         ids=lambda p: p.stem)
def test_program(tmp_path, src, flags):
    want = src.with_suffix(".out").read_text()
    assert build_and_run(tmp_path, src, flags).strip() == want.strip()
