#!/usr/bin/env python3
"""Random counted loops: uc80 against the host compiler.

usage: bench/loopfuzz.py SEED COUNT [-j N] [--flags=...]  (docs/k2_code_size.md)

Each program has nested loops with constant init and bound, small steps,
several counter types, bodies with array stores through the counters,
accumulations, if/else with break/continue, calls, a global counter, and a
checksum that is printed.  The program uses only types whose size is the
same for the host and for uc80 (int16_t, uint16_t, int8/uint8, int32).
"""
import argparse
import os
import random
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CPMEMU = str(REPO.parent / "cpmemu" / "src" / "cpmemu")
LIB = REPO / "src" / "uc80" / "lib"

TYPES = ["int16_t", "uint16_t", "uint8_t", "int8_t", "int32_t", "uint32_t"]
OPS = ["<", "<=", "!="]


def gen(seed: int) -> str:
    r = random.Random(seed)
    nvars = r.randint(1, 4)
    names = ["i", "j", "k", "m"][:nvars]
    vtypes = {n: r.choice(TYPES) for n in names}
    lines = ["#include <stdio.h>", "#include <stdint.h>",
             "static uint16_t sum;",
             "int16_t gtab[4][64];",
             "int16_t gc;",
             "static void bump(void) { gc++; }",
             "static uint16_t f(uint16_t x) { return (uint16_t)(x * 7 + 3); }",
             "int main(void) {"]
    for n in names:
        lines.append(f"  {vtypes[n]} {n} = 0;")
    lines.append("  int16_t a[64]; uint16_t t; int q;")
    lines.append("  for (q = 0; q < 64; q++) a[q] = (int16_t)(q * 3 + 1);")
    lines.append("  sum = 1; t = 0;")

    def expr(depth=0):
        choices = []
        for n in names:
            choices.append(f"(int16_t){n}")
        choices += [str(r.randint(0, 9)), "(int16_t)t"]
        e = r.choice(choices)
        if depth < 2 and r.random() < 0.6:
            op = r.choice(["+", "-", "*", "&", "|", "^"])
            e = f"((int16_t)({e} {op} {expr(depth + 1)}))"
        return e

    def idx(avail):
        parts = []
        for n in avail:
            c = r.choice([1, 2, 3, 4, 8])
            parts.append(f"{n} * {c}")
        parts.append(str(r.randint(0, 3)))
        return "(" + " + ".join(parts) + ") & 63"

    def body(avail, depth, in_loop):
        out = []
        for _ in range(r.randint(1, 3)):
            kind = r.random()
            if kind < 0.35:
                out.append(f"a[{idx(avail)}] = (int16_t)({expr()});")
            elif kind < 0.55:
                out.append(f"sum = (uint16_t)(sum * 31 + (uint16_t)({expr()}));")
            elif kind < 0.65:
                out.append(f"gtab[{r.randint(0,3)}][{idx(avail)}] = (int16_t)({expr()});")
            elif kind < 0.72:
                out.append("bump(); sum = (uint16_t)(sum + gc);")
            elif kind < 0.78:
                out.append(f"t = f((uint16_t)({expr()}));")
            elif kind < 0.9 and in_loop:
                c = r.choice(["break", "continue"])
                out.append(f"if (({expr()} & 3) == {r.randint(0,3)}) {c};")
            else:
                out.append(f"if (({expr()}) > {r.randint(-3, 9)}) sum++; else t += 2;")
        return out

    def loop(avail_all, remaining, depth):
        n = remaining[0]
        t = vtypes[n]
        start = r.randint(0, 3)
        op = r.choice(OPS)
        step = r.choice([1, 1, 1, 2, 3])
        bound = start + r.randint(0, 5) * (step if op == "!=" else 1)
        if op == "!=":
            bound = start + step * r.randint(0, 4)
        cond = f"{n} {op} {bound}"
        upd = f"{n}++" if step == 1 and r.random() < 0.7 else f"{n} += {step}"
        cur = avail_all + [n]
        inner = []
        if len(remaining) > 1 and r.random() < 0.8:
            inner += loop(cur, remaining[1:], depth + 1)
        inner += body(cur, depth, True)
        if r.random() < 0.3:
            inner = body(cur, depth, True) + inner
        text = [f"for ({n} = {start}; {cond}; {upd}) {{"] + ["  " + x for x in inner] + ["}"]
        return text
    k = r.randint(1, 3)
    for _ in range(k):
        order = names[:]
        r.shuffle(order)
        order = order[:r.randint(1, len(order))]
        for ln in loop([], order, 0):
            lines.append("  " + ln)
        lines.append("  " + " ".join(f'sum = (uint16_t)(sum * 3 + (uint16_t){n});' for n in names))
    lines.append("  for (q = 0; q < 64; q++) sum = (uint16_t)(sum * 5 + (uint16_t)a[q]);")
    lines.append("  for (q = 0; q < 64; q++) sum = (uint16_t)(sum * 5 + (uint16_t)gtab[1][q] + (uint16_t)gtab[2][q]);")
    lines.append('  printf("%lu %lu %lu\\n", (unsigned long)sum, (unsigned long)t, (unsigned long)(uint16_t)gc);')
    lines.append("  return 0;")
    lines.append("}")
    return "\n".join(lines) + "\n"


