import re
def edit(kind, t):
    s = t.index("_main:\n"); e = t.index("\n@main_ret:", s)
    body = t[s:e]
    n = body.count("\tcall\t_line\n\tpop\tDE\n\tpop\tDE\n\tpop\tDE\n")
    body2 = body.replace("\tcall\t_line\n\tpop\tDE\n\tpop\tDE\n\tpop\tDE\n", "\tcall\t_line\n")
    # restore SP before the final ld HL,0
    assert body2.rstrip().endswith("\tld\tHL,0")
    body2 = body2.rstrip()[:-len("\tld\tHL,0")] + "\tld\tHL,%d\n\tadd\tHL,SP\n\tld\tSP,HL\n\tld\tHL,0" % (6*n) + "\n"
    print("deferred calls", n)
    return t[:s] + body2.rstrip("\n") + t[e:]
