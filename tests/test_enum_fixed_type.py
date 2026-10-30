"""C23 enumerations with a fixed underlying type (6.7.3.3):
``enum e : unsigned char { ... }`` and ``enum e : T;``.

Each program runs on the Z80 (16-bit int and --int=32).  The expected text
is what a host compiler gives for the same program in C23 mode, except the
size of ``unsigned long``, which is 4 bytes here."""

import subprocess
import sys

import pytest

from tests.test_struct_return_abi import (  # noqa: F401
    REPO, build_and_run, pytestmark)

PROGRAM = r'''#include <stdio.h>
#include <stdint.h>
enum u8 : unsigned char { U_A = 1, U_B = 200, U_C = 255 };
enum s8 : signed char { S_A = -3, S_B = 127 };
enum i16 : short { H_A = -300, H_B = 30000 };
enum big : long long { B_A = -5, B_B = 5000000000LL };
enum ul : unsigned long { L_A = 4000000000UL };
enum fwd : unsigned short;
enum fwd : unsigned short { F_A = 65535 };
enum tdef : uint8_t { T_A = 7 };
enum : unsigned char { ANON_A = 9, ANON_B = 250 };
enum noty { N_A = 1, N_B = 2 };
struct bits {
  enum u8 u : 3;
  enum s8 s : 4;
  unsigned char tail;
};
static const char *kind(unsigned char x) { return "uchar"; }
int main(void) {
  enum u8 a = U_C;
  enum s8 b = S_A;
  enum i16 c = H_A;
  enum big d = B_B;
  enum fwd f = F_A;
  printf("sizes %d %d %d %d %d %d %d\n", (int)sizeof(enum u8), (int)sizeof(enum s8),
         (int)sizeof(enum i16), (int)sizeof(enum big), (int)sizeof(enum ul),
         (int)sizeof(enum fwd), (int)sizeof(enum tdef));
  printf("const sizes %d %d %d %d\n", (int)sizeof(U_A), (int)sizeof(S_A),
         (int)sizeof(H_A), (int)sizeof(B_A));
  printf("anon %d %d\n", (int)sizeof(ANON_A), ANON_B);
  a++;
  printf("wrap u8 %d\n", a);
  b--; b--; b--;
  printf("s8 %d\n", b);
  b = S_B; b++;
  printf("s8 wrap %d\n", b);
  c = (enum i16)(c - 1000);
  printf("i16 %d\n", c);
  d = (enum big)(d + 1);
  printf("big %lld\n", (long long)d);
  f++;
  printf("fwd wrap %u\n", (unsigned)f);
  printf("arith %d %d\n", U_B + U_C, S_A * 2);
  printf("cmp %d %d\n", U_B > U_A, S_A < 0);
  struct bits sb = {0, 0, 77};
  sb.u = U_A; sb.s = (enum s8)-3;
  printf("bits %d %d %d\n", sb.u, sb.s, sb.tail);
  unsigned char *p = (unsigned char *)&a;
  printf("ptr %d\n", *p);
  enum u8 g = U_B;
  printf("generic %d %d %d\n",
         _Generic(g, unsigned char: 1, int: 2, default: 0),
         _Generic(U_A, unsigned char: 1, int: 2, default: 0),
         _Generic(b, signed char: 1, unsigned char: 2, default: 0));
  printf("plain enum %d\n", (int)sizeof(enum noty) == (int)sizeof(int));
  printf("%s\n", kind(g));
  { enum local : unsigned char { LC = 3 }; enum local q = LC; printf("local %d %d\n", (int)sizeof(q), q); }
  return 0;
}
'''

WANT = """sizes 1 1 2 8 4 2 1
const sizes 1 1 2 8
anon 1 250
wrap u8 0
s8 -6
s8 wrap -128
i16 -1300
big 5000000001
fwd wrap 0
arith 455 -6
cmp 1 1
bits 1 -3 77
ptr 0
generic 1 1 1
plain enum 1
uchar
local 1 3
"""


