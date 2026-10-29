"""The value of an assignment to a bit-field is the stored value.

``sh = bf.x = 5`` for an unsigned 2-bit field stores 1, so sh is 1 (C23
6.5.16.1p3).  The same holds for every compound assignment, for ++ and --
in prefix form, and for postfix forms (which give the old stored value).
With --int 32 the field lives in a 4-byte unit and the result is in DEHL;
that path used to hand back the unmasked right-hand side, and the postfix
form lost the high word.  The expected text was produced by a host
compiler.  The 64-bit narrow bit-field has no code generator, so the
compiler refuses it with a diagnostic instead of giving wrong values.
"""

import subprocess
import sys

import pytest

from tests.test_struct_return_abi import (  # noqa: F401
    REPO, build_and_run, pytestmark)

NARROW = '#include <stdio.h>\nstruct B { unsigned x:2; signed y:3; } bf;\nint main(void){\n  int sh; unsigned u; long l;\n  sh = bf.x = 5; printf("%d\\n", sh);\n  sh = bf.y = 5; printf("%d\\n", sh);\n  sh = (bf.x += 6); printf("%d\\n", sh);\n  sh = (bf.x |= 7); printf("%d\\n", sh);\n  sh = (bf.x <<= 1); printf("%d\\n", sh);\n  sh = (bf.x++); printf("%d %d\\n", sh, bf.x);\n  sh = (++bf.x); printf("%d %d\\n", sh, bf.x);\n  bf.y=3; sh = (++bf.y); printf("%d %d\\n", sh, bf.y);\n  bf.y=3; sh = (bf.y++); printf("%d %d\\n", sh, bf.y);\n  l = bf.x = 7; printf("%ld\\n", l);\n  u = bf.y = 9; printf("%u\\n", u);\n  sh = (bf.y *= 5); printf("%d\\n", sh);\n  sh = (bf.y -= 20); printf("%d\\n", sh);\n  printf("%d\\n", (bf.x = 6) + 10);\n  return 0;\n}\n'
WIDE = '#include <stdio.h>\nstruct B { unsigned char a:3; signed char b:3; unsigned short c:5; short d:5; unsigned long e:20; long f:20; unsigned g:15; int h:15;} bf;\nint main(void){\n  long long l, ll; int sh; unsigned u;\n  l = bf.a = 13; printf("%lld\\n", l);\n  l = bf.b = 13; printf("%lld\\n", l);\n  l = bf.c = 77; printf("%lld\\n", l);\n  l = bf.d = 77; printf("%lld\\n", l);\n  l = bf.e = 0x1abcde; printf("%llx\\n", l);\n  l = bf.f = 0xfabcde; printf("%llx\\n", l);\n  l = bf.g = 0x1abcde; printf("%llx\\n", l);\n  l = bf.h = 0xfabcde; printf("%llx\\n", l);\n  l = (bf.a += 7); printf("%lld\\n", l);\n  l = (bf.b -= 9); printf("%lld\\n", l);\n  l = (bf.d *= 9); printf("%lld\\n", l);\n  l = (bf.e += 0xfffff); printf("%llx\\n", l);\n  l = (bf.f <<= 3); printf("%llx\\n", l);\n  l = (bf.g |= 0x1000000); printf("%llx\\n", l);\n  l = (bf.h ^= 0x1fffff); printf("%llx\\n", l);\n  l = ++bf.a; printf("%lld\\n", l);\n  l = ++bf.h; printf("%lld\\n", l);\n  l = --bf.g; printf("%lld\\n", l);\n  l = ++bf.e; printf("%lld\\n", l);\n  l = ++bf.f; printf("%lld\\n", l);\n  bf.a=7; bf.b=3; bf.c=31; bf.d=15; bf.e=0xfffff; bf.f=0x7ffff; bf.g=0xfffff; bf.h=0x7ffff;\n  l = ++bf.a; printf("%lld\\n", l);\n  l = ++bf.b; printf("%lld\\n", l);\n  l = ++bf.c; printf("%lld\\n", l);\n  l = ++bf.d; printf("%lld\\n", l);\n  l = ++bf.e; printf("%lld\\n", l);\n  l = ++bf.f; printf("%lld\\n", l);\n  l = ++bf.g; printf("%lld\\n", l);\n  l = ++bf.h; printf("%lld\\n", l);\n  l = bf.c++; printf("%lld %d\\n", l, bf.c);\n  l = bf.h++; printf("%lld %d\\n", l, bf.h);\n  printf("%lld\\n", (bf.a = 9) + 100LL);\n  printf("%d\\n", (bf.h = 0x80000) < 0);\n  return 0;\n}\n'
EXPECT_NARROW = '1\n-3\n3\n3\n2\n2 3\n0 0\n-4 -4\n3 -4\n3\n1\n-3\n1\n12\n'
EXPECT_WIDE = '5\n-3\n13\n13\nabcde\nfffffffffffabcde\n3cde\n3cde\n4\n-4\n-11\nabcdd\n5e6f0\n3cde\nffffffffffffc321\n5\n-15582\n15581\n703710\n386801\n0\n-4\n0\n-16\n0\n-524288\n0\n0\n0 1\n0 1\n101\n0\n'


@pytest.mark.parametrize("flags", [(), ("--int", "32")], ids=["int16", "int32"])
def test_assignment_to_a_narrow_field_gives_the_stored_value(tmp_path, flags):
    assert build_and_run(tmp_path, NARROW, flags) == EXPECT_NARROW


@pytest.mark.parametrize("flags", [(), ("--int", "32")], ids=["int16", "int32"])
def test_every_field_type_and_operator(tmp_path, flags):
    assert build_and_run(tmp_path, WIDE, flags) == EXPECT_WIDE


def _compile(tmp_path, source):
    c_file = tmp_path / "d.c"
    c_file.write_text(source)
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), "-o",
         str(tmp_path / "d.mac")], capture_output=True, text=True,
        cwd=str(REPO))


def test_a_narrow_64_bit_bit_field_is_refused_with_its_line(tmp_path):
    r = _compile(tmp_path, "struct B { int a;\n  unsigned long long i:40; } bf;\n"
                           "int main(void) { return bf.i; }\n")
    assert r.returncode == 1
    assert "line 2: bit-field 'i' of a 64-bit integer type" in r.stderr


def test_the_address_of_a_bit_field_is_a_diagnostic_not_a_crash(tmp_path):
    r = _compile(tmp_path, "struct B { int a:3; } bf;\n"
                           "int main(void) { int *p = &bf.a; return *p; }\n")
    assert r.returncode == 1
    assert "line 2: cannot take address of bit-field" in r.stderr
