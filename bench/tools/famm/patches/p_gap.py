import re
def edit(t):
    out_blk = "_out:\tLD\tC,A\n\tLD\tA,E\n\tLD\tB,0\n\tOUT\t(C),A\n\tRET\n"
    assert out_blk in t
    t = t.replace(out_blk, "")
    m = re.search(r"; Function cmd_pop.*?(?=\n\n; Function dispatch)", t, re.S)
    blk = m.group(0); t = t.replace(blk, "")
    t = t.replace("__vec_rst08:\n\tRET\n", "__vec_rst08:\n\tRET\n\tORG\t0009H\n"+out_blk, 1)
    t = t.replace("__vec_int:\n\tEI\n\tRETI\n", "__vec_int:\n\tEI\n\tRETI\n\tORG\t003AH\n"+blk+"\n", 1)
    return t
