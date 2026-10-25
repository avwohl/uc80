"""Per-instruction T-state sweep of the core against z88dk-ticks.

Every opcode of the base, CB, ED, DD, FD, DDCB and FDCB tables runs alone
(after one of two flag preambles) under z88dk-ticks and under the core.
Control flow that leaves the line (RET, RST, JP (HL), HALT) is left out; a
jump, call or relative jump gets a target that is the next address.

    python3 -m romrun.sweep --write golden/ticks_sweep.txt   (needs ticks)
    python3 -m romrun.sweep --check golden/ticks_sweep.txt   (needs no ticks)

The golden file has one line per case: the instruction bytes, the
preamble number, the T-states ticks reports and the T-states the core
reports.  `--check` runs the core only and compares with the core column.
"""

from __future__ import annotations

import argparse
import os
import sys

from .difftest import START, TICKS_DEFAULT, run_ticks

PREAMBLES = [b"", b"\x3e\xff\xb7\x37"]    # none; A=FF, OR A, SCF
SKIP_BASE = {0x76, 0xC9, 0xE9, 0xCB, 0xDD, 0xED, 0xFD, 0xC7, 0xCF, 0xD7,
             0xDF, 0xE7, 0xEF, 0xF7, 0xFF, 0xC0, 0xC8, 0xD0, 0xD8, 0xE0,
             0xE8, 0xF0, 0xF8}
GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "golden", "ticks_sweep.txt")


def cases() -> list[bytes]:
    """The instruction byte strings, with operands for the jump targets
    left as the marker 'XX' bytes patched by `build`."""
    out = []
    for op in range(256):
        if op in SKIP_BASE:
            continue
        out.append(bytes([op]))
    for op in range(256):
        out.append(bytes([0xCB, op]))
    for op in range(256):
        # no RETN/RETI; no repeating block ops (65536 iterations)
        if 0x40 <= op < 0xC0 and op not in (
                0x45, 0x4D, 0x55, 0x5D, 0x65, 0x6D, 0x75, 0x7D,
                0xB0, 0xB1, 0xB2, 0xB3, 0xB8, 0xB9, 0xBA, 0xBB):
            out.append(bytes([0xED, op]))
    for pre in (0xDD, 0xFD):
        for op in range(256):
            if op in SKIP_BASE or op in (0xCB, 0xDD, 0xED, 0xFD, 0xE9):
                continue
            out.append(bytes([pre, op]))
        for op in range(256):
            out.append(bytes([pre, 0xCB, 0, op]))
    return out


def operands(ins: bytes, end: int) -> bytes:
    """The bytes that follow `ins`: displacement, immediate, address.  A
    jump or call gets `end` as the target."""
    if len(ins) == 4 or (ins[0] == 0xCB):
        return b""                     # CB op, DD CB d op
    if ins[0] == 0xED:
        return bytes(2) if ins[1] in (0x43, 0x4B, 0x53, 0x5B, 0x63, 0x6B,
                                      0x73, 0x7B) else b""
    o = ins[-1]
    x, y, z = o >> 6, (o >> 3) & 7, o & 7
    out = b""
    if len(ins) == 2 and (o in (0x34, 0x35, 0x36) or (
            x in (1, 2) and (z == 6 or (x == 1 and y == 6)))):
        out += b"\x00"                 # (IX+d)
    if o in (0xC3, 0xCD) or (x == 3 and z in (2, 4)):
        return out + bytes([end & 255, end >> 8])
    if (x == 0 and z == 1 and y % 2 == 0) or (x == 0 and z == 2 and y >= 4):
        return out + bytes(2)
    if (x == 0 and z == 6) or (x == 3 and z in (3, 6) and (
            z == 6 or y in (2, 3))):
        return out + b"\x00"
    if o in (0x10, 0x18) or (x == 0 and z == 0 and y >= 4):
        return out + b"\x00"
    return out


def image(ins: bytes, pre: bytes) -> tuple[bytes, int]:
    """The image (1 KiB) and the end address for one case."""
    # the length is known before the target: operands() only needs `end`
    # for its value, not for its size
    n = len(operands(ins, 0))
    end = START + len(pre) + len(ins) + n
    code = pre + ins + operands(ins, end)
    img = bytearray(0x400)
    img[START:START + len(code)] = code
    return bytes(img), end


def core_t(img: bytes, end: int) -> int:
    from .z80 import Z80
    c = Z80()
    c.mem[:len(img)] = img
    c.set(PC=START, SP=0, A=0, F=0x40, A_=0, F_=0x40)
    c.poke(budget=1 << 40)
    # run one instruction at a time until PC reaches end
    for _ in range(100):
        s = c.get()
        if s["PC"] == end:
            return s["T"]
        c.poke(budget=s["T"] + 1)
        c.run()
    return -1


def write(path: str, ticks: str) -> int:
    lines = []
    for ins in cases():
        for pi, pre in enumerate(PREAMBLES):
            img, end = image(ins, pre)
            _, _, tt = run_ticks(ticks, img, end)
            lines.append(f"{ins.hex()} {pi} {tt} {core_t(img, end)}")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("# bytes preamble ticks_T core_T (see romrun/sweep.py)\n")
        f.write("\n".join(lines) + "\n")
    return len(lines)


def load(path: str = GOLDEN) -> list[tuple[bytes, int, int | None, int]]:
    out = []
    for ln in open(path):
        if ln.startswith("#") or not ln.strip():
            continue
        h, pi, tt, ct = ln.split()
        out.append((bytes.fromhex(h), int(pi),
                    None if tt == "None" else int(tt), int(ct)))
    return out


def check(path: str = GOLDEN) -> list[str]:
    """Run the core on each golden case; list the cases whose T-states
    differ from the core column."""
    bad = []
    for ins, pi, tt, ct in load(path):
        img, end = image(ins, PREAMBLES[pi])
        t = core_t(img, end)
        if t != ct:
            bad.append(f"{ins.hex()} pre{pi}: golden core={ct} now={t}")
    return bad


def diffs(path: str = GOLDEN) -> list[tuple[str, int, int | None, int]]:
    """The cases where ticks and the core differ."""
    return [(i.hex(), p, tt, ct) for i, p, tt, ct in load(path) if tt != ct]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", metavar="FILE")
    ap.add_argument("--check", metavar="FILE", nargs="?", const=GOLDEN)
    ap.add_argument("--ticks", default=TICKS_DEFAULT)
    a = ap.parse_args(argv)
    if a.write:
        print(write(a.write, a.ticks), "cases written")
    if a.check:
        bad = check(a.check)
        print("\n".join(bad) or "core agrees with the golden core column")
        for d in diffs(a.check):
            print("ticks differs:", d)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
