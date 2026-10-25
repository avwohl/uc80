"""Translate the MACRO-80 text of uc80 (and of its libraries) to sdas syntax.

``uc80 --asm-syntax=sdas`` writes a .s file for sdasz80 and links with
sdldz80.  See docs/rom_interop.md.

    translate(text)              MACRO-80 text -> sdas text
    link_flags(style)            the sdldz80 flags the output needs
    build_library(dir, style)    translate and assemble runtime and libc

The translator is a line-by-line pass.  It does not guess: a statement or
an expression it does not know raises SdasError with the line number.

Areas.  The role of an M80 segment decides the sdas area:

    CSEG                      code      _CODE        CODE
    DSEG, the image           rom_init  _INITIALIZER INITIALIZER
    DSEG, the RAM bytes       ram_init  _INITIALIZED INITIALIZED
    COMMON (any name)         bss       _DATA        DATA
    ASEG, ORG in CSEG         absolute  _ABS (ABS)   START (ABS)

The first column is the "sdcc" style (the SDCC crt0 names).  The second
is the "ngdevkit" style (the names that nullsound uses for CODE and DATA).

A DSEG variable has its initial bytes in the rom_init area and its
label, with the same size, in the ram_init area.  The start-up code copies
the first area to the second.  The two areas have the same layout,
because each module adds the same number of bytes to both.
"""

import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import asmexpr as X
from .asmexpr import AsmSyntaxError


class SdasError(Exception):
    """A statement that the translator cannot translate."""

    def __init__(self, line, message, source=""):
        self.line, self.message, self.source = line, message, source
        where = f"line {line}" if line else "input"
        super().__init__(f"{where}: {message}"
                         + (f"\n    {source.strip()}" if source else ""))


@dataclass(frozen=True)
class AreaNames:
    code: str
    rom_init: str
    ram_init: str
    bss: str
    absolute: str


STYLES = {
    "sdcc": AreaNames("_CODE", "_INITIALIZER", "_INITIALIZED", "_DATA",
                      "_ABS"),
    "ngdevkit": AreaNames("CODE", "INITIALIZER", "INITIALIZED", "DATA",
                          "START"),
}


def area_names(style="sdcc", overrides: str = "") -> AreaNames:
    """The AreaNames of a style name, with ``role=NAME,role=NAME``
    overrides (roles: code, rom_init, ram_init, bss, absolute)."""
    if isinstance(style, AreaNames):
        base = style
    elif style in STYLES:
        base = STYLES[style]
    else:
        raise ValueError(f"unknown sdas style '{style}' "
                         f"(choose {', '.join(sorted(STYLES))})")
    vals = dict(base.__dict__)
    for item in filter(None, (overrides or "").split(",")):
        role, _, name = item.partition("=")
        if role.strip() not in vals or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.$]*", name.strip()):
            raise ValueError(f"bad area override '{item}': write "
                             f"role=NAME, role is one of {', '.join(vals)}")
        vals[role.strip()] = name.strip()
    return AreaNames(**vals)


def link_flags(style="sdcc", code_origin=0x68, ram_origin=0xF800,
               ram_end=0, overrides: str = "", target="rom") -> list:
    """The sdldz80 flags for a program that uc80 built.  The linker defines
    the symbols that the uc80 start-up code needs.  Add ``-i out.ihx`` and
    the modules.

    target "rom" (--target=rom): code_origin is the address after the
    vectors (0068h); ram_origin is the first RAM address; ram_end is the
    first value of SP, 0 for 10000h.  target "cpm" (--target=cpm):
    code_origin is 0100h; the data follows the code in the image.  A
    program that links malloc also needs ``-g __END__=s_BSS+l_BSS`` (the
    start of the heap; BSS is the bss area name).  sdldz80 refuses a
    ``-g`` for a symbol that no module has used, so the flags carry only
    the symbols that every program uses.
    """
    a = area_names(style, overrides)
    if target == "cpm":
        return ["-n", "-b", f"{a.code}=0x{code_origin:X}",
                "-g", f"__bss_start=s_{a.bss}",
                "-g", f"__bss_end=s_{a.bss}+l_{a.bss}"]
    return ["-n", "-b", f"{a.code}=0x{code_origin:X}",
            "-b", f"{a.ram_init}=0x{ram_origin:X}",
            "-g", f"__RAM_END=0x{ram_end:X}",
            "-g", f"__DATA_ROM=s_{a.rom_init}",
            "-g", f"__DATA_RAM=s_{a.ram_init}",
            "-g", f"__DATA_LEN=l_{a.ram_init}",
            "-g", f"__BSS_START=s_{a.bss}",
            "-g", f"__BSS_END=s_{a.bss}+l_{a.bss}"]


