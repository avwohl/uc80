import re
def edit(t):
    n = [0]
    def sub(pat, rep):
        nonlocal t
        t, k = re.subn(pat, rep, t); n[0] += k
    # call/jp tail marker
    def tail(op): return "\n\tret" if op == "jp" else ""
    # A: value in A -> ld E,A ; ld A,N ; call _out
    t = re.sub(r"\tld\tE,A\n\tld\tA,(\d+)\n\t(call|jp)\t_out\n", lambda m: "\tout\t(%s),A\n" % m.group(1) + ("\tret\n" if m.group(2)=="jp" else ""), t)
    # B: ld A,N ; ld E,imm ; call _out
    def fb(m):
        v = m.group(2)
        ld = "\txor\tA\n" if v == "0" else "\tld\tA,%s\n" % v
        return ld + "\tout\t(%s),A\n" % m.group(1) + ("\tret\n" if m.group(3)=="jp" else "")
    t = re.sub(r"\tld\tA,(\d+)\n\tld\tE,(\d+)\n\t(call|jp)\t_out\n", lambda m: fb(type('M',(),{'group':lambda s,i:[None,m.group(1),m.group(2),m.group(3)][i]})()), t)
    # C: ld e,(hl) ; ld A,254 ; [push BC] ; call _out
    t = re.sub(r"\tld e,\(hl\)\n\tld\tA,254\n\tpush\tBC\n\tcall\t_out\n", "\tld\tA,(HL)\n\tpush\tBC\n\tout\t(254),A\n", t)
    t = re.sub(r"\tld e,\(hl\)\n\tld\tA,254\n\tcall\t_out\n", "\tld\tA,(HL)\n\tout\t(254),A\n", t)
    t = re.sub(r"\tld\tE,C\n\tld\tA,(\d+)\n\t(call|jp)\t_out\n", lambda m: "\tld\tA,C\n\tout\t(%s),A\n" % m.group(1) + ("\tret\n" if m.group(2)=="jp" else ""), t)
    left = len(re.findall(r"\t(?:call|jp)\t_out\n", t))
    print("left calls of _out:", left)
    if left == 0:
        t = t.replace("_out:\tLD\tC,A\n\tLD\tA,E\n\tLD\tB,0\n\tOUT\t(C),A\n\tRET\n", "")
    return t
