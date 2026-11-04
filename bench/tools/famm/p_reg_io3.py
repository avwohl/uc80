import importlib.util,os,re
spec=importlib.util.spec_from_file_location("p",os.path.join(os.path.dirname(__file__),"p_reg_io2.py")); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
SH=re.compile(r"\tld\tA,\(\?\?AUTO\+3\)\n\tld\tHL,1\n\tor\tA\n\tjr\tZ,(@SHL\d+)\n\tcp\t16\n\tjr\tC,(@SHL\d+)\n\tld\tL,H\n\tjr\t\1\n\2:\n\tadd\tHL,HL\n\tdec\tA\n\tjr\tNZ,\2\n\1:\n(?:\tld\tH,0\n\tld\tE,L\n|\tld\tA,L\n\tor\t128\n\tld\tE,A\n)")
def edit(t):
    t=m.edit(t)
    def rep(mm):
        a,b=mm.group(1),mm.group(2)
        return f"\tld\tA,(??AUTO+3)\n\tld\tB,A\n\tld\tA,1\n\tinc\tB\n\tjr\t{b}b\n{b}a:\tadd\tA,A\n{b}b:\tdjnz\t{b}a\n"+("\tld\tE,A\n" if "ld\tH,0" in mm.group(0) else "\tor\t128\n\tld\tE,A\n")
    t,n=SH.subn(rep,t)
    print("byte shifts",n)
    # the shift result was consumed as "or 128 / ld E,L"-style on L: result now in A, L unused: adapt following ld E,L -> ld E,A
    return t
