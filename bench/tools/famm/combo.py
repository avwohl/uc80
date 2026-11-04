#!/usr/bin/env python3
"""combo.py OUT.py p1.py p2.py ...: write OUT.py whose edit() applies the edit() of each patch in order."""
import sys
out = sys.argv[1]; ps = sys.argv[2:]
with open(out, "w") as f:
    f.write("import importlib.util\nPS=%r\n" % ps)
    f.write("def _ld(p):\n    s=importlib.util.spec_from_file_location('m',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m\n")
    f.write("def edit(*a):\n    t=a[-1]\n    for p in PS:\n        t=_ld(p).edit(*a[:-1],t)\n    return t\n")
