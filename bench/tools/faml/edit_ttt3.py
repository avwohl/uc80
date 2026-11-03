import re,importlib.util,os
spec=importlib.util.spec_from_file_location("e","/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/faml_an/edit_ttt2.py"); e=importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
def edit(kind,t):
    t=e.edit(kind,t)
    t,n=re.subn(r"\tcall\t_MinMax\n\tinc\tSP\n\tinc\tSP\n\tinc\tSP\n\tinc\tSP\n",r"\tcall\t_MinMax\n\tpop\tHL\n\tpop\tHL\n",t)
    print("pop cleanup",n)
    return t
