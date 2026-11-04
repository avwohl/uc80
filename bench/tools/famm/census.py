#!/usr/bin/env python3
"""census.py N top : aggregate normalized N-instruction windows over user code of all uc80 time builds in bench/_work."""
import re,sys,collections,subprocess,glob,os
N=int(sys.argv[1]); top=int(sys.argv[2])
W="/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan/bench/_work"
OUT="/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/faml_an/cen"
os.makedirs(OUT,exist_ok=True)
def norm(t):
    t=re.sub(r"\s+"," ",t.lower())
    t=re.sub(r"\(ix[+-]\d+\)","(ix+d)",t); t=re.sub(r"\(iy[+-]\d+\)","(iy+d)",t)
    t=re.sub(r"\?\?auto[+\d]*","AUTO",t)
    t=re.sub(r"\b\d+\b","N",t); t=re.sub(r"\b[0-9a-f]+h\b","N",t)
    t=re.sub(r"[@_][a-z0-9_]+","L",t)
    return t
cnt=collections.Counter(); byt={}; progs=collections.defaultdict(set)
files=sorted(glob.glob(W+"/*/uc80/size/time/prog.mac"))
for f in files:
    prog=f.split("/")[-5]
    prn=os.path.join(OUT,prog+".prn")
    if not os.path.exists(prn):
        subprocess.run(["um80",f,"-o","/dev/null","-l",prn],capture_output=True)
    ins=[]; on=False
    for l in open(prn,errors="replace"):
        if re.match(r"\s*\d+\s+;\s*Function ",l): on=True; continue
        if re.match(r"\s*\d+\s+;\s*(Implicit external functions|Global variables)",l): on=False
        if not on: continue
        m=re.match(r"\s*\d+\s+([0-9A-F]{4})\s+((?:[0-9A-F]{2} ?)+)\s+(\S.*)$",l)
        if m:
            txt=m.group(3).split(";")[0].strip()
            ins.append((norm(txt),len(m.group(2).split())))
        elif re.match(r"\s*\d+\s+[@\w]+:",l): ins.append(("<label>",0))
    for i in range(len(ins)-N):
        w=ins[i:i+N]
        if any(x[0]=="<label>" for x in w): continue
        key=" ; ".join(x[0] for x in w)
        cnt[key]+=1; byt[key]=sum(b for _,b in w); progs[key].add(prog)
rows=sorted(cnt.items(),key=lambda kv:-kv[1]*byt[kv[0]])
for k,v in rows[:top]: print(f"{v:5d} x {byt[k]:2d}B = {v*byt[k]:6d} in {len(progs[k]):3d} progs  {k}")
