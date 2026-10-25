"""Turn the text of an SDCC ``__asm ... __endasm;`` block into the
MACRO-80 text that uc80 puts in the output for um80.

The block is sdas (sdasz80) syntax.  The translation covers what C code
uses in practice:

    ld a,#5            immediate operand: the # goes away
    ld hl,#_name       a C symbol is the same word in both syntaxes
    ld a,(_name)       a memory operand
    ld a,2(ix)         the same as ld a,(ix+2)
    ld a,(ix+2)
    .db .byte .fcb     DB
    .dw .word .fdb     DW
    .ds .blkb .blkw    DS
    .ascii .asciz      DB with the characters
    .globl name        PUBLIC when the block defines the label, else EXTRN
    name = value       EQU (and .equ)
    1$: ... jr 1$      a local label; the scope ends at the next global label
    0x1F 0b101 <x >x   0FFH style numbers, LOW x and HIGH x

Not supported, and a loud error: .area, .org, .module, .include,
.macro, .if, .rept, .even and every other directive; and an operand without
# where sdas needs one.  An error names the line of the block.
"""

import re

from . import asmexpr as X
from .asmexpr import AsmSyntaxError


class InlineAsmError(Exception):
    """An error in a block.  ``line`` counts from 1 at the first line of
    the block."""

    def __init__(self, line, message):
        self.line, self.message = line, message
        super().__init__(message)


_AFX = "af\x01"
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
_NO_HASH = {"jp", "jr", "call", "djnz", "rst", "im", "bit", "set", "res",
            "ret", "in", "out"}

_LABEL = re.compile(r"\s*([A-Za-z_.$][A-Za-z0-9_.$]*|\d+\$)(::?)")
_IDX = re.compile(r"^(.*)\(\s*(ix|iy)\s*\)$", re.I | re.S)


def _split_comment(line):
    q = None
    for i, c in enumerate(line):
        if q:
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == ";":
            return line[:i], line[i:]
    return line, ""


def _split_args(text):
    out, cur, depth, q = [], [], 0, None
    for c in text:
        if q:
            cur.append(c)
            if c == q:
                q = None
        elif c in "'\"":
            q = c
            cur.append(c)
        elif c in "([":
            depth += 1
            cur.append(c)
        elif c in ")]":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    last = "".join(cur).strip()
    if last or out:
        out.append(last)
    return out


