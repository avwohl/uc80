import re
def edit(kind, t):
    s = t.index("_g_rand:\n"); e = t.index("@g_rand_ret:\n", s)
    new = """_g_rand:
	ld	HL,(__g_rand_S0)
	ld	DE,(__g_rand_S0+2)
	ld	B,D
	ld	C,E
	ld	A,H
	srl	B
	rr	C
	rra
	srl	B
	rr	C
	rra
	xor	L
	ld	L,A
	ld	A,C
	xor	H
	ld	H,A
	ld	A,B
	xor	E
	ld	E,A
	ld	B,E
	ld	C,H
	ld	A,L
	add	A,A
	rl	C
	rl	B
	xor	H
	ld	H,A
	ld	A,C
	xor	E
	ld	E,A
	ld	A,B
	xor	D
	ld	D,A
	ld	A,D
	srl	A
	xor	L
	ld	L,A
	ld	(__g_rand_S0),HL
	ld	(__g_rand_S0+2),DE
	ld	A,H
	and	127
	ld	H,A
	ld	DE,0
"""
    return t[:s] + new + t[e:]
