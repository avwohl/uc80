"""Differential test of the Z80 core against z88dk-ticks.

A random straight-line instruction stream runs under ticks with -trace and
under the romrun core.  The registers before every instruction, the final
memory and the final T-state count must agree.  Jumps, calls with a random
target, returns, RST, HALT and port I/O are not generated.

    python3 -m romrun.difftest [--seed N] [--count N] [--ticks PATH]
"""

from __future__ import annotations

import argparse
import os
import random
import re
import subprocess
import sys
import tempfile

TICKS_DEFAULT = os.path.expanduser(
    "~/src/external/tools/z88dk-2.4/z88dk/bin/z88dk-ticks")
START = 0x0100


def gen_stream(rng: random.Random, n: int) -> bytes:
    """n random instructions as bytes (no control flow that leaves)."""
    out = bytearray()
    # random register preload: LD rr,nn with data-area values
    for op in (0x01, 0x11, 0x21, 0x31):
        out += bytes([op, rng.randrange(256), rng.randrange(0x80, 0xF0)])
    for pre in (0xDD, 0xFD):
        out += bytes([pre, 0x21, rng.randrange(256), rng.randrange(0x80, 0xF0)])
    out += bytes([0x3E, rng.randrange(256)])  # LD A,n
    for _ in range(n):
        out += one(rng)
    return bytes(out)


def one(rng: random.Random) -> bytes:
    r = rng.random()
    if r < 0.55:
        return base(rng)
    if r < 0.70:
        while True:
            o = rng.randrange(256)
            if (o & 0xC7) == 0x46:      # BIT n,(HL): X and Y come from WZ
                continue
            if o < 0x40 and (o & 7) == 6:
                continue                # ticks: Z=0 for a zero result
            return bytes([0xCB, o])
    if r < 0.85:
        return ed(rng)
    pre = rng.choice([0xDD, 0xFD])
    if r < 0.93:
        while True:
            op = rng.randrange(256)
            if op != 0xCB and not documented_indexed(op):
                continue
            b = base(rng, op)
            if b[0] == op:
                return bytes([pre]) + b
    # DD CB d op: the documented forms only (the register in the opcode is
    # 6); ticks gets the flags of the undocumented register copies wrong
    d = rng.randrange(256)
    op = (rng.randrange(0x40, 256) & 0xF8) | 6   # no shifts: see above
    return bytes([pre, 0xCB, d, op])


def documented_indexed(op: int) -> bool:
    """Opcodes with a documented DD/FD form (no IXH, IXL halves)."""
    x, y, z = op >> 6, (op >> 3) & 7, op & 7
    if op in (0x21, 0x22, 0x2A, 0x23, 0x2B, 0xE1, 0xE5, 0xE3, 0xF9, 0x34,
              0x35, 0x36):
        return True
    if op in (0x09, 0x19, 0x29, 0x39):
        return True
    if x == 1 and op != 0x76:
        return (z == 6) != (y == 6)
    if x == 2:
        return z == 6
    return False


def base(rng: random.Random, op: int | None = None) -> bytes:
    while True:
        o = rng.randrange(256) if op is None else op
        op = None
        # 8Ch (ADC A,H) takes 7 T-states in ticks; the Zilog figure is 4
        if o in (0x76, 0xC9, 0xE9, 0xDB, 0xD3, 0xCB, 0xDD, 0xED, 0xFD,
                 0xC3, 0xCD, 0x8C):
            continue
        x, y, z = o >> 6, (o >> 3) & 7, o & 7
        if x == 3 and z in (0, 7):      # RET cc, RST
            continue
        if x == 3 and z == 2:           # JP cc,nn -> next address handled
            return bytes([o, 0, 0])      # placeholder, patched by caller
        if x == 3 and z == 4:
            return bytes([o, 0, 0])
        if o in (0x10, 0x18) or (x == 0 and z == 0 and y >= 4):
            return bytes([o, 0])         # jump over nothing
        n = {0: 1, 1: 0, 2: 0, 3: 0}[x]
        ln = 1
        if x == 0:
            if z == 6:
                ln = 2
            elif z == 1 and y % 2 == 0:
                ln = 3
            elif z == 2 and y >= 4:
                ln = 3
        if x == 3 and z == 6:
            ln = 2
        if x == 3 and z == 1 and y in (1, 5):
            continue
        return bytes([o] + [rng.randrange(256) for _ in range(ln - 1)])


