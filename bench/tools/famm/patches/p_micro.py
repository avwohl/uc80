import re
def edit(t):
    # (2) 1 << n with n in 0..7: rla trick
    pat = re.compile(r"\tld\tE,A\n\tld\tA,1\n\tinc\tE\n\tjr\t(@SHLT\d+)\n(@SHLB\d+):\n\tadd\tA,A\n\1:\n\tdec\tE\n\tjr\tNZ,\2\n")
    t, n2 = pat.subn("\tinc\tA\n\tld\tB,A\n\txor\tA\n\tscf\n@RLX\\1:\n\trla\n\tdjnz\t@RLX\\1\n", t)
    t = re.sub(r"@RLX@SHLT(\d+)", r"@RLXq\1", t)
    # (1) unreachable 'ret' after jp/ret/jr when its label is unreferenced
    lines = t.split("\n"); out = []; n1 = 0; i = 0
    while i < len(lines):
        l = lines[i]; out.append(l)
        if re.match(r"\t(jp|jr|ret)\b[^\n]*$", l) and not re.match(r"\t(jp|jr|ret)\s+(nz|z|c|nc|p|m|po|pe)\b", l, re.I) and not re.match(r"\t(jp|jr|ret)\t(nz|z|c|nc|p|m|po|pe),", l, re.I) and not l.strip().lower() == "ret" or l.strip().lower() == "ret":
            # next lines: labels (@..._ret etc.) then 'ret'
            j = i + 1; labs = []
            while j < len(lines) and re.match(r"@\w+:$", lines[j]): labs.append(lines[j][:-1]); j += 1
            if j < len(lines) and lines[j] == "\tret" and labs and all(("_ret" in x or x.startswith("@ENDSWITCH") or x.startswith("@ENDWHILE") or x.startswith("@ENDFOR")) and not re.search(r"\b" + re.escape(x) + r"\b", "\n".join(lines[:i] + lines[j+1:])) for x in labs):
                out.extend(x + ":" for x in labs); i = j + 1; n1 += 1; continue
        i += 1
    t = "\n".join(out)
    # (4) cmd_pop: dead ld A,B before ld L,B / ld H,0 / ret
    t, n4 = re.subn(r"\tld\tA,B\n\tld\tL,B\n\tld\tH,0\n(@cmd_pop_ret:\n)?\tret\n", r"\tld\tL,B\n\tld\tH,0\n\1\tret\n", t)
    print("rla", n2, "deadret", n1, "dead ld A,B", n4)
    return t
