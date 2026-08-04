"""A comma expression yielding long long carries its value, not the last one.

Once the comma operator got a result type (11cbd0d), `(f(), 42LL)` started
reporting itself as a 64-bit expression -- and three places took that to
mean gen_expr had left the value in __acc64.  It had not: a 64-bit literal
has a fast path that emits a bare 16-bit ``ld HL,42``, so the comma
evaluated to whatever the accumulator happened to hold from an earlier
long long in the same function.  Before the result type existed the same
code went through the sign-extend path and happened to work, so this was a
regression.

The three sites, all of which now go through _gen_64bit_operand, which
always stores the full width:

  - initialising or assigning a long long
  - a condition (if/while/do/for/&&/||/?:), tested by ORing __acc64's bytes
  - a variadic argument, pushed by __push64_acc straight from __acc64

Each test poisons __acc64 first with a distinctive value, because the bug
is invisible when the accumulator happens to be zero.

TestStructValued covers a second, separate gap of the same kind that the
result-type work never reached: struct-valued commas, where the shape
dispatches produced the struct's first word where its address belonged.
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

POISON = 123456789012


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or built libraries not available")

PRELUDE = """#include <stdio.h>
int flag = 0;
void note(void) { flag++; }
long long poison(void) { return %dLL; }
long long sink;
long long twoll(long long a, long long b) { return a * 100 + b; }
""" % POISON


def build_and_run(tmp_path, body, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(PRELUDE + "int main(void){\nsink = poison();\n%s\nreturn 0;}\n"
                      % body)
    mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    env = dict(os.environ, PYTHONHASHSEED="0")
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=60, env=env)
    return run.stdout.decode("latin-1").replace("\r", "")


class TestInitAndAssign:
    def test_initialiser(self, tmp_path):
        out = build_and_run(tmp_path, """
  long long a = (note(), 42LL);
  printf("a=%lld flag=%d\\n", a, flag);""")
        assert out == "a=42 flag=1\n"

    def test_assignment(self, tmp_path):
        out = build_and_run(tmp_path, """
  long long a;
  a = (note(), 7LL);
  printf("a=%lld\\n", a);""")
        assert out == "a=7\n"

    def test_unsigned_max(self, tmp_path):
        out = build_and_run(tmp_path, """
  unsigned long long u = (note(), 18446744073709551615ULL);
  printf("u=%llu\\n", u);""")
        assert out == "u=18446744073709551615\n"

    def test_cast_around_the_comma(self, tmp_path):
        out = build_and_run(tmp_path, """
  long long a = (long long)(note(), 42LL);
  printf("a=%lld\\n", a);""")
        assert out == "a=42\n"

    def test_nested_commas(self, tmp_path):
        out = build_and_run(tmp_path, """
  long long a = (note(), (note(), 7LL));
  printf("a=%lld flag=%d\\n", a, flag);""")
        assert out == "a=7 flag=2\n"

    def test_left_operand_still_runs(self, tmp_path):
        """The value is the right operand; the left is still evaluated."""
        out = build_and_run(tmp_path, """
  long long a = (note(), note(), 3LL);
  printf("a=%lld flag=%d\\n", a, flag);""")
        assert out == "a=3 flag=2\n"


class TestCondition:
    """__acc64 is poisoned non-zero, so a stale read takes the true branch."""

    def test_if_false(self, tmp_path):
        out = build_and_run(tmp_path, """
  if ((note(), 0LL)) printf("true\\n"); else printf("false\\n");""")
        assert out == "false\n"

    def test_if_true(self, tmp_path):
        out = build_and_run(tmp_path, """
  if ((note(), 1LL)) printf("true\\n"); else printf("false\\n");""")
        assert out == "true\n"

    def test_while(self, tmp_path):
        out = build_and_run(tmp_path, """
  while ((note(), 0LL)) printf("body\\n");
  printf("done flag=%d\\n", flag);""")
        assert out == "done flag=1\n"

    def test_do_while(self, tmp_path):
        out = build_and_run(tmp_path, """
  do { printf("once\\n"); } while ((note(), 0LL));""")
        assert out == "once\n"

    def test_for(self, tmp_path):
        out = build_and_run(tmp_path, """
  for (; (note(), 0LL); ) printf("body\\n");
  printf("done\\n");""")
        assert out == "done\n"

    def test_logical_and(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("r=%d\\n", (note(), 0LL) && 1);""")
        assert out == "r=0\n"

    def test_logical_or(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("r=%d\\n", (note(), 0LL) || 0);""")
        assert out == "r=0\n"

    def test_ternary(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("r=%d\\n", (note(), 0LL) ? 11 : 22);""")
        assert out == "r=22\n"

    def test_high_half_alone_is_enough(self, tmp_path):
        """A value that is zero in its low 32 bits is still true."""
        out = build_and_run(tmp_path, """
  if ((note(), 4294967296LL)) printf("true\\n"); else printf("false\\n");""")
        assert out == "true\n"


