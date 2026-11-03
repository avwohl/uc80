import re
def edit(kind,t):
    i=t.index("_loop_schar:"); j=t.index("_loop_uint:")
    f=t[i:j]
    pat=re.compile(r"\tld\tA,E\n\txor\t128\n\tld\tHL,\?\?AUTO\+4\n\tld\tD,A\n\tld\tA,\(HL\)\n\txor\t128\n\tpush\tDE\n\tld\tE,A\n\tld\tA,D\n\tcp\tE\n\tpop\tDE\n\tjr nc,(@\w+)\n")
    f,n=pat.subn(lambda m:"\tld\tA,E\n\tld\tHL,??AUTO+4\n\tsub\t(HL)\n\tjp\tPO,@SCX\n\txor\t128\n@SCX:\tjp\tP,"+m.group(1)+"\n",f)
    assert n==1,n
    return t[:i]+f+t[j:]
