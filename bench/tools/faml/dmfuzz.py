#!/usr/bin/env python3
"""dmfuzz.py [--count N] [--seed S] [-j J] [--flags=F]: random programs of
x / y and x % y pairs (family L, L4), uc80 against the host compiler.

Each function has int and unsigned locals, a divisor that is never zero
and never -1, and a random block of statements: quotients and remainders of
the same or of different operands, assignments that change an operand
between two divides, loops, `if` blocks, calls.  Values are masked after
each assignment so that 16-bit and 32-bit arithmetic agree.  The program
prints a checksum of every function.  Statuses: PASS, WRONG, BUILD-FAIL.
"""
import argparse, os, random, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def gen(seed):
    r = random.Random(seed)
    out = ["#include <stdio.h>", "static long chk;",
           "static int gi = 7;", "static unsigned gu = 9;",
           "static int bump(int v) { gi = (gi * 3 + v) & 255; return gi; }"]
    nf = r.randint(2, 4)
    const_ok = r.random() < 0.4      # constant divisors make lone divides
    pure = r.random() < 0.6          # signed divides only in pairs
    for f in range(nf):
        sv = ["x", "y", "z"]
        uv = ["u", "v", "w"]
        body = []

        def sdiv():
            return r.choice(["d1", "d2"])

        def stmt(depth=0):
            k = r.randint(0, 13)
            if not const_ok and k in (8, 9):
                k = 0
            a, b = r.choice(sv), r.choice(sv)
            c = r.choice(sv)
            d = sdiv()
            if pure and k in (0, 1, 2, 3, 8, 9):
                # a signed pair, with an unrelated statement between
                a = r.choice([v for v in sv if v != b])
                first = r.choice([f"{a} = {b} % {d};", f"{a} = {b} / {d};"])
                second = (f"{c} = {b} / {d};" if "%" in first
                          else f"{c} = {b} % {d};")
                if r.random() < 0.5:
                    second = second.replace("= ", "= 1 + ", 1) \
                        if r.random() < 0.3 else second
                fill = ""
                if r.random() < 0.5:
                    fill = "\n    gi = ((gi + 3) & 255);"
                return first + fill + "\n    " + second
            if k == 0:
                return f"{a} = {b} % {d};"
            if k == 1:
                return f"{a} = {b} / {d};"
            if k == 2 and not pure:
                return f"{a} = ({b} / {d}) + ({c} % {d});"
            if k == 3:
                return f"{a} = {a} / {d};"
            if k == 4:
                return f"{a} = (({a} + {c}) & 1023) - 512;"
            if k == 5 and not pure:
                return f"{d} = {d} + 1;" if d == "d1" else f"{d} = {d} - 1;"
            if k == 6:
                ua, ub = r.choice(uv), r.choice(uv)
                ud = r.choice(["e1", "e2"])
                return f"{ua} = {ub} % {ud};"
            if k == 7:
                ua, ub = r.choice(uv), r.choice(uv)
                ud = r.choice(["e1", "e2"])
                return f"{ua} = ({ub} / {ud} + {ua}) & 0xffff;"
            if k == 8:
                return f"{a} = {b} % 10;"
            if k == 9:
                return f"{a} = {b} / 10;"
            if k == 10:
                return f"{a} = bump({a}) - 100;"
            if k == 11 and depth < 2:
                n = r.randint(1, 3)
                inner = "\n    ".join(stmt(depth + 1) for _ in range(n))
                cv = "ij"[depth]
                return f"for ({cv} = 0; {cv} < {r.randint(1, 3)}; {cv}++) {{\n    {inner}\n    }}"
            if k == 12 and depth < 2:
                n = r.randint(1, 3)
                inner = "\n    ".join(stmt(depth + 1) for _ in range(n))
                return f"if ({a} > {b}) {{\n    {inner}\n    }}"
            ub = r.choice(uv)
            ua = r.choice([v for v in uv if v != ub])
            uc = r.choice(uv)
            e = r.choice(["e1", "e2"])
            f1 = f"{ua} = {ub} % {e};"
            f2 = f"{uc} = ({ub} / {e}) & 0xffff;"
            if r.random() < 0.5:
                f1, f2 = f"{ua} = ({ub} / {e}) & 0xffff;", f"{uc} = {ub} % {e};"
            return f1 + ("\n    gu = (gu + 5) & 255;" if r.random() < 0.5 else "") + "\n    " + f2
        n = r.randint(3, 9)
        for _ in range(n):
            body.append(stmt())
        body_s = "\n    ".join(body)
        out.append(f"""static long f{f}(int a, int b, unsigned ua, unsigned ub)
{{
    int x = (a & 1023) - 512, y = (b & 1023) - 512, z = (a ^ b) & 255;
    unsigned u = ua & 0x3fff, v = ub & 0x3fff, w = (ua + ub) & 0xff;
    int d1 = (a & 15) + 2, d2 = -((b & 7) + 2);
    unsigned e1 = (ua & 31) + 2, e2 = (ub & 7) + 3;
    int i, j;
    long s;
    {body_s}
    s = (long) x * 3 + (long) y * 5 + (long) z * 7 + (long) u * 11
        + (long) v * 13 + (long) w * 17 + d1 + d2;
    return s;
}}""")
    calls = "\n    ".join(
        f"chk = (chk * 31 + f{f}(i * 37 - 100, i * 91 + 5, i * 333 + 1, i * 77 + 3)) & 0xffffff;"
        for f in range(nf))
    out.append(f"""int main(void)
{{
    int i;
    for (i = 0; i < 12; i++) {{
    {calls}
    }}
    printf("%ld\\n", chk & 0xffffff);
    return 0;
}}""")
    return "\n".join(out) + "\n"


