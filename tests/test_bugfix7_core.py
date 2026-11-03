"""Bug round 7, uc_core items: a label at the end of a block, a label before a
declaration, and the macro cases that a host cc -E settles.  Each program
runs under cpmemu and the host cc, and the two outputs must agree."""

import shutil
import subprocess

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

HOST = shutil.which("cc")


def host_run(tmp_path, src):
    c = tmp_path / "host.c"
    c.write_text(src)
    exe = tmp_path / "host"
    r = subprocess.run([HOST, "-w", "-std=c2x", str(c), "-o", str(exe)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return subprocess.run([str(exe)], capture_output=True, text=True,
                          timeout=60).stdout


def same_as_host(tmp_path, src, want):
    (tmp_path / "z80").mkdir()
    (tmp_path / "h").mkdir()
    got = build_and_run(tmp_path / "z80", src)
    assert got == want
    if HOST:
        assert host_run(tmp_path / "h", src) == want


def test_labels_at_end_of_blocks_run(tmp_path):
    src = r"""#include <stdio.h>
int sw(int x)
{
  int r = 0;
  switch (x) {
  case 0: r = 5; break;
  case 1:
  }
  return r;
}
int sw2(int x)
{
  int r = 7;
  switch (x) {
  case 0: r = 1; break;
  default:
  }
  return r;
}
int lab(int n)
{
  int s = 0;
  goto L;
  s = 99;
  L:
  for (; n > 0; n--) {
    s += n;
    if (n == 2) goto END;
  }
  END:
  return s;
}
int decl(int a)
{
  goto D;
D: int y = 4;
  return y + a;
}
int main(void)
{
  printf("%d %d %d %d\n", sw(0), sw(1), sw(2), sw2(0));
  printf("%d %d %d\n", sw2(1), lab(4), decl(1));
  {
  L1:
  }
  return 0;
}
"""
    same_as_host(tmp_path, src, "5 0 0 1\n7 9 5\n")


def test_case_label_before_declaration_run(tmp_path):
    src = r"""#include <stdio.h>
int f(int x)
{
  int r = 0;
  switch (x) {
  case 1:
    int y = 2;
    r = y + 10;
    break;
  default:
    static int z = 3;
    r = z;
  }
  return r;
}
int main(void)
{
  printf("%d %d\n", f(1), f(9));
  return 0;
}
"""
    same_as_host(tmp_path, src, "12 3\n")


def test_macro_gaps_run(tmp_path):
    src = r"""#include <stdio.h>
#define EMPTY
#define f(x) x
#define g(x) (x + 1)
#define obj g
#define S(x, ...) #__VA_OPT__(x, __VA_ARGS__)
#define H(x, ...) #__VA_OPT__(x##x)
#define NEXT __COUNTER__
int main(void)
{
  int a = 10;
  int f EMPTY = 20;
  printf("%d %d\n", a, f);
  printf("%d\n", obj
         (4));
  puts(S(a, b, c));
  puts(S(a));
  puts(H(p, 1));
  printf("%d %d %d\n", NEXT, NEXT, __COUNTER__);
  return 0;
}
"""
    same_as_host(tmp_path, src, "10 20\n5\na, b, c\n\npp\n0 1 2\n")


def test_dash_e_keeps_pragma_lines(tmp_path):
    src = tmp_path / "p.c"
    src.write_text('#pragma foo bar\n_Pragma("baz 1") int x;\n')
    r = subprocess.run(["python3", "-m", "uc80.main", "-E", str(src)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "#pragma foo bar" in r.stdout
    assert "#pragma baz 1" in r.stdout
