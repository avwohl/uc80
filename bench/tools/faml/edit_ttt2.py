import re,importlib.util,os
spec=importlib.util.spec_from_file_location("e","/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/faml_an/edit_ttt.py"); e=importlib.util.module_from_spec(spec); spec.loader.exec_module(e)
def edit(kind,t):
    t=e.edit(kind,t)
    t,n=re.subn(r"(\tor\tA\n\tjp\tNZ,@ENDIF\d+\n)\tld\tHL,_g_board\n\tadd\tHL,BC\n(\tld\tA,\(IX-5\)\n\tld\t\(HL\),A\n)",r"\1\2",t)
    print("addr reuse",n)
    return t
