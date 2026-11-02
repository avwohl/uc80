import re, importlib.util, os
spec = importlib.util.spec_from_file_location("e1", os.path.join(os.path.dirname(__file__), "edit_w.py")); e1 = importlib.util.module_from_spec(spec); spec.loader.exec_module(e1)
spec = importlib.util.spec_from_file_location("e2", os.path.join(os.path.dirname(__file__), "edit_wr2.py")); e2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(e2)
def edit(kind, t):
    t = e1.edit(kind, t)
    t = e2.LOOP.sub(lambda m: "\tld\tHL,10\n\tld\t(_i),HL\n" if ("sub\t10\n" in m.group(0) and "call" not in m.group(0) and "@RB" in m.group(0)) else m.group(0), t)
    return t
