"""HI-TECH C v3.09 for CP/M as a benchmark compiler (bench/cc_hitech.py).

HI-TECH C 3.09 is a native CP/M-80 compiler.  It is freeware: "provided
free of charge for any use, private or commercial, strictly as-is", and
the copyright stays with HI-TECH Software.  We use the consolidated
release by Tony Nicholson:

    https://github.com/agn453/HI-TECH-Z80-C  tag v3.09-21
    commit c7e943fb01855213a20e15dba9535be7d178d086
    tarball sha256 b8970e248438675e8c01af36b5034192efe24b169e75a8f93cc43f821525661d

Install: copy every file of dist/ to ~/src/external/tools/hitech-3.09/
with lower-case names (zxcc needs lower-case 8.3 names), and copy
dist/C309-21.COM to c.com there.  PROVENANCE.txt in that directory
records the same facts.  Set HITECH=<dir> to use another copy.

The passes run under zxcc (CP/M emulator for cross compiling).  The
adapter runs the passes itself instead of C.COM, with the same
arguments that "c -O -N" (size mode) or "c -OF -N" (speed mode) gives
them, for three reasons:

  * P1 writes its error messages to a device that zxcc drops, so a
    failed C.COM run shows no reason.
  * The asm("...") statement of the manual does not work in this P1
    (the decompiled source at github.com/nikitinprior/dp1 shows why:
    parseStmtAsm compares a token with ')' instead of T_RPAREN, and the
    T_ASM case falls through into the while-loop parser).  bench.h
    uses asm("...") for the TIMER labels, so the adapter turns every
    asm("text"); in the CPP output into an #asm / text / #endasm block,
    which P1 handles correctly.  This gives the documented meaning.
  * A CP/M command tail holds at most 127 bytes; with more -D options
    the defines go in #define lines at the top of each source.

More source-level adaptations, in the adapter's private copy of the
sources and headers, none of which changes the meaning of the program
(see modernize()):

  * // comments become /* */ comments (HI-TECH 3.09 is from 1987);
  * blanks before or after the # of a directive go away;
  * #elif becomes #else + #if, with one more #endif;
  * #pragma and #warning lines become empty lines, and #error becomes
    a line that P1 rejects (CPP rejects all three, even when skipped);
  * an integer constant with a U suffix becomes a cast to unsigned or
    unsigned long (HI-TECH 3.09 knows L but not U); in #if lines the U
    goes away;
  * file names become lower-case 8.3 names, as CP/M truncates them,
    and #include "dir/file.h" names the flat copy of that header.

HI-TECH 3.09 has no const, volatile or signed keyword.  The adapter
defines each one as an empty macro (-Dconst= -Dvolatile= -Dsigned=).
This does not change the work: const only restricts the program, char
is signed already, and the benchmarks use volatile only to hide a
constant from the compiler, which HI-TECH 3.09 never propagates from an
initializer.  (Without volatile, OPTIM may drop a second load of the
same variable in one basic block, so a program that polls memory that
an interrupt changes would differ; a CP/M benchmark does not do that.)

Not fixed, so a program that needs them fails to build: the # and ##
preprocessor operators, prototypes with a function-pointer parameter
(see the README of the repository), and most C99 features.

Library choice: LIBC.LIB always; LIBF.LIB (float printf and float
runtime) too when a source of the program mentions float or double or
includes math.h, as "c -LF" would.  HI-TECH 3.09 float and double are
both 4 bytes (measured with sizeof; the bit format is not checked).
The start-up module is NRTCPM.OBJ (-N): minimal _getargs(), no command
line redirection or wild cards.

TIMER labels: the HI_TECH_C branch of bench/bench.h defines them.
OPTIM deletes every label that no jump uses unless its name starts with
_, so the labels are _TIMER_START and _TIMER_STOP.  The map symbols get
the C view too: each _X is also listed as X (when no X exists).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from compilers import TOOLS, Build, Compiler, Objects

HT_ROOT = Path(os.environ.get("HITECH", TOOLS / "hitech-3.09"))
TAIL_MAX = 120          # CP/M command tail limit is 127; keep a margin
LINK_MAX = 108          # zxcc cut a LINQ tail of 117 bytes (measured)
# The macros C.COM defines, then the C89 keywords that HI-TECH 3.09 does
# not know, defined away (see the module comment).
PREDEFINED = ["CPM", "HI_TECH_C", "z80", "const=", "volatile=", "signed="]


def _zxcc() -> str | None:
    return os.environ.get("ZXCC") or shutil.which("zxcc") or (
        "/opt/homebrew/bin/zxcc"
        if Path("/opt/homebrew/bin/zxcc").exists() else None)


def name83(name: str) -> str:
    """The lower-case 8.3 name that CP/M makes of `name`."""
    base, dot, ext = name.rpartition(".")
    if not dot:
        base, ext = name, ""
    out = base[:8].lower()
    return f"{out}.{ext[:3].lower()}" if ext else out


_DIRECTIVE = re.compile(r"[ \t]*#[ \t]*(\w*)")


def _skip_literal(text: str, i: int) -> int:
    """Index after the string or character literal that starts at i."""
    q, j, n = text[i], i + 1, len(text)
    while j < n and text[j] != q and text[j] != "\n":
        j += 2 if text[j] == "\\" else 1
    return min(j + 1, n)


def _directive_end(text: str, i: int) -> int:
    """Index of the newline that ends the directive starting at i."""
    j, n = i, len(text)
    while j < n and text[j] != "\n":
        if text.startswith("/*", j):
            k = text.find("*/", j + 2)
            j = n if k < 0 else k + 2
        elif text[j] in "\"'":
            j = _skip_literal(text, j)
        elif text[j] == "\\" and text.startswith("\n", j + 1):
            j += 2
        elif text[j] == "\\" and text.startswith("\r\n", j + 1):
            j += 3
        else:
            j += 1
    return j


_NUMBER = re.compile(r"(0[xX][0-9A-Fa-f]+|[0-9]+)([uU][lL]?|[lL][uU]?)?(?![\w.])")


def _number(m: re.Match, in_if: bool) -> str:
    """An integer constant with its U suffix written as a cast.

    HI-TECH 3.09 knows the L suffix but not the U suffix (C89).  With a
    16-bit int, "Nu" is unsigned int when N fits in 16 bits, else
    unsigned long, so the cast gives the same type and value.  In #if
    and #elif lines a cast is not allowed, so the U goes away there
    (CPP computes #if in long).
    """
    digits, suf = m.group(1), (m.group(2) or "")
    if "u" not in suf.lower():
        return m.group(0)
    long_ = "l" in suf.lower()
    if in_if:
        return digits + ("L" if long_ else "")
    val = int(digits, 16) if digits[:2].lower() == "0x" else (
        int(digits, 8) if digits.startswith("0") else int(digits))
    if long_ or val > 0xFFFF:
        return f"((unsigned long){digits}L)"
    return f"((unsigned){digits})"


def modernize(text: str, directives: bool = True,
              in_if: bool = False) -> str:
    """Rewrite C89/C99 forms that the 1987 HI-TECH CPP does not know.

    None of these changes the meaning of the program:
      * a // comment becomes a /* */ comment;
      * a directive may have blanks before and after its # (CPP finds a
        directive only as "#word" in column 1);
      * "#elif e" becomes "#else" + "#if e", with one more #endif at the
        end of the group (CPP rejects #elif, even in a skipped group);
      * a #pragma or #warning line becomes an empty line (CPP rejects
        both, even in a skipped group; C ignores a pragma it does not
        know);
      * "#error text" becomes a line that P1 rejects, so it still stops
        the build in an active group and does nothing in a skipped one
        (CPP rejects #error, even in a skipped group);
      * an integer constant with a U suffix becomes a cast (see
        _number()).
    String and character literals and block comments are left alone.
    Each #elif adds one line, so __LINE__ after it is off by one.
    """
    out, i, n = [], 0, len(text)
    stack: list[int] = []            # extra #endif count per open #if
    line_start = True
    while i < n:
        if line_start and directives:
            line_start = False
            m = _DIRECTIVE.match(text, i)
            if m:
                end = _directive_end(text, i)
                kw = m.group(1)
                rest = modernize(text[m.end():end], directives=False,
                                 in_if=kw in ("if", "elif"))
                line = f"#{kw}{rest}"
                if kw in ("if", "ifdef", "ifndef"):
                    stack.append(0)
                elif kw == "elif" and stack:
                    line = f"#else\n#if{rest}"
                    stack[-1] += 1
                elif kw == "endif" and stack:
                    line += "\n#endif" * stack.pop()
                elif kw in ("pragma", "warning"):
                    line = "\n" * text.count("\n", i, end)
                elif kw == "error":
                    msg = rest.replace("*/", "* /").replace("\\\n", " ")
                    line = f"error directive reached!! /*{msg} */"
                out.append(line)
                i = end
                continue
        c = text[i]
        if c in "\"'":
            j = _skip_literal(text, i)
            out.append(text[i:j])
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(text[i:j])
            i = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            body = text[i + 2:j].rstrip("\r").replace("*/", "* /")
            out.append(f"/*{body} */")
            i = j
        elif c == "\\" and text.startswith("\n", i + 1):
            out.append("\\\n")               # spliced line: no line start
            i += 2
        elif c.isdigit() and not (i and (text[i - 1].isalnum()
                                         or text[i - 1] in "_.")):
            m = _NUMBER.match(text, i)
            j = m.end() if m else i + 1
            out.append(_number(m, in_if) if m else c)
            line_start = False
            i = j
        else:
            line_start = c == "\n"
            out.append(c)
            i += 1
    return "".join(out)


_ASM_OR_STRING = re.compile(
    r'(?P<asm>\basm\s*\(\s*"(?P<body>(?:[^"\\\n]|\\.)*)"\s*\)\s*;)'
    r'|"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'')


def fix_asm_statements(text: str) -> str:
    """Turn asm("text"); in CPP output into an #asm block (see above).

    String and character literals match as a whole, so an "asm(" inside
    a literal is left alone.
    """
    def repl(m: re.Match) -> str:
        if not m.group("asm"):
            return m.group(0)
        body = (m.group("body").replace('\\"', '"').replace("\\t", "\t")
                .replace("\\n", "\r\n").replace("\\\\", "\\"))
        return f"\r\n#asm\r\n{body}\r\n#endasm\r\n"
    return _ASM_OR_STRING.sub(repl, text)


_INCLUDE = re.compile(r'^([ \t]*#[ \t]*include[ \t]*)"([^"\n]*/[^"\n]*)"',
                      re.M)


def flatten_includes(text: str, here: Path, dirs: list[Path],
                     found: dict[str, Path]) -> str:
    """Point each #include "dir/file.h" at a flat 8.3 copy in the work dir.

    The private copies of all headers sit in one directory, so an
    include name with a directory part is searched as the including
    file's directory, then the include path, as a C compiler does; the
    directive then names the flat copy.  `found` collects the headers
    to copy (flat name -> source path).
    """
    def repl(m: re.Match) -> str:
        for d in [here] + dirs:
            h = Path(d) / m.group(2)
            if h.is_file():
                flat = name83(h.name)
                if found.setdefault(flat, h).resolve() != h.resolve():
                    return m.group(0)        # name clash: leave it
                return f'{m.group(1)}"{flat}"'
        return m.group(0)
    return _INCLUDE.sub(repl, text)


def uses_float(text: str) -> bool:
    code = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return bool(re.search(r"\b(float|double)\b|<math\.h>", code))


class HiTech(Compiler):
    """HI-TECH C 3.09 (CP/M native) under zxcc, -N (NRTCPM) start-up."""
    name = "hitech"
    modes = ("size", "speed")

    def available(self) -> bool:
        return _zxcc() is not None and all(
            (HT_ROOT / f).exists()
            for f in ("cpp.com", "p1.com", "cgen.com", "optim.com",
                      "zas.com", "linq.com", "nrtcpm.obj", "libc.lib"))

    def version(self) -> str:
        ver = "3.09"
        h = HT_ROOT / "stdio.h"
        if h.exists():
            m = re.search(r'_HTC_VERSION\s+"([^"]+)"',
                          h.read_text(errors="replace"))
            ver = m.group(1) if m else ver
        # zxcc has no --version; Homebrew keeps the version in the path.
        m = re.search(r"/zxcc/([\d.]+)/", str(Path(_zxcc()).resolve()))
        zx = f"zxcc {m.group(1)}" if m else "zxcc"
        return f"HI-TECH C {ver} (agn453/HI-TECH-Z80-C), {zx}"

    def _run(self, args: list[str], cwd: Path, log: list[str],
             timeout: int = 600) -> int:
        cmd = [_zxcc()] + args
        log.append("$ " + " ".join(cmd))
        try:
            p = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            log.append(f"timeout after {timeout}s")
            return 124
        out = (p.stdout + p.stderr).decode("latin-1").replace("\r", "")
        if out.strip():
            log.append(out.rstrip())
        return p.returncode

    def objects(self, sources, defines, includes, work, mode):
        """Per-module sizes from the link map of the full build.

        HI-TECH has no tool that prints the size of one .obj, but the
        map that LINQ writes lists, for each module, the length of its
        text, data and bss psects.  Code is the text length, data is
        data plus bss.  The map lists the user modules first and then
        the library modules (after the "libc.lib" line); only the user
        modules are read.  A build that stops before the link has no map, so
        such a program has no per-module sizes.
        """
        b = self.build(sources, defines, includes, work, mode)
        mp = work / "hitech" / "prog.map"
        # A link that stops (for example "Fixup overflow" when the
        # program is larger than 64 KB) still writes the map.
        if not mp.exists():
            return Objects(False, log=b.log)
        # Partial links (see build()) have the per-module lines.
        maps = sorted((work / "hitech").glob("p[0-9]*.map")) + [mp]
        want = [name83(Path(s).name).rsplit(".", 1)[0] for s in sources]
        found: dict[str, list[int]] = {}
        cur = None
        lines = []
        for m_ in maps:
            lines += m_.read_text(errors="replace").splitlines()
        for line in lines:
            if re.search(r"\.lib\s*$", line):
                break
            m = re.match(r"(?:\w:)?(\S+)\.obj\s", line)
            if m:
                cur = m.group(1)
                found[cur] = [0, 0]
            elif not (line[:1].isspace() and cur):
                cur = None
                continue
            if cur is None or cur == "nrtcpm":
                continue
            # A module may continue on the next lines (data, bss).
            for ps, _addr, ln in re.findall(
                    r"(text|data|bss)\s+([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)",
                    line):
                found[cur][0 if ps == "text" else 1] += int(ln, 16)
        if any(w not in found for w in want):
            return Objects(False, log=b.log + "\nno map line for a module")
        return Objects(True, [(Path(s).stem,) + tuple(found[w])
                              for s, w in zip(sources, want)], b.log)

    def build(self, sources, defines, includes, work, mode):
        ht = work / "hitech"
        if ht.exists():
            shutil.rmtree(ht)
        ht.mkdir(parents=True)
        log: list[str] = []
        tool = lambda f: str(HT_ROOT / f)          # noqa: E731
        fail = lambda: Build(False, log="\n".join(log),   # noqa: E731
                             command=command)
        opt = ["-O"] if mode == "size" else ["-OF"]
        command = " ".join(["c"] + opt + ["-N"]
                           + [f"-D{d}" for d in defines]
                           + [Path(s).name for s in sources])

        # Headers: the sources' own directory first (as for "file"
        # includes), then the include path in order.
        dirs: list[Path] = []
        for d in [Path(s).parent for s in sources] + list(includes):
            if Path(d).resolve() not in [x.resolve() for x in dirs]:
                dirs.append(Path(d))
        found: dict[str, Path] = {}
        todo = [h for d in dirs for h in sorted(Path(d).glob("*.h"))]
        while todo:
            h = todo.pop(0)
            dest = ht / name83(h.name)
            if dest.exists():
                continue
            text = h.read_text(encoding="latin-1")
            new: dict[str, Path] = {}
            text = flatten_includes(text, h.parent, dirs, new)
            todo += [p for f, p in new.items() if not (ht / f).exists()]
            dest.write_text(modernize(text), encoding="latin-1")

        # Defines on the command line if they fit, else in the sources.
        dargs = [f"--D{d}" for d in PREDEFINED + defines]
        prefix = ""
        if sum(len(a) for a in dargs) + 40 > TAIL_MAX:
            dargs = [f"--D{d}" for d in PREDEFINED]
            for d in defines:
                k, eq, v = d.partition("=")
                prefix += f"#define {k} {v if eq else '1'}\n"

        cnames, float_lib = [], False
        for s in sources:
            cn = name83(Path(s).name)
            if cn in cnames:
                log.append(f"two sources map to the 8.3 name {cn}")
                return fail()
            text = Path(s).read_text(encoding="latin-1")
            float_lib |= uses_float(text)
            new: dict[str, Path] = {}
            text = flatten_includes(text, Path(s).parent, dirs, new)
            for f, p in new.items():
                if not (ht / f).exists():
                    t = flatten_includes(p.read_text(encoding="latin-1"),
                                         p.parent, dirs, {})
                    (ht / f).write_text(modernize(t), encoding="latin-1")
            (ht / cn).write_text(prefix + modernize(text),
                                 encoding="latin-1")
            cnames.append(cn)

        objs = []
        for cn in cnames:
            stem = cn.rsplit(".", 1)[0]
            if self._run([tool("cpp.com")] + dargs
                         + ["--I", f"+{HT_ROOT}/", cn, "t1.tmp"],
                         ht, log) != 0 or not (ht / "t1.tmp").exists():
                return fail()
            pre = (ht / "t1.tmp").read_bytes().decode("latin-1")
            (ht / "t1.tmp").write_bytes(
                fix_asm_statements(pre.rstrip("\x1a")).encode("latin-1"))
            # As C.COM: -O runs OPTIM and ZAS -J; -OF runs OPTIM -F and
            # ZAS without -J.
            steps = [[tool("p1.com"), "t1.tmp", "t2.tmp", "t3.tmp"],
                     [tool("cgen.com"), "t2.tmp", "t1.tmp"],
                     [tool("optim.com")]
                     + (["--F"] if mode == "speed" else [])
                     + ["t1.tmp", "t2.tmp"],
                     [tool("zas.com")]
                     + (["--J"] if mode == "size" else [])
                     + ["--N", f"--O{stem}.obj", "t2.tmp"]]
            for st in steps:
                if self._run(st, ht, log) != 0:
                    return fail()
            # Keep the optimized assembler text for reading.
            shutil.copy(ht / "t2.tmp", ht / f"{stem}.as")
            if not (ht / f"{stem}.obj").exists():
                log.append(f"no {stem}.obj")
                return fail()
            objs.append(f"{stem}.obj")

        # The CP/M command tail holds 127 bytes, so a link with many
        # modules cannot name them all.  The start-up module and the
        # libraries are copied to the work directory (bare names), and
        # when the modules still do not fit, LINQ -R joins them in groups
        # into relocatable partial objects (pN.obj, with a map pN.map that
        # objects() reads for the per-module sizes), which the last link
        # names instead.
        for f in ("nrtcpm.obj", "libc.lib", "libf.lib"):
            if (HT_ROOT / f).exists() and not (ht / f).exists():
                shutil.copy(HT_ROOT / f, ht / f)
        libs = (["libf.lib"] if float_lib else []) + ["libc.lib"]
        head = ["--Z", "--Mprog.map", "--Ptext=0,data,bss", "--C100H",
                "--Oprog.com", "nrtcpm.obj"]
        items = list(objs)
        npart = 0
        while len(" ".join(head + items + libs)) > LINK_MAX:
            joined: list[str] = []
            while items:
                phead = ["--Z", f"--Mp{npart}.map", "--Ptext=0,data,bss",
                         "--R", f"--Op{npart}.obj"]
                chunk = [items.pop(0)]
                while items and len(" ".join(phead + chunk + [items[0]])) \
                        <= LINK_MAX:
                    chunk.append(items.pop(0))
                if len(chunk) == 1:
                    joined += chunk
                    continue
                if self._run([tool("linq.com")] + phead + chunk, ht, log) != 0:
                    return fail()
                joined.append(f"p{npart}.obj")
                npart += 1
            if len(joined) >= len(objs) or len(joined) == len(items):
                log.append("link line too long")
                return fail()
            items = joined
        if self._run([tool("linq.com")] + head + items + libs, ht, log) != 0:
            return fail()
        com, mp = ht / "prog.com", ht / "prog.map"
        maptext = mp.read_text(errors="replace") if mp.exists() else ""
        if not com.exists() or "undefined symbol" in maptext.lower() \
                or any("undefined symbol" in l.lower() for l in log):
            log.append("link failed")
            return fail()
        if float_lib:
            command += " -LF"
        syms: dict[str, int] = {}
        tab = maptext.split("Symbol Table", 1)
        if len(tab) == 2:
            for name, _psect, addr in re.findall(
                    r"(\S+)\s+(\S+)\s+([0-9A-Fa-f]{4})\b", tab[1]):
                syms[name.upper()] = int(addr, 16)
        # A C name X is _X in HI-TECH assembler, and the bench.h labels
        # need the _ too (OPTIM deletes unused labels without one), so
        # also list each _X as X when no X exists.
        for name, addr in list(syms.items()):
            if name.startswith("_"):
                syms.setdefault(name[1:], addr)
        b = Build(True, com, syms, "\n".join(log), command)
        if "__LBSS" in syms:
            b.image = syms["__LBSS"] - 0x100
            b.bss = syms.get("__HBSS", syms["__LBSS"]) - syms["__LBSS"]
        return b


COMPILERS = [HiTech()]
