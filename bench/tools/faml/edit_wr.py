import re
ASC = """_ascending_order:
	ld	C,(HL)
	inc	HL
	ld	B,(HL)
	ex	DE,HL
	ld	E,(HL)
	inc	HL
	ld	D,(HL)
	ld	H,B
	ld	L,C
	or	A
	sbc	HL,DE
	ret
"""
CALL = re.compile(r"\tld\tHL,_ascending_order\n\tpush\tHL\n\tld\tHL,2\n\tpush\tHL\n\tld\tHL,(\d+)\n\tpush\tHL\n\tld\tHL,_numbers\n\tpush\tHL\n\tcall\t(?:_qsort|__qsort_s)\n\tex\tDE,HL\n\tld\tHL,8\n\tadd\tHL,SP\n\tld\tSP,HL\n\tex\tDE,HL\n")
def edit(kind, t):
    t, n = CALL.subn(lambda m: f"\tld\tBC,_ascending_order\n\tld\tDE,{m.group(1)}\n\tld\tHL,_numbers\n\tcall\t__qsort_wr\n", t)
    assert n == 1, n
    t = re.sub(r"\tEXTRN\t_qsort\n", "", t); t = re.sub(r"\tEXTRN\t__qsort_s\n", "", t)
    t = t.replace("PUBLIC\t_main", "EXTRN\t__qsort_wr\n\tPUBLIC\t_main",1)
    t, n = re.subn(r"_ascending_order:\n.*?\n@ascending_order_ret:\n\tpop\tIX\n\tret\n", ASC, t, flags=re.S)
    assert n == 1, "asc"
    return t
