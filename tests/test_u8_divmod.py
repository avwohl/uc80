"""Unsigned 8-bit divide and modulo by a constant 1..255.

When the left operand is known to be 0..255 (_byte_kind: an unsigned char,
a masked value, the result of another such division) and the divisor is a
constant from 1 to 255, the code generator divides in 8 bits: one call of
__dv8n (a power of two is a shift or a mask).  The 16-bit helpers
__sdiv16, __smod16, __div16, __mod16 and __dv8w are then not in the
program.  ``q = x / K; r = x % K;`` in two adjacent statements is one
division.  A signed operand stays on the signed path.

The end-to-end tests run every dividend 0..255 with every divisor 1..255
on the Z80 (cpmemu), in several forms, and compare a checksum of the
results with a Python model.  A form that is fused, one that is not, a
compound assignment, a declaration pair and a value in an int context are
checked.
"""

import re
import shutil
import subprocess
import sys

import pytest

from tests.test_const_muldiv import (CPMEMU, LIB_DIR, REPO, build_and_run,
                               c_div, needs_toolchain)

HELPERS_16 = ("__sdiv16", "__smod16", "__div16", "__mod16", "__dv8w")

# (name, source of the loop body; x is the dividend, K the divisor, the
# body adds into the checksums cq and cr)
FORMS = {
    # two adjacent statements: one fused division
    "fused": ("unsigned char q, r; q = x / K; r = x % K; "
              "cq = cq * 31 + q; cr = cr * 31 + r;"),
    # the remainder first
    "fused_rev": ("unsigned char q, r; r = x % K; q = x / K; "
                  "cq = cq * 31 + q; cr = cr * 31 + r;"),
    # two declarations
    "decl": ("unsigned char q = x / K; unsigned char r = x % K; "
             "cq = cq * 31 + q; cr = cr * 31 + r;"),
    # a statement between the two: not fused
    "apart": ("unsigned char q, r; q = x / K; cq = cq * 31 + q; "
              "r = x % K; cr = cr * 31 + r;"),
    # compound assignment on a char
    "compound": ("unsigned char q = x, r = x; q /= K; r %= K; "
                 "cq = cq * 31 + q; cr = cr * 31 + r;"),
    # a value in an int context (the 16-bit path with H = 0)
    "intctx": ("cq = cq * 31 + (x / K); cr = cr * 31 + (x % K);"),
    # a masked int, not a char variable
    "masked": ("int y = vx + 256 * (i & 3); cq = cq * 31 + ((y & 255) / K); "
               "cr = cr * 31 + ((y & 255) % K);"),
}


def program(ks, form):
    body = FORMS[form]
    funcs, calls = [], []
    for k in ks:
        funcs.append(
            f"void t{k}(void) {{ unsigned i, cq = 0, cr = 0; "
            f"for (i = 0; i < 256; i++) {{ unsigned char x; "
            f"vx = (unsigned char) i; x = vx; "
            f"{body.replace('K', str(k))} }} "
            f'printf("%u %u %u\\n", {k}, cq, cr); }}')
        calls.append(f"t{k}();")
    return ("volatile unsigned char vx;\n" + "\n".join(funcs)
            + "\nint main(void) { " + " ".join(calls) + " return 0; }")


