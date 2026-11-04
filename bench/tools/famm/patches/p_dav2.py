import re
def edit(kind, t):
    t = t.replace("\tEX\tDE,HL\t\t; HL = dividend, DE = divisor\n\tLD\tA,D\n\tOR\tE\n\tJR\tZ,__div16_zero\n\tPUSH\tBC\n", "\tEX\tDE,HL\t\t; HL = dividend, DE = divisor\n\tPUSH\tBC\n", 1)
    a = t.index("__div16_zero:\n"); b = t.index("\tRET\n", a) + 5
    t = t[:a] + t[b:]
    return t
