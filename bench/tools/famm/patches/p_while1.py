import re
def edit(kind, t):
    t, a = re.subn(r"\tld\tHL,1\n\tld\tA,H\n\tor\tL\n\tj[rp]\s+z,@\w+\n", "", t, flags=re.I)
    t, b = re.subn(r"(@RB\d+:\n)\tld\tHL,1\n\tld\tA,H\n\tor\tL\n\tjp\tnz,(@RB\d+)\n", lambda m: m.group(1) + "\tjr\t" + m.group(2) + "\n", t)
    t, c = re.subn(r"\tld\tHL,1\n\tld\tA,H\n\tor\tL\n\tjr nz,(@DO\d+)\n", lambda m: "\tjr\t" + m.group(1) + "\n", t)
    print("while1 top", a, "rb", b, "do", c)
    return t
