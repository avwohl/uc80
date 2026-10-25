"""What an interrupt function reaches, in the assembly text.

Two questions, answered from the call graph of the final assembly (the
generated code, the embedded runtime, the appended assembly files, and for
a name that only a library defines, the library source):

1. Does the interrupt function reach code that uses the alternate
   registers (EXX, EX AF,AF')?  The float runtime does.  The prologue then
   saves them (``resolve_alt_markers``).
2. Does it reach a runtime or libc routine that keeps its temporaries at
   fixed RAM addresses (``__tmp32``, ``__divrem``, ``__div32_dvd``, the
   64-bit and float work areas, libc statics)?  If main code uses the same
   routine, an interrupt that arrives inside the routine corrupts the
   temporaries.  The caller prints a warning with the call chain.

The graph is by label: a region runs from a label to the next label, and
has an edge to every label its instructions name, and to the next region
when the region does not end in an unconditional transfer.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from .z80_intrinsics import ISR_ALT_SAVE, ISR_ALT_RESTORE

_LABEL = re.compile(r"^([A-Za-z_@?$.][\w@?$.]*)\s*:{0,2}(?=\s|$|;)")
_LABEL_COLON = re.compile(r"^([A-Za-z_@?$.][\w@?$.]*)::?")
_SYM = re.compile(r"[A-Za-z_@?$.][\w@?$.]*")
_TERMINAL = re.compile(
    r"^(RET|RETI|RETN|JP\s+(?!(?:NZ|Z|NC|C|PO|PE|P|M)\s*,)\S.*|"
    r"JR\s+(?!(?:NZ|Z|NC|C)\s*,)\S.*|HALT)$", re.I)
_ALT = re.compile(r"^(EXX|EX\s+AF\s*,\s*AF')$", re.I)
_DIRECTIVE = re.compile(
    r"^(PUBLIC|EXTRN|EXT|EXTERNAL|COMMON|DSEG|CSEG|ASEG|ORG|END|DS|DB|DW|DD|"
    r"EQU|\.Z80|\.PHASE|\.DEPHASE|NAME|TITLE|SUBTTL|PAGE|IF|ELSE|ENDIF)\b",
    re.I)


@dataclass
class Region:
    label: str
    unit: int
    uses_alt: bool = False
    refs: set = field(default_factory=set)
    falls_through: bool = True
    next_label: str | None = None


@dataclass
class Graph:
    regions: dict = field(default_factory=dict)    # label -> Region
    fixed: dict = field(default_factory=dict)      # data label -> block name
    display: set = field(default_factory=set)      # labels worth printing


def _strip(line):
    out = []
    quote = None
    for i, ch in enumerate(line):
        if ch == "'" and not quote and line[max(0, i - 2):i].upper() == "AF":
            out.append(ch)          # the alternate register AF'
            continue
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch == ";":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _parse_unit(text: str, unit: int, graph: Graph, is_lib: bool,
                only_missing: bool) -> None:
    seg = "CSEG"
    common = None
    cur = None
    publics = set()
    labels_here = []
    for raw in text.splitlines():
        line = _strip(raw)
        if not line.strip():
            continue
        body = line
        m = _LABEL_COLON.match(body)
        label = None
        if m:
            label = m.group(1)
            body = body[m.end():]
        elif body[0] not in " \t":
            m2 = re.match(r"^([A-Za-z_@?$.][\w@?$.]*)\s+(EQU|DS|DB|DW|DD)\b",
                          body, re.I)
            if m2:
                label = m2.group(1)
                body = body[m2.start(2):]
        body = body.strip()
        up = body.upper()
        if up.startswith("COMMON"):
            nm = re.search(r"/([^/]*)/", body)
            seg, common = "COMMON", (nm.group(1) if nm else "")
            cur = None
            continue
        if up.startswith("DSEG"):
            seg, common, cur = "DSEG", None, None
            continue
        if up.startswith("CSEG") or up.startswith("ASEG"):
            seg, common = "CSEG", None
            if label is None:
                continue
        if up.startswith("PUBLIC"):
            publics.update(w.strip() for w in body[6:].split(",") if w.strip())
            continue
        if label is not None and seg == "COMMON":
            # a work area of a library module (named COMMON block)
            if common:
                graph.fixed[label] = common
            continue
        if seg != "CSEG":
            continue
        if label is not None:
            if only_missing and label in graph.regions:
                cur = None
                continue
            cur = Region(label=label, unit=unit)
            labels_here.append(cur)
            graph.regions[label] = cur
            if len(labels_here) > 1 and labels_here[-2].falls_through:
                labels_here[-2].next_label = label
        if cur is None or not body:
            continue
        if _DIRECTIVE.match(body):
            continue
        if _ALT.match(body):
            cur.uses_alt = True
        for sym in _SYM.findall(body.split(None, 1)[1] if " " in body or
                                "\t" in body else ""):
            cur.refs.add(sym)
        cur.falls_through = not _TERMINAL.match(body)
    for r in labels_here:
        if r.label in publics or not r.label.startswith(("@", "?")):
            if is_lib:
                if r.label in publics:
                    graph.display.add(r.label)
            else:
                graph.display.add(r.label)


_LIB_DIRS = ("rt", "lc", "math48")


def build_graph(code: str, lib_dir: Path | None) -> Graph:
    g = Graph()
    _parse_unit(code, 0, g, is_lib=False, only_missing=False)
    if lib_dir is not None:
        unit = 1
        files = [lib_dir / "float48.mac"]
        for sub in _LIB_DIRS:
            d = lib_dir / sub
            if d.is_dir():
                files += sorted(d.glob("*.mac"))
        for path in files:
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            _parse_unit(text, unit, g, is_lib=True, only_missing=True)
            unit += 1
    return g


def _reach(g: Graph, start: str):
    """BFS from ``start``: {label: parent label}."""
    parent = {start: None}
    queue = [start]
    while queue:
        cur = queue.pop(0)
        r = g.regions.get(cur)
        if r is None:
            continue
        nxt = [s for s in r.refs if s in g.regions]
        if r.falls_through and r.next_label:
            nxt.append(r.next_label)
        for s in nxt:
            if s not in parent:
                parent[s] = cur
                queue.append(s)
    return parent


def _chain(g: Graph, parent: dict, end: str) -> list:
    path = []
    cur = end
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    shown = [p for p in path if p in g.display or p == path[0]]
    return shown or path[-1:]


@dataclass
class IsrReport:
    name: str
    label: str
    alt_chain: list | None = None            # path to code that uses EXX
    # work-area block -> (symbols used, shortest call chain to a user)
    fixed: dict = field(default_factory=dict)


def analyze(code: str, isrs: dict, lib_dir: Path | None) -> list:
    """``isrs`` maps an interrupt function name to its label."""
    g = build_graph(code, lib_dir)
    reports = []
    for name, label in sorted(isrs.items()):
        rep = IsrReport(name, label)
        parent = _reach(g, label)
        order = sorted(parent, key=lambda x: (len(_chain(g, parent, x)), x))
        for lab in order:
            r = g.regions.get(lab)
            if r is not None and r.uses_alt:
                rep.alt_chain = _chain(g, parent, lab)
                break
        for lab in order:
            r = g.regions.get(lab)
            if r is None:
                continue
            for sym in sorted(r.refs):
                if sym in g.fixed:
                    block = g.fixed[sym]
                    syms, chain = rep.fixed.setdefault(
                        block, (set(), _chain(g, parent, lab)))
                    syms.add(sym)
        reports.append(rep)
    return reports


def format_reports(reports: list) -> list:
    """Warning lines for the reports."""
    out = []
    for rep in reports:
        if rep.alt_chain:
            out.append(
                f"interrupt function '{rep.name}' reaches code that uses "
                f"the alternate registers ({' -> '.join(rep.alt_chain)}); "
                f"the prologue saves and restores them")
        for block, (syms, chain) in sorted(rep.fixed.items()):
            names = ", ".join(sorted(syms))
            out.append(
                f"interrupt function '{rep.name}' reaches {names} "
                f"(work area of {block}) at a fixed address "
                f"({' -> '.join(chain)}); main code that calls the same "
                f"routine can be corrupted by the interrupt")
    return out


ALT_SAVE = ("\tex\tAF,AF'\n\tpush\tAF\n\tex\tAF,AF'\n\texx\n\tpush\tBC\n"
            "\tpush\tDE\n\tpush\tHL\n\texx")
ALT_RESTORE = ("\texx\n\tpop\tHL\n\tpop\tDE\n\tpop\tBC\n\texx\n"
               "\tex\tAF,AF'\n\tpop\tAF\n\tex\tAF,AF'")


def resolve_alt_markers(code: str, need_alt: set) -> str:
    """Replace the two marker lines of each interrupt function: with the
    save and restore code for a function in ``need_alt``, else with
    nothing."""
    if ISR_ALT_SAVE not in code:
        return code
    out = []
    for line in code.split("\n"):
        s = line.strip()
        if s.startswith(ISR_ALT_SAVE):
            if s[len(ISR_ALT_SAVE):].strip() in need_alt:
                out.append(ALT_SAVE)
        elif s.startswith(ISR_ALT_RESTORE):
            if s[len(ISR_ALT_RESTORE):].strip() in need_alt:
                out.append(ALT_RESTORE)
        else:
            out.append(line)
    return "\n".join(out)
