"""llvm-z80 as a benchmark compiler, label "llvm-z80" (bench/cc_llvmz80.py).

llvm-z80 is an LLVM/clang back end for the Z80
(https://github.com/llvm-z80/llvm-z80, Apache-2.0 WITH LLVM-exception).
We use the prebuilt macOS ARM64 release:

    tag     llvmz80-23.1.3-r1 (2026-10-20)
    clang   23.1.3, commit 031e4b1c4550de9e5d52f77bf1dbabcf5ffdda51
    asset   LLVM-Z80-23.1.3-r1-macOS-ARM64.tar.xz
    sha256  a6e7fd38934f9bc04b9734c58e5735a74b948a878e147bae00a62f5cd35559d7

Install: unpack the tarball with --strip-components=1 into
$UC80_BENCH_TOOLS/llvm-z80-llvmz80-23.1.3-r1 (default
~/src/external/tools/...).  Set LLVMZ80=<dir> to use another copy.

Tool chain (the "SDCC path" of the llvm-z80 README and wiki page
SDCC-Interoperability):

  clang --target=z80-unknown-none-sdcc -S   C to sdasz80 assembly
  sdasz80 (SDCC 4.6.0, Homebrew)            assembly to .rel
  sdldz80 (SDCC 4.6.0, Homebrew)            link to Intel HEX, then .com

The adapter runs the three steps itself (the clang driver would run
whatever sdasz80/sdldz80 is first on PATH, with its own crt0 at 0000h).

Start-up and console I/O: bench/crt/sdcc/crt0_cpm.s and cpmio.s, the
same files as the "sdcc" column.  llvm-z80 uses __sdcccall(1) by
default (wiki: "By default, Clang utilizes the __sdcccall(1)
convention"), which is the convention cpmio.s implements (int argument
in HL, int result in DE); we checked the clang output for main and for
a call of putchar.  llvm-z80 puts globals in the SDCC areas (_CODE,
_DATA for zeroed data, _INITIALIZED/_INITIALIZER for initialized data),
so crt0_cpm.s clears and copies them as it does for SDCC.

Libraries, in this order:
  1. llvm-z80 lib/z80/z80_rt.lib, the compiler runtime (GCC-style
     helpers such as ___mulhi3, ___divsi3, ___addsf3, and memset,
     memcpy, strlen, strcmp, ...).  The wiki says that a program that
     links SDCC code must link both runtimes.
  2. SDCC 4.6.0 z80.lib for the rest of the C library (printf,
     puts, ...) and for the SDCC helpers that this library code calls.
     llvm-z80 has no C library of its own.

Headers, in search order:
  1. bench/crt/llvm-z80/include (-isystem): small wrappers that make
     the SDCC headers work with clang; each says why in its comment.
     sdcc-lib.h selects the SDCC z80 features (clang does not define
     __SDCC_z80); stddef.h, stdlib.h and wchar.h keep clang's 16-bit
     wchar_t and stop the SDCC headers from declaring it again as
     unsigned long; math.h maps the double names (sqrt, sin, pow, ...)
     to the SDCC float functions, which are the same functions with
     -mdouble=32.
  2. clang's resource headers (stdarg.h, stddef.h, stdint.h, limits.h,
     float.h, ...).
  3. the SDCC 4.6.0 headers (-idirafter) for stdio.h, string.h, ...
-D__reentrant= as well: __reentrant is an SDCC keyword that means
nothing on the z80; SDCC math.h uses it unless __SDCC_z80 is defined
(SDCC stdlib.h defines it empty in the same way).  None of this changes
the program; it makes the SDCC headers parse.

Diagnostics: clang 16 and later make some C89 constructs errors by
default; SDCC, z88dk and uc80 accept them with a warning.  The adapter
turns them back into warnings (-Wno-error=implicit-function-declaration,
implicit-int, int-conversion, incompatible-pointer-types).  The code
that clang generates does not change.

-fno-builtin-exp2f: clang turns powf(2.0f, x) into exp2f(x), a C99
function that the SDCC z80.lib does not have (binary-trees then fails
to link).  The switch tells clang that the library has no exp2f, so the
program calls powf as written.

Switches: -Os for size mode, -O2 for speed mode.  -mdouble=32 in both:
llvm-z80 23.1.3 has a 64-bit double by default but its runtime has no
f64 routines (README: "planned support for f64"), so a program with
double does not link.  With -mdouble=32, double is IEEE single, as for
uc80 and SDCC.  clang's other defaults for this target stay (frame
pointer, static frames for non-reentrant functions, C17 with GNU
extensions).

TIMER labels: the __z80__ && __clang__ branch of bench/bench.h maps
intrinsic_label(X) to __asm__ volatile("X::" ::: "memory").  When clang
inlines a function that holds a label and also keeps its out-of-line
copy, the label appears twice and sdasz80 fails ("multiple
definitions"); the program then needs the function to be static (see
the sort-* entries in programs.d/z88dk_misc.toml).

Heap: the link puts the z80.lib _HEAP_END area at SDCC_HEAP_TOP, as for
standalone SDCC (see compilers.py), so malloc gets the free memory and
not the 1023 bytes of z80.lib heap.s.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from compilers import (BENCH, SDCC_HEAP_TOP, TOOLS, Build, Compiler, _run,
                       ihx_to_com, read_sdld_map)

TAG = "llvmz80-23.1.3-r1"


class LlvmZ80(Compiler):
    name = "llvm-z80"

    def __init__(self):
        self.root = Path(os.environ.get("LLVMZ80",
                                        TOOLS / f"llvm-z80-{TAG}"))
        self.clang = self.root / "bin" / "clang"

    def _sdcc_bin(self) -> Path | None:
        sdcc = shutil.which("sdcc")
        return Path(sdcc).resolve().parent if sdcc else None

    def _sdcc_share(self) -> Path | None:
        b = self._sdcc_bin()
        if b is None:
            return None
        for cand in (b.parent / "share" / "sdcc",
                     Path(shutil.which("sdcc")).parent.parent / "share"
                     / "sdcc"):
            if (cand / "include" / "stdio.h").exists():
                return cand
        return None

    def available(self) -> bool:
        b = self._sdcc_bin()
        return (self.clang.exists() and b is not None
                and (b / "sdasz80").exists() and (b / "sdldz80").exists()
                and self._sdcc_share() is not None)

    def version(self) -> str:
        out = _run([str(self.clang), "--version"], Path("."))[1]
        m = re.search(r"clang version (\S+) \(\S+ ([0-9a-f]{7})", out)
        cv = f"clang {m.group(1)} {m.group(2)}" if m else "clang ?"
        sv = _run([str(self._sdcc_bin() / "sdcc"), "-v"], Path("."))[1]
        m = re.search(r"\d+\.\d+\.\d+ #\d+", sv)
        return (f"llvm-z80 {TAG} ({cv}), sdasz80/sdldz80 and z80.lib "
                f"from SDCC {m.group(0) if m else '?'}")

    def build(self, sources, defines, includes, work, mode):
        log = []
        sbin = self._sdcc_bin()
        share = self._sdcc_share()
        sdas, sdld = str(sbin / "sdasz80"), str(sbin / "sdldz80")
        crt = BENCH / "crt" / "sdcc"
        mine = BENCH / "crt" / "llvm-z80"
        for s in ("crt0_cpm", "cpmio"):
            rc, out = _run([sdas, "-g", "-o", f"{s}.rel",
                            str(crt / f"{s}.s")], work)
            if rc != 0:
                return Build(False, log=out)
        opt = ["-Os"] if mode == "size" else ["-O2"]
        flags = (["--target=z80-unknown-none-sdcc"] + opt + ["-mdouble=32"]
                 + [f"-I{i}" for i in includes]
                 + ["-Wno-error=" + w for w in (
                     "implicit-function-declaration", "implicit-int",
                     "int-conversion", "incompatible-pointer-types")]
                 + ["-fno-builtin-exp2f"]
                 + ["-D__reentrant=", "-isystem", str(mine / "include"),
                    "-idirafter", str(share / "include")]
                 + [f"-D{d}" for d in defines])
        command = " ".join([str(self.clang)] + flags)
        rels = []
        for src in sources:
            stem = Path(src).stem
            rc, out = _run([str(self.clang), "-S"] + flags
                           + [str(src), "-o", f"{stem}.s"], work)
            log.append(out)
            if rc != 0:
                return Build(False, log="\n".join(log), command=command)
            rc, out = _run([sdas, "-g", "-o", f"{stem}.rel", f"{stem}.s"],
                           work)
            log.append(out)
            if rc != 0:
                return Build(False, log="\n".join(log), command=command)
            rels.append(f"{stem}.rel")
        link = ([sdld, "-mjwx", "-i", "prog.ihx",
                 "-b", "_CODE=0x0100", "-b", "_DATA=0x0000",
                 "-b", f"_HEAP_END=0x{SDCC_HEAP_TOP:04x}",
                 "-k", str(self.root / "lib" / "z80"), "-l", "z80_rt",
                 "-k", str(share / "lib" / "z80"), "-l", "z80",
                 "crt0_cpm.rel", "cpmio.rel"] + rels + ["-e"])
        rc, out = _run(link, work)
        log.append(" ".join(link))
        log.append(out)
        # sdldz80 exits 0 even with undefined symbols; it says so.
        if (rc != 0 or "?ASlink-Warning-Undefined" in out
                or not (work / "prog.ihx").exists()):
            return Build(False, log="\n".join(log), command=command)
        image = ihx_to_com(work / "prog.ihx", work / "prog.com")
        syms, ram, err = read_sdld_map(work / "prog.map")
        if err:
            log.append(err)
            return Build(False, log="\n".join(log), command=command)
        return Build(True, work / "prog.com", syms, "\n".join(log),
                     command, image=image, bss=ram)


COMPILERS = [LlvmZ80()]
