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

from .z80_intrinsics import (ISR_ALT_SAVE, ISR_ALT_RESTORE, ISR_WA_SAVE,
                             ISR_WA_RESTORE)

_LABEL = re.compile(r"^([A-Za-z_@?$.][\w@?$.]*)\s*:{0,2}(?=\s|$|;)")
_LABEL_COLON = re.compile(r"^([A-Za-z_@?$.][\w@?$.]*)::?")
_SYM = re.compile(r"[A-Za-z_@?$.][\w@?$.]*")
_TERMINAL = re.compile(
    r"^(RET|RETI|RETN|JP\s+(?!(?:NZ|Z|NC|C|PO|PE|P|M)\s*,)\S.*|"
    r"JR\s+(?!(?:NZ|Z|NC|C)\s*,)\S.*|HALT)$", re.I)
_ALT = re.compile(r"^(EXX|EX\s+AF\s*,\s*AF')$", re.I)
_BRANCH = {"CALL", "JP", "JR", "DJNZ", "RST", "RET", "RETI", "RETN"}
_INDIRECT = re.compile(r"^JP\s+\((HL|IX|IY)\)$", re.I)
_DIRECTIVE = re.compile(
    r"^(PUBLIC|EXTRN|EXT|EXTERNAL|COMMON|DSEG|CSEG|ASEG|ORG|END|DS|DB|DW|DD|"
    r"EQU|\.Z80|\.PHASE|\.DEPHASE|NAME|TITLE|SUBTTL|PAGE|IF|ELSE|ENDIF)\b",
    re.I)


@dataclass
class Region:
    label: str
    unit: int
    uses_alt: bool = False
    indirect: bool = False         # calls through a function pointer
    addr_refs: set = field(default_factory=set)   # symbols named, not jumped to
    refs: set = field(default_factory=set)
    calls: set = field(default_factory=set)       # CALL, JP, JR, RST targets
    has_code: bool = False
    falls_through: bool = True
    next_label: str | None = None


@dataclass
class Graph:
    regions: dict = field(default_factory=dict)    # label -> Region
    fixed: dict = field(default_factory=dict)      # data label -> block name
    display: set = field(default_factory=set)      # labels worth printing
    # work area layout: block name -> [(label, size or None), ...] in
    # the order of the file; fixed_size: data label -> size or None
    blocks: dict = field(default_factory=dict)
    fixed_size: dict = field(default_factory=dict)
    # symbols that an instruction or a DW names without a jump or a call:
    # the functions whose address is taken (when the label has a region)
    data0: set = field(default_factory=set)      # data labels of the program
    taken: set = field(default_factory=set)      # of the program (unit 0)
    unit_taken: dict = field(default_factory=dict)   # unit -> DW symbols
    # the zeroed data (COMMON) of the program text in the order of the text:
    # [(label, size)].  A label that shares its address with the label
    # before has size 0.  Whole-program builds embed the work areas here,
    # in a layout that differs from the lib files.
    layout0: list = field(default_factory=list)
    equ0: set = field(default_factory=set)         # EQU labels of unit 0
    # block -> bytes: the library modules that export the save helpers
    # __wasv_<block> and __wars_<block> (lib/gen_wa_helpers.py)
    helpers: dict = field(default_factory=dict)


