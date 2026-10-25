"""SDCC source spellings that uc80 accepts, and the uc80 spellings that
share their code path.  See docs/rom.md ("SDCC spellings").

A prepass on the preprocessed text (before the parser) rewrites:

    void f(void) __interrupt { }              interrupt function, EI first
    void f(void) __interrupt(n) { }           the same (n is not used)
    void f(void) __critical __interrupt { }   non-maskable function (RETN)
    void f(void) __critical __interrupt(n) { } maskable, EI before RETI
    __sfr __at(0x10) PORT;                    8-bit port, IN and OUT
    __sfr __banked __at(0x1234) PORT;         16-bit port (BC), IN and OUT
    T name __at(0xF800);                      absolute symbol, no storage
    [[uc80::at(0xF800)]] T name;              the same, C23 attribute
    __critical { ... }                        interrupts off for the block

and records the result.  The text is changed in place, and every newline
stays, so the line numbers of later messages stay right.  Every other
SDCC spelling stays an error: the parser (or the strict mode of the uc_core
frontend, for the interrupt words) refuses it.  The prepass never erases
a spelling it does not handle.
"""

import re
from dataclasses import dataclass, field

from uc_core import ast
from uc_core._const import int_value


class SdccCompatError(Exception):
    """A spelling that the prepass recognises but cannot honour."""

    def __init__(self, filename, line, message):
        self.filename, self.line, self.message = filename, line, message
        super().__init__(f"{filename}:{line}: {message}")


@dataclass
class Found:
    """What the prepass saw, over all the files of one compile."""
    isrs: list = field(default_factory=list)      # (kind, name, file, line)
    sfrs: dict = field(default_factory=dict)      # name -> (port, banked)
    absolute: dict = field(default_factory=dict)  # name -> address


_TOKEN = re.compile(
    r"(?P<ws>\s+)"
    r"|(?P<str>\"(?:[^\"\\\n]|\\.)*\")"
    r"|(?P<chr>'(?:[^'\\\n]|\\.)*')"
    r"|(?P<cmt>//[^\n]*|/\*.*?\*/)"
    r"|(?P<id>[A-Za-z_]\w*)"
    r"|(?P<num>\d[\w.]*)"
    r"|(?P<attr>\[\[|\]\])"
    r"|(?P<p>.)", re.S)

_LEFTOVER = {
    "__naked": "'__naked' is not supported; write the function in "
               "assembly, or use #pragma uc80 interrupt",
    "__critical": "'__critical' is supported only as '__critical { ... }' "
                  "or in front of '__interrupt'",
    "__banked": "'__banked' is supported only in '__sfr __banked "
                "__at(port) NAME;'",
    "__sdcccall": "'__sdcccall' is not supported: uc80 has its own "
                  "calling convention",
    "__preserves_regs": "'__preserves_regs' is not supported",
    "__using": "'__using' is not supported (the Z80 has one bank of "
               "registers in C code)",
    "__sfr": "'__sfr' is supported only as '__sfr __at(port) NAME;' at "
             "file scope",
    "__at": "'__at' is supported only on a file-scope object: "
            "'T name __at(address);'",
}


def _parse_int(text, filename, line, what):
    t = text.strip().rstrip("uUlL")
    try:
        if t.lower().startswith("0x"):
            return int(t, 16)
        if t.lower().startswith("0b"):
            return int(t, 2)
        if len(t) > 1 and t.startswith("0"):
            return int(t, 8)
        return int(t, 10)
    except ValueError:
        raise SdccCompatError(
            filename, line,
            f"{what}: the address must be an integer constant, "
            f"not '{text.strip()}'") from None


