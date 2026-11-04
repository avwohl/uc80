import re, sys
def edit(kind, t, fn="main", callee="line"):
    s = t.index("_%s:\n" % fn); e = t.index("\n@%s_ret:" % fn, s)
    body = t[s:e]
    pat = re.compile(r"\tcall\t_%s\n((?:\tpop\tDE\n)+)" % callee)
    ns = [len(m.group(1)) // len("\tpop\tDE\n") for m in pat.finditer(body)]
    body2 = pat.sub("\tcall\t_%s\n" % callee, body)
    tot = 2 * sum(ns)
    assert body2.rstrip().endswith("\tld\tHL,0")
    body2 = body2.rstrip()[:-len("\tld\tHL,0")] + "\tld\tHL,%d\n\tadd\tHL,SP\n\tld\tSP,HL\n\tld\tHL,0" % tot + "\n"
    print("deferred calls", len(ns), "bytes", tot)
    return t[:s] + body2.rstrip("\n") + t[e:]
