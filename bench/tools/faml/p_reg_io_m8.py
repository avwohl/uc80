import importlib.util,os,re
spec=importlib.util.spec_from_file_location("p",os.path.join(os.path.dirname(__file__),"p_reg_io.py")); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def edit(t):
    t=m.edit(t)
    t,n=re.subn(r"\tpush\tHL\n\tld\tA,\(IX\+4\)\n(\tneg\n)?\tld\tL,A\n\tld\tH,0\n\tpop\tDE\n\tcall\t__mul16\n",lambda mm:"\tex\tDE,HL\n\tld\tA,(IX+4)\n"+(mm.group(1) or "")+"\tcall\t__mul8x16\n",t)
    print("mul8 sites",n)
    if "EXTRN\t__mul8x16" not in t: t=t.replace("EXTRN\t_out","EXTRN\t__mul8x16\n\tEXTRN\t_out",1)
    return t
