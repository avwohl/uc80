#!/usr/bin/env python3
"""fsz.py prog.mac -> per-function code size from um80 listing (functions = '; Function name' markers)."""
import sys,re,subprocess,collections
f=sys.argv[1]
subprocess.run(["um80",f,"-o","/dev/null","-l",f+".prn"],capture_output=True)
cur=None;sizes=collections.OrderedDict();inline=False
for l in open(f+".prn"):
    m=re.match(r"\s*\d+\s+(?:[0-9A-F]{4}\s+)?(?:(?:[0-9A-F]{2} ?)+)?\s*;\s*(?:Function|Library function)\s+(\w+)",l)
    if re.match(r"\s*\d+\s+;\s*(Implicit external functions|Global variables)",l): cur='<lib+data>'; sizes.setdefault(cur,0); continue
    m2=re.match(r"\s*\d+\s+;\s*Function (\w+)",l)
    if m2: cur=m2.group(1); sizes.setdefault(cur,0); continue
    m=re.match(r"\s*\d+\s+([0-9A-F]{4})\s+((?:[0-9A-F]{2} ?)+)",l)
    if m:
        nb=len(m.group(2).split())
        sizes[cur]=sizes.get(cur,0)+nb
for k,v in sorted(sizes.items(),key=lambda x:-x[1]): print(v,k)
print("total",sum(sizes.values()))
