#!/usr/bin/env python3
"""rmods.py DIR [extra.rel]: per-module bytes of a ROM build (DIR/k.rel, neogeo layout)."""
import sys, subprocess
from pathlib import Path
from um80 import ul80
d = Path(sys.argv[1]); extra = sys.argv[2:]
lib = Path(subprocess.run(["uc80","--print-lib-dir"],capture_output=True,text=True).stdout.strip())
orig = ul80.Linker.calculate_addresses
def patched(self):
    r = orig(self); tot = 0
    for m in self.modules:
        n = self._cseg_len(m); tot += n
        print("%5d code %4d data  %-8s @%04X  %s" % (n, m.data_size, m.name, m.code_base, " ".join(sorted(m.publics))[:90]))
    print("%5d total code, %d data" % (tot, self.data_total))
    return r
ul80.Linker.calculate_addresses = patched
sys.argv = ["ul80","--bin","-p","0","-d","F800","--rom-data","--rom-size","8000","--ram-size","800","--min-stack","100","-o","/dev/null", str(d/"k.rel"), *extra, str(lib/"libc_rom.lib"), str(lib/"runtime.lib")]
ul80.main()
