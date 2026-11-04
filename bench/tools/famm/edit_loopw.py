import re,importlib.util,os
spec=importlib.util.spec_from_file_location("s",os.path.join(os.path.dirname(__file__),"edit_schar.py")); s=importlib.util.module_from_spec(spec); spec.loader.exec_module(s)
CMP=r"\tld\tH,D\n\tld\tL,E\n\tpush\tDE\n\tld\tDE,\(\?\?AUTO\+4\)\n\tld\tA,L\n\tsub\tE\n\tld\tA,H\n\tsbc\tA,D\n\tpop\tDE\n"
NEW="\tld\tHL,(??AUTO+4)\n\tld\tA,E\n\tsub\tL\n\tld\tA,D\n\tsbc\tA,H\n"
def edit(kind,t):
    t=s.edit(kind,t)
    for fn,nxt in (("_loop_uint:","_loop_int:"),("_loop_int:","_loop_down:")):
        i=t.index(fn); j=t.index(nxt)
        f=t[i:j]
        f,n=re.subn(CMP,NEW,f)
        assert n>=1,(fn,n)
        # the body reads i from L after the compare: now i is in E
        f=f.replace("\tld\tA,L\n\tand\t63\n","\tld\tA,E\n\tand\t63\n")
        t=t[:i]+f+t[j:]
    return t