def _eval_size(text: str, equs: dict):
    """The value of a DS operand: a number, an EQU name, + - *.  None
    when the text has something else."""
    expr = text.strip()
    for _ in range(4):
        expr = re.sub(r"[A-Za-z_@?$.][\w@?$.]*",
                      lambda m: "(" + equs[m.group(0).upper()] + ")"
                      if m.group(0).upper() in equs
                      and not re.fullmatch(r"[0-9][0-9A-F]*H", m.group(0),
                                           re.I) else m.group(0), expr)
    expr = re.sub(r"\b([0-9][0-9A-Fa-f]*)[hH]\b",
                  lambda m: str(int(m.group(1), 16)), expr)
    if not re.fullmatch(r"[0-9+\-*() ]+", expr):
        return None
    try:
        return int(eval(expr, {"__builtins__": {}}, {}))
    except Exception:
        return None


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
    equs = {}
    pending = []
    prev_op = ""
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
        if label is not None and unit and label.startswith("__wasv_"):
            graph.helpers[label[7:]] = None      # the size comes at the end
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
        meq = re.match(r"^EQU\s+(.*)$", body, re.I)
        if label is not None and meq:
            equs[label.upper()] = meq.group(1).strip()
            if unit == 0:
                graph.equ0.add(label)
        if label is not None and seg == "COMMON" and unit == 0:
            graph.data0.add(label)
        if label is not None and seg == "COMMON":
            # a work area of a library module (named COMMON block)
            # (the BSS block of a separately compiled uc80 module, named
            # uc_<stem>_<hash>, holds the program's own variables: not one)
            if common and not (unit == 0 and common.startswith("uc_")):
                graph.fixed[label] = common
            pending.append(label)
        if seg == "COMMON":
            md = re.match(r"^(?:DS|DEFS)\s+(.*)$", body, re.I)
            if md:
                size = _eval_size(md.group(1), equs)
                if unit == 0:
                    for i, lab in enumerate(pending):
                        graph.layout0.append((lab, size if i == 0 else 0))
                elif common:
                    for i, lab in enumerate(pending):
                        # labels that share an address: the first has the size
                        graph.fixed_size[lab] = size
                        graph.blocks.setdefault(common, []).append(
                            (lab, size if i == 0 else 0))
                pending = []
            continue
        mdw = re.match(r"^(?:DW|DEFW|DD)\s+(.*)$", body, re.I)
        if mdw:
            (graph.taken if unit == 0 else graph.unit_taken.setdefault(
                unit, set())).update(_SYM.findall(mdw.group(1)))
        if seg != "CSEG":
            if label is not None and unit == 0:
                # a data label of the program: it replaces a library
                # routine of the same name (a global named log, sin ...)
                graph.data0.add(label)
            continue
        if label is not None:
            if only_missing and (label in graph.regions
                                 or label in graph.data0):
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
        if not re.match(r"^(DB|DW|DD|DEFB|DEFW)\b", body, re.I):
            cur.has_code = True
        if _ALT.match(body):
            cur.uses_alt = True
        operands = body.split(None, 1)[1] if " " in body or "\t" in body \
            else ""
        for sym in _SYM.findall(operands):
            cur.refs.add(sym)
        op = up.split()[0] if up else ""
        if op in _BRANCH and op not in ("RET", "RETI", "RETN"):
            tgt = operands.split(",")[-1].strip()
            if _SYM.fullmatch(tgt):
                cur.calls.add(tgt)
        if op not in _BRANCH:
            cur.addr_refs.update(_SYM.findall(operands))
            if unit == 0:
                graph.taken.update(cur.addr_refs)
        if _INDIRECT.match(up) and (
                is_lib or cur.label.upper() == "__CALLHL"
                or prev_op != "EX"):
            cur.indirect = True
        prev_op = op
        cur.falls_through = not _TERMINAL.match(body)
    for blk in list(graph.helpers):
        if graph.helpers[blk] is None:
            sizes = [sz for _l, sz in graph.blocks.get(blk, [])]
            graph.helpers[blk] = (sum(sizes) if sizes and None not in sizes
                                  else 0)
    # a call to a data label is a call through a trampoline in RAM (qsort
    # writes JP compar there): an indirect call
    data0 = {l for l, _s in graph.layout0} if unit == 0 else set()
    for r in labels_here:
        if any(c in graph.fixed or c in data0 for c in r.calls):
            r.indirect = True
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


def taken_regions(g: Graph, extra=()) -> list:
    """The labels of the functions whose address the program takes: a
    region label that an instruction or a DW of the program names without
    a jump or a call, and the labels in ``extra`` (the C functions that the
    call graph of the code generator lists as address-taken).  A library
    routine adds its own when the interrupt function reaches the routine
    (see _reach)."""
    return sorted(s for s in (g.taken | set(extra))
                  if _is_function(g, s))


def _is_function(g: Graph, label: str) -> bool:
    r = g.regions.get(label)
    # a label that has no code of its own (an alias such as _cos before
    # _cosf) stands for the code that it falls into
    seen = set()
    while (r is not None and not r.has_code and r.falls_through
           and r.next_label and r.label not in seen):
        seen.add(r.label)
        r = g.regions.get(r.next_label)
    return (r is not None and r.has_code
            and not label.startswith(("@", "?")))


