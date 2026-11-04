import re
def edit(kind, t):
    def rep(m):
        hi = int(m.group(2)) - 128
        return "\tld\tA,L\n\tsub\t%s\n\tld\tA,H\n\tsbc\tA,%d\n" % (m.group(1), hi)
    t, n = re.subn(r"\tld\tA,H\n\txor\t128\n\tld\tD,A\n\tld\tA,L\n\tsub\t(\d+)\n\tld\tA,D\n\tsbc\tA,(\d+)\n", rep, t)
    old = "_ascending_order:\n\tld\tB,D\n\tld\tC,E\n\tld\tE,(HL)\n\tinc\tHL\n\tld\tD,(HL)\n\tex\tDE,HL\n\tpush\tHL\n\tld\tH,B\n\tld\tL,C\n\tld\tE,(HL)\n\tinc\tHL\n\tld\tD,(HL)\n\tex\tDE,HL\n\tpop\tDE\n\tex\tDE,HL\n\tor\tA\n\tsbc\tHL,DE\n"
    new = "_ascending_order:\n\tld\tC,(HL)\n\tinc\tHL\n\tld\tB,(HL)\n\tex\tDE,HL\n\tld\tE,(HL)\n\tinc\tHL\n\tld\tD,(HL)\n\tld\tH,B\n\tld\tL,C\n\tor\tA\n\tsbc\tHL,DE\n"
    assert old in t
    t = t.replace(old, new)
    print("range cmp sites", n)
    return t