def expected(ks, form):
    rows = []
    for k in ks:
        cq = cr = 0
        for i in range(256):
            if form == "masked":
                x = i & 255
            else:
                x = i
            cq = (cq * 31 + x // k) & 0xFFFF
            cr = (cr * 31 + x % k) & 0xFFFF
        rows.append(f"{k} {cq} {cr}")
    return rows


def run_form(tmp_path, ks, form):
    out = build_and_run(tmp_path, program(ks, form), form)
    got = [l.strip() for l in out.splitlines()
           if re.fullmatch(r"\d+ \d+ \d+", l.strip())]
    want = expected(ks, form)
    assert len(got) == len(want), out[-1500:]
    bad = [(w, g) for w, g in zip(want, got) if w != g]
    assert not bad, bad[:5]


KS_ALL = list(range(1, 256))
BATCHES = [KS_ALL[0:64], KS_ALL[64:128], KS_ALL[128:192], KS_ALL[192:]]


@needs_toolchain
@pytest.mark.parametrize("form", ["fused", "decl", "compound", "apart"])
@pytest.mark.parametrize("batch", range(len(BATCHES)))
def test_every_dividend_every_divisor(tmp_path, form, batch):
    run_form(tmp_path, BATCHES[batch], form)


@needs_toolchain
@pytest.mark.parametrize("form", ["fused_rev", "intctx", "masked"])
def test_other_forms_sample_divisors(tmp_path, form):
    ks = [1, 2, 3, 5, 7, 10, 12, 16, 100, 127, 128, 129, 200, 254, 255]
    run_form(tmp_path, ks, form)


@needs_toolchain
def test_signed_and_wide_operands_stay_correct(tmp_path):
    """A signed char, an int and an unsigned short are not 0..255: the
    signed or 16-bit path divides them (C truncates toward zero)."""
    ks = [3, 7, 10, 100, 255]
    funcs, calls = [], []
    for k in ks:
        funcs.append(
            f"void s{k}(void) {{ int i; unsigned cq = 0, cr = 0; "
            f"for (i = -128; i < 128; i++) {{ signed char x = i; "
            f"cq = cq * 31 + (unsigned)(x / {k}); "
            f"cr = cr * 31 + (unsigned)(x % {k}); }} "
            f'printf("%u %u\\n", cq, cr); }}\n'
            f"void w{k}(void) {{ unsigned n, cq = 0, cr = 0; "
            f"for (n = 0; n < 256; n++) {{ unsigned short x = n * 257u; "
            f"int y = (int)x; cq = cq * 31 + (x / {k}) + (y / {k}); "
            f"cr = cr * 31 + (x % {k}) + (y % {k}); }} "
            f'printf("%u %u\\n", cq, cr); }}')
        calls.append(f"s{k}(); w{k}();")
    src = "\n".join(funcs) + "\nint main(void) { " + " ".join(calls) + " }"
    out = build_and_run(tmp_path, src, "wide")
    got = [l.strip() for l in out.splitlines()
           if re.fullmatch(r"\d+ \d+", l.strip())]
    want = []
    for k in ks:
        cq = cr = 0
        for i in range(-128, 128):
            cq = (cq * 31 + (c_div(i, k) & 0xFFFF)) & 0xFFFF
            cr = (cr * 31 + ((i - c_div(i, k) * k) & 0xFFFF)) & 0xFFFF
        want.append(f"{cq} {cr}")
        cq = cr = 0
        for i in range(0, 65536, 257):  # 256 values
            y = i - 65536 if i >= 32768 else i
            cq = (cq * 31 + i // k + c_div(y, k)) & 0xFFFF
            cr = (cr * 31 + i % k + (y - c_div(y, k) * k)) & 0xFFFF
        want.append(f"{cq} {cr}")
    assert got == want


# ---------------------------------------------------------- code shape

def compile_mac(tmp_path, source):
    c_file = tmp_path / "shape.c"
    c_file.write_text(source)
    mac = tmp_path / "shape.mac"
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return mac.read_text()


def calls(mac, name):
    """Calls of ``name`` in main (the embedded helpers call each other)."""
    start = mac.index("\n_main:")
    end = mac.index("@main_ret:", start)
    return len(re.findall(rf"\bcall\s+{name}\b", mac[start:end],
                          re.IGNORECASE))


def test_unsigned_char_divide_links_no_16_bit_helper(tmp_path):
    mac = compile_mac(tmp_path, """
        #include <stdio.h>
        unsigned char a, q, r;
        int main(void) { a = getchar(); q = a / 7; r = a % 11; return q + r; }""")
    assert calls(mac, "__dv8n") == 2
    for h in HELPERS_16:
        assert not re.search(rf"^{h}:", mac, re.MULTILINE), h


def test_adjacent_quotient_and_remainder_are_one_division(tmp_path):
    mac = compile_mac(tmp_path, """
        #include <stdio.h>
        unsigned char a, q, r;
        int main(void) { a = getchar(); q = a / 11; r = a % 11; return q + r; }""")
    assert calls(mac, "__dv8n") == 1


def test_signed_char_keeps_the_signed_helper(tmp_path):
    mac = compile_mac(tmp_path, """
        #include <stdio.h>
        signed char a; int q;
        int main(void) { a = getchar(); q = a / 10; return q; }""")
    assert calls(mac, "__dv8n") == 0
    assert calls(mac, "__sdiv16") == 1


def test_power_of_two_is_a_shift_or_a_mask(tmp_path):
    mac = compile_mac(tmp_path, """
        #include <stdio.h>
        unsigned char a, q, r;
        int main(void) { a = getchar(); q = a / 8; r = a % 8; return q + r; }""")
    assert calls(mac, "__dv8n") == 0
    for h in HELPERS_16:
        assert not re.search(rf"^{h}:", mac, re.MULTILINE), h


def test_reciprocal_multiply_needs_no_division_helper(tmp_path):
    """K = 10 and K = 12 divide by an exact reciprocal multiply: no call
    and no division helper in the program."""
    mac = compile_mac(tmp_path, """
        #include <stdio.h>
        unsigned char a, q, r, s;
        int main(void) { a = getchar(); q = a / 10; r = a % 10;
                         s = a / 12 + a % 12; return q + r + s; }""")
    assert calls(mac, "__dv8n") == 0
    for h in HELPERS_16 + ("__dv8n",):
        assert not re.search(rf"^{h}:", mac, re.MULTILINE), h
