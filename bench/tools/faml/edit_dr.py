import re
pat=re.compile(r"\tld\tA,(\S+)\n\txor\t128\n\tcp\t(\d+)\n\tjr c,(@\w+)\n\tld\tA,\1\n\txor\t128\n\tcp\t(\d+)\n\tjr nc,\3\n")
def rep(m):
    lo=(int(m.group(2))-128)&255; hi=(int(m.group(4))-128)&255
    return f"\tld\tA,{m.group(1)}\n\tsub\t{lo}\n\tcp\t{(hi-lo)&255}\n\tjr nc,{m.group(3)}\n"
def edit(kind,t):
    t,n=pat.subn(rep,t); assert n==1; return t
