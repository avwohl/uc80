"""Compile a C file while every text pass of uc80 also runs on its input
with a ``;#line`` marker after each instruction and each label, and report
how many calls gave other instructions (the probe of the debugger plan,
D2).  Usage: python tests/_marker_probe.py EVERY C-FILE [uc80 args].

The last line of standard error is ``RES {json}``: for each pass,
[calls, calls with other instructions, calls that lost a marker].
"""

import json
import re
import sys

every = int(sys.argv[1])
args = sys.argv[2:]

import uc80.main as M                      # noqa: E402
import uc80.peep_local as PL               # noqa: E402
import uc80.peep_flow as PF                # noqa: E402
import uc80.looprot as LR                  # noqa: E402
import uc80.reglocals as RL                # noqa: E402
import uc80.iyptr as IY                    # noqa: E402
from upeepz80 import PeepholeOptimizer     # noqa: E402

_LABEL = re.compile(r"^@?[A-Za-z_?$.][\w@?$.]*:")
counter = [0]


def with_markers(code):
    out = []
    n = 0
    for ln in code.split("\n"):
        out.append(ln)
        ins = (ln[:1] in (" ", "\t") and ln.strip()
               and not ln.lstrip().startswith(";"))
        lab = ln[:1] not in (" ", "\t", ";", "") and _LABEL.match(ln)
        if ins or lab:
            n += 1
            if n % every == 0:
                counter[0] += 1
                out.append(";#line 1 %d" % counter[0])
    return "\n".join(out)


def markers(code):
    return [ln for ln in code.split("\n") if ln.startswith(";#")]


def code_of(code):
    return "\n".join(ln for ln in code.split("\n") if not ln.startswith(";#"))


res = {}


def record(name, plain, marked_in, marked_out):
    r = res.setdefault(name, [0, 0, 0])
    r[0] += 1
    if code_of(plain) != code_of(marked_out):
        r[1] += 1
    want, got = markers(marked_in), markers(marked_out)
    # a marker of code that a pass deleted may go, with that code
    if name not in ("asm_dce",) and any(
            got.count(m) < want.count(m) for m in set(want)):
        r[2] += 1


def wrap(name, fn):
    def run(*a, **k):
        plain = fn(*a, **k)
        if a and isinstance(a[0], str):
            marked = with_markers(a[0])
            record(name, plain, marked, fn(marked, *a[1:], **k))
        return plain
    return run


_optimize = PeepholeOptimizer.optimize


def upeep(self, code):
    plain = _optimize(self, code)
    marked = with_markers(code)
    record("upeepz80", plain, marked, _optimize(PeepholeOptimizer(), marked))
    return plain


PeepholeOptimizer.optimize = upeep
PL.optimize = wrap("peep_local", PL.optimize)
PF.optimize = wrap("peep_flow", PF.optimize)
LR.rotate = wrap("looprot", LR.rotate)
RL.prune = wrap("reglocals.prune", RL.prune)
RL.drop_dead_copies = wrap("reglocals.drop_dead_copies", RL.drop_dead_copies)
IY.prune_iy = wrap("iyptr.prune_iy", IY.prune_iy)
M.asm_eliminate_dead_code = wrap("asm_dce", M.asm_eliminate_dead_code)
sys.argv = ["uc80"] + args
rc = M.main()
print("RES " + json.dumps(res), file=sys.stderr)
sys.exit(rc)
