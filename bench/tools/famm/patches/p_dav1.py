import re
def edit(kind, t):
    t, a = re.subn(r"\tld\tH,D\n\tld\tL,E\n\tex\tDE,HL\n(\tld\tHL,)", r"\1", t)
    t, b = re.subn(r"\txor\t128\n\tpush\tDE\n\tld\tD,A\n(\tld\tA,L\n\tsub\t\d+\n)\tld\tA,D\n\tpop\tDE\n", r"\txor\t128\n\tld\tB,A\n\1\tld\tA,B\n", t)
    print("dead ex", a, "pushpop", b)
    return t
