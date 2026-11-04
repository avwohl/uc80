import sys,re,subprocess
f=sys.argv[1]
subprocess.run(["um80",f,"-o","/dev/null","-l",f+".prn"],capture_output=True)
rows=[];last=0;pend=[]
for l in open(f+".prn"):
    m=re.match(r"\s*\d+\s+([0-9A-F]{4})\s+((?:[0-9A-F]{2} ?)+)\s*(.*)",l)
    if m:
        a=int(m.group(1),16); nb=len(m.group(2).split())
        for p in pend: rows.append((a,p))
        pend=[]
        lab=re.match(r"(\w+):",m.group(3))
        if lab: rows.append((a,lab.group(1)))
        last=a+nb
        continue
    m=re.match(r"\s*\d+\s+(\w+):\s*$",l)
    if m: pend.append(m.group(1))
rows.append((last,"<end>"))
for (a,n),(b,_) in zip(rows,rows[1:]): print(f"{a:04X} {b-a:4d} {n}")
print("total",last)
