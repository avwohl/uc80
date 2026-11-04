import re, importlib.util
def edit(kind, t):
    spec = importlib.util.spec_from_file_location("q1", __import__("os").environ["A"] + "/p_q1.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    t = m.edit(kind, t)
    t, n = re.subn(r"\tadd\tA,LOW \((_\w+)\)\n\tld\tL,A\n\tld\tA,HIGH \(\1\)\n\tadc\tA,0\n\tld\tH,A\n", r"\tadd\tA,LOW (\1)\n\tld\tL,A\n\tld\tH,HIGH (\1)\n", t)
    print("page-safe sites", n)
    return t
