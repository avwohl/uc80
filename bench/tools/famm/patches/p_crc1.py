import re
def edit(kind, t):
    inl = "\tor\tA\n\tjr\tz,@SHRX\n@SHRL:\n\tsrl\tD\n\trr\tE\n\trr\tH\n\trr\tL\n\tdec\tA\n\tjr\tnz,@SHRL\n@SHRX:\n"
    assert t.count("\tcall\t__shr32c\n") == 1
    t = t.replace("\tcall\t__shr32c\n", inl)
    s = t.index("__shr32c:\n"); e = t.index("__shr32c_z:\n", s); e2 = t.index("\tRET\n", e) + 5
    t = t[:s] + t[e2:]
    t = t.replace("\tPUBLIC\t__shr32c\n", "")
    return t
