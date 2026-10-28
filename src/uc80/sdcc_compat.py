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
    __asm ... __endasm;                       inline assembly (sdas syntax),
                                              statement or file level
    void f(void) __naked { __asm ... }        naked function: no frame code,
    [[uc80::naked]] void f(void) { ... }      the body is assembly only

and records the result.  The text is changed in place, and every newline
stays, so the line numbers of later messages stay right.  Every other
SDCC spelling stays an error: the parser (or the strict mode of the uc_core
frontend, for the interrupt words) refuses it.  The prepass never erases
a spelling it does not handle.
"""

import re
from dataclasses import dataclass, field

from uc_core import ast

from . import sdas_inline
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
    asm_blocks: int = 0                           # __asm blocks, all files
    naked: list = field(default_factory=list)     # (name, file, line)
    naked_protos: list = field(default_factory=list)  # (name, file, line)


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
    "__naked": "'__naked' must follow the parameter list of a function: "
               "'void f(void) __naked { __asm ... __endasm; }'",
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


_ASM_SCAN = re.compile(
    r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'|//[^\n]*|/\*.*?\*/"
    r"|\b__asm\b|\b__endasm\b", re.S)


def _asm_blocks(source: str, filename: str, found: Found) -> str:
    """Replace each ``__asm ... __endasm;`` by ``asm("...");`` with the
    block in MACRO-80 syntax.  The line count stays the same."""
    if "__asm" not in source and "__endasm" not in source:
        return source
    out, pos = [], 0
    for m in _ASM_SCAN.finditer(source):
        if m.start() < pos:
            continue            # inside a block that was handled
        word = m.group()
        if word == "__endasm":
            raise SdccCompatError(
                filename, source.count("\n", 0, m.start()) + 1,
                "'__endasm' without '__asm'")
        if word != "__asm":
            continue
        line0 = source.count("\n", 0, m.start()) + 1
        end = re.compile(r"\b__endasm\b").search(source, m.end())
        if end is None:
            raise SdccCompatError(filename, line0,
                                  "'__asm' has no '__endasm'")
        body = source[m.end():end.start()]
        # the block may start on the line of __asm: its first line
        # counts as line 1, the rest follow
        first = line0
        try:
            lines = sdas_inline.sdas_block_to_m80(
                body.strip("\n"), found.asm_blocks)
        except sdas_inline.InlineAsmError as e:
            skip = len(body) - len(body.lstrip("\n"))
            raise SdccCompatError(
                filename, first + skip + e.line - 1,
                f"__asm: {e.message}") from None
        found.asm_blocks += 1
        tail = end.end()
        t = re.compile(r"[ \t]*;").match(source, tail)
        if t:
            tail = t.end()
        nl = source.count("\n", m.start(), tail)
        text = "\n".join(lines) + "\n"
        lit = (text.replace("\\", "\\\\").replace('"', '\\"')
               .replace("\n", "\\n").replace("\t", "\\t"))
        out.append(source[pos:m.start()])
        stmt = f'asm("{lit}");'
        # the grammar has no asm as an unbraced sub-statement
        if re.search(r"(?:\)|\belse|\bdo)\s*$", "".join(out)[-200:]):
            stmt = "{ " + stmt + " }"
        out.append(stmt + "\n" * nl)
        pos = tail
    out.append(source[pos:])
    return "".join(out)


_ASM_WORDS = ("asm", "__asm__", "__asm")
_ASM_QUALS = ("volatile", "__volatile__", "__volatile", "inline", "goto")


def _after_case_label(toks, sig, colon: int) -> bool:
    """True when the ':' at sig[colon] ends a ``case X:`` or ``default:``
    label (and not a goto label or a ``?:``)."""
    depth = 0
    j = colon - 1
    while j >= 0:
        t = toks[sig[j]][1]
        if t in (")", "]"):
            depth += 1
        elif t in ("(", "["):
            depth -= 1
        elif depth == 0:
            if t in (";", "{", "}"):
                return False
            if t in ("case", "default"):
                return True
            if t == ":" and j < colon:
                # an earlier label or a ?: ; a label ends the walk
                return False
        j -= 1
    return False


def _asm_substatements(source: str) -> str:
    """Put braces round an asm statement that is the body of if, else, for,
    while or do without braces.  The grammar of uc_core (statement in
    data/c23.json) has asm only as a declaration in a block, not as a
    sub-statement.  The line count stays the same."""
    if not re.search(r"\b(?:asm|__asm__|__asm)\b", source):
        return source
    toks = [(m.lastgroup, m.group(), m.start(), m.end())
            for m in _TOKEN.finditer(source)]
    sig = [i for i, t in enumerate(toks) if t[0] not in ("ws", "cmt")]
    n = len(sig)

    def text(k):
        return toks[sig[k]][1] if 0 <= k < n else ""

    edits = []
    for k in range(1, n):
        if toks[sig[k]][0] != "id" or text(k) not in _ASM_WORDS:
            continue
        prev = text(k - 1)
        if prev == ")":
            op = _match_paren_back(toks, sig, k - 1)
            if op is None or text(op - 1) not in ("if", "for", "while",
                                                  "switch"):
                continue
        elif prev == ":":
            if not _after_case_label(toks, sig, k - 1):
                continue
        elif prev not in ("else", "do"):
            continue
        j = k + 1
        while text(j) in _ASM_QUALS:
            j += 1
        if text(j) != "(":
            continue
        depth = 0
        while j < n:
            if text(j) == "(":
                depth += 1
            elif text(j) == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j + 1 >= n or text(j + 1) != ";":
            continue
        edits.append((toks[sig[j + 1]][3], " }"))
        edits.append((toks[sig[k]][2], "{ "))
    for pos, ins in sorted(edits, reverse=True):
        source = source[:pos] + ins + source[pos:]
    return source


_GNU_ATTR = re.compile(
    r"__attribute(?:__)?\s*\(\(([^()]*(?:\([^()]*\)[^()]*)*)\)\)")
_NAKED_WORD = re.compile(r"^(?:__)?naked(?:__)?$")


def _attribute_naked(source: str) -> str:
    """Write ``__attribute__((naked))`` as ``[[uc80::naked]]``.  The other
    attributes of the list go away (the frontend would strip them), except
    ``interrupt``: the list stays as it is, and the frontend refuses it.
    The line count stays the same."""
    if "naked" not in source:
        return source

    def swap(m):
        items = [x.strip() for x in m.group(1).split(",")]
        if not any(_NAKED_WORD.match(x) for x in items):
            return m.group(0)
        if any(re.match(r"^(?:__)?interrupt(?:__)?\b", x) for x in items):
            return m.group(0)
        return "[[uc80::naked]]" + "\n" * m.group(0).count("\n")

    out, pos = [], 0
    for m in _TOKEN.finditer(source):       # skip strings and comments
        if m.lastgroup == "id" and m.group().startswith("__attribute"):
            a = _GNU_ATTR.match(source, m.start())
            if a and a.start() >= pos:
                out.append(source[pos:a.start()])
                out.append(swap(a))
                pos = a.end()
    out.append(source[pos:])
    return "".join(out)


def _naked_pass(source: str, filename: str, found: Found) -> str:
    """Find ``__naked`` and ``[[uc80::naked]]`` on function definitions,
    record the function names, and blank the spellings.  The check of the
    body (asm only) is made by the code generator, which knows the lines
    of the statements."""
    if "naked" not in source:
        return source
    toks = [(m.lastgroup, m.group(), m.start(), m.end())
            for m in _TOKEN.finditer(source)]
    sig = [i for i, t in enumerate(toks) if t[0] not in ("ws", "cmt")]
    n = len(sig)
    edits = []

    def text(k):
        return toks[sig[k]][1] if 0 <= k < n else ""

    def line_of(k):
        return source.count("\n", 0, toks[sig[k]][2]) + 1

    def record(close, at, what):
        """``close`` is the ')' of the parameter list."""
        op = _match_paren_back(toks, sig, close)
        name = None
        if op is not None and op > 0 and toks[sig[op - 1]][0] == "id":
            name = text(op - 1)
        k = close + 1
        while True:
            if text(k) == "[[" and text(k + 4) == "naked":
                k += 6
            elif text(k) in ("__critical", "__interrupt", "__naked"):
                k += 1
                if text(k) == "(":
                    while k < n and text(k) != ")":
                        k += 1
                    k += 1
            else:
                break
        if name is None or name in ("__interrupt", "if", "while", "for",
                                    "switch"):
            raise SdccCompatError(filename, line_of(at),
                                  f"'{what}' must be on a function")
        x = op - 1
        while x > 0 and text(x - 1) not in (";", "{", "}"):
            x -= 1
            if text(x) == "typedef":
                raise SdccCompatError(
                    filename, line_of(at),
                    f"'{what}' on the typedef '{name}': a function type "
                    f"cannot be naked; put it on each function definition")
        if text(k) == "{":
            found.naked.append((name, filename, line_of(at)))
        elif text(k) == ";":
            found.naked_protos.append((name, filename, line_of(op - 1)))
        else:
            raise SdccCompatError(filename, line_of(at),
                                  f"'{what}' must be on a function")

    for k in range(n):
        tx = text(k)
        if tx == "__naked" and toks[sig[k]][0] == "id":
            j = k - 1
            while True:
                if text(j) in ("__critical", "__interrupt"):
                    j -= 1
                elif text(j) == ")":
                    op = _match_paren_back(toks, sig, j)
                    if op is not None and op > 0 and text(op - 1) == \
                            "__interrupt":
                        j = op - 2
                    else:
                        break
                else:
                    break
            if text(j) != ")":
                continue            # left for the leftover check
            record(j, k, "__naked")
            edits.append((toks[sig[k]][2], toks[sig[k]][3]))
        elif (tx == "[[" and text(k + 1) == "uc80" and text(k + 2) == ":"
              and text(k + 3) == ":" and text(k + 4) == "naked"
              and text(k + 5) == "]]"):
            end = k + 5
            if text(k - 1) == ")":
                close = k - 1
            else:
                x = end + 1
                while x < n and text(x) not in ("(", "{", ";"):
                    x += 1
                if text(x) != "(":
                    raise SdccCompatError(
                        filename, line_of(k),
                        "'[[uc80::naked]]' must be on a function")
                close = x
                depth = 0
                while close < n:
                    if text(close) == "(":
                        depth += 1
                    elif text(close) == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    close += 1
            if close >= n or text(close) != ")":
                raise SdccCompatError(
                    filename, line_of(k),
                    "'[[uc80::naked]]' must be on a function")
            record(close, k, "[[uc80::naked]]")
            edits.append((toks[sig[k]][2], toks[sig[end]][3]))
    for st, en in sorted(edits, reverse=True):
        source = (source[:st] + re.sub(r"[^\n]", " ", source[st:en])
                  + source[en:])
    return source


def _fn_name_token(declarator):
    """The name token of a function declarator, or None when the
    declarator is not a function (the FnDeclarator that wraps the name)."""
    d, prev = declarator, None
    while hasattr(d, "inner"):
        d, prev = d.inner, d
    name = getattr(d, "name", None)
    if prev is not None and type(prev).__name__.startswith("FnDeclarator"):
        return name
    return None


def naked_mismatches(asts, paths, found: Found) -> list:
    """Warnings for a function that is naked in its definition but not in
    a prototype, or the reverse.  The code of the call never changes, but
    the programmer meant one of the two."""
    naked_defs = {n for n, _f, _l in found.naked}
    proto_marks = {(n, f, l) for n, f, l in found.naked_protos}
    defined = {}                  # name -> (file, line) of a definition
    protos = []                   # (name, file, line) of each prototype
    for unit, path in zip(asts, paths):
        for item in unit.items or []:
            if isinstance(item, ast.FunctionDef):
                tok = _fn_name_token(item.declarator)
                if tok is not None:
                    defined.setdefault(tok.text, (path, tok.line))
            elif isinstance(item, ast.Declaration):
                if any(getattr(sp, "kw", None) is not None
                       and sp.kw.text == "typedef"
                       for sp in item.decl_specs or []):
                    continue
                for d in item.declarators or []:
                    tok = _fn_name_token(getattr(d, "declarator", d))
                    if tok is not None:
                        protos.append((tok.text, path, tok.line))
    out = []
    # a static that the merge renamed (h__s0) is shown by its C name
    shown = lambda name: re.sub(r"__s\d+$", "", name)
    for name, f, l in sorted(proto_marks):
        if name in defined and name not in naked_defs:
            df, dl = defined[name]
            out.append(f"{f}:{l}: the prototype of '{shown(name)}' is naked, but "
                       f"the definition at {df}:{dl} is not, so the function "
                       f"gets a normal frame code and RET. Write __naked "
                       f"on the definition")
    for name, f, l in protos:
        if name in naked_defs and (name, f, l) not in proto_marks:
            out.append(f"{f}:{l}: '{shown(name)}' is naked in its definition, but "
                       f"this declaration is not; write __naked on every "
                       f"declaration of the function")
    return out


def prepass(source: str, filename: str, found: Found) -> str:
    """Rewrite ``source`` and add what it declares to ``found``."""
    source = _asm_blocks(source, filename, found)
    source = _asm_substatements(source)
    source = _attribute_naked(source)
    source = _naked_pass(source, filename, found)
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
