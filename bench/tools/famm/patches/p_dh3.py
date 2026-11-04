import re
def edit(kind, t):
    # loop counter __Func_2_S6 in 0..3: signed compare with constant -> unsigned
    t, n = re.subn(r"(\tld\tHL,\(__Func_2_S6\)\n)\tld\tA,H\n\txor\t128\n\tld\tB,A\n\tld\tA,L\n\tsub\t3\n\tld\tA,B\n\tsbc\tA,128\n", r"\1\tld\tA,L\n\tsub\t3\n\tld\tA,H\n\tsbc\tA,0\n", t)
    print("static range cmp", n)
    return t
_edit1 = edit
def edit(kind, t):
    t = _edit1(kind, t)
    t, m = re.subn(r"\tld\tHL,\(_Int_Glob\)\n\tld\tA,H\n\txor\t128\n\tld\tD,A\n\tld\tA,L\n\tsub\t101\n\tld\tA,D\n\tsbc\tA,128\n", "\tld\tHL,(_Int_Glob)\n\tld\tA,L\n\tsub\t101\n\tld\tA,H\n\tsbc\tA,0\n", t)
    print("Int_Glob cmp", m)
    return t