class TestVariadicArgument:
    def test_single_argument(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("a=%lld\\n", (note(), 42LL));""")
        assert out == "a=42\n"

    def test_cast_around_the_comma(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("a=%lld\\n", (long long)(note(), 42LL));""")
        assert out == "a=42\n"

    def test_two_arguments(self, tmp_path):
        out = build_and_run(tmp_path, """
  printf("a=%lld b=%lld\\n", (note(), 1LL), (note(), 42LL));""")
        assert out == "a=1 b=42\n"

    def test_named_parameters(self, tmp_path):
        """Not variadic -- a different push path, checked for the same slip."""
        out = build_and_run(tmp_path, """
  printf("r=%lld\\n", twoll((note(), 1LL), (note(), 42LL)));""")
        assert out == "r=142\n"


class TestStructValued:
    """`(f(), s)` designates s, so it copies s and still runs f().

    Struct-valued commas were never fixed by the result-type work: the
    shape dispatches for struct copies and for _gen_address matched
    Identifier, Call and *ptr and nothing else, so a comma fell to a
    generic arm that produced the struct's first word where its address
    belonged.  The value was garbage and, in argument position, the left
    operand was not emitted at all.
    """

    PRELUDE = """
struct S { int a; long long b; double c; };
struct S g = { 1, 123456789012LL, 2.5 };
void takes(struct S v){ printf("a=%d b=%lld c=%f\\n", v.a, v.b, v.c); }
struct S ret(void){ return (note(), g); }
"""

    def build(self, tmp_path, body, name):
        # PRELUDE has to land before main(), which build_and_run wraps.
        c_file = tmp_path / (name + ".c")
        c_file.write_text(PRELUDE + self.PRELUDE
                          + "int main(void){\nsink = poison();\n%s\nreturn 0;}\n"
                          % body)
        mac, rel, com = (tmp_path / (name + e) for e in (".mac", ".rel", ".com"))
        r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                            "-o", str(mac)], capture_output=True, text=True,
                           cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)],
                             capture_output=True, timeout=60, env=env)
        return run.stdout.decode("latin-1").replace("\r", "")

    def test_initialiser(self, tmp_path):
        out = self.build(tmp_path, """
  struct S v = (note(), g);
  printf("a=%d b=%lld c=%f n=%d\\n", v.a, v.b, v.c, flag);""", "si")
        assert out == "a=1 b=123456789012 c=2.500000 n=1\n"

    def test_argument_keeps_the_side_effect(self, tmp_path):
        out = self.build(tmp_path, """
  takes((note(), g));
  printf("n=%d\\n", flag);""", "sa")
        assert out == "a=1 b=123456789012 c=2.500000\nn=1\n"

    def test_struct_return(self, tmp_path):
        out = self.build(tmp_path, """
  struct S r = ret();
  printf("a=%d b=%lld n=%d\\n", r.a, r.b, flag);""", "sr")
        assert out == "a=1 b=123456789012 n=1\n"

    def test_member_access(self, tmp_path):
        out = self.build(tmp_path, """
  long long b = (note(), g).b;
  printf("b=%lld n=%d\\n", b, flag);""", "sm")
        assert out == "b=123456789012 n=1\n"

    def test_nested(self, tmp_path):
        out = self.build(tmp_path, """
  int a = (note(), (note(), g)).a;
  printf("a=%d n=%d\\n", a, flag);""", "sn")
        assert out == "a=1 n=2\n"
