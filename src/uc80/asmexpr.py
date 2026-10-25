"""Assembler expressions in two dialects, for the sdas translators.

The module parses an operand expression of MACRO-80 (the syntax of um80)
or of sdas (the syntax of sdasz80) into a small tree, folds constants, and
prints the tree in the other dialect.  uc80.sdas uses the parser to turn
the compiler output into sdas text.  uc80.sdas_inline uses the parser to
turn an SDCC ``__asm`` block into MACRO-80 text.

Every failure raises AsmSyntaxError.  The translators do not guess.
"""

import re
from dataclasses import dataclass


class AsmSyntaxError(Exception):
    """An expression or statement that the translator does not handle."""


@dataclass(frozen=True)
class Num:
    value: int
    base: int = 10          # 10, 16, 2 or 8: the base of the written number


@dataclass(frozen=True)
class Chr:
    value: int              # 'A'


@dataclass(frozen=True)
class Sym:
    name: str


@dataclass(frozen=True)
class Loc:
    """The location counter: $ in MACRO-80, . in sdas."""


@dataclass(frozen=True)
class Un:
    op: str                 # neg, pos, not, hi, lo
    arg: object


@dataclass(frozen=True)
class Bin:
    op: str                 # + - * / mod shl shr and or xor, and the
    left: object            # relations eq ne lt le gt ge
    right: object


# ---- tokens ------------------------------------------------------------

_M80_WORDS = {"MOD": "mod", "SHL": "shl", "SHR": "shr", "AND": "and",
              "OR": "or", "XOR": "xor", "NOT": "not", "HIGH": "hi",
              "LOW": "lo", "EQ": "eq", "NE": "ne", "LT": "lt", "LE": "le",
              "GT": "gt", "GE": "ge"}

_SYM_M80 = r"[A-Za-z_@?.$][A-Za-z0-9_@?.$]*"
_SYM_SDAS = r"[A-Za-z_.$][A-Za-z0-9_.$]*|\d+\$"

_TOK_M80 = re.compile(
    r"\s*(?:(?P<num>\d[0-9A-Za-z]*)"
    r"|(?P<str>'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\")"
    r"|(?P<sym>" + _SYM_M80 + r")"
    r"|(?P<op>[-+*/()<>=,]))")

_TOK_SDAS = re.compile(
    r"\s*(?:(?P<num>0[xX][0-9A-Fa-f]+|0[bB][01]+|0[oOqQ][0-7]+|\d+(?![\d$]))"
    r"|(?P<chr>'(?:[^'\\]|\\.)')"
    r"|(?P<sym>" + _SYM_SDAS + r")"
    r"|(?P<op><<|>>|[-+*/%&|^~()<>\[\]]))")


def parse_m80_number(text: str):
    """(value, base) of a MACRO-80 number such as 0FFH, 101B, 17Q, 255."""
    s = text.lower()
    try:
        if s.endswith("h") and re.fullmatch(r"[0-9a-f]+h", s):
            return int(s[:-1], 16), 16
        if s.endswith("b") and re.fullmatch(r"[01]+b", s):
            return int(s[:-1], 2), 2
        if s[-1:] in ("o", "q") and re.fullmatch(r"[0-7]+[oq]", s):
            return int(s[:-1], 8), 8
        if s.endswith("d") and re.fullmatch(r"[0-9]+d", s):
            return int(s[:-1], 10), 10
        if s.isdigit():
            return int(s, 10), 10
    except ValueError:
        pass
    raise AsmSyntaxError(f"bad number '{text}'")


def _tokens(text: str, dialect: str):
    rx = _TOK_M80 if dialect == "m80" else _TOK_SDAS
    pos, out = 0, []
    text = text.rstrip()
    while pos < len(text):
        m = rx.match(text, pos)
        if not m:
            raise AsmSyntaxError(f"cannot read '{text[pos:].strip()}'")
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "sym" and dialect == "m80":
            w = _M80_WORDS.get(val.upper())
            if w:
                out.append(("w", w))
                continue
            if val == "$":
                out.append(("loc", None))
                continue
        if kind == "sym" and dialect == "sdas" and val == ".":
            out.append(("loc", None))
            continue
        out.append((kind, val))
    return out


# precedence tables: higher binds tighter
_PREC_M80 = {"or": 1, "xor": 1, "and": 2,
             "eq": 4, "ne": 4, "lt": 4, "le": 4, "gt": 4, "ge": 4,
             "+": 5, "-": 5, "*": 6, "/": 6, "mod": 6, "shl": 6, "shr": 6}
