"""The float interpreter of the math library (docs/libm_vm.md).

The math functions are byte programs in vmsrc/*.vc.  gen_vm_modules.py makes
lc/*.mac from them.  These tests check that the checked-in files are current,
that no chain of calls between programs is deeper than the stack of the
interpreter, and that each operation of the interpreter works.
"""

import math
import re
import struct
import subprocess
import sys
from pathlib import Path

from ._clib import (build_and_run, encode_f32, skip_without_tools,
                    ulp_distance)

LIB = Path(__file__).resolve().parent.parent / "src" / "uc80" / "lib"
GEN = LIB / "gen_vm_modules.py"


def test_generated_files_are_current(tmp_path):
    """lc/*.mac is what gen_vm_modules.py makes from vmsrc/."""
    env = {"GEN_VM_OUT": str(tmp_path), "PATH": ""}
    r = subprocess.run([sys.executable, str(GEN)], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    made = sorted(p.name for p in tmp_path.glob("*.mac"))
    assert made, "the generator made no file"
    for name in made:
        assert (tmp_path / name).read_text() == (LIB / "lc" / name).read_text(), (
            name + " is not current: run gen_vm_modules.py")


def stack_graph():
    """{program label: (height, [labels it calls])} from the ;STACK lines."""
    graph = {}
    for mac in (LIB / "lc").glob("*.mac"):
        for line in mac.read_text().splitlines():
            m = re.match(r";STACK (\S+) (\d+)\s*(.*)", line)
            if m:
                graph[m.group(1)] = (int(m.group(2)), m.group(3).split())
    return graph


def test_stack_depth():
    """The deepest chain of programs fits in the stack of the interpreter."""
    text = (LIB / "lc" / "lc_vm.mac").read_text()
    size = int(re.search(r"_vm_stk:\s*DS\s+(\d+)", text).group(1)) // 4
    graph = stack_graph()
    assert len(graph) > 30
    memo = {}

    def depth(label):
        if label not in memo:
            memo[label] = 0     # a loop would be an error
            height, calls = graph[label]
            memo[label] = height + max([depth(c) for c in calls] or [0])
        return memo[label]

    worst = max(depth(label) for label in graph)
    assert worst <= size, "a chain of programs needs %d of %d elements" % (
        worst, size)


@skip_without_tools
def test_operations(tmp_path):
    """sin, cos, tan, pow and atan2 of a few values: the reduction, the
    polynomial, the table, the calls and the conditions of the machine."""
    out = build_and_run(tmp_path, """
#include <stdio.h>
#include <math.h>
union u { float f; unsigned long l; };
volatile float vx = 1.0f, vy = 100.0f, vz = 1e30f, vw = 3.0f;
int main(void)
{
    union u r;
    r.f = sin(vx); printf("%08lX\\n", r.l);
    r.f = cos(vy); printf("%08lX\\n", r.l);
    r.f = tan(vz); printf("%08lX\\n", r.l);
    r.f = pow(vx + vx, vw); printf("%08lX\\n", r.l);
    r.f = atan2(vx, -vw); printf("%08lX\\n", r.l);
    r.f = exp(-vy); printf("%08lX\\n", r.l);
    return 0;
}
""")
    got = [int(w, 16) for w in out.split()]
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]
    want = [math.sin(1.0), math.cos(100.0), math.tan(f32(1e30)), 8.0,
            math.atan2(1.0, -3.0), math.exp(-100.0)]
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert ulp_distance(g, encode_f32(w)) <= 3, (hex(g), w)
