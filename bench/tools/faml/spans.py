#!/usr/bin/env python3
"""spans.py uc80|sdcc DIR [--mod=M] FUNC... : inclusive T per function (romrun spans) between TIMER_START and TIMER_STOP."""
import sys,re,subprocess
from pathlib import Path
WT=Path("/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan")
sys.path.insert(0,str(WT/"bench"))
from romrun import Machine
kind=sys.argv[1]; d=Path(sys.argv[2]).resolve(); args=sys.argv[3:]
mod=None
if args and args[0].startswith("--mod="): mod=args[0][6:]; args=args[1:]
funcs=args
syms={}
if kind=="uc80":
    lib=Path(subprocess.run(["uc80","--print-lib-dir"],capture_output=True,text=True).stdout.strip())
    subprocess.run(["um80","-g","prog.mac","-o","pg.rel"],cwd=d,capture_output=True)
    subprocess.run(["ul80","pg.rel",str(lib/"libc.lib"),str(lib/"runtime.lib"),"-o","pg.com","-S","pg.sym"],cwd=d,capture_output=True)
    for l in (d/"pg.sym").read_text().splitlines():
        p=l.split()
        if len(p)==2: syms[p[1].upper()]=int(p[0],16)
    com=d/"pg.com"; ent={f:syms["_"+f.upper()] for f in funcs}
    ts,te=syms["TIMER_START"],syms["TIMER_STOP"]
else:
    lst=(d/f"{mod}.lst").read_text().splitlines(); mp=(d/"prog.map").read_text()
    glob={m.group(2):int(m.group(1),16) for m in re.finditer(r"\s+([0-9A-F]{8})\s+(\S+)\s+"+re.escape(mod)+r"\b",mp)}
    cur=None; fe={}; lab={}
    for l in lst:
        m=re.match(r"\s{20,}\d+\s+;\s*Function\s+(\S+)",l)
        if m: cur=m.group(1); continue
        m=re.match(r"\s+([0-9A-F]{8})\s+(?:[0-9A-F]{2}[r]?[0-9A-F]{0,2} ?)+\s*(?:\[\s*\d+\])?\s+\d+\s+(\S.*)",l)
        if m and cur: fe.setdefault(cur,int(m.group(1),16))
        m=re.match(r"\s+([0-9A-F]{8})\s+\d+\s+(\w+)::?",l)
        if m: lab[m.group(2)]=int(m.group(1),16)
    base=None
    for g,a in glob.items():
        if g in lab: base=a-lab[g]; break
    com=d/"prog.com"; ent={f:base+fe[f] for f in funcs}
    ts=base+lab["TIMER_START"]; te=base+lab["TIMER_STOP"]
m=Machine(mode='cpm')
m.load_com(str(com))
tot=m.measure("REGION",ts,te)
sp={f:m.measure(f,a) for f,a in ent.items()}
res=m.run(max_tstates=3_000_000_000)
print("region",tot.total,res.reason)
for f in funcs: print(sp[f].summary())