def _reach(g: Graph, start: str, taken=(), with_taken=False):
    """BFS from ``start``: {label: parent label}.  A region that calls
    through a pointer (JP (HL) of ``__callhl``) reaches every label in
    ``taken``, and every function whose address a reached library routine
    takes.  Run again until no function is added."""
    taken = set(taken)
    while True:
        parent = {start: None}
        queue = [start]
        units = {0}
        seen_taken = set(taken)
        indirect = False
        while queue:
            cur = queue.pop(0)
            r = g.regions.get(cur)
            if r is None:
                continue
            nxt = [s for s in r.refs if s in g.regions]
            if r.indirect:
                indirect = True
                nxt.extend(sorted(taken))
            if r.unit not in units:
                units.add(r.unit)
                seen_taken |= {s for s in g.unit_taken.get(r.unit, ())
                               if _is_function(g, s)}
            seen_taken |= {s for s in r.addr_refs if _is_function(g, s)
                           and r.unit != 0}
            if r.falls_through and r.next_label:
                nxt.append(r.next_label)
            for s in nxt:
                if s not in parent:
                    parent[s] = cur
                    queue.append(s)
        if not indirect or seen_taken == taken:
            return (parent, taken) if with_taken else parent
        taken = seen_taken


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
    indirect_chain: list | None = None       # path to a call through a pointer
    taken: list = field(default_factory=list)  # labels reached through it
    alt_users: dict = field(default_factory=dict)  # label -> chain
    # labels that reached code calls or jumps to and that no text defines
    # (a routine of another module): label -> chain
    unknown: dict = field(default_factory=dict)


# The blocks that hold state, not temporaries: an interrupt function that
# reaches them changes them for good, so the save-and-restore of the work
# areas never touches them.
STATE_BLOCKS = frozenset({
    "lc_errno", "lc_exit_data", "lc_exit_hook", "lc_fslots", "lc_malloc",
    "lc_malloc_data", "lc_quick_exit", "lc_signal", "lc_strtok",
    "lc_thread_mode", "lc_fseek", "lc_fread", "lc_fwrite", "lc_fprintf",
})


# A plan entry whose label starts with HELPER saves a library work area
# through the routines __wasv_<block> (save) and __wars_<block> (restore).
HELPER = "call:"


def save_plan(g: Graph, rep: "IsrReport") -> tuple:
    """([(blocks, first label, bytes)], [block, ...], {block, ...}): the
    areas that the save code copies, the reached blocks that it cannot
    cover (state, or no size known), and the blocks it covers.

    The addresses come from the program text (``layout0``), where the
    whole-program build embeds the work areas: the order of the labels
    there differs from the lib files.  Labels that follow each other in
    the text join into one area.  A label that the program text does not
    define is never named (a lib module that is not embedded would be
    linked a second time)."""
    index = {lab: i for i, (lab, _sz) in enumerate(g.layout0)}
    chosen = {}                       # layout index -> block
    skipped = []
    calls = []
    for block in sorted(rep.fixed):
        labels = [l for l, _s in g.blocks.get(block) or []]
        here = [l for l in labels if l in index]
        # a label that the reached code names counts even when the block
        # list of the lib file lacks it
        here += [l for l in rep.fixed[block][0]
                 if l in index and l not in here]
        sizes = [g.layout0[index[l]][1] for l in here]
        if block in STATE_BLOCKS:
            skipped.append(block)
            continue
        if not here:
            # a library module that the link adds: its labels are private,
            # its save helpers are public
            size = g.helpers.get(block)
            if size:
                calls.append((block, HELPER + block, size))
            else:
                skipped.append(block)
            continue
        if None in sizes or sum(sizes) == 0:
            skipped.append(block)
            continue
        for l in here:
            chosen[index[l]] = block
    plan, saved = [], set()
    run = None                       # [first index, last index, blocks]
    def flush():
        if run is None:
            return
        lo, hi, blocks = run
        size = sum(g.layout0[i][1] for i in range(lo, hi + 1))
        first = g.layout0[lo][0]
        if size:
            plan.append((", ".join(sorted(blocks)), first, size))
            saved.update(blocks)
    for i in sorted(chosen):
        if run is not None and i == run[1] + 1:
            run[1] = i
            run[2].add(chosen[i])
        else:
            flush()
            run = [i, i, {chosen[i]}]
    flush()
    for block, label, size in calls:
        plan.append((block, label, size))
        saved.add(block)
    return plan, skipped, saved


