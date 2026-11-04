import re
def edit(kind, t):
    s = t.index("\tadd\tHL,HL\n\tadd\tHL,HL\n\tld\tBC,_sortlist\n\tadd\tHL,BC\n\tld\t(??AUTO+8),HL\n")
    e = t.index("@ENDIF19:\n", s)
    new = """	add	HL,HL
	add	HL,HL
	ld	BC,_sortlist
	add	HL,BC
	ld	(??AUTO+8),HL
	ld	E,(HL)
	inc	HL
	ld	D,(HL)
	inc	HL
	ld	C,(HL)
	inc	HL
	ld	B,(HL)
	inc	HL
	ld	A,(HL)
	sub	E
	inc	HL
	ld	A,(HL)
	sbc	A,D
	inc	HL
	ld	A,(HL)
	sbc	A,C
	inc	HL
	ld	A,(HL)
	sbc	A,B
	jp	PO,@CMP_NV20
	xor	128
@CMP_NV20:
	jp	P,@ENDIF19
	ld	A,(HL)
	ld	(HL),B
	ld	B,A
	dec	HL
	ld	A,(HL)
	ld	(HL),C
	ld	C,A
	dec	HL
	ld	A,(HL)
	ld	(HL),D
	ld	D,A
	dec	HL
	ld	A,(HL)
	ld	(HL),E
	ld	E,A
	ld	HL,(??AUTO+8)
	ld	(HL),E
	inc	HL
	ld	(HL),D
	inc	HL
	ld	(HL),C
	inc	HL
	ld	(HL),B
"""
    return t[:s] + new + t[e:]
