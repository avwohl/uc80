#!/usr/bin/env python3
"""sdmods.py DIR: per-module bytes of an SDCC bench build (map 'Files Linked' + libraries).
Needs z80.lib extracted to famm_an/z80lib (ar x).  Prints code (_CODE+_HOME+_GSINIT+_GSFINAL+_INITIALIZER) and ram bytes per module."""
import sys, re, os
from pathlib import Path
d = Path(sys.argv[1])
LIBX = Path(os.environ.get("A","/tmp"))/"z80lib"
mp = (d/"prog.map").read_text()
files = re.findall(r"^(\S+)\.rel\s+\[ \S+ \]", mp.split("Files Linked")[1].split("Libraries Linked")[0], re.M)
libs = re.findall(r"\[ (\S+)\.rel \]", mp.split("Libraries Linked")[1]) if "Libraries Linked" in mp else []
def sizes(p):
    c = r = 0
    for l in p.read_text().splitlines():
        m = re.match(r"A (\S+) size ([0-9A-F]+) flags", l)
        if m:
            n = int(m.group(2), 16)
            if m.group(1) in ("_CODE","_HOME","_GSINIT","_GSFINAL","_INITIALIZER","_CABS"): c += n
            else: r += n
    return c, r
tc = 0
for f in files:
    p = d/(f+".rel"); 
    if not p.exists(): p = Path(os.environ["W"])/"bench"/"crt"/"sdcc"/(f+".rel")
    if not p.exists(): print(f, "?"); continue
    c, r = sizes(p); tc += c; print("%5d code %5d ram  %s" % (c, r, f))
for f in libs:
    c, r = sizes(LIBX/(f+".rel")); tc += c; print("%5d code %5d ram  lib:%s" % (c, r, f))
print(tc, "total code (incl. gsinit; gsfinal/initializer in sizes)")