# ---- the instruction set ------------------------------------------------

_AFX = "af\x01"          # af' while a line is read: the quote is no string
_REGS = {"a", "b", "c", "d", "e", "h", "l", "i", "r", "af", _AFX, "bc",
         "de", "hl", "sp", "ix", "iy", "ixh", "ixl", "iyh", "iyl"}
_CONDS = {"nz", "z", "nc", "po", "pe", "p", "m"}
_INDIRECT = {"hl", "bc", "de", "sp", "c"}
_MNEMONICS = set("""
adc add and bit call ccf cp cpd cpdr cpi cpir cpl daa dec di djnz ei ex exx
halt im in inc ind indr ini inir jp jr ld ldd lddr ldi ldir neg nop or
otdr otir out outd outi pop push res ret reti retn rl rla rlc rlca rld rr
rra rrc rrca rrd rst sbc scf set sla sll sra srl sub xor
""".split())
# The operand of these is not an immediate value: no # in sdas.
_NO_HASH = {"jp", "jr", "call", "djnz", "rst", "im", "bit", "set", "res",
            "ret", "in", "out"}

_IGNORED = {".Z80", "TITLE", "SUBTTL", "PAGE", ".LIST", ".XLIST", ".LALL",
            ".SALL", ".SFCOND", ".LFCOND", ".PRINTX"}
_DIRECTIVES = (_IGNORED | {
    "CSEG", "DSEG", "ASEG", "COMMON", "ORG", "PUBLIC", "ENTRY", "GLOBAL",
    "EXTRN", "EXT", "EXTERNAL", "EQU", "DEFL", "SET", "DB", "DEFB", "DEFM",
    "FCB", "DW", "DEFW", "FDB", "DS", "DEFS", "RMEM", "END", "NAME",
    ".PHASE", ".DEPHASE", "IF", "IFE", "IF1", "IF2", "IFDEF", "IFNDEF",
    "IFB", "IFNB", "IFIDN", "IFDIF", "ELSE", "ENDIF", "MACRO", "ENDM",
    "REPT", "IRP", "IRPC", "EXITM", "LOCAL", "INCLUDE", "$INCLUDE",
    "COMMENT", ".COMMENT", ".REQUEST", "ASSERT", ".8080", ".RADIX"})

_LABEL = re.compile(r"\s*([A-Za-z_@?.$][A-Za-z0-9_@?.$]*)(::?)")
_WORD = re.compile(r"\s*([A-Za-z_@?.$][A-Za-z0-9_@?.$]*)")


def _split_comment(line: str):
    """(code, comment) of a line; comment keeps its ';'."""
    i, n, q = 0, len(line), None
    while i < n:
        c = line[i]
        if q:
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == ";":
            return line[:i], line[i:]
        i += 1
    return line, ""


def _split_args(text: str):
    """Split on the commas that are outside quotes and parentheses."""
    out, cur, depth, q = [], [], 0, None
    for c in text:
        if q:
            cur.append(c)
            if c == q:
                q = None
        elif c in "'\"":
            q = c
            cur.append(c)
        elif c == "(":
            depth += 1
            cur.append(c)
        elif c == ")":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if q:
        raise AsmSyntaxError("a string has no closing quote")
    last = "".join(cur).strip()
    if last or out:
        out.append(last)
    return out


def _string_item(item: str):
    """The bytes of an item that is one quoted string, else None."""
    if len(item) >= 2 and item[0] in "'\"" and item[-1] == item[0]:
        body = item[1:-1]
        if item[0] not in body.replace(item[0] * 2, ""):
            return body.replace(item[0] * 2, item[0]).encode("latin-1")
    return None


