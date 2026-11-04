def fix(wd):
    p = wd/"dhry.h"; t = p.read_text()
    a = t.index("  typedef       enum    {Ident_1"); b = t.index("Enumeration;", a) + len("Enumeration;")
    t = t[:a] + "  enum {Ident_1, Ident_2, Ident_3, Ident_4, Ident_5};\n  typedef unsigned char Enumeration;" + t[b:]
    p.write_text(t)
