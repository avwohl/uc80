"""The type of an enumeration is an integer type that holds its values.

``enum { E = 50000 }`` used to be an int of 2 bytes: ``long l = E`` gave
-15536, ``enum { F = 70000 }`` lost its high bits, and with --int 32 an
enumeration constant above 65535 or an object of enumeration type was
handled as 16 bits.  C23 6.7.3.3 gives an enumeration without a fixed
underlying type an implementation-defined integer type that can represent
all members.  uc80 takes the first of int, unsigned int, long, unsigned
long, long long and unsigned long long that holds the lowest and highest
value, and each constant has that type once the enumeration is complete
when an int does not hold every value.  The expected text is worked out
from those rules for int 2 bytes and long 4 bytes (a host compiler gave
the same numbers except for the sizes).
"""

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

ENUMS = '#include <stdio.h>\nenum A { E = 50000 };\nenum B { F = 70000, G = -1 };\nenum C { H = 40000, I };\nenum D { J = 4000000000u };\nenum A ga = E;\nenum B gb = F;\nint main(void){\n  long l = E; unsigned u = E; long m = ga; enum A la = E; long long ll = E;\n  printf("%ld %u %ld %ld %lld\\n", l, u, m, (long)la, ll);\n  printf("%d %d %d %d\\n", (int)sizeof(enum A), (int)sizeof(enum B), (int)sizeof(enum C), (int)sizeof(enum D));\n  printf("%ld %ld %ld\\n", (long)F, (long)G, (long)I);\n  printf("%d %d\\n", E > 0, la > 0);\n  printf("%ld %ld\\n", (long)gb, (long)E * 2);\n  printf("%d\\n", (int)(E / 7));\n  printf("%lu\\n", (unsigned long)J);\n  return 0;\n}\n'
ENUM_USES = '#include <stdio.h>\ntypedef enum { BIG = 50000, SMALL = 3 } BigT;\nenum { ANON = 70000, NEG = -5 } av;\nenum { PLAIN = 1, TWO } pv;\nstruct S { BigT t; enum { M1 = 60000 } m; char c; } st;\nBigT arr[3] = { BIG, SMALL, 40000 };\nBigT pass(BigT x) { return x + 1; }\nint sw(BigT x) {\n  switch (x) { case BIG: return 1; case SMALL: return 2; case 50001: return 3; default: return 0; }\n}\nint main(void){\n  BigT v = BIG;\n  long l = v; long q = SMALL; unsigned u = v;\n  av = ANON; l = av; \n  printf("%ld %ld %u %ld %ld\\n", l, q, u, (long)NEG, (long)ANON);\n  printf("%d %d %d\\n", (int)sizeof(BigT), (int)sizeof av, (int)sizeof pv);\n  printf("%d %d\\n", (int)sizeof st, (int)sizeof(struct S));\n  st.t = BIG; st.m = M1; st.c = 9;\n  printf("%ld %ld %d\\n", (long)st.t, (long)st.m, st.c);\n  printf("%ld %ld %ld\\n", (long)arr[0], (long)arr[1], (long)arr[2]);\n  printf("%ld %d %d %d\\n", (long)pass(BIG), sw(BIG), sw(SMALL), sw(pass(BIG)));\n  printf("%d %d\\n", v == BIG, v > 40000);\n  v = SMALL; printf("%d %ld\\n", v < BIG, (long)(BIG - v));\n  printf("%d %d\\n", (int)(BIG / 2), BIG % 7);\n  printf("%ld\\n", (long)(ANON + NEG));\n  printf("%d\\n", (int)(BigT)SMALL);\n  return 0;\n}\n'


def test_enum_values_above_int_widen_to_the_enum_type(tmp_path):
    assert build_and_run(tmp_path, ENUMS) == '50000 50000 50000 50000 50000\n2 4 2 4\n70000 -1 40001\n1 1\n70000 100000\n7142\n4000000000\n'


def test_enum_in_typedefs_structs_arrays_switches_and_arithmetic(tmp_path):
    assert build_and_run(tmp_path, ENUM_USES) == '70000 3 50000 -5 70000\n2 4 2\n5 5\n50000 60000 9\n50000 3 40000\n50001 1 2 3\n1 1\n1 49997\n25000 6\n69995\n3\n'


def test_the_same_with_32_bit_int(tmp_path):
    assert build_and_run(tmp_path, ENUMS, flags=("--int", "32")) == '50000 50000 50000 50000 50000\n4 4 4 4\n70000 -1 40001\n1 1\n70000 100000\n7142\n4000000000\n'
    assert build_and_run(tmp_path, ENUM_USES, flags=("--int", "32")) == '70000 3 50000 -5 70000\n4 4 4\n9 9\n50000 60000 9\n50000 3 40000\n50001 1 2 3\n1 1\n1 49997\n25000 6\n69995\n3\n'
