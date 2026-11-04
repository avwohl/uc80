import re
def edit(kind, t):
    m = re.search(r"\tld\tA,(\(\?\?AUTO\+\d+\))\n\tcp\t5\n\tret\tnc\n\tld\tL,A\n\tld\tH,0\n\tadd\tHL,HL\n\tld\tDE,@SWTAB\d+\n\tadd\tHL,DE\n\tld\tE,\(HL\)\n\tinc\tHL\n\tld\tD,\(HL\)\n\tex\tDE,HL\n\tjp\t\(HL\)\n@SWTAB\d+:\n(?:\tdw\t@CASE\d+\n){5}(@CASE\d+):\n", t)
    assert m, "no match"
    lab0 = m.group(2)
    # case labels in order
    labs = re.findall(r"\tdw\t(@CASE\d+)\n", t[m.start():m.end()])
    new = ("\tld\tA,%s\n\tor\tA\n\tjr\tz,%s\n\tdec\tA\n\tjr\tz,%s\n\tdec\tA\n\tjr\tz,%s\n\tdec\tA\n\tret\tz\n\tdec\tA\n\tret\tnz\n\tjr\t%s\n%s:\n"
           % (m.group(1), labs[0], labs[1], labs[2], labs[4], lab0))
    return t[:m.start()] + new + t[m.end():]