@dataclass
class Rec:
    """One output line.  size is the number of bytes that the line adds
    to its area.  jr is (mnemonic, condition, label) for a JR or DJNZ,
    which the relaxation pass can change to JP."""
    text: str
    size: int = 0
    label: str = ""
    area: str = ""          # for a ".area" line: the name
    org: int = -1           # for a ".org" line: the address
    jr: tuple = ()
    addr: int = 0
    where: str = ""         # the area of the line, set by relax()


_R8 = set("abcdehl")
_R16 = {"bc", "de", "hl", "sp", "af"}
_RIXY = {"ix", "iy"}
_RIXY8 = {"ixh", "ixl", "iyh", "iyl"}
_SIZE1 = {"nop", "halt", "di", "ei", "exx", "ccf", "scf", "cpl", "daa",
          "rlca", "rrca", "rla", "rra", "ret", "rst"}
_SIZE2 = {"neg", "retn", "reti", "ldi", "ldd", "ldir", "lddr", "cpi", "cpd",
          "cpir", "cpdr", "ini", "ind", "inir", "indr", "outi", "outd",
          "otir", "otdr", "rld", "rrd", "im"}
_ALU = {"add", "adc", "sub", "sbc", "and", "or", "xor", "cp"}
_SHIFT = {"rl", "rr", "rlc", "rrc", "sla", "sra", "srl", "sll"}


def insn_size(op, cs):
    """The size in bytes of a Z80 instruction.  ``cs`` are the classified
    operands.  Raises AsmSyntaxError for a form that the table lacks."""
    def kind(c):
        return c[0]

    def reg(c):
        return c[1] if c[0] == "reg" else None

    n = len(cs)
    if op in _SIZE1 and (n == 0 or op in ("ret", "rst")):
        return 1
    if op in _SIZE2:
        return 2
    if op in ("jr", "djnz"):
        return 2
    if op == "call":
        return 3
    if op == "jp":
        if n == 1 and cs[0][0] == "ind":
            return 1
        if n == 1 and cs[0][0] == "idx":
            return 2
        return 3
    if op in ("push", "pop"):
        return 2 if reg(cs[0]) in _RIXY else 1
    if op in ("inc", "dec"):
        c = cs[0]
        if kind(c) == "idx":
            return 3
        if reg(c) in _RIXY | _RIXY8:
            return 2
        return 1
    if op == "ex":
        if kind(cs[0]) == "ind" and reg(cs[1]) in _RIXY:
            return 2
        return 1
    if op in _SHIFT:
        return 4 if kind(cs[-1]) == "idx" else 2
    if op in ("bit", "set", "res"):
        return 4 if kind(cs[1]) == "idx" else 2
    if op in ("in", "out"):
        return 2
    if op in _ALU:
        if n == 2:
            d = reg(cs[0])
            if d == "hl":
                return 2 if op in ("adc", "sbc") else 1
            if d in _RIXY:
                return 2
            cs = cs[1:]
        c = cs[0]
        if kind(c) == "idx":
            return 3
        if kind(c) == "imm":
            return 2
        if reg(c) in _RIXY8:
            return 2
        return 1
    if op == "ld":
        d, s = cs
        dr, sr = reg(d), reg(s)
        if dr in _R8:
            if sr in _R8:
                return 1
            if sr in ("i", "r"):
                return 2
            if kind(s) == "ind":
                return 1
            if kind(s) == "idx":
                return 3
            if kind(s) == "mem":
                return 3
            if kind(s) == "imm":
                return 2
            if sr in _RIXY8:
                return 2
        if dr in ("i", "r"):
            return 2
        if dr in _RIXY8:
            return 3 if kind(s) == "imm" else 2
        if kind(d) == "ind":
            if d[1] == "hl":
                return 2 if kind(s) == "imm" else 1
            return 1
        if kind(d) == "idx":
            return 4 if kind(s) == "imm" else 3
        if kind(d) == "mem":
            if sr in ("a", "hl"):
                return 3
            return 4
        if dr in _R16 | _RIXY:
            if dr == "sp" and sr in ("hl",):
                return 1
            if dr == "sp" and sr in _RIXY:
                return 2
            if kind(s) == "imm":
                return 4 if dr in _RIXY else 3
            if kind(s) == "mem":
                return 3 if dr == "hl" else 4
        raise AsmSyntaxError("cannot find the size of this LD")
    raise AsmSyntaxError(f"cannot find the size of '{op}'")