def ed(rng: random.Random) -> bytes:
    while True:
        o = rng.randrange(0x40, 0xC0)
        z = o & 7
        if o >= 0xA0 and (o & 7) > 3:
            continue
        if o >= 0xA0 and (o & 0x10 or (o & 3) >= 2):
            continue                    # no repeat, no block I/O
        if 0x80 <= o < 0xA0:
            continue
        if z == 0 or z == 1:
            if (o >> 3) & 7 == 6 and z == 0:
                pass
            continue                    # IN/OUT (C)
        if z == 5:
            continue                    # RETN / RETI
        if z == 3:
            return bytes([0xED, o, rng.randrange(256), rng.randrange(0x80, 0xF0)])
        return bytes([0xED, o])


CCJ = (0xC2, 0xCA, 0xD2, 0xDA, 0xE2, 0xEA, 0xF2, 0xFA, 0xC4, 0xCC, 0xD4, 0xDC,
       0xE4, 0xEC, 0xF4, 0xFC)


def build(rng: random.Random, n: int) -> bytes:
    prog = bytearray()
    prog += gen_stream(rng, 0)
    for _ in range(n):
        ins = one(rng)
        # patch absolute jumps/calls: target = address after this instruction
        k = 1 if ins[0] in (0xDD, 0xFD) else 0
        if (len(ins) == 3 + k and ins[k] in CCJ and ins[k + 1:] == b"\0\0"):
            nxt = START + len(prog) + len(ins)
            ins = ins[:k + 1] + bytes([nxt & 255, nxt >> 8])
        prog += ins
    return bytes(prog)


def make_image(rng: random.Random, n: int) -> tuple[bytes, int]:
    code = build(rng, n)
    img = bytearray(rng.randrange(256) for _ in range(65536))
    img[0:START] = bytes(START)
    img[START:START + len(code)] = code
    end = START + len(code)
    return bytes(img), end


TRACE_RE = re.compile(
    r"pc=([0-9A-F]{4}), \[pc\]=\w+,\s+bc=([0-9A-F]{4}),\s+de=([0-9A-F]{4}),"
    r"\s+hl=([0-9A-F]{4}),\s+af=([0-9A-F]{4}),\s+ix=([0-9A-F]{4}),"
    r"\s+iy=([0-9A-F]{4})")
TRACE2_RE = re.compile(
    r"sp=([0-9A-F]{4}), \[sp\]=\w+,\s+bc'=([0-9A-F]{4}),\s+de'=([0-9A-F]{4}),"
    r"\s+hl'=([0-9A-F]{4}),\s+af'=([0-9A-F]{4})")


def run_ticks(ticks: str, img: bytes, end: int, mode: str = "-mz80"):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.bin")
        out = os.path.join(d, "mem.bin")
        open(p, "wb").write(img)
        try:
            cp = subprocess.run(
                [ticks, mode, "-trace", "-l", "0", "-pc", f"{START:x}",
                 "-end", f"{end:x}", "-output", out, "-counter", "20000", p],
                capture_output=True, text=True, errors="replace", timeout=10)
        except subprocess.TimeoutExpired:
            return [], b"", None
        mem = open(out, "rb").read()[:65536] if os.path.exists(out) else b""
    states = []
    cur = None
    for line in cp.stdout.splitlines():
        m = TRACE_RE.match(line)
        if m:
            cur = [int(g, 16) for g in m.groups()]
            continue
        m = TRACE2_RE.match(line)
        if m and cur is not None:
            states.append(tuple(cur + [int(g, 16) for g in m.groups()]))
            cur = None
    tail = cp.stdout.rstrip().splitlines()[-1] if cp.stdout.strip() else ""
    m = re.search(r"(\d+)\s*$", tail)
    return states, mem, int(m.group(1)) if m else None


