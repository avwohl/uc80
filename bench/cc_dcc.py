"""dcc (davidly/dcc) as a benchmark compiler (bench/cc_dcc.py).

dcc is a C89 (plus some C99/C11) cross compiler for CP/M 2.2 on the Z80.
It writes Microsoft M80 .MAC assembly; its run-time library DCCRTL.MAC is
Z80 assembly source that dccrtlstrip cuts down to the parts a program
uses.  dcc has 16-bit int, 32-bit long, 32-bit float and no double
(double is float).  License: MIT.

    https://github.com/davidly/dcc  tag v3.6.0 (2026-09-05)
    commit 20290235dda4f5282c4e6abf86bd3e3c34bbd353

Install: "git archive v3.6.0 | tar -x -C ~/src/external/tools/dcc-v3.6.0",
then "sh m-posix.sh" in that directory (plain sh and clang; the canonical
scripts/build-dcc.ps1 needs pwsh, m-posix.sh builds the same six tools).
PROVENANCE.txt in that directory records the same facts.  Set DCC_HOME
to use another copy.

Pipeline, the same steps and order as dccmake (src/dccmake/dccmake.c):

    dcc [-module for every file after the first] -> X.MAC
    dccpeep -Os (size mode) or -Ot (speed mode; dccmake's default)
    dccrtlstrip --strip-apps  (cross-module dead code removal)
    m80c =X.MAC /X /O /Z /L /C
    dccrtlstrip -r DCCRTL.MAC -o RTLMIN.MAC  X.MAC ...
    m80c =RTLMIN.MAC /X /O /Z /L /C
    l80c /P:100,RTLMIN,<modules>,MAIN,PROG/N/E/Y

m80c and l80c are dcc's native clones of M80 and L80.  um80 and ul80
(the uc80 toolchain) also assemble and link dcc output and the stripped
run time: with "um80 -t -e .Z80" (dcc output has no .Z80 directive; M80
gets /Z) and "ul80 RTLMIN.REL <modules> MAIN.REL", the .COM files were
byte-identical to the m80c/l80c ones for empty, sieve and sieve+static.
The adapter uses dcc's own m80c/l80c, so dcc is measured as shipped;
DCC_ASM=um80 selects um80/ul80 instead (for cross-checks).

The main source (the one that starts with #include "bench.h") goes
first, as dcc wants main() in the first module.  The stack is dcc's
default (512 bytes; dcc grows it for large frames).

TIMER labels: dcc has no asm statement inside a function (an #asm block
is emitted between functions), so the bench.h branch for dcc calls two
empty functions, bt_beg() and bt_end(), whose entry points are the
labels.  The timed region therefore includes one RET and one CALL (27
T-states) that the other compilers do not have.  L80 keeps 6 characters
of a name, so the map has _BT_BE and _BT_EN; the adapter lists them as
TIMER_START and TIMER_STOP.

Caveat for readers of the results: dcc 3.6.0 contains about 150,000
lines of "exact schedule" emitters (src/dcc/dcc_mir_machine_*.c) that
match the shape of specific kernels (for example a fixed sieve build,
interpreter loops of dcc's own test programs) and emit hand-scheduled
code for them.  There is no switch to turn them off.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from compilers import TOOLS, Build, Compiler, Objects, um80_sizes

DCC_TAG = "v3.6.0"
DCC_COMMIT = "2029023"
DCC_HOME = Path(os.environ.get("DCC_HOME", TOOLS / f"dcc-{DCC_TAG}"))
TOOLS_NEEDED = ("dcc", "dccpeep", "dccrtlstrip", "m80c", "l80c",
                "DCCRTL.MAC", "stdio.h")
# The bench.h labels for dcc; L80 writes the first 6 characters.
LABELS = {"_bt_beg": "TIMER_START", "_bt_end": "TIMER_STOP"}


def _crlf(path: Path) -> None:
    """M80 reads CP/M text: CR LF line ends (dccmake's to_crlf)."""
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    path.write_bytes(data)


def _upper83(name: str) -> str:
    """An upper-case CP/M module name of at most 8 characters."""
    base = re.sub(r"[^A-Za-z0-9]", "", Path(name).stem).upper() or "M"
    return base[:8]


class Dcc(Compiler):
    """dcc -> dccpeep -> dccrtlstrip -> m80c -> l80c (see module comment)."""
    name = "dcc"
    modes = ("size", "speed")

    def __init__(self) -> None:
        self.asm = os.environ.get("DCC_ASM", "m80c")

    def available(self) -> bool:
        if not all((DCC_HOME / t).exists() for t in TOOLS_NEEDED):
            return False
        if self.asm == "um80":
            return all(shutil.which(t) for t in ("um80", "ul80"))
        return True

    def version(self) -> str:
        tools = ("um80 -t -e .Z80 + ul80" if self.asm == "um80"
                 else "m80c + l80c")
        return f"dcc {DCC_TAG} (davidly/dcc {DCC_COMMIT}), {tools}"

    def _run(self, cmd: list[str], cwd: Path, log: list[str],
             timeout: int = 600) -> int:
        log.append("$ " + " ".join(cmd))
        try:
            p = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            log.append(f"timeout after {timeout}s")
            return 124
        except FileNotFoundError as e:
            log.append(str(e))
            return 127
        out = (p.stdout + p.stderr).decode("latin-1").replace("\r", "")
        if out.strip():
            log.append(out.rstrip())
        return p.returncode

    def _assemble(self, mod: str, work: Path, log: list[str]) -> bool:
        if self.asm == "um80":
            rc = self._run(["um80", "-t", "-e", ".Z80", f"{mod}.MAC",
                            "-o", f"{mod}.REL", "-l", f"{mod}.PRN"],
                           work, log)
            return rc == 0 and (work / f"{mod}.REL").exists()
        rc = self._run([str(DCC_HOME / "m80c"), f"={mod}.MAC",
                        "/X", "/O", "/Z", "/L", "/C"], work, log)
        prn = work / f"{mod}.PRN"
        # dccmake's check_no_fatal_errors: a listing line "<n> Fatal
        # error(s)" with n > 0 means the module did not assemble.
        text = prn.read_text(errors="replace") if prn.exists() else ""
        fatal = any(int(n) > 0 for n in re.findall(
            r"^\s*(\d+)\s+Fatal error", text, re.M))
        ok = rc == 0 and (work / f"{mod}.REL").exists() and not fatal
        if not ok:
            log.append(f"assembly of {mod}.MAC failed")
        return ok

    def objects(self, sources, defines, includes, work, mode):
        """dcc and dccpeep for each source, then um80 reads the size.

        The sizes are the Code and Data segments that um80 reports for
        the module's .MAC file (assembled with "-t -e .Z80", as for
        DCC_ASM=um80), whatever DCC_ASM is.  Run time modules and the
        cross-module dead code removal of dccrtlstrip are not part of
        it, as for the other compilers' objects.
        """
        log: list[str] = []
        home = DCC_HOME
        peep = "-Os" if mode == "size" else "-Ot"
        dflags = [f"-D{d}" for d in defines]
        iflags = []
        for i in list(includes) + [home]:
            iflags += ["-I", str(i)]
        srcs = [Path(s) for s in sources]
        srcs.sort(key=lambda s: not s.read_text(encoding="latin-1")
                  .startswith('#include "bench.h"'))
        objs = []
        for i, src in enumerate(srcs):
            mac = work / f"M{i}.MAC"
            cmd = ([str(home / "dcc")] + (["-module"] if i else [])
                   + dflags + iflags + [str(src), "-o", str(mac)])
            if self._run(cmd, work, log) != 0 or not mac.exists():
                return Objects(False, log="\n".join(log))
            tmp = work / f"_PEEP{i}.MAC"
            if self._run([str(home / "dccpeep"), peep, str(mac), str(tmp)],
                         work, log) != 0 or not tmp.exists():
                return Objects(False, log="\n".join(log))
            tmp.replace(mac)
            _crlf(mac)
            p = subprocess.run(["um80", "-t", "-e", ".Z80", mac.name,
                                "-o", f"M{i}.REL"], cwd=work,
                               capture_output=True, text=True)
            log.append(p.stdout + p.stderr)
            sz = um80_sizes(p.stdout)
            if p.returncode != 0 or sz is None:
                return Objects(False, log="\n".join(log))
            objs.append((src.stem, sz[0], sz[1]))
        return Objects(True, objs, "\n".join(log))

    def build(self, sources, defines, includes, work, mode):
        log: list[str] = []
        home = DCC_HOME
        peep = "-Os" if mode == "size" else "-Ot"
        dflags = [f"-D{d}" for d in defines]
        iflags = []
        for i in list(includes) + [home]:
            iflags += ["-I", str(i)]
        tools = ("um80, ul80" if self.asm == "um80" else "m80c, l80c")
        command = " ".join(["dcc"] + dflags + ["; dccpeep", peep,
                                               "; dccrtlstrip;", tools])

        def fail() -> Build:
            return Build(False, log="\n".join(log), command=command)

        # main() must be in the first module; the runner prepends
        # #include "bench.h" to the main source only.
        srcs = [Path(s) for s in sources]
        srcs.sort(key=lambda s: not s.read_text(encoding="latin-1")
                  .startswith('#include "bench.h"'))
        mods: list[str] = []
        for i, src in enumerate(srcs):
            mod = "PROG" if i == 0 else _upper83(src.name)
            if mod in mods or mod == "RTLMIN":
                mod = f"M{i}"
            mods.append(mod)
            mac = work / f"{mod}.MAC"
            cmd = ([str(home / "dcc")] + (["-module"] if i else [])
                   + dflags + iflags + [str(src), "-o", str(mac)])
            if self._run(cmd, work, log) != 0 or not mac.exists():
                return fail()
            tmp = work / f"_PEEP{i}.MAC"
            if self._run([str(home / "dccpeep"), peep, str(mac), str(tmp)],
                         work, log) != 0 or not tmp.exists():
                return fail()
            tmp.replace(mac)
            _crlf(mac)

        macs = [str(work / f"{m}.MAC") for m in mods]
        if self._run([str(home / "dccrtlstrip"), "--strip-apps"] + macs,
                     work, log) != 0:
            return fail()
        for m in mods:
            if not self._assemble(m, work, log):
                return fail()

        rtl = work / "DCCRTL.MAC"
        shutil.copy(home / "DCCRTL.MAC", rtl)
        _crlf(rtl)
        if self._run([str(home / "dccrtlstrip"), "-r", str(rtl), "-o",
                      str(work / "RTLMIN.MAC")] + macs, work, log) != 0:
            return fail()
        _crlf(work / "RTLMIN.MAC")
        if not self._assemble("RTLMIN", work, log):
            return fail()

        # L80 order: run time first (its START is at 100H), then the
        # other modules, then the main module, as dccmake does.
        order = ["RTLMIN"] + mods[1:] + mods[:1]
        com = work / "PROG.COM"
        sym = work / "PROG.SYM"
        if self.asm == "um80":
            rc = self._run(["ul80"] + [f"{m}.REL" for m in order]
                           + ["-o", "PROG.COM", "-S", "PROG.SYM"], work, log)
        else:
            rc = self._run([str(home / "l80c"),
                            "/P:100," + ",".join(order) + ",PROG/N/E/Y"],
                           work, log)
        if rc != 0 or not com.exists() or not sym.exists():
            log.append("link failed")
            return fail()
        if re.search(r"undefined|unresolved", "\n".join(log[-2:]), re.I):
            log.append("link reported undefined symbols")
            return fail()

        syms: dict[str, int] = {}
        for addr, name in re.findall(r"([0-9A-Fa-f]{4})\s+([^\s]+)",
                                     sym.read_text(errors="replace")):
            if ":" in name:            # RTLMIN:LOCAL, a module-local label
                continue
            syms[name.upper()] = int(addr, 16)
        # bench.h defines both functions in every program, but only a
        # program that calls them has a timed region (z88dk-ticks never
        # stops counting when the start label is not reached).
        text = "\n".join(Path(m).read_text(errors="replace") for m in macs)
        for label, full in LABELS.items():
            short = label[:6].upper()
            used = len(re.findall(rf"\b{label}\b", text)) > 2
            if short in syms and used:
                syms.setdefault(full, syms[short])
        b = Build(True, com, syms, "\n".join(log), command)
        if "__BSSB" in syms:
            b.image = syms["__BSSB"] - 0x100
            b.bss = syms.get("__BSSE", syms["__BSSB"]) - syms["__BSSB"]
        return b


COMPILERS = [Dcc()]
