"""Several C files on one command line.

``uc80 a.c b.c`` merges the files into one program.  A ``static`` function
or variable of one file is not the same object as a static of the same name
in another file, and a typedef or a struct tag that two files define
differently is not one type.  Each program is built from two files, run
under cpmemu, and the output is compared with the value the C rules give.
Headers that two files include (a static inline function, a typedef) still
give each file its own copy of the static and share the identical typedef.
"""

import os
import subprocess
import sys

import pytest

from tests.test_struct_return_abi import CPMEMU, LIB_DIR, REPO, pytestmark  # noqa: F401


def build_files_and_run(tmp_path, files, flags=()):
    paths = []
    for name, text in files.items():
        (tmp_path / name).write_text(text)
        if name.endswith(".c"):
            paths.append(str(tmp_path / name))
    mac, rel, com = (tmp_path / ("prog" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", *paths, *flags, "-o", str(mac)],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)],
                   check=True, capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120, cwd=str(tmp_path))
    out = run.stdout.decode("latin-1").replace("\r", "")
    return "\n".join(ln for ln in out.split("\n")
                     if not ln.startswith(("CPU mode", "Loaded ",
                                           "Program exit", "Note:")))


FLAGS = [(), ("-O0",), ("--no-inlining",)]

SAME_NAMES = {
    "m1.c": '#include <stdio.h>\nextern int shared;\nint twice(int);\nint counter(void);\nstatic int hidden = 10;\nstatic int helper(int x) { return x + 1; }\nstruct P { int a; };\ntypedef int T;\nint total;\nint main(void) {\n  struct P p; p.a = 3;\n  T t = helper(hidden);\n  printf("%d %d %d\\n", t, twice(4), shared);\n  printf("%d %d\\n", counter(), counter());\n  printf("%d\\n", p.a);\n  total += 1;\n  printf("%d\\n", total);\n  return 0;\n}\n',
    "m2.c": 'int shared = 77;\nstatic int hidden = 500;\nstatic int helper(int x) { return x * 100 + hidden; }\nstruct P { char a; char b; long c; };\ntypedef long T;\nint total;\nint twice(int v) { struct P q; q.c = 5; T z = helper(v); return (int)(z + q.c + sizeof(struct P)); }\nint counter(void) { static int n; return ++n + hidden; }\n',
}

SAME_NAMES_OUT = "11 911 77\n502 501\n3\n1\n"

HEADER = {
    "common.h": 'typedef struct node { int v; struct node *next; } node_t;\nstatic inline int sq(int x) { return x * x; }\nstatic int ticks;\n',
    "a.c": '#include <stdio.h>\n#include "common.h"\nstruct pt { int x, y; };\ntypedef struct pt PT;\nenum color { RED, GREEN };\nstatic int count = 1;\nstatic int bump(int count) { count += 10; return count; }\nstatic int local_counter(void) { static int n = 100; return ++n; }\nstruct S { int count; };\nint afunc(int);\nint bfunc(int);\nvoid bdump(void);\nextern int gl;\nint main(void) {\n  node_t a, b; a.v = 3; a.next = &b; b.v = 4; b.next = 0;\n  PT p; p.x = 2; p.y = 5;\n  struct S s; s.count = 9;\n  int i;\n  ticks += 2;\n  for (i = 0; i < 2; i++) { int count = i; ticks += count; }\n  printf("%d %d %d\\n", sq(a.v), a.next->v, p.x * 10 + p.y);\n  printf("%d %d %d\\n", count, bump(1), s.count);\n  printf("%d %d\\n", local_counter(), local_counter());\n  printf("%d %d %d\\n", afunc(2), bfunc(2), ticks);\n  count++;\n  printf("%d %d %d\\n", count, gl, RED + GREEN);\n  bdump();\n  return 0;\n}\nint afunc(int v) { return bump(v) + count; }\n',
    "b.c": '#include <stdio.h>\n#include "common.h"\nstruct pt { char x; long y; long z; };\ntypedef struct pt PT;\nenum color { BLUE = 5, RED };\nstatic int count = 50;\nstatic int bump(int v) { count += v; return count; }\nstatic int local_counter(void) { static int n = 200; return ++n; }\nint gl = 123;\nstatic PT q = { 1, 7L, 8L };\nint bfunc(int v) { ticks += 100; return bump(v) + sq(v) + (int)q.y + (int)sizeof(PT) + local_counter(); }\nvoid bdump(void) { printf("b %d %d %d %d\\n", count, ticks, RED, (int)sizeof(struct pt)); }\n',
}

HEADER_OUT = '9 4 25\n1 11 9\n102 101\n13 273 3\n2 123 1\nb 52 100 6 9\n'


@pytest.mark.parametrize("flags", FLAGS)
def test_statics_typedefs_and_tags_of_two_files_stay_apart(tmp_path, flags):
    out = build_files_and_run(tmp_path, SAME_NAMES, flags)
    assert out == SAME_NAMES_OUT, out


@pytest.mark.parametrize("flags", FLAGS)
def test_header_statics_locals_and_shadowing(tmp_path, flags):
    out = build_files_and_run(tmp_path, HEADER, flags)
    assert out == HEADER_OUT, out