def run_core(img: bytes, end: int, nsteps: int):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from romrun.z80 import Z80
    c = Z80()
    c.mem[:] = img
    c.set(PC=START, SP=0, A=0, F=0x40)
    # ticks starts with af=0040, sp=0000, rest zero
    c.set(A=0, F=0x40, A_=0, F_=0x40)
    states = []
    while True:
        s = c.get()
        if s["PC"] == end:
            break
        states.append((s["PC"], (s["B"] << 8) | s["C"], (s["D"] << 8) | s["E"],
                       (s["H"] << 8) | s["L"], (s["A"] << 8) | s["F"], s["IX"],
                       s["IY"], s["SP"], (s["B_"] << 8) | s["C_"],
                       (s["D_"] << 8) | s["E_"], (s["H_"] << 8) | s["L_"],
                       (s["A_"] << 8) | s["F_"]))
        c.poke(budget=s["T"] + 1)
        c.run()
        if len(states) > 2 * nsteps + 20:
            break
    return states, bytes(c.mem), c.get()["T"]


def compare(seed: int, n: int, ticks: str, verbose=False) -> list[str]:
    rng = random.Random(seed)
    img, end = make_image(rng, n)
    ts, tmem, tt = run_ticks(ticks, img, end)
    cs, cmem, ct = run_core(img, end, len(ts))
    flt = []
    for i, st in enumerate(cs):
        if flt:
            pv = flt[-1]
            if (img[pv[0]] in (0xDD, 0xFD) and st[0] == pv[0] + 1
                    and st[1:] == pv[1:]):
                continue
        flt.append(st)
    cs = flt
    for i, st in enumerate(ts):
        if img[st[0]] == 0x76:      # HALT: a self-modified stream
            ts, cs = ts[:i], cs[:i]
            break
    halted = len(ts) < len(flt)
    errs = []
    names = ["pc", "bc", "de", "hl", "af", "ix", "iy", "sp", "bc'", "de'",
             "hl'", "af'"]
    for i, (a, b) in enumerate(zip(ts, cs)):
        if a != b:
            diffs = [f"{nm}: ticks={x:04X} core={y:04X}"
                     for nm, x, y in zip(names, a, b) if x != y]
            errs.append(f"seed {seed} step {i}: " + ", ".join(diffs)
                        + f"  (prev state {ts[i-1] if i else None})")
            # show the instruction bytes at the previous pc
            if i:
                pc = ts[i - 1][0]
                errs.append("   instruction: " + img[pc:pc + 4].hex())
            break
    if not errs and tt is not None and tt >= 20000:
        pass                      # the stream left the straight line
    elif not errs and halted:
        pass
    elif not errs:
        if len(ts) != len(cs):
            errs.append(f"seed {seed}: step count ticks={len(ts)} core={len(cs)}")
        elif tmem and tmem != cmem:
            bad = [i for i in range(65536) if tmem[i] != cmem[i]]
            errs.append(f"seed {seed}: memory differs at {bad[:5]}")
        elif tt is not None and tt != ct:
            errs.append(f"seed {seed}: T ticks={tt} core={ct}")
    return errs


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--count", type=int, default=300)
    ap.add_argument("--ticks", default=TICKS_DEFAULT)
    a = ap.parse_args(argv)
    bad = 0
    for s in range(a.seed, a.seed + a.runs):
        e = compare(s, a.count, a.ticks)
        if e:
            bad += 1
            print("\n".join(e))
    print(f"{a.runs - bad} of {a.runs} runs agree")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
