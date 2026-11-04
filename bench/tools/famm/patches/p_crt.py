def edit(t):
    old_bss = t[t.index("\t; Zero the BSS (COMMON): BC = size"):t.index("__nobss:")]
    new_bss = "\t; Zero the BSS (known to be 2 bytes or more)\n\tLD\tBC,__BSS_END-__BSS_START-1\n\tLD\tHL,__BSS_START\n\tLD\t(HL),0\n\tLD\tDE,__BSS_START+1\n\tLDIR\n"
    t = t.replace(old_bss, new_bss)
    t = t.replace("\tLD\tBC,__DATA_LEN\n\tLD\tA,B\n\tOR\tC\n\tJR\tZ,__nodata\n\tLD\tHL,__DATA_ROM","\tLD\tBC,__DATA_LEN\n\tLD\tHL,__DATA_ROM")
    return t