def analyze(code: str, isrs: dict, lib_dir: Path | None,
            address_taken=(), with_graph=False):
    """``isrs`` maps an interrupt function name to its label.
    ``address_taken`` are the labels of the C functions whose address the
    code generator found.  With ``with_graph`` the result is
    (reports, graph)."""
    g = build_graph(code, lib_dir)
    taken = [t for t in taken_regions(g, address_taken)
             if t not in set(isrs.values())]
    reports = []
    for name, label in sorted(isrs.items()):
        rep = IsrReport(name, label)
        parent, reached_taken = _reach(g, label, taken, with_taken=True)
        order = sorted(parent, key=lambda x: (len(_chain(g, parent, x)), x))
        for lab in order:
            r = g.regions.get(lab)
            if r is not None and r.indirect:
                rep.indirect_chain = _chain(g, parent, lab)
                rep.taken = sorted(
                    t for t in reached_taken if t in parent)
                break
        for lab in order:
            r = g.regions.get(lab)
            if r is None:
                continue
            for sym in sorted(r.calls):
                if (sym not in g.regions and sym not in g.equ0
                        and sym not in g.fixed and sym not in g.fixed_size
                        and not sym.startswith(("@", "?", "$"))
                        and not re.fullmatch(r"[0-9][0-9A-Fa-f]*[hH]?", sym)
                        and sym not in rep.unknown):
                    rep.unknown[sym] = _chain(g, parent, lab) + [sym]
        for lab in order:
            r = g.regions.get(lab)
            if r is not None and r.uses_alt:
                rep.alt_users[lab] = _chain(g, parent, lab)
                if rep.alt_chain is None:
                    rep.alt_chain = rep.alt_users[lab]
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
    return (reports, g) if with_graph else reports


def format_notes(reports: list) -> list:
    """Note lines: the handlers that call through a function pointer."""
    out = []
    for rep in reports:
        if rep.indirect_chain and not rep.taken:
            out.append(
                f"interrupt function '{rep.name}' calls through a function "
                f"pointer ({' -> '.join(rep.indirect_chain)}); the program "
                f"takes the address of no function that uc80 can see")
            continue
        if rep.indirect_chain:
            names = ", ".join(t.lstrip("_") or t for t in rep.taken[:8])
            more = (f" and {len(rep.taken) - 8} more"
                    if len(rep.taken) > 8 else "")
            out.append(
                f"interrupt function '{rep.name}' calls through a function "
                f"pointer ({' -> '.join(rep.indirect_chain)}); uc80 cannot "
                f"tell the target, so it treats every function whose "
                f"address is taken as reachable from the interrupt "
                f"({names}{more})")
    return out


def format_reports(reports: list, saved=None, whole_program=True,
                   opt_out=False) -> list:
    """Warning lines for the reports.  ``saved`` maps an interrupt
    function name to the set of blocks that its prologue saves: no
    warning for those."""
    out = []
    for rep in reports:
        for sym, chain in sorted(rep.unknown.items()):
            out.append(
                f"interrupt function '{rep.name}' calls '{sym}' "
                f"({' -> '.join(chain[:-1])}), which this compilation does "
                f"not define; uc80 cannot see which work areas or alternate "
                f"registers the routine uses, and the save of the work "
                f"areas does not cover them")
        if rep.alt_chain:
            out.append(
                f"interrupt function '{rep.name}' reaches code that uses "
                f"the alternate registers ({' -> '.join(rep.alt_chain)}); "
                f"the prologue saves and restores them")
        for block, (syms, chain) in sorted(rep.fixed.items()):
            if block in (saved or {}).get(rep.name, ()):
                continue
            names = ", ".join(sorted(syms))
            out.append(
                f"interrupt function '{rep.name}' reaches {names} "
                f"(work area of {block}) at a fixed address "
                f"({' -> '.join(chain)}); main code that calls the same "
                f"routine can be corrupted by the interrupt"
                + ("" if block in STATE_BLOCKS else
                   ("; the handler saves no work area because of "
                    "--no-isr-save-workareas" if opt_out else
                    "; the save of the work areas needs the whole program "
                    "(drop --no-whole-program)" if not whole_program else
                    "; --isr-save-workareas makes the handler save and "
                    "restore the area"))
                + ("; the block holds state, and no save can revert it"
                   if block in STATE_BLOCKS else ""))
    return out


ALT_SAVE = ("\tex\tAF,AF'\n\tpush\tAF\n\tex\tAF,AF'\n\texx\n\tpush\tBC\n"
            "\tpush\tDE\n\tpush\tHL\n\texx")
ALT_RESTORE = ("\texx\n\tpop\tHL\n\tpop\tDE\n\tpop\tBC\n\texx\n"
               "\tex\tAF,AF'\n\tpop\tAF\n\tex\tAF,AF'")


def _wa_word_area(label: str, size: int):
    """(save lines, restore lines) of an area of up to 6 bytes: words by
    HL on the stack, a last odd byte by A (AF is already saved)."""
    save, restore = [], []
    off = 0
    sym = lambda o: f"{label}+{o}" if o else label
    while size - off >= 2:
        save += [f"\tld\tHL,({sym(off)})", "\tpush\tHL"]
        restore.insert(0, f"\tld\t({sym(off)}),HL")
        restore.insert(0, "\tpop\tHL")
        off += 2
    if size - off:
        save += [f"\tld\tA,({sym(off)})", "\tpush\tAF"]
        restore.insert(0, f"\tld\t({sym(off)}),A")
        restore.insert(0, "\tpop\tAF")
    return save, restore


