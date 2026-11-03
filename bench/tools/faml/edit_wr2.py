import re, importlib.util, os
spec = importlib.util.spec_from_file_location("e1", os.path.join(os.path.dirname(__file__), "edit_wr.py")); e1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(e1)
LOOP = re.compile(r"\tld\tHL,0\n\tld\t\(_i\),HL\n@FOR(\d+):\n(?:(?!@FOR\d).*\n)*?@ENDFOR(\d+):\n")
def edit(kind, t):
    t = e1.edit(kind, t)
    def rep(m):
        body = m.group(0)
        if "sub\t10\n" in body and "call" not in body and "@RB" in body:
            return "\tld\tHL,10\n\tld\t(_i),HL\n"
        return body
    t = LOOP.sub(rep, t)
    return t
