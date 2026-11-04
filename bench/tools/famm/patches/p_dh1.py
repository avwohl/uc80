import re
def edit(kind, t):
    old = "\tand\t1\n\tld d,0\n\tld\te,A\n\tld\tHL,(_Bool_Glob)\n\tld\tA,H\n\tor\tD\n\tld\tH,A\n\tld\tA,L\n\tor\tE\n\tld\tL,A\n\tld\t(_Bool_Glob),HL\n"
    new = "\tand\t1\n\tld\tHL,_Bool_Glob\n\tor\t(HL)\n\tld\t(HL),A\n"
    assert old in t
    return t.replace(old, new)