WA_WORD_LIMIT = 6        # bytes: up to this size a word copy is shorter


def wa_code(plan: list) -> tuple:
    """(save text, restore text) for ``plan``, a list of (block, label,
    bytes).  The registers are free: the prologue saved AF, BC, DE, HL.
    An area of up to WA_WORD_LIMIT bytes is copied word by word through
    the stack; a larger one is copied to the stack with LDIR."""
    save, restore = [], []
    for block, label, size in plan:
        if label.startswith(HELPER):
            s = [f"\tcall\t__wasv_{block}"]
            r = [f"\tcall\t__wars_{block}"]
        elif size <= WA_WORD_LIMIT:
            s, r = _wa_word_area(label, size)
        else:
            s = [f"\tld\tHL,-{size}", "\tadd\tHL,SP", "\tld\tSP,HL",
                 "\tex\tDE,HL", f"\tld\tHL,{label}", f"\tld\tBC,{size}",
                 "\tldir"]
            r = ["\tld\tHL,0", "\tadd\tHL,SP", f"\tld\tDE,{label}",
                 f"\tld\tBC,{size}", "\tldir", "\tld\tSP,HL"]
        save += s
        restore = r + restore          # the last area saved comes back first
    return "\n".join(save), "\n".join(restore)


def resolve_alt_markers(code: str, need_alt: set, wa: dict | None = None) -> str:
    """Replace the marker lines of each interrupt function: with the
    save and restore code for a function in ``need_alt``, else with
    nothing.  ``wa`` maps an interrupt function name to its plan for the
    work areas (see wa_code); a function not in it gets nothing."""
    if ISR_ALT_SAVE not in code:
        return code
    wa = wa or {}
    declared = {w.strip().upper() for m in re.finditer(
        r"^\s*(?:EXTRN|EXT|EXTERNAL)\s+([^;\n]+)", code, re.I | re.M)
        for w in m.group(1).split(",")}
    out = []
    for line in code.split("\n"):
        s = line.strip()
        if s.startswith(ISR_ALT_SAVE):
            if s[len(ISR_ALT_SAVE):].strip() in need_alt:
                out.append(ALT_SAVE)
        elif s.startswith(ISR_ALT_RESTORE):
            if s[len(ISR_ALT_RESTORE):].strip() in need_alt:
                out.append(ALT_RESTORE)
        elif s.startswith(ISR_WA_SAVE):
            plan = wa.get(s[len(ISR_WA_SAVE):].strip())
            if plan:
                for _b, lab, _n in plan:
                    if lab.startswith(HELPER):
                        for h in ("__wasv_" + _b, "__wars_" + _b):
                            if h.upper() not in declared:
                                declared.add(h.upper())
                                out.append(f"\tEXTRN\t{h}")
                        continue
                    if lab.upper() not in declared and not re.search(
                            r"^" + re.escape(lab) + r"::?", code,
                            re.I | re.M):
                        declared.add(lab.upper())
                        out.append(f"\tEXTRN\t{lab}")
                out.append(wa_code(plan)[0])
        elif s.startswith(ISR_WA_RESTORE):
            plan = wa.get(s[len(ISR_WA_RESTORE):].strip())
            if plan:
                out.append(wa_code(plan)[1])
        else:
            out.append(line)
    return "\n".join(out)


def wa_cost(plan: list) -> tuple:
    """(bytes, T-states) that the save and the restore of ``plan`` add."""
    nbytes = tstates = 0
    for _b, label, size in plan:
        if label.startswith(HELPER):
            # CALL + the helper (POP IY ... JP (IY)), save and restore
            nbytes += 6
            tstates += (17 + 14 + 10 + 11 + 6 + 4 + 10 + 10 + 21 * size - 5
                        + 8) + (17 + 14 + 10 + 11 + 10 + 10 + 21 * size - 5
                                + 6 + 8)
        elif size <= WA_WORD_LIMIT:
            words, odd = divmod(size, 2)
            nbytes += words * 8 + odd * 8        # LD,PUSH ... POP,LD
            tstates += words * 53 + odd * (13 + 11 + 10 + 13)
        else:
            nbytes += 27
            tstates += 51 + 21 * size - 5 + 47 + 21 * size - 5
    return nbytes, tstates
