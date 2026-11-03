#!/usr/bin/env python3
"""ngram.py prog.mac N [top]: most frequent normalized N-instruction windows (code part only), with bytes."""
import re,sys,collections,subprocess
f=sys.argv[1]; N=int(sys.argv[2]); top=int(sys.argv[3]) if len(sys.argv)>3 else 25
subprocess.run(["um80",f,"-o","/dev/null","-l",f+".prn"],capture_output=True)
ins=[]  # (text, bytes)
for l in open(f+".prn"):
    m=re.match(r"\s*\d+\s+([0-9A-F]{4})\s+((?:[0-9A-F]{2} ?)+)\s+(\S.*)$",l)
    if m:
        txt=m.group(3).split(";")[0].strip()
        if not txt or txt.startswith(("DB","db","DW","dw","DS","ds")): ins.append(("<data>",0)); continue
        ins.append((txt,len(m.group(2).split())))
    else:
        m=re.match(r"\s*\d+\s+(\S+):",l)
        if m: ins.append(("<label>",0))
def norm(t):
    t=re.sub(r"\s+"," ",t.lower())
    t=re.sub(r"\(ix[+-]\d+\)","(ix+d)",t); t=re.sub(r"\(iy[+-]\d+\)","(iy+d)",t)
    t=re.sub(r"\?\?auto[+\d]*","AUTO",t)
    t=re.sub(r"\b\d+\b","N",t); t=re.sub(r"[0-9a-f]+h\b","N",t)
    t=re.sub(r"[@_][a-z0-9_]+","L",t)
    return t
cnt=collections.Counter(); byt={}
nn=[norm(t) for t,_ in ins]
for i in range(len(ins)-N):
    w=ins[i:i+N]
    if any(x[0] in("<label>","<data>") for x in w): continue
    key=" ; ".join(nn[i:i+N])
    cnt[key]+=1; byt[key]=sum(b for _,b in w)
rows=sorted(cnt.items(),key=lambda kv:-kv[1]*byt[kv[0]])
for k,v in rows[:top]: print(f"{v:4d} x {byt[k]:2d}B = {v*byt[k]:5d}  {k}")
