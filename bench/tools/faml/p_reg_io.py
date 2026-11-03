import re
NEW_IO = """_out:	LD	C,A
	LD	A,E
	LD	B,0
	OUT	(C),A
	RET

	PUBLIC	_in
_in:	LD	C,A
	LD	B,0
	IN	A,(C)
	RET
"""
POPS=r"(?:\tpop\tDE\n\tpop\tDE\n|\tpop\tBC\n\tpop\tBC\n|(?:\tinc\tSP\n){4})"
def edit(t):
    t,n=re.subn(r"_out:\tLD\tHL,2\n.*?\tRET\n\n\tPUBLIC\t_in\n_in:\tLD\tHL,2\n.*?\tRET\n",NEW_IO,t,flags=re.S)
    assert n==1
    t,n1=re.subn(r"\tld\tHL,(\d+)\n\tpush\tHL\n\tld\tHL,(\d+)\n\tpush\tHL\n\tcall\t_out\n"+POPS, r"\tld\tE,\1\n\tld\tA,\2\n\tcall\t_out\n",t)
    t,n2=re.subn(r"\tpush\tHL\n\tld\tHL,(\d+)\n\tpush\tHL\n\tcall\t_out\n"+POPS, r"\tld\tE,L\n\tld\tA,\1\n\tcall\t_out\n",t)
    # port computed in HL after the value was pushed
    pat=re.compile(r"\tpush\tHL\n((?:(?!\tcall|\tpush|\tpop|\tld\tE|\tex\tDE|\tld\tDE).*\n)*?)\tpush\tHL\n\tcall\t_out\n"+POPS)
    t,n3=pat.subn(lambda m:"\tld\tE,L\n"+m.group(1)+"\tld\tA,L\n\tcall\t_out\n",t)
    t,n4=re.subn(r"\tld\tHL,(\d+)\n\tpush\tHL\n\tcall\t_in\n\tpop\tDE\n", r"\tld\tA,\1\n\tcall\t_in\n\tld\tL,A\n",t)
    print("out const",n1,"out hl",n2,"out ternary",n3,"in",n4,"stack-style calls left:",len(re.findall(r"\tpush\tHL\n\tcall\t_out",t)),len(re.findall(r"\tpush\tHL\n\tcall\t_in",t)))
    return t
