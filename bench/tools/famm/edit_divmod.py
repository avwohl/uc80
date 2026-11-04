import re
NEW = """__sdivmod16:
	PUSH	BC
	LD	B,D		; B = sign of the dividend
	LD	C,H		; C = sign of the divisor
	BIT	7,D
	JR	Z,__sdm_1
	XOR	A
	SUB	E
	LD	E,A
	SBC	A,A
	SUB	D
	LD	D,A
__sdm_1:
	BIT	7,H
	JR	Z,__sdm_2
	XOR	A
	SUB	L
	LD	L,A
	SBC	A,A
	SUB	H
	LD	H,A
__sdm_2:
	CALL	__div16		; HL = quotient, DE = remainder
	LD	A,B
	XOR	C
	JP	P,__sdm_3
	XOR	A
	SUB	L
	LD	L,A
	SBC	A,A
	SUB	H
	LD	H,A
__sdm_3:
	BIT	7,B
	JR	Z,__sdm_4
	XOR	A
	SUB	E
	LD	E,A
	SBC	A,A
	SUB	D
	LD	D,A
__sdm_4:
	POP	BC
	RET
"""
def edit(kind,t):
    old = re.compile(r"\tld\tHL,\(\?\?AUTO\+2\)\n\tex\tDE,HL\n\tld\tHL,\(\?\?AUTO\+4\)\n\tcall\t__smod16\n\tex\tDE,HL\n(?P<mid>.*?)\tpush\tHL\n\tld\tHL,\(\?\?AUTO\+2\)\n\tex\tDE,HL\n\tld\tHL,\(\?\?AUTO\+4\)\n\tcall\t__sdiv16\n\tpop\tDE\n",re.S)
    def rep(m):
        return "\tld\tHL,(??AUTO+2)\n\tex\tDE,HL\n\tld\tHL,(??AUTO+4)\n\tcall\t__sdivmod16\n\tpush\tHL\n"+m.group("mid")+"\tpop\tDE\n"
    t,n=old.subn(rep,t); assert n==1,n
    # replace the two signed wrappers by the combined routine
    i=t.index("__sdiv16:"); j=t.index("; rt_smod32.mac") if "; rt_smod32.mac" in t else None
    k=t.index("__smod16:")
    # cut from __sdiv16 label to the end of __smod16 routine (its final RET before the next banner)
    end=t.index("\tRET\n", t.index("__smod16_2:"))+5
    t=t[:i]+NEW+"\n"+t[end:]
    t=t.replace("PUBLIC\t__sdiv16","PUBLIC\t__sdivmod16").replace("\tPUBLIC\t__smod16\n","")
    t=t.replace("\tPUBLIC\t__sdivmod16\n\tPUBLIC\t__sdivmod16\n","\tPUBLIC\t__sdivmod16\n")
    return t