def prepass(source: str, filename: str, found: Found) -> str:
    """Rewrite ``source`` and add what it declares to ``found``."""
    if not re.search(r"__interrupt|__critical|__sfr|__at\b|uc80::at|"
                     r"__naked|__banked|__sdcccall|__preserves_regs|"
                     r"__using", source):
        return source
    toks = []          # (kind, text, start, end)
    for m in _TOKEN.finditer(source):
        toks.append((m.lastgroup, m.group(), m.start(), m.end()))
    # significant tokens: index into toks
    sig = [i for i, t in enumerate(toks) if t[0] not in ("ws", "cmt")]
    edits = []         # (start, end, replacement)  never overlapping

    def line_of(pos):
        return source.count("\n", 0, pos) + 1

    def text(k):
        return toks[sig[k]][1]

    def blank(start, end):
        """Replace source[start:end] by spaces, keeping the newlines."""
        edits.append((start, end, re.sub(r"[^\n]", " ", source[start:end])))

    n = len(sig)
    depth = 0
    decl_start = 0          # index into sig of the first token of the decl
    k = 0
    consumed = set()
    while k < n:
        kind, tx, st, en = toks[sig[k]]
        if k in consumed:
            k += 1
            continue
        if tx == "{":
            depth += 1
            decl_start = k + 1
        elif tx == "}":
            depth = max(0, depth - 1)
            decl_start = k + 1
        elif tx == ";":
            decl_start = k + 1
        # ---- __critical { ... }
        elif kind == "id" and tx == "__critical":
            nxt = text(k + 1) if k + 1 < n else ""
            prev = text(k - 1) if k > 0 else ""
            # after ')' or '__interrupt' it is a function qualifier
            if nxt == "{" and prev not in (")", "__interrupt"):
                close = _match_brace(toks, sig, k + 1)
                if close is None:
                    raise SdccCompatError(filename, line_of(st),
                                          "'__critical {' has no closing '}'")
                _check_critical_body(toks, sig, k + 1, close, filename,
                                     line_of)
                edits.append((st, en, "{ __builtin_z80_crit_enter();"))
                cst, cen = toks[sig[close]][2], toks[sig[close]][3]
                edits.append((cen, cen, " __builtin_z80_crit_leave(); }"))
                # the inner tokens are still scanned: a nested block works
        k += 1

    # Function qualifiers: a separate scan, so the order of the two words
    # and the optional (n) are handled in one place.
    k = 0
    while k < n:
        tx = text(k)
        if tx == ")":
            j = k + 1
            crit = False
            seen = False
            start_tok = j
            while j < n and text(j) in ("__critical", "__interrupt"):
                if text(j) == "__critical":
                    if crit:
                        break
                    crit = True
                else:
                    if seen:
                        break
                    seen = True
                    j += 1
                    if j < n and text(j) == "(":
                        # __interrupt(n): the number is not used
                        e = j
                        while e < n and text(e) != ")":
                            e += 1
                        if e >= n:
                            break
                        num = " ".join(text(x) for x in range(j + 1, e))
                        if not re.fullmatch(r"(0[xX][0-9a-fA-F]+|\d+)", num.strip() or "x"):
                            raise SdccCompatError(
                                filename, line_of(toks[sig[j]][2]),
                                "'__interrupt(n)': n must be an integer "
                                "constant")
                        j = e + 1
                    continue
                j += 1
            if seen and j < n and text(j) in ("{", ";"):
                # the declarator's name: the identifier before the '('
                # that matches this ')'
                op = _match_paren_back(toks, sig, k)
                name = None
                if op is not None and op > 0 and toks[sig[op - 1]][0] == "id":
                    name = text(op - 1)
                if name is None:
                    raise SdccCompatError(
                        filename, line_of(toks[sig[start_tok]][2]),
                        "'__interrupt' must follow a function declarator "
                        "'void name(void)'")
                explicit_n = any(text(x) == "(" for x in range(start_tok, j))
                if crit and not explicit_n:
                    kind_ = "nmi"
                elif crit:
                    kind_ = "int_crit"
                else:
                    kind_ = "int"
                line = line_of(toks[sig[start_tok]][2])
                if text(j) == "{":
                    found.isrs.append((kind_, name, filename, line))
                blank(toks[sig[start_tok]][2], toks[sig[j - 1]][3])
                k = j
                continue
        k += 1

    # ---- __sfr and __at declarations (file scope only)
    depth = 0
    k = 0
    decl_start = 0
    while k < n:
        tx = text(k)
        if tx == "{":
            depth += 1
            decl_start = k + 1
        elif tx == "}":
            depth = max(0, depth - 1)
            decl_start = k + 1
        elif tx == ";":
            decl_start = k + 1
        elif depth == 0 and tx == "__sfr":
            end = k
            while end < n and text(end) != ";":
                end += 1
            if end >= n:
                raise SdccCompatError(filename, line_of(toks[sig[k]][2]),
                                      "'__sfr' declaration has no ';'")
            words = [text(x) for x in range(k, end)]
            banked = "__banked" in words
            body = [w for w in words if w not in ("__sfr", "__banked")]
            # __at ( num ) NAME
            if (len(body) == 5 and body[0] == "__at" and body[1] == "("
                    and body[3] == ")" and re.match(r"[A-Za-z_]\w*$", body[4])):
                line = line_of(toks[sig[k]][2])
                port = _parse_int(body[2], filename, line, "__sfr __at")
                limit = 0x10000 if banked else 0x100
                if not 0 <= port < limit:
                    raise SdccCompatError(
                        filename, line,
                        f"__sfr port {port:#x} does not fit "
                        f"{'16' if banked else '8'} bits"
                        + ("" if banked else "; use '__sfr __banked' for "
                           "a 16-bit port"))
                found.sfrs[body[4]] = (port, banked)
                blank(toks[sig[k]][2], toks[sig[end]][3])
            else:
                raise SdccCompatError(
                    filename, line_of(toks[sig[k]][2]),
                    "expected '__sfr __at(port) NAME;' or "
                    "'__sfr __banked __at(port) NAME;'")
            k = end
            decl_start = end + 1
        elif depth == 0 and (tx == "__at" or (
                tx == "[[" and k + 4 < n and text(k + 1) == "uc80"
                and text(k + 2) == ":" and text(k + 3) == ":"
                and text(k + 4) == "at")):
            attr = tx == "[["
            # find the end of the declaration
            end = k
            while end < n and text(end) not in (";", "{"):
                end += 1
            if end >= n or text(end) == "{":
                raise SdccCompatError(
                    filename, line_of(toks[sig[k]][2]),
                    "'__at' is allowed on an object declaration, not on "
                    "a function")
            # parse the at-clause
            j = k + (5 if attr else 1)
            if j >= n or text(j) != "(":
                raise SdccCompatError(filename, line_of(toks[sig[k]][2]),
                                      "expected '(address)' after '__at'")
            e = j
            while e < n and text(e) != ")":
                e += 1
            line = line_of(toks[sig[k]][2])
            addr = _parse_int(" ".join(text(x) for x in range(j + 1, e)),
                              filename, line, "__at")
            if not 0 <= addr < 0x10000:
                raise SdccCompatError(filename, line,
                                      f"__at address {addr:#x} is not a "
                                      f"16-bit address")
            clause_end = e + 1
            if attr:
                if clause_end >= n or text(clause_end) != "]]":
                    raise SdccCompatError(filename, line,
                                          "expected ']]' after [[uc80::at(...)")
                clause_end += 1
            # the declared name: the last identifier of the declaration
            # before '[', '=' or ';', outside the at-clause
            words = [(x, text(x)) for x in range(decl_start, end)
                     if not (k <= x < clause_end)]
            name = None
            for x, w in words:
                if w in ("=", "["):
                    break
                if toks[sig[x]][0] == "id" and w not in _SPECIFIERS:
                    name = w
            if name is None or any(w == "=" for _x, w in words):
                raise SdccCompatError(
                    filename, line,
                    "an '__at' object has no storage and no initial value: "
                    "write 'T name __at(address);'")
            found.absolute[name] = addr
            blank(toks[sig[k]][2], toks[sig[clause_end - 1]][3])
            # the object is hardware or shared with an interrupt handler:
            # every access must happen as written
            first = toks[sig[decl_start]][2]
            edits.append((first, first, "volatile "))
            k = end
            decl_start = end + 1
            continue
        k += 1

    out = source
    for st, en, rep in sorted(edits, key=lambda x: (x[0], x[1]),
                              reverse=True):
        out = out[:st] + rep + out[en:]
    _reject_leftovers(out, filename)
    return out


