import importlib.util,os,re
spec=importlib.util.spec_from_file_location("p",os.path.join(os.path.dirname(__file__),"p_reg_io.py")); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def edit(t):
    t=m.edit(t)
    t,n=re.subn(r"\tld\tL,A\n\tld\tH,0\n\tld\tE,L\n\tld\tA,(\d+)\n\tcall\t_out\n",r"\tld\tE,A\n\tld\tA,\1\n\tcall\t_out\n",t)
    print("byte-in-A args",n)
    return t
