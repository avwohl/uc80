import re
OLD = """_put_str:
	ld	(??AUTO+0),HL
@WHILE10:
	ld	HL,(??AUTO+0)
	ld	A,(HL)
	or	A
	ret	z
	push	HL
	inc	HL
	ld	(??AUTO+0),HL
	pop	HL
	ld	A,(HL)
	ld	E,A
	call	__putchar_v
	jr @WHILE10
@ENDWHILE11:
@put_str_ret:
	ret
"""
NEW = """_put_str:
@WHILE10:
	ld	A,(HL)
	or	A
	ret	z
	inc	HL
	push	HL
	ld	E,A
	call	__putchar_v
	pop	HL
	jr @WHILE10
"""
def edit(kind, t):
    # tolerate different label numbers
    m = re.search(r"_put_str:\n\tld\t\(\?\?AUTO\+0\),HL\n(@WHILE\d+):\n\tld\tHL,\(\?\?AUTO\+0\)\n\tld\tA,\(HL\)\n\tor\tA\n\tret\tz\n\tpush\tHL\n\tinc\tHL\n\tld\t\(\?\?AUTO\+0\),HL\n\tpop\tHL\n\tld\tA,\(HL\)\n\tld\tE,A\n\tcall\t__putchar_v\n\tjr (@WHILE\d+)\n@ENDWHILE\d+:\n@put_str_ret:\n\tret\n", t)
    assert m, "put_str not found"
    lab = m.group(1)
    new = NEW.replace("@WHILE10", lab)
    return t[:m.start()] + new + t[m.end():]