def relax(recs):
    """Assign addresses; change each JR and DJNZ whose target is too far
    to JP (um80 does the same: JR to JP, DJNZ to DEC B and JP NZ).
    Returns the number of lines changed."""
    changed = 0
    while True:
        pos, labels, area = {}, {}, ""
        for r in recs:
            if r.area:
                area = r.area
                pos.setdefault(area, 0)
            elif r.org >= 0:
                pos[area] = r.org
            r.where = area
            r.addr = pos.get(area, 0)
            if r.label:
                labels[r.label] = (area, r.addr)
            pos[area] = pos.get(area, 0) + r.size
        grew = False
        for r in recs:
            if not r.jr or r.size != 2:
                continue
            op, cond, target = r.jr
            t = labels.get(target)
            if t is None or t[0] != r.where:
                continue
            if not -128 <= t[1] - (r.addr + 2) <= 127:
                if op == "djnz":
                    r.text = f"\tdec\tb\n\tjp\tnz,{target}"
                    r.size = 4
                else:
                    r.text = f"\tjp\t{cond + ',' if cond else ''}{target}"
                    r.size = 3
                r.jr = ()
                grew = True
                changed += 1
        if not grew:
            return changed


class _Translator:
    def __init__(self, text, style, module, filename, comments, flat=False):
        self.flat = flat
        self.lines = text.splitlines()
        self.areas = area_names(style) if not isinstance(
            style, AreaNames) else style
        self.module = module
        self.filename = filename
        self.comments = comments
        self.out = []               # Rec
        self.mode = None            # code, init, bss, abs
        self.rom, self.ram = [], []
        self.consts = {}            # name -> int, from EQU
        self.canon = {}             # NAME.upper() -> first spelling
        self.lineno = 0
        self.cur = ""
        self.code_origin = None

    # ---- output -----------------------------------------------------
    def emit(self, text, size=0, **kw):
        r = Rec(text, size, **kw)
        (self.ram if self.mode == "init" else self.out).append(r)
        return r

    def emit_anywhere(self, text):
        self.out.append(Rec(text))

    def error(self, msg):
        raise SdasError(self.lineno, msg, self.cur)

    def area_line(self, name, suffix=""):
        self.out.append(Rec(f"\t.area\t{name}{suffix}", area=name))

    def enter(self, mode):
        if mode == self.mode and mode != "abs":
            return
        self.flush_init()
        a = self.areas
        if mode == "code":
            self.area_line(a.code)
        elif mode == "bss":
            self.area_line(a.bss)
        elif mode == "data":
            self.area_line(a.ram_init)
        elif mode == "abs" and self.mode != "abs":
            self.area_line(a.absolute, " (ABS)")
        self.mode = mode

    def flush_init(self):
        if self.mode != "init":
            return
        a = self.areas
        self.area_line(a.rom_init)
        self.out.extend(self.rom)
        self.area_line(a.ram_init)
        self.out.extend(self.ram)
        self.rom, self.ram = [], []
        self.mode = None

    # ---- names ------------------------------------------------------
    def name(self, sym):
        key = sym.upper()
        sym = self.canon.setdefault(key, sym)
        out = []
        for i, ch in enumerate(sym):
            if ch.isalnum() or ch in "_.$":
                out.append(ch)
            elif ch == "@":
                out.append("L$" if i == 0 else "$a")
            elif ch == "?":
                out.append("Q$")
            else:
                raise AsmSyntaxError(f"the character '{ch}' in the symbol "
                                     f"'{sym}' has no sdas form")
        s = "".join(out)
        return "_" + s if s[0].isdigit() else s

    def expr(self, text):
        return X.to_sdas(X.parse(text, "m80"), self.name)

    # ---- the pass ---------------------------------------------------
    def run(self):
        mod = self.module
        for ln in self.lines:
            m = re.match(r"\s*NAME\s*\(?\s*'?([A-Za-z0-9_]+)'?\s*\)?\s*(;.*)?$",
                         ln, re.I)
            if m:
                mod = m.group(1)
                break
        a = self.areas
        self.out.append(Rec(f"; sdas text made by uc80 from {self.filename}"
                            if self.comments else ""))
        mod = re.sub(r"[^A-Za-z0-9_]", "_", mod) or "uc80"
        self.out.append(Rec(f"\t.module\t{mod}"))
        for name in (a.code, a.rom_init, a.ram_init, a.bss):
            self.area_line(name)
        self.mode = None
        self.enter("code")
        for i, line in enumerate(self.lines, 1):
            self.lineno, self.cur = i, line
            try:
                self.statement(line)
            except AsmSyntaxError as e:
                raise SdasError(i, str(e), line) from None
        self.flush_init()
        self.lineno, self.cur = 0, ""
        relax(self.out)
        if self.code_origin is not None:
            self.out[0].text = (
                f"; uc80-sdas: code-origin 0x{self.code_origin:X} "
                f"(link with -b {a.code}=0x{self.code_origin:X})")
        return "\n".join(r.text for r in self.out) + "\n"

    def statement(self, line):
        line = re.sub(r"(?i)\baf'", _AFX, line)
        code, comment = _split_comment(line)
        if comment.startswith(";@RELOC") and self.mode == "abs":
            self.enter("code")
            if comment[7:].strip():
                self.code_origin = X.fold(X.parse(comment[7:].strip(),
                                                  "m80"))
        if not code.strip():
            if comment and self.comments:
                self.emit(comment if not line[:1].isspace()
                          else "\t" + comment)
            return
        label, colons, rest = None, "", code
        m = _LABEL.match(code)
        if m:
            label, colons, rest = m.group(1), m.group(2), code[m.end():]
        elif not line[:1].isspace():
            m2 = _WORD.match(code)
            up = m2.group(1).upper() if m2 else ""
            if m2 and up not in _DIRECTIVES and up.lower() not in _MNEMONICS:
                label, rest = m2.group(1), code[m2.end():]
        op, operands = "", ""
        rest = rest.strip()
        if rest:
            m3 = re.match(r"(=|[A-Za-z_.$][A-Za-z0-9_.$]*)\s*(.*)$", rest,
                          re.S)
            if not m3:
                self.error(f"cannot read '{rest}'")
            op, operands = m3.group(1), m3.group(2).strip()
        upop = op.upper()
        # "SET n,r" is an instruction.  "name SET value" is the directive.
        if upop in ("EQU", "=", "DEFL") or (upop == "SET" and label):
            if label is None:
                self.error(f"{upop} without a name")
            self.equate(label, operands)
            return
        if label is not None:
            n = self.name(label)
            self.emit(f"{n}{colons or ':'}", label=n)
        before = len(self.out)
        if op:
            self.dispatch(upop, op, operands)
        if comment and self.comments and self.mode != "init" \
                and len(self.out) > before:
            self.out[-1].text += "\t" + comment

    def equate(self, label, operands):
        e = X.parse(operands, "m80")
        v = X.fold(e, self.consts)
        if v is not None:
            self.consts[label.upper()] = v
            self.consts[label] = v
        self.emit_anywhere(f"\t.equ\t{self.name(label)}, "
                           f"{X.to_sdas(e, self.name)}")

    def dispatch(self, up, op, operands):
        if up in _IGNORED or up in ("END", "NAME"):
            return
        if up == "CSEG":
            self.enter("code")
        elif up == "DSEG":
            self.enter("data" if self.flat else "init")
        elif up == "COMMON":
            self.enter("bss")
        elif up == "ASEG":
            self.enter("abs")
            self.emit("\t.org\t0", org=0)
        elif up == "ORG":
            self.org(operands)
        elif up in ("PUBLIC", "ENTRY", "GLOBAL", "EXTRN", "EXT", "EXTERNAL"):
            for n in _split_args(operands):
                if n:
                    self.emit_anywhere(f"\t.globl\t{self.name(n)}")
        elif up in ("DB", "DEFB", "DEFM", "FCB"):
            self.data_bytes(operands)
        elif up in ("DW", "DEFW", "FDB"):
            self.data_words(operands)
        elif up in ("DS", "DEFS", "RMEM"):
            self.data_space(operands)
        elif up in _DIRECTIVES and op.lower() not in _MNEMONICS:
            self.error(f"the directive {up} is not supported by the sdas "
                       f"translator")
        elif op.lower() in _MNEMONICS:
            self.instruction(op.lower(), operands)
        else:
            self.error(f"unknown statement '{op}'")

    def org(self, operands):
        v = X.fold(X.parse(operands, "m80"), self.consts)
        if v is None:
            self.error("ORG needs a constant address")
        if self.mode not in ("code", "abs"):
            self.error("ORG is supported in CSEG and ASEG only")
        self.enter("abs")
        self.emit(f"\t.org\t0x{v:X}", org=v)

    # ---- data -------------------------------------------------------
    def data_bytes(self, operands):
        pieces = []                 # ("s", bytes) or ("e", text)
        n = 0
        for item in _split_args(operands):
            s = _string_item(item)
            if s is not None:
                pieces.append(("s", s))
                n += len(s)
            else:
                pieces.append(("e", self.expr(item)))
                n += 1
        if self.mode == "bss":
            self.error("initial data in COMMON: use DSEG, or DS")
        lines, run = [], []

        def flush():
            if run:
                lines.append("\t.db\t" + ", ".join(run))
                run.clear()
        for kind, v in pieces:
            if kind == "e":
                run.append(v)
                continue
            safe = all(32 <= c < 127 and c not in (0x22, 0x5C) for c in v)
            if v and safe and len(v) > 1:
                flush()
                lines.append('\t.ascii\t"%s"' % v.decode("ascii"))
            else:
                run.extend(str(c) if c < 10 else "0x%02X" % c for c in v)
        flush()
        self.put_data(lines, n)

    def data_words(self, operands):
        items = _split_args(operands)
        if self.mode == "bss":
            self.error("initial data in COMMON: use DSEG, or DS")
        vals = []
        for it in items:
            if _string_item(it) is not None and len(_string_item(it)) != 1:
                self.error("DW with a string of more than one character")
            vals.append(self.expr(it))
        self.put_data(["\t.dw\t" + ", ".join(vals)] if vals else [],
                      2 * len(vals))

    def data_space(self, operands):
        args = _split_args(operands)
        if len(args) != 1:
            self.error("DS with a fill value is not supported")
        v = X.fold(X.parse(args[0], "m80"), self.consts)
        if v is None or v < 0:
            self.error("DS needs a constant size")
        if self.mode == "init":
            if v:
                self.rom.append(Rec(f"\t.ds\t{v}", v))
                self.ram.append(Rec(f"\t.ds\t{v}", v))
        else:
            self.emit(f"\t.ds\t{v}", v)

    def put_data(self, lines, size):
        """Put the data lines (together ``size`` bytes).  In DSEG the
        bytes go to the image area and the RAM area gets the space."""
        if self.mode == "init":
            for i, ln in enumerate(lines):
                self.rom.append(Rec(ln, size if i == 0 else 0))
            if size:
                self.ram.append(Rec(f"\t.ds\t{size}", size))
        else:
            for i, ln in enumerate(lines):
                self.emit(ln, size if i == 0 else 0)

    # ---- instructions -----------------------------------------------
    def classify(self, text):
        s = text.strip()
        low = s.lower()
        if low in _REGS or low in _CONDS:
            return ("reg", low)
        if s.startswith("("):
            depth = 0
            for i, c in enumerate(s):
                depth += (c == "(") - (c == ")")
                if depth == 0:
                    break
            if i == len(s) - 1:
                inner = s[1:-1].strip()
                il = inner.lower()
                if il in _INDIRECT:
                    return ("ind", il)
                if il in ("ix", "iy"):
                    return ("idx", il, None)
                e = X.parse(inner, "m80")
                if (isinstance(e, X.Bin) and e.op in ("+", "-")
                        and isinstance(e.left, X.Sym)
                        and e.left.name.lower() in ("ix", "iy")):
                    d = e.right if e.op == "+" else X.Un("neg", e.right)
                    return ("idx", e.left.name.lower(), d)
                return ("mem", e)
        return ("imm", X.parse(s, "m80"))

    def fmt(self, c, op):
        kind = c[0]
        if kind == "reg":
            return c[1].replace(_AFX, "af'")
        if kind == "ind":
            return f"({c[1]})"
        if kind == "idx":
            d = c[2]
            if d is None:
                return f"({c[1]})"
            v = X.fold(d, self.consts)
            if v is not None:
                return f"({c[1]}{'+' if v >= 0 else '-'}{abs(v)})"
            return f"({c[1]}+{X.to_sdas(d, self.name)})"
        if kind == "mem":
            return "(" + X.to_sdas(c[1], self.name) + ")"
        txt = X.to_sdas(c[1], self.name)
        return txt if op in _NO_HASH else "#" + txt

    def instruction(self, op, operands):
        ops = _split_args(operands) if operands else []
        cs = [self.classify(o) for o in ops]
        text = "\t" + op
        if cs:
            text += "\t" + ",".join(self.fmt(c, op) for c in cs)
        size = insn_size(op, [(c[0], c[1].replace(_AFX, "af'")
                               if isinstance(c[1], str) else c[1])
                              + tuple(c[2:]) for c in cs])
        jr = ()
        if op in ("jr", "djnz"):
            tgt = cs[-1]
            if tgt[0] == "imm" and isinstance(tgt[1], X.Sym):
                cond = cs[0][1] if len(cs) == 2 else ""
                jr = (op, cond, self.name(tgt[1].name))
        self.emit(text, size, jr=jr)