_SPECIFIERS = frozenset((
    "extern", "static", "const", "volatile", "unsigned", "signed", "char",
    "short", "int", "long", "float", "double", "void", "_Bool", "register",
    "struct", "union", "enum", "restrict", "auto", "_Atomic", "bool"))


def _match_brace(toks, sig, k):
    depth = 0
    for x in range(k, len(sig)):
        t = toks[sig[x]][1]
        if t == "{":
            depth += 1
        elif t == "}":
            depth -= 1
            if depth == 0:
                return x
    return None


def _match_paren_back(toks, sig, k):
    depth = 0
    for x in range(k, -1, -1):
        t = toks[sig[x]][1]
        if t == ")":
            depth += 1
        elif t == "(":
            depth -= 1
            if depth == 0:
                return x
    return None


_LOOPS = {"for", "while", "do", "switch"}


def _check_critical_body(toks, sig, open_k, close_k, filename, line_of):
    """A jump out of the block would skip the code that restores the
    interrupt state."""
    words = [toks[sig[x]][1] for x in range(open_k + 1, close_k)]
    has_loop = any(w in _LOOPS for w in words)
    for x in range(open_k + 1, close_k):
        w = toks[sig[x]][1]
        bad = w in ("return", "goto") or (
            w in ("break", "continue") and not has_loop)
        if bad:
            raise SdccCompatError(
                filename, line_of(toks[sig[x]][2]),
                f"'{w}' inside '__critical {{ }}' would leave the block "
                f"with interrupts off; move the '{w}' out of the block")