_PREC_SDAS = {"|": 1, "^": 2, "&": 3, "<<": 4, ">>": 4, "+": 5, "-": 5,
              "*": 6, "/": 6, "%": 6}
_SDAS_OPS = {"|": "or", "^": "xor", "&": "and", "<<": "shl", ">>": "shr",
             "+": "+", "-": "-", "*": "*", "/": "/", "%": "mod"}
_M80_SYMOPS = {"+": "+", "-": "-", "*": "*", "/": "/"}


class _Parser:
    def __init__(self, text, dialect):
        self.dialect = dialect
        self.toks = _tokens(text, dialect)
        self.i = 0
        self.src = text

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def binop(self):
        """(canonical op, precedence) of the next token, or None."""
        kind, val = self.peek()
        if self.dialect == "m80":
            if kind == "op" and val in _M80_SYMOPS:
                op = _M80_SYMOPS[val]
                return op, _PREC_M80[op]
            if kind == "w" and val in _PREC_M80:
                return val, _PREC_M80[val]
            return None
        if kind == "op" and val in _PREC_SDAS:
            return _SDAS_OPS[val], _PREC_SDAS[val]
        return None

    def expr(self, minprec=0):
        left = self.unary()
        while True:
            b = self.binop()
            if b is None or b[1] < minprec:
                return left
            self.take()
            right = self.expr(b[1] + 1)
            left = Bin(b[0], left, right)

    def unary(self):
        kind, val = self.take()
        if kind is None:
            raise AsmSyntaxError(f"expression ends too soon: '{self.src}'")
        if kind == "num":
            if self.dialect == "m80":
                v, base = parse_m80_number(val)
            else:
                low = val.lower()
                if low.startswith("0x"):
                    v, base = int(low[2:], 16), 16
                elif low.startswith("0b"):
                    v, base = int(low[2:], 2), 2
                elif low[:2] in ("0o", "0q"):
                    v, base = int(low[2:], 8), 8
                else:
                    v, base = int(low, 10), 10
            return Num(v, base)
        if kind == "str":
            body = val[1:-1].replace(val[0] * 2, val[0])
            if len(body) != 1:
                raise AsmSyntaxError(
                    f"a string of {len(body)} characters in an expression: "
                    f"{val}")
            return Chr(ord(body))
        if kind == "chr":
            body = val[1:-1]
            if body.startswith("\\"):
                body = {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
                        "\\": "\\", "'": "'"}.get(body[1:], body[1:])
            return Chr(ord(body))
        if kind == "loc":
            return Loc()
        if kind == "sym":
            return Sym(val)
        if kind == "w":
            if val in ("hi", "lo", "not"):
                return Un(val, self.unary() if val != "not"
                          else self.expr(3))
            raise AsmSyntaxError(f"'{val}' where a value is expected")
        if kind == "op":
            if val == "-":
                return Un("neg", self.unary())
            if val == "+":
                return self.unary()
            if val == "~":
                return Un("not", self.unary())
            if val == "<" and self.dialect == "sdas":
                return Un("lo", self.unary())
            if val == ">" and self.dialect == "sdas":
                return Un("hi", self.unary())
            if val in ("(", "["):
                e = self.expr()
                k2, v2 = self.take()
                if v2 != (")" if val == "(" else "]"):
                    raise AsmSyntaxError(f"missing ')' in '{self.src}'")
                return e
        raise AsmSyntaxError(f"unexpected '{val}' in '{self.src}'")


def parse(text: str, dialect: str):
    """Parse one expression.  dialect is "m80" or "sdas"."""
    p = _Parser(text, dialect)
    e = p.expr()
    if p.i != len(p.toks):
        raise AsmSyntaxError(f"cannot read '{text}' after the expression")
    return e


# ---- folding -----------------------------------------------------------

def fold(e, symbols=None):
    """The integer value of ``e``, or None when ``e`` has a symbol that is
    not in ``symbols`` (a dict name -> int) or the location counter."""
    if isinstance(e, Num):
        return e.value
    if isinstance(e, Chr):
        return e.value
    if isinstance(e, Sym):
        return (symbols or {}).get(e.name)
    if isinstance(e, Loc):
        return None
    if isinstance(e, Un):
        a = fold(e.arg, symbols)
        if a is None:
            return None
        return {"neg": -a, "not": ~a & 0xFFFF, "hi": (a >> 8) & 0xFF,
                "lo": a & 0xFF, "pos": a}[e.op]
    a, b = fold(e.left, symbols), fold(e.right, symbols)
    if a is None or b is None:
        return None
    op = e.op
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op in ("/", "mod"):
        if b == 0:
            raise AsmSyntaxError("division by zero")
        q = abs(a) // abs(b) * (1 if (a < 0) == (b < 0) else -1)
        return q if op == "/" else a - q * b
    if op == "shl":
        return a << b
    if op == "shr":
        return (a & 0xFFFF) >> b
    if op == "and":
        return a & b
    if op == "or":
        return a | b
    if op == "xor":
        return a ^ b
    rel = {"eq": a == b, "ne": a != b, "lt": a < b, "le": a <= b,
           "gt": a > b, "ge": a >= b}
    return 0xFFFF if rel[op] else 0