def translate(text: str, *, style="sdcc", module="uc80",
              filename="uc80 output", comments=True, flat_data=False) -> str:
    """MACRO-80 text -> sdas text.  Raises SdasError.

    flat_data=False: the initial bytes of DSEG go to the rom_init area and
    the RAM area gets the space (a ROM; the start-up code copies).
    flat_data=True: DSEG is one area with its bytes in the image (a CP/M
    .com file, which is loaded as it is)."""
    return _Translator(text, style, module, filename, comments,
                       flat_data).run()


def area_sizes(text: str, *, style="sdcc", flat_data=False) -> dict:
    """The size in bytes that the translator computes for each area of
    ``text`` (MACRO-80).  sdasz80 reports the same figures: the test
    suite compares the two, to check the table of instruction sizes that
    the JR range check uses."""
    t = _Translator(text, style, "m", "m", False, flat_data)
    t.run()
    sizes = {}
    for r in t.out:
        if r.where:
            sizes[r.where] = max(sizes.get(r.where, 0), r.addr + r.size)
    return sizes


def code_origin_of(sdas_text: str):
    """The address where the relocatable code of a ROM module must start
    (the first byte after the vectors), or None."""
    m = re.match(r"; uc80-sdas: code-origin 0x([0-9A-F]+)", sdas_text)
    return int(m.group(1), 16) if m else None


