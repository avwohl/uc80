import re
def edit(kind, t):
    s = t.index("_place:\n"); e = t.index("@ENDIF2:\n", s)
    new = """_place:
	ld	B,A
	ld	A,(_n)
	cp	B
	jr nz,@ENDIF2
	ld	DE,0
	ld	HL,(_solutions)
	inc	HL
	ld	(_solutions),HL
	ld	C,0
@FOR3:
	ld	A,C
	ld	HL,_n
	cp	(HL)
	jr nc,@ENDFOR5
	ld	A,C
	add	A,LOW (_pos)
	ld	L,A
	ld	A,HIGH (_pos)
	adc	A,0
	ld	H,A
	ld	B,(HL)
	ld	L,E
	ld	H,D
	add	HL,HL
	add	HL,HL
	add	HL,HL
	add	HL,DE
	ld	E,B
	ld	D,0
	add	HL,DE
	ex	DE,HL
	inc	C
	jr @FOR3
@ENDFOR5:
	ld	HL,(_sum)
	add	HL,DE
	ld	(_sum),HL
	ret
"""
    t = t[:s] + new + t[e:]
    t = t.replace("@ENDFOR8:\n@place_ret:\n\tld\tSP,IX\n\tpop\tIX\n\tret\n", "@ENDFOR8:\n@place_ret:\n\tret\n")
    return t
