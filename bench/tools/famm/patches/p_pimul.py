import re
def edit(kind, t):
    a = t.index("\tex\tDE,HL\n\tld\tDE,0\n\tld\t(__tmp32),HL\n")
    b = t.index("\tld\tBC,(__main_S4)\n\tadd\tHL,BC\n", a)
    print("chain bytes removed (lines):", t[a:b].count("\n"))
    new = "\tld\tB,D\n\tld\tC,E\n\tld\tDE,10000\n\tcall\t__mul16_32\n"
    # after 'ex DE,HL' HL=r[i]? original: ld E,(HL);inc HL;ld D,(HL);ex DE,HL -> HL=r[i]; then 'ex DE,HL' above? keep: we need BC = r[i]
    return t[:a] + new + t[b:]