def build_help(name, style, overrides, origin, rom) -> str:  # noqa: C901
    """The commands that assemble and link a module, as a text."""
    stem = name.rsplit(".", 1)[0]
    lines = ["  Assemble and link (adjust the origins for your hardware):",
             f"    sdasz80 -g -l -o {name}"]
    if rom:
        flags = " ".join(link_flags(style, origin or 0x68,
                                    overrides=overrides))
        lines.append(f"    sdldz80 {flags} -i -j -m {stem}.ihx {stem}.rel "
                     f"-l uc80_lc.lib -l uc80_rt.lib")
        lines.append(f"    sdobjcopy -I ihex -O binary {stem}.ihx {stem}.bin")
        lines.append("  Build uc80_lc.lib and uc80_rt.lib once with "
                     "uc80 --build-sdas-libs DIR")
    else:
        flags = " ".join(link_flags(style, 0x100, overrides=overrides,
                                    target="cpm"))
        lines.append(f"    sdldz80 {flags} -i {stem}.ihx {stem}.rel "
                     "-l uc80_lc.lib -l uc80_rt.lib")
        lines.append(f"    sdobjcopy -I ihex -O binary {stem}.ihx {stem}.com")
    return "\n".join(lines)


# ---- the tools ----------------------------------------------------------

