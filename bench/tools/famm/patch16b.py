import re,sys
t=open(sys.argv[1]).read()
blk=r"\tld\tL,\(HL\)\n\tld\tH,L\n\tld\tL,0\n\tpush\tHL\n\t(?:ld\tL,C\n\tld\tH,B|ld\tH,B\n\tld\tL,C)\n\tinc\tHL\n\tld\tL,\(HL\)\n\tld\tH,0\n\tpop\tDE\n\tld\tA,H\n\tor\tD\n\tld\tH,A\n\tld\tA,L\n\tor\tE\n\tld\tL,A\n"
pre=r"(?:\tld\tL,C\n\tld\tH,B\n|\tld\tH,B\n\tld\tL,C\n|\tld\tC,L\n\tld\tB,H\n|\tld\tB,H\n\tld\tC,L\n)"
pat=re.compile("("+pre+")"+blk)
t2,n=pat.subn(lambda m:m.group(1)+"\tld\tD,(HL)\n\tinc\tHL\n\tld\tE,(HL)\n\tex\tDE,HL\n",t)
open(sys.argv[2],"w").write(t2); print("patched",n,file=sys.stderr)