def symbols_of(e):
    """The symbol names in ``e``."""
    if isinstance(e, Sym):
        return {e.name}
    if isinstance(e, Un):
        return symbols_of(e.arg)
    if isinstance(e, Bin):
        return symbols_of(e.left) | symbols_of(e.right)
    return set()


# ---- printing ----------------------------------------------------------

_SDAS_SYMOP = {"+": "+", "-": "-", "*": "*", "/": "/", "mod": "%",
               "shl": "<<", "shr": ">>", "and": "&", "or": "|", "xor": "^"}
_M80_SYMOP = {"+": "+", "-": "-", "*": "*", "/": "/", "mod": " MOD ",
              "shl": " SHL ", "shr": " SHR ", "and": " AND ", "or": " OR ",
              "xor": " XOR "}
_SAFE_CHR = re.compile(r"[A-Za-z0-9 !#%&()*+./:<=>?@\[\]^_`{|}~-]")


def _num_sdas(n: Num) -> str:
    v = n.value
    if v < 0:
        return "-" + _num_sdas(Num(-v, n.base))
    if n.base == 16:
        return "0x%X" % v
    return str(v)


def _num_m80(n: Num) -> str:
    v = n.value
    if v < 0:
        return "-" + _num_m80(Num(-v, n.base))
    if n.base == 16:
        h = "%X" % v
        return ("0" + h if h[0] in "ABCDEF" else h) + "H"
    return str(v)


def to_sdas(e, name=lambda s: s):
    """The sdas text of ``e``.  ``name`` maps a symbol to its sdas name."""
    if isinstance(e, Num):
        return _num_sdas(e)
    if isinstance(e, Chr):
        c = chr(e.value)
        return "'%s'" % c if _SAFE_CHR.fullmatch(c) else str(e.value)
    if isinstance(e, Sym):
        return name(e.name)
    if isinstance(e, Loc):
        return "."
    if isinstance(e, Un):
        a = e.arg
        inner = to_sdas(a, name)
        if isinstance(a, Bin):
            inner = "(" + inner + ")"
        if e.op == "neg":
            return "-" + inner
        if e.op == "not":
            return "~" + inner
        if e.op == "hi":
            return ">" + inner
        if e.op == "lo":
            return "<" + inner
        return inner
    if e.op not in _SDAS_SYMOP:
        raise AsmSyntaxError(
            f"the operator {e.op.upper()} has no sdas form for a value "
            f"that is not a constant")

    def side(x, right):
        s = to_sdas(x, name)
        if isinstance(x, Bin):
            same_left = (not right and _PREC_SDAS.get(_SDAS_SYMOP[x.op], 0)
                         == _PREC_SDAS[_SDAS_SYMOP[e.op]])
            if not same_left:
                return "(" + s + ")"
        return s
    return side(e.left, False) + _SDAS_SYMOP[e.op] + side(e.right, True)


def to_m80(e, name=lambda s: s):
    """The MACRO-80 text of ``e``."""
    if isinstance(e, Num):
        return _num_m80(e)
    if isinstance(e, Chr):
        c = chr(e.value)
        return "'%s'" % c if _SAFE_CHR.fullmatch(c) else str(e.value)
    if isinstance(e, Sym):
        return name(e.name)
    if isinstance(e, Loc):
        return "$"
    if isinstance(e, Un):
        a = e.arg
        inner = to_m80(a, name)
        if isinstance(a, Bin):
            inner = "(" + inner + ")"
        if e.op == "neg":
            return "-" + inner
        if e.op == "not":
            return "NOT " + inner
        if e.op == "hi":
            return "HIGH " + inner
        if e.op == "lo":
            return "LOW " + inner
        return inner
    if e.op not in _M80_SYMOP:
        raise AsmSyntaxError(f"the operator {e.op} has no MACRO-80 form")

    def side(x, right):
        s = to_m80(x, name)
        if isinstance(x, Bin):
            plain = e.op in ("+", "-") and x.op in ("+", "-")
            if not (plain and not right):
                return "(" + s + ")"
        return s
    return side(e.left, False) + _M80_SYMOP[e.op] + side(e.right, True)