def one(seed: int, flags: list[str]):
    d = Path(tempfile.mkdtemp(prefix=f"lf{seed}_"))
    src = d / "t.c"
    src.write_text(gen(seed))
    host = subprocess.run(["cc", "-O0", "-w", "-o", str(d / "host"), str(src)],
                          capture_output=True, text=True, timeout=60)
    if host.returncode:
        return seed, "HOST-FAIL", host.stderr[:100]
    try:
        want = subprocess.run([str(d / "host")], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except subprocess.TimeoutExpired:
        return seed, "HOST-HANG", ""
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src), "-o",
                        str(d / "t.mac"), *flags], capture_output=True,
                       text=True, env=env, timeout=300, cwd=str(REPO))
    if r.returncode:
        return seed, "UC80-FAIL", r.stderr[-200:]
    a = subprocess.run(["um80", str(d / "t.mac"), "-o", str(d / "t.rel")],
                       capture_output=True, text=True, timeout=120)
    if a.returncode or "Error" in a.stdout + a.stderr:
        return seed, "ASM-FAIL", (a.stdout + a.stderr)[-200:]
    ln = subprocess.run(["ul80", str(d / "t.rel"), str(LIB / "libc.lib"),
                         str(LIB / "runtime.lib"), "-o", str(d / "t.com")],
                        capture_output=True, text=True, timeout=120)
    if ln.returncode:
        return seed, "LINK-FAIL", (ln.stdout + ln.stderr)[-200:]
    try:
        run = subprocess.run([CPMEMU, str(d / "t.com")], capture_output=True,
                             timeout=120)
    except subprocess.TimeoutExpired:
        return seed, "HANG", ""
    got = run.stdout.decode("latin-1").replace("\r", "")
    got = "\n".join(x for x in got.split("\n")
                    if not x.startswith(("CPU mode", "Loaded ", "Program exit", "Note:"))).strip()
    if got != want:
        return seed, "WRONG", f"want {want!r} got {got!r}"
    subprocess.run(["rm", "-rf", str(d)])
    return seed, "PASS", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("seed", type=int)
    ap.add_argument("count", type=int)
    ap.add_argument("-j", type=int, default=6)
    ap.add_argument("--flags", default="")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    if a.print:
        print(gen(a.seed))
        return
    flags = a.flags.split()
    stats = {}
    with ThreadPoolExecutor(a.j) as ex:
        for seed, status, note in ex.map(lambda s: one(s, flags),
                                         range(a.seed, a.seed + a.count)):
            stats[status] = stats.get(status, 0) + 1
            if status != "PASS":
                print(f"seed {seed}: {status} {note}", flush=True)
    print(f"{a.count} programs: {stats}")


if __name__ == "__main__":
    main()