@pytest.mark.parametrize("flags", [(), ("--int=32",)], ids=["int16", "int32"])
def test_fixed_enum_program(tmp_path, flags):
    assert build_and_run(tmp_path, PROGRAM, flags) == WANT


def compile_c(tmp_path, text):
    (tmp_path / "e.c").write_text(text + "\nint main(void) { return 0; }\n")
    return subprocess.run([sys.executable, "-m", "uc80.main", "e.c", "-o",
                           "e.mac"], capture_output=True, text=True,
                          cwd=str(tmp_path))


@pytest.mark.parametrize("text,message", [
    ("enum e : unsigned char { A = 256 };",
     "enumerator 'A' of 'enum e' has the value 256, which does not fit "
     "the underlying type unsigned char"),
    ("enum e : signed char { A = 128 };", "does not fit the underlying type"),
    ("enum e : unsigned char { A = -1 };", "does not fit the underlying type"),
    ("enum e : short; enum e : long { A };", "declared with the underlying "
     "types 'short' and 'long'"),
    ("enum e : float { A };", "is not an integer type"),
    ("enum e : nosuch_t { A };", "is not a type name"),
])
def test_fixed_enum_errors(tmp_path, text, message):
    r = compile_c(tmp_path, text)
    assert r.returncode == 1
    assert message in r.stderr


def test_a_constant_that_fits_is_accepted(tmp_path):
    r = compile_c(tmp_path, "enum e : unsigned char { A = 255 };\n"
                  "enum f : signed char { B = -128, C = 127 };")
    assert r.returncode == 0, r.stderr


def test_a_forward_declaration_gives_the_type(tmp_path):
    out = build_and_run(tmp_path, r'''#include <stdio.h>
enum e : short;
struct s { enum e v; char c; };
enum e : short { E1 = -2, E2 = 30000 };
int main(void) {
    struct s x = { E2, 'z' };
    printf("%d %d %d %c\n", (int) sizeof(enum e), (int) sizeof(struct s),
           x.v, x.c);
    return 0;
}
''')
    assert out == "2 3 30000 z\n"


def test_a_64_bit_enumeration_constant_loads_all_bytes(tmp_path):
    """The constants of an enumeration that does not fit 32 bits were not
    loaded into the 64-bit accumulator at all: the store wrote what was
    left there."""
    out = build_and_run(tmp_path, r'''#include <stdio.h>
enum wide { W_A = 5000000000LL, W_B = 0x123456789ALL };
int main(void) {
    long long z = 7LL, y = W_A, x = W_B;
    printf("%lu %lu\n", (unsigned long) (y >> 32), (unsigned long) y);
    printf("%lu %lu\n", (unsigned long) (x >> 32), (unsigned long) x);
    return z == 7 ? 0 : 1;
}
''')
    assert out == "1 705032704\n18 878082202\n"


def test_two_files_give_one_tag_different_types(tmp_path):
    from tests.test_struct_return_abi import CPMEMU, LIB_DIR
    (tmp_path / "a.c").write_text(r'''#include <stdio.h>
enum e : unsigned char { A = 1, A2 = 255 };
int fb(void);
int main(void) { enum e x = A2; x++;
    printf("%d %d %d\n", (int) sizeof(enum e), x, fb()); return 0; }
''')
    (tmp_path / "b.c").write_text('''enum e : short { B = 300, B2 = -300 };
int fb(void) { enum e y = B2; return (int) sizeof(enum e) * 1000 + (y - 1); }
''')
    r = subprocess.run([sys.executable, "-m", "uc80.main", "a.c", "b.c",
                        "-o", "m.mac"], capture_output=True, text=True,
                       cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", "m.mac", "-o", "m.rel"], cwd=str(tmp_path),
                   check=True, capture_output=True)
    subprocess.run(["ul80", "m.rel", str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", "m.com"],
                   cwd=str(tmp_path), check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), "m.com"], cwd=str(tmp_path),
                         capture_output=True, timeout=60)
    assert run.stdout.decode("latin-1").replace("\r", "") == "1 0 1699\n"