def _reject_leftovers(text, filename):
    # An __interrupt that is left is refused by the strict mode of the
    # frontend, with its own message; do not hide it behind another one.
    skip = ({"__critical"} if re.search(r"\b__interrupt\b", text)
            else set())
    for m in _TOKEN.finditer(text):
        if (m.lastgroup == "id" and m.group() in _LEFTOVER
                and m.group() not in skip):
            line = text.count("\n", 0, m.start()) + 1
            raise SdccCompatError(filename, line, _LEFTOVER[m.group()])


# ---- __sfr: reads and writes become IN and OUT --------------------------

def _tok(name):
    from uc_core.c23_parser import Token
    return Token(name="IDENT", text=name, line=0, column=0, offset=0,
                 file_id=0)


def _int_lit(v):
    from uc_core._const import make_int_lit
    return make_int_lit(v)


def rewrite_sfr(unit, sfrs: dict, filename="") -> None:
    """In place: each use of an __sfr name in ``unit`` becomes a call of
    __builtin_z80_in or __builtin_z80_out (the 16 forms for a banked
    port).  ``P |= x`` becomes IN, OR, OUT.  The address of a port, and a
    port name used as a function argument by address, are errors."""
    if not sfrs:
        return

    def call(fn, *args, pos=None):
        c = ast.Call(func=ast.Identifier(name=_tok(fn)), args=list(args))
        if pos is not None:
            c.pos = pos
        return c

    def fnname(port_banked, what):
        return f"__builtin_z80_{what}16" if port_banked else \
            f"__builtin_z80_{what}"

    def is_sfr(node):
        return (isinstance(node, ast.Identifier)
                and node.name.text in sfrs)

    def read(node):
        port, banked = sfrs[node.name.text]
        return call(fnname(banked, "in"), _int_lit(port), pos=node.pos)

    def opstr(op):
        return op.text if hasattr(op, "text") else op

    def cast_u8(expr):
        from uc_core import ast as a
        return expr

    def visit(node):
        """Return the replacement for ``node`` (possibly node itself)."""
        if isinstance(node, list):
            return [visit(x) for x in node]
        if isinstance(node, tuple):
            return tuple(visit(x) for x in node)
        if not hasattr(node, "__dataclass_fields__"):
            return node
        if isinstance(node, ast.BinaryOp) and is_sfr(node.left):
            op = opstr(node.op)
            if op in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                      "<<=", ">>="):
                port, banked = sfrs[node.left.name.text]
                rhs = visit(node.right)
                if op != "=":
                    rhs = ast.BinaryOp(
                        left=read(node.left),
                        op=_tok_op(op[:-1]), right=rhs, pos=node.pos)
                return call(fnname(banked, "out"), _int_lit(port), rhs,
                            pos=node.pos)
        if isinstance(node, ast.UnaryOp) and is_sfr(node.operand):
            op = opstr(node.op)
            port, banked = sfrs[node.operand.name.text]
            if op == "&":
                raise SdccCompatError(
                    filename, getattr(node.pos, "start_line", 0),
                    f"cannot take the address of the port "
                    f"'{node.operand.name.text}'")
            if op in ("++", "--"):
                new = ast.BinaryOp(left=read(node.operand),
                                   op=_tok_op(op[0]), right=_int_lit(1),
                                   pos=node.pos)
                return call(fnname(banked, "out"), _int_lit(port), new,
                            pos=node.pos)
        if isinstance(node, ast.PostfixOp) and is_sfr(node.operand):
            op = opstr(node.op)
            if op in ("++", "--"):
                port, banked = sfrs[node.operand.name.text]
                new = ast.BinaryOp(left=read(node.operand),
                                   op=_tok_op(op[0]), right=_int_lit(1),
                                   pos=node.pos)
                out = call(fnname(banked, "out"), _int_lit(port), new,
                           pos=node.pos)
                inv = "-" if op == "++" else "+"
                return ast.BinaryOp(left=out, op=_tok_op(inv),
                                    right=_int_lit(1), pos=node.pos)
        if is_sfr(node):
            return read(node)
        for f in node.__dataclass_fields__:
            if f == "pos":
                continue
            v = getattr(node, f)
            if isinstance(v, (list, tuple)) or hasattr(
                    v, "__dataclass_fields__"):
                nv = visit(v)
                if nv is not v:
                    setattr(node, f, nv)
        return node

    visit(unit)


def _tok_op(text):
    from uc_core.c23_parser import Token
    return Token(name=text, text=text, line=0, column=0, offset=0, file_id=0)
