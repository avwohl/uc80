import re,sys
t=open(sys.argv[1]).read().split("\n")
out=[];i=0;n=0;saved=0
cur=None  # HL == BC + cur after last store
def isline(k,s): return k<len(t) and t[k]==s
while i<len(t):
    l=t[i]
    m=re.match(r"\tld\tHL,(\d+)$",l)
    if m and isline(i+1,"\tadd\tHL,BC"):
        N=int(m.group(1))
        if cur is not None and 0<=N-cur<=3:
            out.extend(["\tinc\tHL"]*(N-cur)); saved+= 4-(N-cur); n+=1
            i+=2
            if re.match(r"\tld\tDE,",t[i]) and isline(i+1,"\tld\t(HL),E") and isline(i+2,"\tinc\tHL") and isline(i+3,"\tld\t(HL),D"):
                out.extend(t[i:i+4]); i+=4; cur=N+1
            else:
                cur=None
            continue
        cur=N
        out.extend([l,t[i+1]]); i+=2
        # now the store: expect ld DE,x ; ld (HL),E ; inc HL ; ld (HL),D
        if re.match(r"\tld\tDE,",t[i]) and isline(i+1,"\tld\t(HL),E") and isline(i+2,"\tinc\tHL") and isline(i+3,"\tld\t(HL),D"):
            out.extend(t[i:i+4]); i+=4; cur=N+1
        else:
            cur=N
            # unknown store: cannot track; reset
            cur=None
        continue
    # any other instruction touching HL or labels resets
    if cur is not None:
        if re.match(r"^[@_\w]+:|^\t(call|jp|jr|ret|ld\tHL|pop\tHL|push|ex|inc\tHL|add\tHL|ld\tH|ld\tL|ldir|ldi|pop|xor|or|and|sub)",l) and not l.startswith("\tld\tDE") and not l.startswith("\tld\tBC"):
            cur=None
    out.append(l); i+=1
open(sys.argv[2],"w").write("\n".join(out)); print("chained",n,"saved bytes approx",saved,file=sys.stderr)
