import re, importlib.util, os
spec = importlib.util.spec_from_file_location("e2", os.path.join(os.path.dirname(__file__), "edit_w2.py")); e2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(e2)
ASC = """_ascending_order:
	pop	BC
	pop	HL
	pop	DE
	push	DE
	push	HL
	push	BC
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
def edit(kind, t):
    t = e2.edit(kind, t)
    t, n = re.subn(r"_ascending_order:\n.*?\n@ascending_order_ret:\n\tpop\tIX\n\tret\n", ASC, t, flags=re.S)
    assert n == 1
    return t
