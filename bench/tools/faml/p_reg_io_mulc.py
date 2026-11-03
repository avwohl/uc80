import importlib.util,os
spec=importlib.util.spec_from_file_location("p",os.path.join(os.path.dirname(__file__),"p_reg_io.py")); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
def edit(t):
    t=m.edit(t)
    t=t.replace("call\t__mul16\n","call\t__mul16c\n")
    if "EXTRN\t__mul16c" not in t: t=t.replace("EXTRN\t_out","EXTRN\t__mul16c\n\tEXTRN\t_out",1)
    return t