def tools_available() -> bool:
    return all(shutil.which(t) for t in ("sdasz80", "sdldz80", "sdar"))


def assemble(path: Path, flags=("-g", "-l")) -> tuple:
    """Run sdasz80 on ``path``.  Returns (ok, output)."""
    r = subprocess.run(["sdasz80", *flags, "-o", str(path)],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stdout + r.stderr


def build_library(out_dir, style="sdcc", lib_dir=None, kinds=("rt", "lc"),
                  jobs=8, flat_data=False) -> dict:
    """Translate the library modules in ``lib_dir``/<kind>/*.mac to
    ``out_dir``/<kind>/*.s, assemble them and pack each kind into
    ``out_dir``/uc80_<kind>.lib.  Returns {"libs": [...], "failed":
    [(module, message), ...]}.

    uc80_rt.lib has the runtime (the helpers for 16-bit and 32-bit
    arithmetic, float, long long, setjmp).  uc80_lc.lib has libc.  The
    libc modules that use the CP/M BDOS are in the library too.  A ROM
    program does not reference them, because uc80 --target=rom refuses
    the calls (docs/rom.md).  Link with ``-l out_dir/uc80_lc.lib``: the
    linker takes only the modules that the program needs.

    flat_data=False builds the libraries for a ROM (the initial data is
    in the image area and the start-up code copies it).  flat_data=True
    builds them for CP/M, where the data is in the loaded image.
    """
    from . import lib_dir as _lib_dir
    lib = Path(lib_dir) if lib_dir else _lib_dir()
    out = Path(out_dir)
    failed, libs = [], []
    for kind in kinds:
        src = lib / kind
        d = out / kind
        d.mkdir(parents=True, exist_ok=True)
        mods = sorted(src.glob("*.mac"))

        def one(mac):
            s = d / (mac.stem + ".s")
            try:
                s.write_text(translate(mac.read_text(errors="replace"),
                                       style=style, module=mac.stem,
                                       filename=f"{kind}/{mac.name}",
                                       comments=False,
                                       flat_data=flat_data))
            except SdasError as e:
                return mac.stem, str(e)
            ok, msg = assemble(s, flags=("-g",))
            return (mac.stem, None if ok else msg.strip())
        with ThreadPoolExecutor(jobs) as pool:
            for name, err in pool.map(one, mods):
                if err:
                    failed.append((f"{kind}/{name}", err))
        rels = sorted(str(p) for p in d.glob("*.rel"))
        lib_path = out / f"uc80_{kind}.lib"
        lib_path.unlink(missing_ok=True)
        r = subprocess.run(["sdar", "-rcs", str(lib_path), *rels],
                           capture_output=True, text=True)
        if r.returncode != 0:
            failed.append((f"uc80_{kind}.lib", r.stdout + r.stderr))
        else:
            libs.append(str(lib_path))
    return {"libs": libs, "failed": failed}


def main(argv=None) -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        description="Translate a MACRO-80 file to sdas syntax, or build "
                    "the uc80 libraries for sdldz80.")
    ap.add_argument("input", nargs="?", help="a .mac file")
    ap.add_argument("-o", "--output")
    ap.add_argument("--style", default="sdcc", choices=sorted(STYLES))
    ap.add_argument("--areas", default="", metavar="ROLE=NAME,...")
    ap.add_argument("--build-libs", metavar="DIR")
    ap.add_argument("--cpm", action="store_true",
                    help="flat data, for a CP/M program (the default is "
                         "a ROM: the image holds the initial data)")
    args = ap.parse_args(argv)
    style = area_names(args.style, args.areas)
    if args.build_libs:
        res = build_library(args.build_libs, style, flat_data=args.cpm)
        for lib in res["libs"]:
            print("built", lib)
        for name, msg in res["failed"]:
            print(f"FAILED {name}: {msg}", file=sys.stderr)
        return 1 if res["failed"] else 0
    if not args.input:
        ap.error("give a .mac file or --build-libs DIR")
    p = Path(args.input)
    try:
        text = translate(p.read_text(errors="replace"), style=style,
                         module=p.stem, filename=p.name,
                         flat_data=args.cpm)
    except SdasError as e:
        print(f"{p}: {e}", file=sys.stderr)
        return 1
    Path(args.output or p.with_suffix(".s")).write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
