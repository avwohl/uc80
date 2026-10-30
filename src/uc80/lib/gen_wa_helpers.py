#!/usr/bin/env python3
"""Add the work-area save helpers to the library modules.

An interrupt function that reaches a library routine with work areas at
fixed RAM addresses (lc_vm, lc_sqrt, rt_fdata, ...) must save and restore
the areas (--isr-save-workareas, docs/rom_hardware.md).  The data labels of
a library module are private, so each module with a work area gets two
public routines that know the area:

    __wasv_<block>   push the area onto the stack (the call returns with
                     the area below the return point)
    __wars_<block>   copy the area back and drop it from the stack

Both routines use IY for the return address (the prologue of an interrupt
function has saved IY) and change AF, BC, DE, HL.  A module is linked only
when a program references one of its symbols, so a handler that reaches no
library work area pays nothing.

    python3 gen_wa_helpers.py          # add or update the helpers
    python3 gen_wa_helpers.py --check  # exit 1 when a module is out of date

The blocks that hold state (STATE_BLOCKS in isr_check.py) get no helper.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from uc80 import isr_check  # noqa: E402

BEGIN = "; ---- BEGIN work-area save helpers (gen_wa_helpers.py) ----"
END_MARK = "; ---- END work-area save helpers ----"
DIRS = ("rt", "lc")


def helper_text(block: str, first: str, size: int) -> str:
    return "\n".join([
        BEGIN,
        "\tCSEG",
        f"\tPUBLIC\t__wasv_{block}",
        f"\tPUBLIC\t__wars_{block}",
        f"__wasv_{block}:",
        "\tPOP\tIY",
        f"\tLD\tHL,-{size}",
        "\tADD\tHL,SP",
        "\tLD\tSP,HL",
        "\tEX\tDE,HL",
        f"\tLD\tHL,{first}",
        f"\tLD\tBC,{size}",
        "\tLDIR",
        "\tJP\t(IY)",
        f"__wars_{block}:",
        "\tPOP\tIY",
        "\tLD\tHL,0",
        "\tADD\tHL,SP",
        f"\tLD\tDE,{first}",
        f"\tLD\tBC,{size}",
        "\tLDIR",
        "\tLD\tSP,HL",
        "\tJP\t(IY)",
        END_MARK, ""])


def strip_helpers(text: str) -> str:
    return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END_MARK) + r"\n\n",
                  "", text, flags=re.S)


def plan_file(path: Path):
    """(block, first label, size) for a module with one work area, else
    None."""
    text = strip_helpers(path.read_text())
    m = re.search(r"^\s*COMMON\s+/([^/]*)/", text, re.M)
    if not m or not m.group(1):
        return None
    block = m.group(1)
    if block in isr_check.STATE_BLOCKS:
        return None
    g = isr_check.Graph()
    isr_check._parse_unit(text, 0, g, is_lib=False, only_missing=False)
    if not g.layout0:
        return None
    sizes = [s for _l, s in g.layout0]
    if None in sizes or sum(sizes) == 0:
        return None
    return block, g.layout0[0][0], sum(sizes)


def main(argv) -> int:
    check = "--check" in argv
    files = [p for d in DIRS for p in sorted((HERE / d).glob("*.mac"))]
    plans = {p: plan_file(p) for p in files}
    count = {}
    for pl in plans.values():
        if pl:
            count[pl[0]] = count.get(pl[0], 0) + 1
    stale = []
    for path, pl in plans.items():
        old = path.read_text()
        base = strip_helpers(old)
        if pl and count[pl[0]] == 1:
            end = re.search(r"^[ \t]*END\b.*$", base, re.M | re.I)
            if end is None:
                continue
            new = (base[:end.start()] + helper_text(*pl) + "\n"
                   + base[end.start():])
        else:
            new = base
        if new != old:
            stale.append(path.name)
            if not check:
                path.write_text(new)
    if check:
        for n in stale:
            print("out of date:", n)
        return 1 if stale else 0
    print(f"{len(stale)} module(s) changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
