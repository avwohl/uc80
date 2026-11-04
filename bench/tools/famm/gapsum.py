#!/usr/bin/env python3
"""gapsum.py PROG...: bucket every byte of uc80 and SDCC (size mode) images from w/PROG/{uc80,sdcc}.
Buckets: crt (startup), io (console library), arith (div/mul/shift/frame helpers), mem (memcpy etc.), funcs (user code), data (strings+initialized data).
Then the gap in the minus-empty column and the accounting part (io)."""
import sys, re, subprocess
from pathlib import Path
H = Path(__file__).parent
def run(*a): return subprocess.run(["python3", *a], capture_output=True, text=True, cwd=H).stdout
EMPTY = {"uc80": 28, "sdcc": 112}
def uc(p):
    lm = run("lmods.py", f"w/{p}/uc80")
    fn = run("fn2.py", f"w/{p}")
    b = dict(crt=24, io=0, arith=0, mem=0, funcs=0, data=0, other=0)
    for l in fn.splitlines()[1:]:
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)", l)
        if not m or "total" in l: continue
        v, n = int(m.group(1)), m.group(3)
        if n == "<crt0>": b["crt"] = 24; b["funcs"] += 0; b["crt"] += v-20 if False else 0
        elif n in ("<data>", "<str>"): b["data"] += v
        elif n.startswith("<helpers>") or n.startswith("<rt:"): b["arith"] += v
        elif n.startswith("<"): b["other"] += v
        else: b["funcs"] += v
    libs = {}
    for l in lm.splitlines():
        m = re.match(r"\s*(\d+) code\s+(\d+) data\s+(\S+)\s+@\w+\s*(.*)", l)
        if m and m.group(3) != "PROG":
            libs[m.group(3)] = libs.get(m.group(3), 0) + int(m.group(1)) + int(m.group(2))
    return b, libs
def sd(p):
    sm = run("sdmods.py", f"w/{p}/sdcc")
    fn = run("fn2.py", f"w/{p}")
    b = dict(crt=62, io=46, arith=0, mem=0, funcs=0, data=0, other=0)
    libs = {}
    for l in sm.splitlines():
        m = re.match(r"\s*(\d+) code\s+(\d+) ram\s+lib:(\S+)", l)
        if m: libs[m.group(3)] = int(m.group(1))
    for l in fn.splitlines()[1:]:
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)", l)
        if not m or "total" in l: continue
        v, n = int(m.group(2)), m.group(3)
        if n in ("<str>", "<init>"): b["data"] += v
        elif n.startswith("<"): b["other"] += v
        else: b["funcs"] += v
    return b, libs
for p in sys.argv[1:]:
    ub, ul = uc(p); sb, sl = sd(p)
    print("==", p)
    print("uc80 buckets", ub, "libs", ul, "sum", sum(ub.values()) + sum(ul.values()))
    print("sdcc buckets", sb, "libs", sl, "sum", sum(sb.values()) + sum(sl.values()))
