#!/usr/bin/env python3
"""table.py PROG...: gap in the minus-empty column and where it comes from (needs w/PROG built for uc80 and sdcc)."""
import sys, re, subprocess
from pathlib import Path
H = Path(__file__).parent
def run(*a): return subprocess.run(["python3", *a], capture_output=True, text=True, cwd=H).stdout
EU, ES = 28, 112
def image(p, c):
    out = subprocess.run(["python3", "-c", f"""
import sys; sys.path.insert(0,'..'); """], capture_output=True)
    return None
def uc(p):
    lm = run("lmods.py", f"w/{p}/uc80"); fn = run("fn2.py", f"w/{p}")
    funcs = data = arith = 0; libs = {}
    for l in fn.splitlines()[1:]:
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)", l)
        if not m or "total" in l: continue
        v, n = int(m.group(1)), m.group(3)
        if n == "<crt0>": continue
        elif n in ("<data>", "<str>"): data += v
        elif n.startswith("<helpers>") or n.startswith("<rt:"): arith += v
        elif n.startswith("<"): pass
        else: funcs += v
    for l in lm.splitlines():
        m = re.match(r"\s*(\d+) code\s+(\d+) data\s+(\S+)\s+@\w+\s*(.*)", l)
        if m and m.group(3) != "PROG": libs[m.group(3)] = libs.get(m.group(3), 0) + int(m.group(1)) + int(m.group(2))
    tot = int(re.search(r"(\d+) total code, (\d+) data", lm).group(1)) + int(re.search(r"(\d+) total code, (\d+) data", lm).group(2))
    return dict(total=tot, crt=24, funcs=funcs + (tot - 24 - funcs - data - arith - sum(libs.values())) * 0, data=data, arith=arith, libs=libs)
def sd(p):
    sm = run("sdmods.py", f"w/{p}/sdcc"); fn = run("fn2.py", f"w/{p}")
    data = 0; libs = {}
    for l in fn.splitlines()[1:]:
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)", l)
        if m and m.group(3) in ("<str>", "<init>"): data += int(m.group(2))
    for l in sm.splitlines():
        m = re.match(r"\s*(\d+) code\s+(\d+) ram\s+lib:(\S+)", l)
        if m: libs[m.group(3)] = int(m.group(1))
    tot = int(re.search(r"^(\d+) total code", sm, re.M).group(1))
    funcs = tot - 62 - 46 - data - sum(libs.values())
    return dict(total=tot, crt=62, io=46, funcs=funcs, data=data, libs=libs)
for p in sys.argv[1:]:
    u, s = uc(p), sd(p)
    gap = (u["total"] - EU) - (s["total"] - ES)
    uio = sum(v for k, v in u["libs"].items() if k.startswith(("LC_PUT", "LC_CON")))
    sio = s["libs"].get("puts", 0)
    print(f"{p}: uc80 {u['total']} sdcc {s['total']}  gap(minus-empty) {gap:+d}")
    print(f"  funcs+const+stuff  uc80 {u['funcs']} vs sdcc {s['funcs']}  d={u['funcs']-s['funcs']:+d}   (data uc80 {u['data']} sdcc {s['data']} d={u['data']-s['data']:+d})")
    ulib = u["arith"] + sum(v for k, v in u["libs"].items() if not k.startswith(("LC_PUT", "LC_CON", "LC_EXI")))
    slib = sum(v for k, v in s["libs"].items() if k != "puts")
    print(f"  arith/lib (not console) uc80 {ulib} vs sdcc {slib}  d={ulib-slib:+d}   uc80 libs {u['libs']}  sdcc libs {s['libs']}")
    print(f"  console I/O in column: uc80 {uio} sdcc {sio} (+46 absorbed in sdcc empty)  d={uio-sio:+d}; gap without console I/O on either side {gap-(uio-sio):+d}; with sdcc cpmio brought into column {gap-46:+d}")
