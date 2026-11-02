#!/usr/bin/env python3
"""pcprof.py uc80|sdcc DIR [--mod=M] [--top=N]: exclusive T per instruction and per function in the TIMER region (romrun breakpoints)."""
import sys,re,subprocess,collections
from pathlib import Path
WT=Path("/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan")
sys.path.insert(0,str(WT/"bench"))
from romrun import Machine
kind=sys.argv[1]; d=Path(sys.argv[2]).resolve(); mod=None; top=40
for a in sys.argv[3:]:
    if a.startswith("--mod="): mod=a[6:]
    if a.startswith("--top="): top=int(a[6:])
ins={}   # addr -> (func, text)
if kind=="uc80":
    lib=Path(subprocess.run(["uc80","--print-lib-dir"],capture_output=True,text=True).stdout.strip())
    subprocess.run(["um80","-g","prog.mac","-o","pg.rel","-l","pg.prn"],cwd=d,capture_output=True)
    subprocess.run(["ul80","pg.rel",str(lib/"libc.lib"),str(lib/"runtime.lib"),"-o","pg.com","-S","pg.sym"],cwd=d,capture_output=True)
    syms={}
    for l in (d/"pg.sym").read_text().splitlines():
        p=l.split()
        if len(p)==2: syms[p[1].upper()]=int(p[0],16)
    com=d/"pg.com"; ts,te=syms["TIMER_START"],syms["TIMER_STOP"]
    cur="<start>"
    for l in (d/"pg.prn").read_text().splitlines():
        m=re.match(r"\s*\d+\s+;\s*Function (\w+)",l)
        if m: cur=m.group(1); continue
        if re.match(r"\s*\d+\s+;\s*(Implicit external functions|Global variables)",l): cur="<lib>"
        m=re.match(r"\s*\d+\s+([0-9A-F]{4})\s+((?:[0-9A-F]{2} ?)+)\s+(\S.*)$",l)
        if m: ins[0x100+int(m.group(1),16)]=(cur,m.group(3).split(";")[0].strip())
else:
    lst=(d/f"{mod}.lst").read_text().splitlines(); mp=(d/"prog.map").read_text()
    glob={m.group(2):int(m.group(1),16) for m in re.finditer(r"\s+([0-9A-F]{8})\s+(\S+)\s+"+re.escape(mod)+r"\b",mp)}
    cur="<none>"; lab={}; raw=[]
    for l in lst:
        m=re.match(r"\s{20,}\d+\s+;\s*Function\s+(\S+)",l)
        if m: cur=m.group(1); continue
        m=re.match(r"\s+([0-9A-F]{8})\s+\d+\s+(\w+)::?",l)
        if m: lab[m.group(2)]=int(m.group(1),16)
        m=re.match(r"\s+([0-9A-F]{8})\s+(?:[0-9A-F]{2}[r]?[0-9A-F]{0,2} ?)+\s*(?:\[\s*\d+\])?\s+\d+\s+(\S.*)",l)
        if m: raw.append((int(m.group(1),16),cur,m.group(2).strip()))
    base=None
    for g,a in glob.items():
        if g in lab: base=a-lab[g]; break
    for a,f,t in raw: ins[base+a]=(f,t)
    com=d/"prog.com"; ts=base+lab["TIMER_START"]; te=base+lab["TIMER_STOP"]
m=Machine(mode='cpm'); m.load_com(str(com))
T={}  # addr -> total T
state={"on":False,"pc":None,"t":0}
def hit(mm,pc):
    t=mm.cpu.get()["T"]
    if state["on"] and state["pc"] is not None:
        T[state["pc"]]=T.get(state["pc"],0)+t-state["t"]
    state["pc"]=pc; state["t"]=t
    return None
def start(mm,pc):
    state["on"]=True; state["pc"]=pc; state["t"]=mm.cpu.get()["T"]; return None
def stop(mm,pc):
    t=mm.cpu.get()["T"]
    if state["on"] and state["pc"] is not None: T[state["pc"]]=T.get(state["pc"],0)+t-state["t"]
    state["on"]=False; return "stop"
for a in ins:
    if a in (ts,te): continue
    m._add_bp(a,hit)
m._add_bp(ts,start); m._add_bp(te,stop)
res=m.run(max_tstates=3_000_000_000)
tot=sum(T.values()); print("region T (instrumented)",tot,res.reason)
byf=collections.Counter()
for a,t in T.items(): byf[ins.get(a,("?",))[0]]+=t
for f,t in byf.most_common(15): print(f"{t:10d} {100*t/tot:5.1f}%  {f}")
print("top instructions:")
for a,t in sorted(T.items(),key=lambda kv:-kv[1])[:top]:
    f,txt=ins.get(a,("?","?")); print(f"{t:9d} {a:04X} {f:14s} {txt}")
