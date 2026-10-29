"""Compiler adapters for the benchmark harness.

Each adapter turns a list of C sources into a CP/M .com file and a symbol
table (label name -> address) that the runner uses to find the
TIMER_START/TIMER_STOP labels.  An adapter never edits sources; every
compiler gets the same patched files and the same defines.  Glue that a
compiler needs (start-up code, header wrappers) lives in bench/crt/ and in
the adapter itself.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

BENCH = Path(__file__).resolve().parent
TOOLS = Path(os.environ.get("UC80_BENCH_TOOLS",
                            BENCH.parent.parent / "external" / "tools"))


@dataclass
class Build:
    ok: bool
    com: Path | None = None
    symbols: dict[str, int] = field(default_factory=dict)
    log: str = ""
    command: str = ""
    # Bytes of code and initialized data in the .com (everything below
    # BSS), and the zero-filled RAM after it.  A z88dk +cpm .com stores
    # its BSS in the file, so the file size alone is not comparable.
    image: int | None = None
    bss: int | None = None


@dataclass
class Objects:
    """Result of Compiler.objects(): per-object sizes of a size-only
    program.  `objects` holds (source stem, code bytes, data bytes)."""
    ok: bool
    objects: list[tuple[str, int, int]] = field(default_factory=list)
    log: str = ""


# A size-only program FITS when its linked image plus BSS ends at least
# TPA_STACK bytes below TPA_LIMIT.  TPA_LIMIT is the BDOS base of a
# 64 KB CP/M 2.2 system (BDOS at E400h; the CCP below it can be
# overwritten), so the TPA is 0100h to E3FFh, 58,112 bytes.  The stack
# and the heap of the program need room too.
TPA_LIMIT = 0xE400
TPA_STACK = 0x200

# A link failure whose message says the program is too large is TOO-BIG,
# any other link failure is BUILD-FAIL(link).
TOO_BIG_RE = re.compile(
    r"too (big|large|many)|overflow|exceeds|out of memory|heap:|"
    r"above the heap|overlap|beyond|address.*(range|64)|"
    r"does not fit|memory full", re.I)


def size_only_status(link_log: str) -> str:
    """TOO-BIG or BUILD-FAIL(link), from the text of a failed link."""
    return "TOO-BIG" if TOO_BIG_RE.search(link_log) else "BUILD-FAIL(link)"


def um80_sizes(out: str) -> tuple[int, int] | None:
    """(code, data) bytes from the summary that um80 prints."""
    c = re.search(r"Code segment:\s*(\d+) bytes", out)
    d = re.search(r"Data segment:\s*(\d+) bytes", out)
    return (int(c.group(1)), int(d.group(1))) if c and d else None


# Areas of an SDCC (sdasz80) .rel file.  Code is the instructions and,
# for SDCC, the constants (strings and const tables live in _CODE).  Data
# is the variables.  _INITIALIZER is the ROM copy of _INITIALIZED, so it
# is not counted again.
SDCC_CODE_AREAS = ("_CODE", "_HOME", "_GSINIT", "_GSFINAL", "_CABS")
SDCC_SKIP_AREAS = ("_INITIALIZER", "_DABS")


def sdcc_rel_sizes(rel: Path) -> tuple[int, int]:
    """(code, data) bytes from the "A <area> size <hex>" lines."""
    code = data = 0
    for line in rel.read_text(errors="replace").splitlines():
        m = re.match(r"A (\S+) size ([0-9A-Fa-f]+) flags", line)
        if not m:
            continue
        area, n = m.group(1), int(m.group(2), 16)
        if area in SDCC_CODE_AREAS:
            code += n
        elif area not in SDCC_SKIP_AREAS:
            data += n
    return code, data


def _run(cmd: list[str], cwd: Path, env: dict | None = None,
         timeout: int = 1800) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s: {' '.join(cmd)}"
    except FileNotFoundError as e:
        return 127, str(e)
    return p.returncode, p.stdout + p.stderr


# Top of the malloc heap for the SDCC z80.lib (standalone SDCC and
# llvm-z80).  The z80.lib heap.s reserves only 1023 bytes; the link puts
# its _HEAP_END area (the ___sdcc_heap_end label) here instead, so the
# heap runs from the end of the program to this address.  z88dk-ticks
# puts ED FE C9 at 0005h, so a crt0 that loads SP from (6) starts the
# stack at 0C9FEh, and 2.5 KB of stack stay above the heap.  A program that does not call malloc
# does not link heap.s, and its .com does not change.
SDCC_HEAP_TOP = 0xC000


def read_sdld_map(mp: Path) -> tuple[dict[str, int], int, str | None]:
    """Symbols, RAM bytes and a heap error from an sdldz80 .map file.

    Labels from __asm__ land in the module .sym files as module-relative
    addresses only, so the global table in the .map is the one to read.
    The error is set when the program links the heap and its data end
    above SDCC_HEAP_TOP, so that malloc would get a heap of negative size.
    """
    syms, ram = {}, 0
    for line in mp.read_text().splitlines():
        m = re.match(r"\s+([0-9A-F]{8})\s+(\S+)", line)
        if m:
            syms[m.group(2).upper()] = int(m.group(1), 16)
        m = re.match(r"(_DATA|_INITIALIZED|_BSEG|_BSS)\s+[0-9A-F]{8}"
                     r"\s+[0-9A-F]{8} =\s+(\d+)\. bytes", line)
        if m:
            ram += int(m.group(2))
    err = None
    if "___SDCC_HEAP" in syms and "S__HEAP_END" in syms:
        if syms["S__HEAP"] + syms.get("L__HEAP", 0) > syms["S__HEAP_END"]:
            err = (f"heap: the program ends at "
                   f"{syms['S__HEAP'] + syms.get('L__HEAP', 0):04X}h, above "
                   f"the heap top {syms['S__HEAP_END']:04X}h")
    return syms, ram, err


def ihx_to_com(ihx: Path, com: Path, origin: int = 0x100) -> int:
    """Write the bytes of an Intel HEX file from `origin` up as a .com."""
    mem: dict[int, int] = {}
    for line in ihx.read_text().splitlines():
        line = line.strip()
        if not line.startswith(":"):
            continue
        b = bytes.fromhex(line[1:])
        n, addr, typ = b[0], (b[1] << 8) | b[2], b[3]
        if typ == 0:
            for i in range(n):
                mem[addr + i] = b[4 + i]
    top = max(a for a in mem if a >= origin) + 1
    com.write_bytes(bytes(mem.get(a, 0) for a in range(origin, top)))
    return top - origin


class Compiler:
    name = ""
    modes = ("size", "speed")

    def available(self) -> bool:
        raise NotImplementedError

    def version(self) -> str:
        raise NotImplementedError

    def build(self, sources: list[Path], defines: list[str],
              includes: list[Path], work: Path, mode: str) -> Build:
        raise NotImplementedError

    def objects(self, sources: list[Path], defines: list[str],
                includes: list[Path], work: Path, mode: str) -> Objects:
        """Compile each source alone (no link) for a size-only program
        and return the code and data bytes of each object."""
        raise NotImplementedError


class Uc80(Compiler):
    """uc80 -> um80 -> ul80, whole-program mode."""
    name = "uc80"
    # uc80 has one optimization setting; both modes build the same way.
    modes = ("size",)

    def available(self) -> bool:
        return all(shutil.which(t) for t in ("uc80", "um80", "ul80"))

    def version(self) -> str:
        from importlib.metadata import version
        _, um = _run(["um80", "--version"], Path("."))
        um = um.strip().splitlines()[0] if um.strip() else "um80 ?"
        sha = _run(["git", "-C", str(BENCH.parent), "rev-parse", "--short",
                    "HEAD"], Path("."))[1].strip()
        dirty = _run(["git", "-C", str(BENCH.parent), "status",
                      "--porcelain", "src"], Path("."))[1].strip()
        return (f"uc80 {version('uc80')} ({sha}"
                f"{'+dirty' if dirty else ''}), {um}")

    def build(self, sources, defines, includes, work, mode):
        lib = Path(_run(["uc80", "--print-lib-dir"], work)[1].strip())
        # UC80_BENCH_FLAGS: extra uc80 options for an A/B run, for example
        # UC80_BENCH_FLAGS=--no-register-cc
        extra = os.environ.get("UC80_BENCH_FLAGS", "").split()
        cc = (["uc80"] + [str(s) for s in sources]
              + [f"-I{i}" for i in includes] + [f"-D{d}" for d in defines]
              + extra + ["-o", "prog.mac"])
        # --no-whole-program puts no start-up code in the module: link
        # crt0.rel first, or the program starts in its first function.
        crt0 = ([str(lib / "crt0.rel")] if "--no-whole-program" in extra
                else [])
        steps = [cc, ["um80", "prog.mac", "-o", "prog.rel"],
                 ["ul80", *crt0, "prog.rel", str(lib / "libc.lib"),
                  str(lib / "runtime.lib"), "-o", "prog.com",
                  "-S", "prog.sym"]]
        log = []
        for cmd in steps:
            rc, out = _run(cmd, work)
            log.append(out)
            if rc != 0:
                return Build(False, log="\n".join(log),
                             command=" ".join(cc))
        syms = {}
        for line in (work / "prog.sym").read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                syms[parts[1].upper()] = int(parts[0], 16)
        b = Build(True, work / "prog.com", syms, "\n".join(log), " ".join(cc))
        if "__BSS_START" in syms:
            b.image = syms["__BSS_START"] - 0x100
            b.bss = syms.get("__BSS_END", syms["__BSS_START"]) - syms["__BSS_START"]
        return b


    def objects(self, sources, defines, includes, work, mode):
        """uc80 --no-whole-program, no embedded runtime or start-up
        code, one .rel per source; um80 prints the code and data sizes
        of each .rel."""
        log, objs = [], []
        for src in sources:
            stem = Path(src).stem
            rc, out = _run(["uc80", str(src), "--no-whole-program",
                            "--no-embed-runtime", "--no-embed-startup"]
                           + [f"-I{i}" for i in includes]
                           + [f"-D{d}" for d in defines]
                           + ["-o", f"{stem}.mac"], work)
            log.append(out)
            if rc == 0:
                rc, out = _run(["um80", f"{stem}.mac", "-o", f"{stem}.rel"],
                               work)
                log.append(out)
            sz = um80_sizes(out) if rc == 0 else None
            if sz is None:
                return Objects(False, log="\n".join(log))
            objs.append((stem, sz[0], sz[1]))
        return Objects(True, objs, "\n".join(log))


class Sdcc(Compiler):
    """Standalone SDCC, z80 port, with a small CP/M crt0 and BDOS putchar.

    Both modes use --max-allocs-per-node 200000, the z88dk wiki setting.
    The malloc heap ends at SDCC_HEAP_TOP (see there).  A second SDCC is a
    plugin that gives another exe and label (bench/cc_sdcc44.py).
    """
    name = "sdcc"

    def __init__(self, exe: str = "sdcc", label: str | None = None,
                 extra_flags: tuple[str, ...] = (), allocs: int = 200000,
                 timeout: int = 1800):
        self.exe = exe
        if label:
            self.name = label
        # bench/difftest.py gives --fsigned-char (uc80 has a signed plain
        # char) and fewer allocs, so that random programs build fast.
        self.extra_flags = list(extra_flags)
        self.allocs = allocs
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which(self.exe) is not None

    def version(self) -> str:
        return _run([self.exe, "-v"], Path("."))[1].splitlines()[0].strip()

    def _bin(self, tool: str) -> str:
        # sdasz80 next to the sdcc we run, so 4.4 and 4.6 never mix.
        d = Path(shutil.which(self.exe)).parent
        return str(d / tool) if (d / tool).exists() else tool

    def _flags(self, defines, includes, mode) -> list[str]:
        opt = ["--opt-code-size"] if mode == "size" else ["--opt-code-speed"]
        return (["-mz80"] + opt + ["--max-allocs-per-node", str(self.allocs)]
                + self.extra_flags + [f"-I{i}" for i in includes]
                + [f"-D{d}" for d in defines])

    def objects(self, sources, defines, includes, work, mode):
        """sdcc -c, one .rel per source; the sizes are the "A _CODE size"
        lines (see sdcc_rel_sizes)."""
        flags = self._flags(defines, includes, mode)
        log, objs = [], []
        for src in sources:
            stem = Path(src).stem
            rc, out = _run([self.exe, "-c"] + flags
                           + [str(src), "-o", f"{stem}.rel"], work,
                           timeout=self.timeout)
            log.append(out)
            if rc != 0 or not (work / f"{stem}.rel").exists():
                return Objects(False, log="\n".join(log))
            objs.append((stem,) + sdcc_rel_sizes(work / f"{stem}.rel"))
        return Objects(True, objs, "\n".join(log))

    def build(self, sources, defines, includes, work, mode):
        log = []
        crt = BENCH / "crt" / "sdcc"
        for s in ("crt0_cpm", "cpmio"):
            rc, out = _run([self._bin("sdasz80"), "-g", "-o", f"{s}.rel",
                            str(crt / f"{s}.s")], work)
            if rc != 0:
                return Build(False, log=out)
        flags = self._flags(defines, includes, mode)
        rels = []
        for src in sources:
            rel = f"{Path(src).stem}.rel"
            rc, out = _run([self.exe, "-c"] + flags + [str(src), "-o", rel],
                           work, timeout=self.timeout)
            log.append(out)
            if rc != 0:
                return Build(False, log="\n".join(log),
                             command=" ".join([self.exe] + flags))
            rels.append(rel)
        link = [self.exe, "-mz80", "--no-std-crt0", "--code-loc", "0x100",
                "--data-loc", "0", f"-Wl-b_HEAP_END=0x{SDCC_HEAP_TOP:x}",
                "crt0_cpm.rel", "cpmio.rel"] + rels + ["-o", "prog.ihx"]
        rc, out = _run(link, work)
        log.append(out)
        if rc != 0:
            return Build(False, log="\n".join(log),
                         command=" ".join([self.exe] + flags))
        image = ihx_to_com(work / "prog.ihx", work / "prog.com")
        syms, ram, err = read_sdld_map(work / "prog.map")
        if err:
            log.append(err)
            return Build(False, log="\n".join(log),
                         command=" ".join([self.exe] + flags))
        return Build(True, work / "prog.com", syms, "\n".join(log),
                     " ".join([self.exe] + flags), image=image, bss=ram)


class Z88dk(Compiler):
    """z88dk zcc +cpm with either the zsdcc or the sccz80 back end."""

    def __init__(self, backend: str):
        self.backend = backend
        self.name = f"z88dk-{backend}"
        self.root = Path(os.environ.get(
            "Z88DK", TOOLS / "z88dk-2.4" / "z88dk"))

    def _env(self) -> dict:
        env = dict(os.environ)
        env["PATH"] = f"{self.root / 'bin'}:{env['PATH']}"
        env["ZCCCFG"] = str(self.root / "lib" / "config")
        return env

    def available(self) -> bool:
        return (self.root / "bin" / "zcc").exists()

    def version(self) -> str:
        if self.backend == "zsdcc":
            _, out = _run(["z88dk-zsdcc", "-v"], Path("."), self._env())
            m = re.search(r"\d+\.\d+\.\d+ #\d+", out)
            return f"z88dk 2.4, zsdcc {m.group(0) if m else '?'}"
        return "z88dk 2.4, sccz80"

    def _flags(self, mode) -> list[str]:
        if self.backend == "zsdcc":
            flags = ["-compiler=sdcc", "-SO3", "--max-allocs-per-node200000"]
            flags += ["--opt-code-size"] if mode == "size" else []
        else:
            flags = ["-compiler=sccz80", "-O3"]
            flags += [] if mode == "size" else ["--opt-code-speed=all"]
        return flags

    def objects(self, sources, defines, includes, work, mode):
        """zcc -c, one .o per source; z88dk-z80nm lists the sections.
        Code is the sections whose name starts with "code"; data is
        rodata, data and bss (the sections of "" and IGNORE are empty)."""
        log, objs = [], []
        nm = self.root / "bin" / "z88dk-z80nm"
        for src in sources:
            stem = Path(src).stem
            cmd = (["zcc", "+cpm"] + self._flags(mode)
                   + [f"-I{i}" for i in includes]
                   + [f"-D{d}" for d in defines]
                   + ["-c", str(src), "-o", f"{stem}.o"])
            rc, out = _run(cmd, work, self._env())
            log.append(out)
            if rc != 0 or not (work / f"{stem}.o").exists():
                return Objects(False, log="\n".join(log))
            rc, out = _run([str(nm), "-a", f"{stem}.o"], work, self._env())
            code = data = 0
            for m in re.finditer(r'Section "?(\w*)"?: (\d+) bytes', out):
                if m.group(1).startswith("code"):
                    code += int(m.group(2))
                elif m.group(1) not in ("", "IGNORE"):
                    data += int(m.group(2))
            if rc != 0 or not re.search(r"Section", out):
                log.append(out)
                return Objects(False, log="\n".join(log))
            objs.append((stem, code, data))
        return Objects(True, objs, "\n".join(log))

    def build(self, sources, defines, includes, work, mode):
        flags = self._flags(mode)
        # Float library, as in the z88dk classic readmes: genmath for
        # sccz80 (-lm), math48 for zsdcc (-lmath48; with -lm zsdcc does
        # not link, and --math32 gives pow(2,n) = 2^n - 1).  A program
        # that uses no float links nothing from it.
        mathlib = ["-lmath48"] if self.backend == "zsdcc" else ["-lm"]
        cmd = (["zcc", "+cpm"] + flags + [f"-I{i}" for i in includes]
               + [f"-D{d}" for d in defines] + [str(s) for s in sources]
               + mathlib + ["-o", "prog", "-m", "-create-app"])
        rc, out = _run(cmd, work, self._env())
        com = next((p for p in work.iterdir()
                    if p.name.lower() == "prog.com"), None)
        if rc != 0 or com is None:
            return Build(False, log=out, command=" ".join(cmd))
        syms = {}
        mp = work / "prog.map"
        if mp.exists():
            for line in mp.read_text(errors="replace").splitlines():
                m = re.match(r"(\w+)\s*=\s*\$([0-9A-Fa-f]+)", line)
                if m:
                    syms[m.group(1).upper()] = int(m.group(2), 16)
        b = Build(True, com, syms, out, " ".join(cmd))
        if "__BSS_HEAD" in syms:
            b.image = syms["__BSS_HEAD"] - 0x100
            b.bss = syms.get("__BSS_END_TAIL", syms["__BSS_HEAD"]) - syms["__BSS_HEAD"]
        return b


def all_compilers() -> list[Compiler]:
    """The built-in adapters plus COMPILERS from every bench/cc_*.py."""
    import importlib.util
    comps: list[Compiler] = [Uc80(), Sdcc(), Z88dk("zsdcc"),
                             Z88dk("sccz80")]
    for path in sorted(BENCH.glob("cc_*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        comps.extend(getattr(mod, "COMPILERS", []))
    return comps
