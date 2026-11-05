"""The ``;#var`` and ``;#type`` markers of ``-g`` (phase 8 of the debugger plan).

``debug_vars.py`` collects what the code generator knows about each variable.
This module writes the markers after the last text pass, because only the
final text of a function says:

- whether the function still has its IX frame (``frame=ix`` on ``;#func``)
- whether a local that ``reglocals`` could move still has a use of its slot
- where a local in a register is valid

The pass adds comment lines and labels.  A label has no bytes, and no pass
runs after this one, so the code is the same with and without ``-g`` (D2).
``ixrange.add_ix_ranges`` runs before this pass.

A register local is valid from the first statement marker of the function to
the last marker (the closing brace), except between a ``push`` of its
register pair and the matching ``pop``: there the register holds a
scratch value, and the local lies on the stack.  The record is
``reg=BC range=LABEL:LABEL ...``, one range for each stretch.  The range
ends at the first ``pop`` of the pair that no ``push`` precedes (the text
loads the register with another value: the local is dead) and at the first
``push`` that no ``pop`` closes.  If no stretch is left, the record says
``none``: the variable is not available.  The pass does not see a write that
reuses the register after the last use of the local without a ``pop``.
"""

import re

from .ixrange import _instruction
from .markers import marker_kind

_FUNC_NAME = re.compile(r"^\s*;\s*#func\s+(\S+)", re.IGNORECASE)
_FILE = re.compile(r'^\s*;\s*#file\s+(\d+)\s+"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_FRAME = re.compile(r"\bframe=(\w+)")
_IX_MEM = re.compile(r"\(ix([+-]\d+)\)", re.IGNORECASE)
_AUTO = re.compile(r"\?\?auto(?:\+(\d+))?", re.IGNORECASE)

_LABEL_ONLY = re.compile(r"^[^\s;:]+:\s*(;.*)?$")
_PAIR = {"BC": "bc", "DE": "de", "B": "bc", "C": "bc", "D": "de",
         "E": "de", "IY": "iy"}


def _escape(path: str) -> str:
    return path.replace("\\", "\\\\").replace('"', '\\"')


class _Files:
    """The ``;#file`` table of the text, and the files that a variable adds."""

    def __init__(self, lines):
        self.numbers: dict[str, int] = {}
        self.last = -1
        for i, ln in enumerate(lines):
            m = _FILE.match(ln)
            if m:
                path = re.sub(r"\\(.)", r"\1", m.group(2))
                self.numbers[path] = int(m.group(1))
                self.last = i
        self.added: list[str] = []

    def number(self, path: str) -> int:
        no = self.numbers.get(path)
        if no is None:
            no = max(self.numbers.values(), default=0) + 1
            self.numbers[path] = no
            self.added.append(f';#file {no} "{_escape(path)}"')
        return no


def _slot_used(body, kind, base, size) -> bool:
    """Whether some instruction of ``body`` names a byte of the slot."""
    for ln in body:
        text = ln.split(";", 1)[0]
        if kind == "x":
            for m in _IX_MEM.finditer(text):
                if base <= int(m.group(1)) < base + size:
                    return True
        else:
            for m in _AUTO.finditer(text):
                k = int(m.group(1) or 0)
                if base <= k < base + size:
                    return True
    return False


def _register_ranges(lines, first, last, pair):
    """([(start, end)] line indexes, reason): the stretches of
    ``lines[first:last]`` where the register pair holds the local.  The
    first is the line of the first marker after the first instruction."""
    func_at = first
    n = last
    k = func_at + 1
    while k < n and _instruction(lines[k]) is None:
        k += 1
    while k < n and not (marker_kind(lines[k]) == "line"):
        k += 1
    start = k
    end = None
    for j in range(n - 1, start, -1):
        if marker_kind(lines[j]) == "line":
            end = j
            break
    if end is None or end <= start:
        return [], "no-range"
    # the epilogue label ahead of the closing brace marker belongs to the
    # epilogue: the range ends in front of it
    while end - 1 > start and _LABEL_ONLY.match(lines[end - 1]):
        end -= 1
    spans = []
    depth = 0
    opened = 0
    stop = end              # the register stops holding the local here
    for idx in range(start, end):
        words = _instruction(lines[idx])
        if not words:
            continue
        if words == ["push", pair]:
            if depth == 0:
                opened = idx
            depth += 1
        elif words == ["pop", pair]:
            if depth == 0:
                stop = idx          # a pop with no push: the register is
                break               # loaded with another value (its local
            depth -= 1              # is dead, or the text moved a value)
            if depth == 0:
                spans.append((opened, idx + 1))
    if depth != 0 and stop == end:
        stop = opened               # a push that no pop closes
    pieces = []
    cur = start
    for a, b in spans:
        if a >= stop:
            break
        if a > cur:
            pieces.append((cur, a))
        cur = b
    if stop > cur:
        pieces.append((cur, stop))
    # a stretch with no instruction in it is an empty range of addresses
    pieces = [(a, b) for a, b in pieces
              if any(_instruction(lines[k]) for k in range(a, b))]
    if not pieces:
        return [], "save-not-paired"
    return pieces, None


def _source_name(var) -> str:
    """The name a file-scope static has in the C source.  A multi-file
    build renames two statics of one name to NAME__sN (multi_unit); the
    link label keeps the unique name, the debugger shows the source name."""
    name = var["name"]
    if var["kind"] == "static" and var.get("loc", ("",))[0] == "label":
        stripped = re.sub(r"__s\d+$", "", name)
        if stripped != name and var["loc"][1] == "_" + name:
            return stripped
    return name


def _type_line(name, d) -> str:
    kind = d.get("kind")
    out = f";#type {name}"
    if d.get("size") is not None:
        out += f" size={d['size']}"
    if kind in ("struct", "union"):
        items = []
        for m in d.get("members", ()):
            item = f"{m[0]}:{m[1]}@{m[2]}"
            if len(m) > 3:
                item += f"#{m[3]}.{m[4]}"
            items.append(item)
        out += " members=" + ",".join(items)
    elif kind == "enum":
        out += " values=" + ",".join(f"{n}={v}" for n, v in d.get("values", ()))
    else:
        out += f" target={d['target']}"
    return out


def add_vars(code: str, info: dict) -> str:
    """``code`` with the ``;#type`` and ``;#var`` markers."""
    if "#func" not in code and not info.get("globals"):
        return code
    lines = code.split("\n")
    files = _Files(lines)
    if files.last < 0:
        return code             # no file table: nothing to point at
    after_func: dict[int, list] = {}
    before: dict[int, list] = {}
    counter = [0]

    def label_before(idx, table):
        if idx not in table:
            counter[0] += 1
            table[idx] = f"??VR{counter[0]}"
        return table[idx]

    def where_words(var):
        out = []
        if var.get("scope"):
            path, a, b = var["scope"]
            out.append(f"scope={files.number(path)}:{a}-{b}")
        if var.get("decl"):
            path, line = var["decl"][0], var["decl"][1]
            out.append(f"decl={files.number(path)}:{line}")
        return out

    def line_for(var, loc_words, why=None):
        words = [f"\t;#var {_source_name(var)} {var['type']}",
                 f"kind={var['kind']}"]
        words += loc_words
        if why:
            words.append(f"why={why}")
        words += where_words(var)
        return " ".join(words)

    labels: dict[int, str] = {}
    i = 0
    n = len(lines)
    while i < n:
        if marker_kind(lines[i]) != "func":
            i += 1
            continue
        m = _FUNC_NAME.match(lines[i])
        j = i + 1
        while j < n and marker_kind(lines[j]) not in ("endfunc", "func"):
            j += 1
        if m is None or j >= n or marker_kind(lines[j]) != "endfunc":
            i = j
            continue
        fvars = info["funcs"].get(m.group(1))
        if fvars:
            fm = _FRAME.search(lines[i])
            frame = fm.group(1) if fm else "none"
            body = lines[i + 1:j]
            out = []
            for v in fvars:
                kind, off = v["loc"]
                if kind == "label":
                    out.append(line_for(v, [f"at={off}"]))
                    continue
                if kind == "ix" and frame != "ix":
                    out.append(line_for(v, ["none"], "no-frame"))
                    continue
                key = v["key"]
                if key is not None:
                    reg = v.get("reg")
                    if reg:
                        pieces, why = _register_ranges(lines, i, j, _PAIR.get(
                            reg, "bc"))
                        if why or not pieces:
                            out.append(line_for(v, ["none"],
                                                why or "no-range"))
                            continue
                        rng = [f"range={label_before(a, labels)}:"
                               f"{label_before(b, labels)}"
                               for a, b in pieces]
                        out.append(line_for(v, [f"reg={reg}"] + rng))
                        continue
                    if not _slot_used(body, key[0], key[1], max(v["size"], 1)):
                        out.append(line_for(v, ["none"], "no-storage"))
                        continue
                if kind == "auto":
                    out.append(line_for(v, [f"at=??AUTO+{off}"]))
                else:
                    out.append(line_for(v, [f"ix={off}"]))
            after_func[i + 1] = out
        i = j

    header = [_type_line(name, d) for name, d in info["types"].items()]
    for g in info["globals"]:
        header.append(line_for(g, [f"at={g['loc'][1]}"]))

    out_lines = []
    for idx, ln in enumerate(lines):
        if idx in labels:
            out_lines.append(f"{labels[idx]}:")
        if idx in after_func:
            out_lines.extend(after_func[idx])
        out_lines.append(ln)
        if idx == files.last:
            out_lines.extend(files.added)
            out_lines.extend(header)
    # the file table lines that variables added come after the table, but
    # header and function lines were built before all numbers were known
    return "\n".join(out_lines)
