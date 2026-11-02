import re,sys
src=open(sys.argv[1]).read()
# split into functions at "; Function name" lines
parts=re.split(r"(?m)^(?=; Function \w+)",src)
out=[];tot=0;nf=0;nrep=0
for p in parts:
    m=re.match(r"; Function (\w+)",p)
    if not m: out.append(p); continue
    if "IY" in p or "(_GState),HL" in p or p.count("(_GState)")<2:
        out.append(p); continue
    cnt=0
    def r1(mm):
        global nrep
        N=int(mm.group(1)); 
        if N>126: return mm.group(0)
        nrep+=1
        return f"\tld\tE,(IY+{N})\n\tld\tD,(IY+{N+1})\n\tex\tDE,HL\n"
    p2,n1=re.subn(r"\tld\tHL,\(_GState\)\n\tld\t(?:DE|BC),(\d+)\n\tadd\tHL,(?:DE|BC)\n\tld\tE,\(HL\)\n\tinc\tHL\n\tld\tD,\(HL\)\n\tex\tDE,HL\n",r1,p)
    # the replacement already includes ex DE,HL equivalent; count actual replaced
    if p2!=p:
        # insert IY load after function label line
        mm=re.search(r"(?m)^_\w+:\n",p2)
        p2=p2[:mm.end()]+"\tld\tIY,(_GState)\n"+p2[mm.end():]
        nf+=1
    out.append(p2)
open(sys.argv[2],"w").write("".join(out)); print("functions",nf,"replaced reads",nrep,file=sys.stderr)
