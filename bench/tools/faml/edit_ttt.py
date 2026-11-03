import re
def edit(kind,t):
    t,n=re.subn(r"(@OR_S\d+:\n\tld\tA,C\n\tld\tl,C\n)\trlca\n\tsbc\tA,A\n\tld\tH,A\n",r"\1",t)
    t,m=re.subn(r"(\txor a\n\tld l,a\n)\trlca\n\tsbc\tA,A\n\tld\tH,A\n",r"\1",t)
    t,k=re.subn(r"\tcall\t__callhl\n\tld\tA,L\n\tor\tH\n",r"\tcall\t__callhl\n\tld\tA,L\n\tor\tA\n",t)
    t,k2=re.subn(r"\tld\tA,L\n\txor\t1\n\tor\tH\n",r"\tld\tA,L\n\txor\t1\n",t)
    print("patched",n,m,k,k2)
    return t