class _Block:
    def __init__(self, body, block_id):
        self.lines = body.split("\n")
        self.id = block_id
        self.scope = 0
        self.defined = set()
        self.globls = []
        self.out = []

    # ---- symbols ----------------------------------------------------
    def sym(self, name):
        if re.fullmatch(r"\d+\$", name):
            return f"@ASM{self.id}_{self.scope}_{name[:-1]}"
        if "$" in name or name.startswith("."):
            raise AsmSyntaxError(f"the symbol '{name}' has no MACRO-80 form")
        return name

    def expr(self, text):
        text = text.strip()
        if text.startswith("#"):
            text = text[1:]
        return X.to_m80(X.parse(text, "sdas"), self.sym)

    # ---- operands ---------------------------------------------------
    def operand(self, text, op):
        s = text.strip()
        low = s.lower()
        if low == "af'" or low == _AFX:
            return "AF'"
        if low in _REGS or low in _CONDS:
            return s.upper()
        m = _IDX.match(s)
        if m and not s.startswith("("):
            d = m.group(1).strip() or "0"
            return self.index(m.group(2).lower(), d)
        if s.startswith("("):
            depth = 0
            for i, c in enumerate(s):
                depth += (c == "(") - (c == ")")
                if depth == 0:
                    break
            if i == len(s) - 1:
                inner = s[1:-1].strip()
                if inner.startswith("#"):
                    inner = inner[1:].strip()
                il = inner.lower()
                if il in _INDIRECT:
                    return f"({il.upper()})"
                if il in ("ix", "iy"):
                    return (f"({il.upper()})" if op == "jp"
                            else f"({il.upper()}+0)")
                e = X.parse(inner, "sdas")
                if (isinstance(e, X.Bin) and e.op in ("+", "-")
                        and isinstance(e.left, X.Sym)
                        and e.left.name.lower() in ("ix", "iy")):
                    d = e.right if e.op == "+" else X.Un("neg", e.right)
                    return self.index(e.left.name.lower(),
                                      X.to_sdas(d))
                return "(" + X.to_m80(e, self.sym) + ")"
        if s.startswith("#"):
            return self.imm(s[1:])
        if op in _NO_HASH:
            return self.imm(s)
        raise AsmSyntaxError(
            f"the operand '{s}' needs a # in sdas syntax: write '#{s}' "
            f"for a value, or '({s})' for a memory address")

    def index(self, reg, d):
        v = X.fold(X.parse(d, "sdas"))
        if v is not None:
            return f"({reg.upper()}{'+' if v >= 0 else '-'}{abs(v)})"
        return f"({reg.upper()}+{X.to_m80(X.parse(d, 'sdas'), self.sym)})"

    def imm(self, text):
        t = X.to_m80(X.parse(text.strip(), "sdas"), self.sym)
        # M80 reads an operand that starts with ( as a memory address
        return "0+" + t if t.startswith("(") else t

    # ---- statements -------------------------------------------------
    def run(self):
        for n, line in enumerate(self.lines, 1):
            try:
                self.statement(line)
            except AsmSyntaxError as e:
                raise InlineAsmError(n, str(e)) from None
        # .globl: PUBLIC for a label of the block, else EXTRN
        for n, names in reversed(self.globls):
            lines = []
            for name in names:
                kw = "PUBLIC" if name in self.defined else "EXTRN"
                lines.append(f"\t{kw}\t{name}")
            self.out[n:n] = lines
        return self.out

    def statement(self, line):
        line = re.sub(r"(?i)\baf'", _AFX, line)
        code, comment = _split_comment(line)
        rest = code
        while True:
            m = _LABEL.match(rest)
            if not m:
                break
            name = m.group(1)
            if not name.endswith("$"):
                self.scope += 1
            sym = self.sym(name)
            self.defined.add(sym)
            self.out.append(f"{sym}:")
            rest = rest[m.end():]
        rest = rest.strip()
        if not rest:
            if comment:
                self.out.append("\t" + comment)
            return
        m = re.match(r"([A-Za-z_.][A-Za-z0-9_.]*)\s*(.*)$", rest, re.S)
        eq = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", rest, re.S)
        if eq:
            self.out.append(f"{eq.group(1)}\tEQU\t{self.expr(eq.group(2))}")
            return
        if not m:
            raise AsmSyntaxError(f"cannot read '{rest}'")
        op, operands = m.group(1).lower(), m.group(2).strip()
        if op.startswith("."):
            self.directive(op, operands)
        elif op in _MNEMONICS:
            ops = _split_args(operands) if operands else []
            text = "\t" + op.upper()
            if ops:
                text += "\t" + ",".join(self.operand(o, op) for o in ops)
            self.out.append(text)
        else:
            raise AsmSyntaxError(f"unknown instruction '{op}'")
        if comment:
            self.out[-1] += "\t" + comment

    def directive(self, op, operands):
        args = _split_args(operands) if operands else []
        if op in (".db", ".byte", ".fcb"):
            for a in args:
                if a[:1] in "\"" :
                    raise AsmSyntaxError(
                        ".db with a string: write .ascii \"text\"")
            self.out.append("\tDB\t" + ",".join(self.expr(a) for a in args))
        elif op in (".dw", ".word", ".fdb"):
            self.out.append("\tDW\t" + ",".join(self.expr(a) for a in args))
        elif op in (".ds", ".blkb", ".blkw"):
            if len(args) != 1:
                raise AsmSyntaxError(f"{op} needs one size")
            v = X.fold(X.parse(args[0].lstrip("#"), "sdas"))
            if v is None or v < 0:
                raise AsmSyntaxError(f"{op} needs a constant size")
            self.out.append(f"\tDS\t{v * (2 if op == '.blkw' else 1)}")
        elif op in (".ascii", ".asciz"):
            m = re.fullmatch(r'\s*"(.*)"\s*', operands, re.S)
            if not m:
                raise AsmSyntaxError(f'{op} needs "text"')
            data = m.group(1).encode("latin-1", "replace")
            if op == ".asciz":
                data += b"\0"
            if data:
                self.out.append("\tDB\t" + _db_items(data))
        elif op in (".globl", ".global"):
            self.globls.append((len(self.out), [self.sym(a) for a in args]))
        elif op == ".equ":
            if len(args) != 2:
                raise AsmSyntaxError(".equ needs a name and a value")
            self.out.append(f"{args[0]}\tEQU\t{self.expr(args[1])}")
        else:
            raise AsmSyntaxError(
                f"the directive {op} is not supported inside __asm")


def _db_items(data: bytes) -> str:
    items, run = [], []

    def flush():
        if run:
            items.append("'" + "".join(run).replace("'", "''") + "'")
            run.clear()
    for c in data:
        if 32 <= c < 127:
            run.append(chr(c))
        else:
            flush()
            items.append(str(c))
    flush()
    return ",".join(items)


def sdas_block_to_m80(body: str, block_id: int = 0) -> list:
    """The MACRO-80 lines of the sdas text ``body``.  Raises
    InlineAsmError."""
    return _Block(body, block_id).run()
