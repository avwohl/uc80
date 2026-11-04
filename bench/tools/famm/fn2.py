#!/usr/bin/env python3
"""fn2.py PROGDIR: per-function bytes, uc80 (PROGDIR/uc80/prog.mac via um80 listing) next to SDCC (PROGDIR/sdcc/*.lst).
Buckets: <crt0> (startup), <helpers> (compiler/embedded runtime code), <data> (initialized data), <str> (strings/const).
Prints  uc80  sdcc  name ; then totals."""
import sys, re, subprocess, collections
from pathlib import Path
d = Path(sys.argv[1])
def uc80(mac):
    subprocess.run(["um80", str(mac), "-o", "/dev/null", "-l", str(mac)+".prn"], capture_output=True)
    cur = "<crt0>"; sizes = collections.OrderedDict(); seen_fn = False; seen_gv = False; instr = False
    for l in open(str(mac)+".prn"):
        m = re.match(r"\s*\d+\s+;\s*Function (\S+)", l)
        if m: cur = m.group(1); seen_fn = True; continue
        if re.match(r"\s*\d+\s+;\s*(Implicit external functions|Global variables)", l): cur = "<helpers>"; continue
        if re.match(r"\s*\d+\s+;\s*String literals", l): cur = "<str>"; continue
        if re.match(r"\s*\d+\s+;\s*rt_\w+\.mac\s*$", l): cur = "<rt:" + re.search(r";\s*(rt_\w+)", l).group(1) + ">"; continue
        m = re.match(r"\s*\d+\s+(?:[0-9A-F]{4}\s+)?DSEG", l)
        if m and seen_fn: cur = "<data>"; continue
        if re.match(r"\s*\d+\s+;\s*(Shared automatic|BSS)", l): cur = "<bss>"; continue
        m = re.match(r"\s*\d+\s+([0-9A-F]{4})\s+((?:[0-9A-F]{2} ?)+)", l) or re.match(r"^\s{7}([0-9A-F]{4})\s+((?:[0-9A-F]{2} ?)+)\s*$", l)
        if m and cur != "<bss>":
            sizes[cur] = sizes.get(cur, 0) + len(m.group(2).split())
    return sizes
def sdcc(lst):
    cur = "<?>"; sizes = collections.OrderedDict(); area = ""
    for l in lst.read_text().splitlines():
        m = re.match(r"\s+\d+\s+;\s*Function (\S+)", l)
        if m: cur = m.group(1); continue
        m = re.match(r"\s+\d+\s+\.area\s+(\S+)", l)
        if m: area = m.group(1); 
        m = re.match(r"\s+[0-9A-F]{8}\s+\d+\s+(___str_\d+|__xinit_\w+|___\w+_\d+):", l)
        if m:
            cur = "<str>" if m.group(1).startswith("___str") else ("<init>" if "xinit" in m.group(1) else "<str>")
        m = re.match(r"^((?:\s+[0-9A-F]{8}|\s{12})\s)(.{0,24})", l)
        if m and re.match(r"^\s+(?:[0-9A-F]{8}\s|\s{9})[0-9A-F]{2}", l):
            b = l[13:36].split("[")[0].replace("r","").replace(" ","")
            if area in ("_CODE","_INITIALIZER","_GSINIT","_GSFINAL","_HOME") and len(b)%2==0:
                key = cur if area=="_CODE" or area=="_HOME" else "<"+area.strip("_").lower()+">"
                if area=="_INITIALIZER": key="<init>"
                sizes[key] = sizes.get(key, 0) + len(b)//2
    return sizes
u = uc80(d/"uc80"/"prog.mac")
s = collections.OrderedDict()
for lst in sorted((d/"sdcc").glob("*.lst")):
    if lst.stem in ("cpmio","crt0_cpm"): continue
    for k, v in sdcc(lst).items(): s[k.lstrip("_") if not k.startswith("<") else k] = s.get(k.lstrip("_") if not k.startswith("<") else k, 0) + v
names = list(dict.fromkeys(list(u) + list(s)))
print("%6s %6s  %s" % ("uc80", "sdcc", "name"))
for n in names:
    print("%6d %6d  %s" % (u.get(n, 0), s.get(n, 0), n))
print("%6d %6d  total (own modules; excludes sdcc crt0/cpmio/libs; uc80 includes crt0+embedded runtime)" % (sum(u.values()), sum(s.values())))
