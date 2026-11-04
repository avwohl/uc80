#!/usr/bin/env python3
"""lmods.py DIR [extra.rel ...]: link DIR/prog.rel with uc80 libs; print per-module code bytes and data bytes (ul80 module map)."""
import sys, subprocess
from pathlib import Path
from um80 import ul80
d = Path(sys.argv[1]); extra = sys.argv[2:]
lib = Path(subprocess.run(["uc80","--print-lib-dir"],capture_output=True,text=True).stdout.strip())
orig = ul80.Linker.calculate_addresses
def patched(self):
    r = orig(self)
    tot = 0
    for m in self.modules:
        n = self._cseg_len(m); tot += n
        print("%5d code %4d data  %-8s @%04X  %s" % (n, m.data_size, m.name, m.code_base, " ".join(sorted(m.publics))[:80]))
    print("%5d total code, %d data" % (tot, self.data_total))
    return r
ul80.Linker.calculate_addresses = patched
sys.argv = ["ul80", str(d/"prog.rel"), *extra, str(lib/"libc.lib"), str(lib/"runtime.lib"), "-o", "/dev/null"]
ul80.main()
