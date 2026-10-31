"""Family J step 5b: the reload of A after a compare and a jump
(docs/byte_cse.md).

Each program runs under cpmemu (with a timeout) and on the host compiler.
Both outputs must be the same.  The shape tests count the loads in the text.
"""

import re

import pytest

from .test_famj_kernels import ids, same_as_host
from .test_famj_startup import compile_c
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

FLAGS = [
    (), ("--no-byte-cse",), ("-O0",), ("--no-register-locals",),
    ("--no-byte-regs",), ("--no-peep-pairs",),
]

SRC = r"""
#include <stdio.h>

struct ent { unsigned char reg, val; };
struct ent tab[16];
unsigned char hits[8];
unsigned char flag;

static int classify(const struct ent *t)
{
    int r = 0;
    if ((t->reg & 0xF0) == 0x40)
        r = 1;
    else if ((t->reg & 0xF0) == 0xA0)
        r = 2;
    else if ((t->reg & 0xF0) == 0xB0)
        r = 3;
    else if ((t->reg | 1) == 0x31)
        r = 4;
    return r;
}

static int sw(unsigned char x)
{
    int r = 0;
    if ((x & 15) == 3)
        r += 10;
    else if ((x & 15) == 7)
        r += 20;
    else if ((x & 15) > 12)
        r += 30;
    if ((x ^ 0x55) == 0x10)
        r += 1;
    else if ((x ^ 0x55) == 0x20)
        r += 2;
    return r;
}

static int with_label(unsigned char x, int n)
{
    int r = 0, i;
    for (i = 0; i < n; i++) {
        if ((x & 0x30) == 0x10)
            r += 1;
        else {
            if ((x & 0x30) == 0x20)
                r += 2;
            else if ((x & 0x30) == 0x30)
                goto three;
        }
        x = (unsigned char)(x + 7);
        continue;
three:
        r += 3;
        x = (unsigned char)(x + 5);
    }
    return r;
}

int main(void)
{
    int i, s = 0;
    for (i = 0; i < 16; i++) {
        tab[i].reg = (unsigned char)(i * 29 + 3);
        tab[i].val = (unsigned char)i;
    }
    tab[3].reg = 0x41; tab[4].reg = 0xA7; tab[5].reg = 0xB0; tab[6].reg = 0x30;
    for (i = 0; i < 16; i++)
        s = s * 5 + classify(&tab[i]);
    printf("%d\n", s & 0x7FFF);
    s = 0;
    for (i = 0; i < 256; i++)
        s = (s * 3 + sw((unsigned char)i)) & 0x7FFF;
    printf("%d\n", s);
    printf("%d %d\n", with_label(0x11, 20), with_label(0x2F, 33));
    return 0;
}
"""


@pytest.mark.parametrize("flags", FLAGS, ids=ids)
def test_byte_cse_against_host(tmp_path, flags):
    same_as_host(tmp_path, SRC, *flags)


def func_text(mac, name):
    a = mac.index(f"\n_{name}:")
    b = mac.index(f"@{name}_ret:", a)
    return mac[a:b]


IY_SRC = r"""
struct regw { unsigned char bank, reg, val; };
const struct regw tbl[8] = {
    {0, 0x40, 1}, {0, 0xA1, 8}, {1, 0xA2, 16}, {1, 0x41, 2},
    {0, 0x10, 3}, {1, 0xA0, 24}, {0, 0x42, 4}, {1, 0x30, 5}};
unsigned char out_sum;

static void emit(unsigned char bank, unsigned char reg, unsigned char val)
{
    out_sum = (unsigned char)(out_sum * 3 + bank + reg + val);
}

void run(void)
{
    const struct regw *t = tbl;
    unsigned char i, val;
    for (i = 0; i < 8; i++, t++) {
        val = t->val;
        if ((t->reg & 0xF0) == 0x40)
            val = (unsigned char)(val + 1);
        else if ((t->reg & 0xF0) == 0xA0 && (val & 7) == 0)
            val = (unsigned char)(val ^ 3);
        emit(t->bank, t->reg, val);
    }
}

int main(void) { run(); return out_sum; }
"""


IY_RUN = IY_SRC.replace("int main(void) { run(); return out_sum; }",
                        "#include <stdio.h>\nint main(void)\n{\n"
                        "    run(); run();\n"
                        "    printf(\"%d\\n\", out_sum);\n    return 0;\n}")


@pytest.mark.parametrize("flags", FLAGS, ids=ids)
def test_byte_cse_iy_chain_against_host(tmp_path, flags):
    assert "printf" in IY_RUN
    same_as_host(tmp_path, IY_RUN, *flags)


def test_byte_cse_drops_the_second_load(tmp_path):
    on = compile_c(tmp_path, IY_SRC).read_text()
    off = compile_c(tmp_path, IY_SRC, "--no-byte-cse", name="o").read_text()
    f_on, f_off = func_text(on, "run"), func_text(off, "run")
    pat = r"\tld\tA,\(IY\+1\)"
    assert len(re.findall(pat, f_on)) < len(re.findall(pat, f_off)), (
        f_on, f_off)


VOL_SRC = r"""
volatile unsigned char reg;
int count;

int poll(void)
{
    if ((reg & 0xF0) == 0x40)
        count = 1;
    else if ((reg & 0xF0) == 0xA0)
        count = 2;
    return count;
}

int main(void) { return poll(); }
"""


def test_a_volatile_register_is_read_twice(tmp_path):
    text = compile_c(tmp_path, VOL_SRC).read_text()
    f = func_text(text, "poll")
    assert len(re.findall(r"\tld\tA,\(_reg\)", f)) == 2, f