def run_one(seed, flags, work):
    d = Path(work) / f"s{seed}"
    d.mkdir(parents=True, exist_ok=True)
    src = d / "p.c"
    src.write_text(gen(seed))
    h = subprocess.run(["cc", "-O1", "-w", "-fwrapv", "-o", str(d / "h"), str(src)],
                       capture_output=True, text=True)
    if h.returncode:
        return seed, "HOST-FAIL", h.stderr[:200]
    want = subprocess.run([str(d / "h")], capture_output=True, text=True,
                          timeout=20).stdout.strip()
    # the host uses 32-bit int: wrap the checksum the way a 32-bit long does
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src), "-o",
                        str(d / "p.mac")] + flags, capture_output=True,
                       text=True, cwd=str(REPO))
    if r.returncode:
        return seed, "BUILD-FAIL", r.stderr[-300:]
    lib = subprocess.run([sys.executable, "-m", "uc80.main", "--print-lib-dir"],
                         capture_output=True, text=True, cwd=str(REPO)).stdout.strip()
    for cmd in (["um80", str(d / "p.mac"), "-o", str(d / "p.rel")],
                ["ul80", str(d / "p.rel"), lib + "/libc.lib", lib + "/runtime.lib",
                 "-o", str(d / "p.com")]):
        x = subprocess.run(cmd, capture_output=True, text=True)
        if x.returncode:
            return seed, "BUILD-FAIL", x.stdout[-200:] + x.stderr[-200:]
    cpm = os.path.expanduser("~/src/cpmemu/src/cpmemu")
    try:
        g = subprocess.run([cpm, str(d / "p.com")], capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return seed, "HANG", ""
    got = [l for l in g.stdout.decode("latin-1").replace("\r", "").split("\n")
           if l and not l.startswith(("CPU mode", "Loaded ", "Program exit", "Note:"))]
    got = got[-1].strip() if got else ""
    if got != want:
        return seed, "WRONG", f"want {want} got {got}"
    text = (d / "p.mac").read_text()
    return seed, "PASS", ("pair" if "call\t__sdivmod16" in text else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-j", type=int, default=3)
    ap.add_argument("--flags", default="")
    ap.add_argument("--work", default=None)
    ap.add_argument("--print", type=int, default=None)
    a = ap.parse_args()
    if a.print is not None:
        print(gen(a.print))
        return 0
    work = a.work or tempfile.mkdtemp(prefix="dmfuzz-")
    flags = a.flags.split()
    res = {}
    with ThreadPoolExecutor(a.j) as ex:
        for seed, st, msg in ex.map(lambda s: run_one(s, flags, work),
                                    range(a.seed, a.seed + a.count)):
            res[st] = res.get(st, 0) + 1
            if msg == "pair":
                res["sdivmod16"] = res.get("sdivmod16", 0) + 1
            elif st != "PASS":
                print(seed, st, msg)
    print(a.count, "programs:", res)
    return 1 if set(res) - {"PASS", "sdivmod16"} else 0


if __name__ == "__main__":
    sys.exit(main())
