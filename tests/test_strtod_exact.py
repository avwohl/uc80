"""strtod rounds every decimal string to the nearest float (ties to even).

The reference is an exact rational rounding in Python.  The strings are
exact ties and strings near a tie (the tie with one more digit, one digit
less and a long tail of nines), long strings, and random decimals.  The
test runs strtod in the Z80 emulator (cpmemu).  The module is
src/uc80/lib/csrc/lc_strtod.c; docs/libc_c_sources.md has the method.
"""

import os
import random
import shutil
import struct
import subprocess
import sys
from decimal import Decimal, getcontext
from fractions import Fraction
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
         and (LIB_DIR / "libc.lib").exists()
         and (LIB_DIR / "runtime.lib").exists()),
    reason="um80/ul80/cpmemu or built libraries not available")

getcontext().prec = 400


def f32_bits(x):
    """The float bits of the non-negative Fraction x, nearest, ties to even."""
    if x == 0:
        return 0
    e = x.numerator.bit_length() - x.denominator.bit_length()
    if Fraction(2) ** e > x:
        e -= 1
    e = max(e, -126)                    # a subnormal number has the unit 2^-149
    q = x / Fraction(2) ** (e - 23)
    m = q.numerator // q.denominator
    r = q - m
    if r > Fraction(1, 2) or (r == Fraction(1, 2) and m & 1):
        m += 1
    if m == 1 << 24:
        m >>= 1
        e += 1
    if e > 127:
        return 0x7F800000
    if m < 1 << 23:
        return m
    return ((e + 127) << 23) + m - (1 << 23)


def reference(text):
    neg = text.startswith("-")
    bits = f32_bits(Fraction(text.lstrip("+-")))
    return bits | (0x80000000 if neg else 0)


def tie(bits):
    """The tie between the float BITS and the next float."""
    low = Fraction(struct.unpack("<f", struct.pack("<I", bits))[0])
    if bits == 0x7F7FFFFF:
        return (low + Fraction(2) ** 128) / 2
    high = Fraction(struct.unpack("<f", struct.pack("<I", bits + 1))[0])
    return (low + high) / 2


def spellings(value, rnd):
    """The exact tie VALUE in several spellings, and strings just above
    and just below it."""
    dec = Decimal(value.numerator) / Decimal(value.denominator)
    digits = format(dec, "f").replace(".", "").lstrip("0").rstrip("0") or "0"
    k = dec.adjusted() + 1                      # value = 0.DIGITS * 10^k
    bases = [digits[0] + "." + digits[1:] + "e%d" % (k - 1),
             digits + "e%d" % (k - len(digits)),
             "0." + digits + "e%d" % k]
    if -30 < k < 30:
        bases.append(format(dec, "f").rstrip("0"))
    out = list(bases)
    for b in bases:
        mant, _, ex = b.partition("e")
        ex = "e" + ex if ex else ""
        dot = "" if "." in mant else "."
        out.append(mant + dot + "0" * rnd.randint(0, 40) + "1" + ex)
        out.append(mant + dot + "9" * rnd.randint(1, 40) + ex)
        last = max(i for i, c in enumerate(mant) if c.isdigit())
        if mant[last] != "0":
            lower = mant[:last] + str(int(mant[last]) - 1) + mant[last + 1:]
            out.append(lower + dot + "9" * rnd.randint(1, 60) + ex)
        if mant[last] != "9":
            upper = mant[:last] + str(int(mant[last]) + 1) + mant[last + 1:]
            out.append(upper + ex)
    return out


def run_strtod(tmp_path, texts):
    """The float bits (hex text) that strtod gives for each string."""
    tmp_path.mkdir()
    src = ("#include <stdio.h>\n#include <stdlib.h>\nstatic const char *t[] = {\n"
           + ",\n".join('"%s"' % t for t in texts) + "\n};\n"
           "int main(void)\n{\n    int i;\n"
           "    for (i = 0; i < %d; i++) {\n        double d = strtod(t[i], 0);\n"
           '        printf("%%08lx\\n", *(unsigned long *)&d);\n    }\n'
           "    return 0;\n}\n" % len(texts))
    (tmp_path / "p.c").write_text(src)
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(tmp_path / "p.c"),
                        "-o", str(tmp_path / "p.mac")], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(tmp_path / "p.mac"), "-o", str(tmp_path / "p.rel")],
                   check=True, capture_output=True)
    subprocess.run(["ul80", str(tmp_path / "p.rel"), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(tmp_path / "p.com")],
                   check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "p.com")],
                         capture_output=True, timeout=600,
                         env=dict(os.environ, PYTHONHASHSEED="0"))
    got = [x for x in run.stdout.decode("latin-1").replace("\r", "").split("\n")
           if x]
    assert len(got) == len(texts)
    return got


def test_strings(tmp_path):
    rnd = random.Random(20260511)
    texts = ["16777217", "16777219", "8388608.5", "8388609.5",
             "0.1", "0.3", "1e38", "3.4028235e38", "3.4028236e38",
             "3.40282356779733661637539395458142568447e38",
             "3.40282356779733661637539395458142568448e38",
             "1.17549435e-38", "1.4e-45", "7e-46", "-7.1e-46",
             "0x1.fffffep127", "0x1.ffffffp127", "0x1p-149", "0x1p-150",
             "0x1.0000010000000001p0", "123456789012345678901234567890",
             "0." + "0" * 40 + "1" + "5" * 130,
             "1" + "0" * 130, "1" + "0" * 130 + "e-130"]
    ties = [0, 1, 2, 3, 0x7FFFFF, 0x800000, 0x800001, 0x3F7FFFFF, 0x3F800000,
            0x4B7FFFFF, 0x4B800000, 0x7F7FFFFE, 0x7F7FFFFF]
    ties += [rnd.randrange(0, 0x7F7FFFFF) for _ in range(14)]
    ties += [(rnd.randrange(1, 254) << 23) | rnd.randrange(1 << 23)
             for _ in range(10)]
    for bits in ties:
        texts += spellings(tie(bits), rnd)
    texts += spellings(Fraction(1, 2 ** 150), rnd)
    for _ in range(40):
        n = rnd.choice([3, 9, 17, 19, 20, 25, 40, 120])
        digs = "".join(rnd.choice("0123456789") for _ in range(n))
        p = rnd.randint(0, n)
        texts.append(digs[:p] + "." + digs[p:] + "e%d" % rnd.randint(-50, 40))
    texts = [t for t in texts if t != "." and not t.startswith(".e")]
    hexfloat = {"0x1.fffffep127": 0x7F7FFFFF, "0x1.ffffffp127": 0x7F800000,
                "0x1p-149": 1, "0x1p-150": 0, "0x1.0000010000000001p0": 0x3F800001}
    batches, cur, size = [], [], 0
    for t in texts:
        if size + len(t) > 16000:       # the CP/M program must fit in memory
            batches.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t) + 4
    batches.append(cur)
    bad = []
    for number, batch in enumerate(batches):
        for text, out in zip(batch, run_strtod(tmp_path / str(number), batch)):
            want = hexfloat[text] if text in hexfloat else reference(text)
            if int(out, 16) != want:
                bad.append((text[:80], out, "%08x" % want))
    assert not bad, bad[:5]
