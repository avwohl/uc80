#!/usr/bin/env python3
"""fprof.py uc80|sdcc DIR FUNC... : T-states from entry of each function to its final ret (first call), via ticks -start/-end.
uc80: DIR has prog.mac (re-assembled with -g and linked here). sdcc: DIR has X.lst, prog.map, prog.com (module chosen by --mod)."""
import sys,re,subprocess
from pathlib import Path
WT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(WT/"bench")); import run
kind=sys.argv[1]; d=Path(sys.argv[2]).resolve(); funcs=sys.argv[3:]
mod=None
if funcs and funcs[0].startswith("--mod="): mod=funcs[0][6:]; funcs=funcs[1:]
def ticks(com,s,e):
    _,c=run.run_ticks(com,s,e,timeout=600); return c
if kind=="uc80":
    lib=Path(subprocess.run(["uc80","--print-lib-dir"],capture_output=True,text=True).stdout.strip())
    subprocess.run(["um80","-g","prog.mac","-o","pg.rel"],cwd=d,capture_output=True)
    subprocess.run(["ul80","pg.rel",str(lib/"libc.lib"),str(lib/"runtime.lib"),"-o","pg.com","-S","pg.sym"],cwd=d,capture_output=True)
    syms={}
    for l in (d/"pg.sym").read_text().splitlines():
        p=l.split()
        if len(p)==2: syms[p[1].upper()]=int(p[0],16)
    for f in funcs:
        s=syms["_"+f.upper()]; e=syms["@"+f.upper()+"_RET"]
        print(f, ticks(d/"pg.com",s,e))
else:
    lst=(d/f"{mod}.lst").read_text().splitlines()
    mp=(d/"prog.map").read_text()
    # base: use _main or any global of the module
    glob={m.group(2):int(m.group(1),16) for m in re.finditer(r"\s+([0-9A-F]{8})\s+(\S+)\s+"+re.escape(mod)+r"\b",mp)}
    cur=None; funcs_ent={}; last_ret={}; labaddr={}
    for l in lst:
        m=re.match(r"\s{20,}\d+\s+;\s*Function\s+(\S+)",l)
        if m: cur=m.group(1); continue
        m=re.match(r"\s+([0-9A-F]{8})\s+(?:[0-9A-F]{2}[r]?[0-9A-F]{0,2} ?)+\s*(?:\[\s*\d+\])?\s+\d+\s+(\S.*)",l)
        if m and cur:
            a=int(m.group(1),16); txt=m.group(2).strip()
            funcs_ent.setdefault(cur,a)
            if txt.startswith("ret"): last_ret[cur]=a
        m=re.match(r"\s+([0-9A-F]{8})\s+\d+\s+(_\w+)::?",l)
        if m: labaddr[m.group(2)]=int(m.group(1),16)
    # base from a global symbol present in both
    base=None
    for g,a in glob.items():
        if g in labaddr: base=a-labaddr[g]; break
    if base is None: raise SystemExit("no base")
    for f in funcs:
        s=base+funcs_ent[f]; e=base+last_ret[f]
        print(f, ticks(d/"prog.com",s,e), hex(s), hex(e))
