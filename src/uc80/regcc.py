"""Register calling convention: the helpers that need no code generator.

See docs/calling_convention.md.  A function the compiler can prove internal
takes its first two scalar parameters in registers:

    parameter 0: a byte in A, a word in HL
    parameter 1: a byte in E, a word in DE

The prepass in codegen.py (CodeGenerator._plan_register_cc) decides which
functions qualify.  This module holds the parts of that decision that read
only the syntax tree and the library sources, and the scan that tells a
call site whether the code of an argument disturbs a register.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from pathlib import Path

from uc_core import ast
from uc_core import _dcfast
from uc_core.volatility import children as _children

try:  # the register effects of one instruction
    from upeepz80.z80 import effect as _effect
except Exception:  # pragma: no cover - the scan then says "unknown"
    _effect = None


@dataclass
class RegPlan:
    """How one function takes its parameters.

    ``types`` holds the legacy type of each parameter, in order.  ``loc``
    holds, for each parameter, the register that carries it ("A", "HL",
    "E", "DE") or None when the caller pushes it.  ``spill`` is True for a
    register parameter that the body names; one that is never named is not
    stored on entry."""
    types: list = field(default_factory=list)
    loc: list = field(default_factory=list)
    sizes: list = field(default_factory=list)
    names: list = field(default_factory=list)   # parameter names, None if none
    spill: list = field(default_factory=list)   # True: store on entry
    ret_a: bool = False       # a char result comes back in A, not in HL
    ret_signed: bool = False  # the signedness of that char result

    def reg_params(self) -> list[int]:
        return [i for i, r in enumerate(self.loc) if r is not None]


# ---------------------------------------------------------------------------
# Names that library and hand-written assembly use
# ---------------------------------------------------------------------------

_DIRECTIVE = re.compile(
    r"^[ \t]*(?:public|extrn|ext|extern|global|entry)[ \t]+([^;\n]*)",
    re.IGNORECASE | re.MULTILINE)
_TOKEN = re.compile(r"[A-Za-z_@$.?][A-Za-z0-9_@$.?]*")
_LIB_NAMES: set[str] | None = None


def lib_asm_names() -> set[str]:
    """The lower-case names that the library sources export or import.  A
    program function with one of these names may be called, or replace a
    routine that is called, by library code that uses the stack
    convention."""
    global _LIB_NAMES
    if _LIB_NAMES is None:
        names: set[str] = set()
        root = Path(__file__).parent / "lib"
        for path in root.rglob("*.mac"):
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            for m in _DIRECTIVE.finditer(text):
                for item in m.group(1).split(","):
                    item = item.strip().lower()
                    if item:
                        names.add(item)
        _LIB_NAMES = names
    return _LIB_NAMES


def asm_text_names(texts) -> set[str]:
    """Every identifier-like token of the given assembly texts, lower case."""
    out: set[str] = set()
    for text in texts:
        out.update(t.lower() for t in _TOKEN.findall(text))
    return out


# ---------------------------------------------------------------------------
# What the program does with each function
# ---------------------------------------------------------------------------

@dataclass
class ProgramScan:
    value_refs: set = field(default_factory=set)   # named other than called
    call_args: dict = field(default_factory=dict)  # name -> {argument counts}
    asm_funcs: set = field(default_factory=set)    # functions with asm in them
    asm_tokens: set = field(default_factory=set)   # lower-case asm tokens
    body_names: dict = field(default_factory=dict) # function -> names used


def _ident(node) -> str:
    n = node.name
    return n.text if hasattr(n, "text") else str(n)


def scan_program(unit, name_of) -> ProgramScan:
    """One walk over the whole translation unit."""
    scan = ProgramScan()
    for item in unit.items or []:
        cur = None
        if isinstance(item, ast.FunctionDef):
            cur = name_of(item)
            if cur is not None:
                scan.body_names.setdefault(cur, set())
        stack = [item]
        seen: set[int] = set()
        while stack:
            node = stack.pop()
            if not _dcfast.is_dataclass(node) or isinstance(node, type):
                continue
            if id(node) in seen:
                continue
            seen.add(id(node))
            if isinstance(node, ast.Identifier):
                nm = _ident(node)
                scan.value_refs.add(nm)
                if cur is not None:
                    scan.body_names[cur].add(nm)
                continue
            if isinstance(node, (ast.Call, ast.CallNoArgs)):
                fn = node.func
                args = getattr(node, "args", None) or []
                if isinstance(fn, ast.Identifier):
                    nm = _ident(fn)
                    scan.call_args.setdefault(nm, set()).add(len(args))
                    if cur is not None:
                        scan.body_names[cur].add(nm)
                    stack.extend(a for a in args)
                    continue
            if isinstance(node, (ast.AsmDeclaration, ast.AsmStmt)):
                tmpl = getattr(node, "template", None) or []
                text = "".join(getattr(getattr(s, "value", None), "text", "")
                               for s in tmpl)
                scan.asm_tokens.update(t.lower() for t in _TOKEN.findall(text))
                if cur is not None:
                    scan.asm_funcs.add(cur)
            stack.extend(_children(node))
    return scan


# ---------------------------------------------------------------------------
# What a run of emitted code does to registers
# ---------------------------------------------------------------------------

def run_effects(lines):
    """(writes, upward_reads) of a run of assembly lines, or None when the
    run is not straight-line register code: a label, a jump, a call, a
    push or pop, a line that is not understood."""
    if _effect is None:
        return None
    written: set[str] = set()
    exposed: set[str] = set()
    for line in lines:
        if not line:
            continue
        if line[0] not in " \t":
            return None                      # a label or directive
        text = line.split(";", 1)[0].strip()
        if not text:
            continue
        parts = text.split(None, 1)
        try:
            eff = _effect(parts[0].lower(), parts[1] if len(parts) > 1 else "")
        except Exception:
            return None
        if eff.flow != "next" or eff.stack != 0 or eff.size is None:
            return None
        exposed |= (set(eff.reads) - written)
        written |= set(eff.writes)
    return written, exposed


# ---------------------------------------------------------------------------
# Keeping register parameters in registers
# ---------------------------------------------------------------------------
#
# A callee stores each register parameter that its body names into a slot on
# entry.  The body then loads the slot where it needs the value.  While no
# label, call or write intervenes, the register still holds the value, so
# the load is a waste (13 T-states for the A or HL form, 19 for (IX+d)).
# forward_slots() finds those loads in the code of one function, replaces
# each by a register move or deletes it, and deletes the stores of a slot
# that nothing reads any more.  It touches only slots of the function's own
# parameters, which nothing else can reach unless the code takes the address
# of the slot; any text that names a slot in a form the pass does not
# recognise leaves the slot alone.

_R8 = ("a", "b", "c", "d", "e", "h", "l")
_PAIRS = {"hl": ("l", "h"), "de": ("e", "d"), "bc": ("c", "b")}
_AUTO_MEM = re.compile(r"^\(\?\?AUTO\+(\d+)\)$", re.IGNORECASE)
_AUTO_ANY = re.compile(r"\?\?AUTO(?:\+(\d+))?", re.IGNORECASE)
_IX_MEM = re.compile(r"^\(IX([+-]\d+)\)$", re.IGNORECASE)
_IX_ANY = re.compile(r"\bIX\b", re.IGNORECASE)
_ALU = ("add", "adc", "sub", "sbc", "and", "or", "xor", "cp")


def _ins(line: str):
    """(mnemonic, [operands]) of an instruction line, else None."""
    if not line or line[0] not in " \t":
        return None
    text = line.split(";", 1)[0].strip()
    if not text:
        return None
    parts = text.split(None, 1)
    ops = [o.strip() for o in parts[1].split(",")] if len(parts) > 1 else []
    return parts[0].lower(), ops


def _slot(op: str):
    """The slot byte an operand names: ("a", n) for (??AUTO+n), ("x", d)
    for (IX+d); else None."""
    m = _AUTO_MEM.match(op)
    if m:
        return ("a", int(m.group(1)))
    m = _IX_MEM.match(op)
    if m:
        return ("x", int(m.group(1)))
    return None


_AUTO_IMM = re.compile(r"^\?\?AUTO\+(\d+)$", re.IGNORECASE)


def _next_ins(region, i):
    """Index of the next instruction line after i, or None when a label
    or the end comes first."""
    j = i + 1
    while j < len(region):
        ln = region[j]
        if _ins(ln) is not None:
            return j
        if ln and ln[0] not in " \t" and not ln.startswith(";"):
            return None
        j += 1
    return len(region)          # the end of the function body


def _hl_dead_after(region, i, end_dead) -> bool:
    """HL is written before it is read, straight on from line i.  At the
    end of the body HL is dead for a function that returns no value."""
    j = i
    written: set = set()
    for _ in range(40):
        j = _next_ins(region, j)
        if j is None:
            return False
        if j >= len(region):
            return end_dead
        mn, ops = _ins(region[j])
        try:
            eff = _effect(mn, ",".join(ops))
        except Exception:
            return False
        if (eff.reads & {"h", "l"}) - written:
            return False
        if eff.flow != "next" or eff.stack != 0:
            return False
        written |= set(eff.writes)
        if {"h", "l"} <= written:
            return True
    return False


def _slot_via_hl(region, i, end_dead):
    """For ``ld HL,??AUTO+n`` at line i followed by an ALU instruction on
    (HL) after which HL is dead: (key, index of the ALU line), else None."""
    mn, ops = _ins(region[i])
    if mn != "ld" or len(ops) != 2 or ops[0].lower() != "hl":
        return None
    m = _AUTO_IMM.match(ops[1])
    if not m:
        return None
    j = _next_ins(region, i)
    if j is None or j >= len(region):
        return None
    mn2, ops2 = _ins(region[j])
    if mn2 not in _ALU or not ops2 or ops2[-1].lower() != "(hl)":
        return None
    if not _hl_dead_after(region, j, end_dead):
        return None
    return ("a", int(m.group(1))), j


_IX_OPERAND = re.compile(r"\(IX[+-]\d+\)", re.IGNORECASE)


def forward_slots(lines: list, start: int, slots: dict, init: dict,
                  end_dead: bool = False) -> int:
    """Rewrite ``lines[start:]`` in place.

    ``slots`` maps a slot byte key (("a", n) or ("x", d)) to the name of
    the parameter that owns it; keys not in it are never touched.  ``init``
    maps a key to the register that holds its value at ``start``.  Returns
    the number of lines removed or changed.  ``end_dead``: HL carries no
    value at the end of the body (the function returns void)."""
    if _effect is None or not slots:
        return 0
    region = lines[start:]
    if any("__UC80_ASM" in ln for ln in region):
        return 0
    ixs = {k for k in slots if k[0] == "x"}

    # ---- which parameters does the text use in a way we do not follow?
    dead: set = set()                   # owners that are left alone
    for idx, ln in enumerate(region):
        ins = _ins(ln)
        if ins is None:
            continue
        mn, ops = ins
        text = ln.split(";", 1)[0]
        known_keys = set()
        if mn == "ld" and len(ops) == 2:
            for a, b in ((ops[0], ops[1]), (ops[1], ops[0])):
                k = _slot(a)
                if k is None:
                    continue
                if b.lower() in _R8 or (k[0] == "a" and b.lower() in _PAIRS):
                    known_keys.add(k)
                    if b.lower() in _PAIRS:
                        known_keys.add(("a", k[1] + 1))
        elif mn in _ALU and ops and _slot(ops[-1]) is not None \
                and _slot(ops[-1])[0] == "x":
            known_keys.add(_slot(ops[-1]))
        via = _slot_via_hl(region, idx, end_dead) if mn == "ld" else None
        if via is not None:
            known_keys.add(via[0])
        for m in _AUTO_ANY.finditer(text):
            n = int(m.group(1) or 0)
            k = ("a", n)
            if k in slots and k not in known_keys:
                dead.add(slots[k])
        if "ix" in text.lower():
            rest = _IX_OPERAND.sub("", text)
            if _IX_ANY.search(rest):
                dead.update(slots[k] for k in ixs)     # IX used as a value
            for m in _IX_OPERAND.finditer(text):
                k = ("x", int(m.group(0)[3:-1]))
                if k in slots and k not in known_keys:
                    dead.add(slots[k])
    tracked = {k: v for k, v in slots.items() if v not in dead}
    if not tracked:
        return 0

    holds: dict = {k: set() for k in tracked}
    for k, r in init.items():
        if k in holds:
            holds[k].add(r)

    def drop_reg(r):
        for st in holds.values():
            st.discard(r)

    def clear_all():
        for st in holds.values():
            st.clear()

    out = list(region)
    changed = 0
    skip: set = set()
    reads: dict = {}              # owner -> reads left
    stores: dict = {}             # owner -> [(index, key)] of its stores
    for i, ln in enumerate(region):
        ins = _ins(ln)
        if ins is None:
            if ln and ln[0] not in " \t" and not ln.startswith(";"):
                clear_all()                  # a label: another path joins
            continue
        mn, ops = ins
        if i in skip:
            continue
        via = _slot_via_hl(region, i, end_dead) if mn == "ld" else None
        if via is not None and via[0] in tracked:
            key, j = via
            owner = tracked[key]
            mn2, ops2 = _ins(region[j])
            if holds[key]:
                r = sorted(holds[key])[0]
                out[i] = None
                out[j] = "\t" + mn2 + "\t" + ",".join(ops2[:-1] + [r.upper()])
                changed += 2
            else:
                reads[owner] = reads.get(owner, 0) + 1
                for w in ("h", "l"):
                    drop_reg(w)
            eff2 = _effect(mn2, ",".join(ops2))
            for w in eff2.writes:
                if w in _R8:
                    drop_reg(w)
            skip.add(j)
            continue
        if mn == "ld" and len(ops) == 2:
            dst, src = ops[0].lower(), ops[1]
            ks = _slot(src)
            if ks is not None and ks in tracked:
                owner = tracked[ks]
                if dst in _PAIRS:                      # ld HL,(??AUTO+n)
                    lo, hi = _PAIRS[dst]
                    k0, k1 = ks, ("a", ks[1] + 1)
                    a = next(iter(sorted(holds[k0])), None)
                    b = next(iter(sorted(holds[k1])), None)
                    if lo in holds[k0] and hi in holds[k1]:
                        out[i] = None
                        changed += 1
                    elif a and b and b != lo:
                        out[i] = (f"\tld\t{lo.upper()},{a.upper()}\n"
                                  f"\tld\t{hi.upper()},{b.upper()}")
                        changed += 1
                        drop_reg(lo)
                        drop_reg(hi)
                        holds[k0].add(lo)
                        holds[k1].add(hi)
                    else:
                        reads[owner] = reads.get(owner, 0) + 1
                        drop_reg(lo)
                        drop_reg(hi)
                        holds[k0].add(lo)
                        holds[k1].add(hi)
                    continue
                if dst in _R8:
                    if dst in holds[ks]:
                        out[i] = None
                        changed += 1
                    elif holds[ks]:
                        r = sorted(holds[ks])[0]
                        out[i] = f"\tld\t{dst.upper()},{r.upper()}"
                        changed += 1
                        drop_reg(dst)
                        holds[ks].add(dst)
                    else:
                        reads[owner] = reads.get(owner, 0) + 1
                        drop_reg(dst)
                        holds[ks].add(dst)
                    continue
            kd = _slot(ops[0])
            if kd is not None and kd in tracked:
                sl = src.lower()
                owner = tracked[kd]
                if sl in _R8:
                    holds[kd].clear()
                    holds[kd].add(sl)
                    stores.setdefault(owner, []).append((i, kd))
                    continue
                if sl in _PAIRS and kd[0] == "a":
                    lo, hi = _PAIRS[sl]
                    k1 = ("a", kd[1] + 1)
                    holds[kd].clear()
                    holds[k1].clear()
                    holds[kd].add(lo)
                    holds[k1].add(hi)
                    stores.setdefault(owner, []).append((i, kd))
                    continue
        try:
            eff = _effect(mn, ",".join(ops))
        except Exception:
            clear_all()
            continue
        if mn in _ALU and ops:
            k = _slot(ops[-1])
            if k is not None and k in tracked:
                owner = tracked[k]
                if holds[k]:
                    r = sorted(holds[k])[0]
                    out[i] = "\t" + mn + "\t" + ",".join(ops[:-1] + [r.upper()])
                    changed += 1
                else:
                    reads[owner] = reads.get(owner, 0) + 1
        if eff.flow in ("call", "jump", "return", "stop", "data"):
            clear_all()
            continue
        if eff.swap_de_hl:
            for r in ("d", "e", "h", "l"):
                drop_reg(r)
        for w in eff.writes:
            if w in _R8:
                drop_reg(w)
    # The stores of a slot that nothing reads any more.  An IX slot is
    # filled by a push in the prologue, so only ??AUTO stores can go.
    for owner, items in stores.items():
        if reads.get(owner, 0) == 0:
            for i, k in items:
                if k[0] == "a" and out[i] is not None:
                    out[i] = None
                    changed += 1
    flat: list = []
    for ln in out:
        if ln is not None:
            flat.extend(ln.split("\n"))
    lines[start:] = flat
    return changed


# ---------------------------------------------------------------------------
# A register parameter across a call: push and pop instead of a slot
# ---------------------------------------------------------------------------
#
# After forward_slots, a parameter slot in ??AUTO can still have a store
# (ld (??AUTO+n),HL) and one load (ld HL,(??AUTO+n)) that come after a call.
# When only straight-line code lies between the two (no label, no jump, no
# return) and the stack is level at the load, the store can be a push and
# the load a pop.  That saves 6 bytes and about 20 T-states for a pair
# register in DE, and 4 bytes and 12 T-states for HL.

_WORD_STORE = re.compile(
    r"^\t(?:ld|LD)\t\(\?\?AUTO\+(\d+)\),(HL|DE)\s*(?:;.*)?$", re.IGNORECASE)
_WORD_LOAD = re.compile(
    r"^\t(?:ld|LD)\t(HL|DE|BC),\(\?\?AUTO\+(\d+)\)\s*(?:;.*)?$", re.IGNORECASE)


# Runtime routines with a net stack effect.  A call to one of these between
# the store and the load of a slot moves SP, so the pop would read the wrong
# word.
_STACK_RUNTIME = {"__save_tmp64", "__restore_tmp64", "__push64_acc"}


def push_pop_slots(lines: list, start: int, offsets) -> int:
    """Turn the store and the single load of a parameter slot into push and
    pop where that is safe.  ``offsets`` holds the ??AUTO offsets of the
    word slots of the register parameters.  Rewrites ``lines[start:]``
    in place and returns the number of slots changed."""
    if _effect is None or not offsets:
        return 0
    region = lines[start:]
    if any("__UC80_ASM" in ln for ln in region):
        return 0
    # Stores that stand side by side: the pop must take the word that was
    # pushed last first, so push the one that is loaded last first.
    load_at = {}
    for i, ln in enumerate(region):
        m = _WORD_LOAD.match(ln or "")
        if m and int(m.group(2)) in offsets:
            load_at.setdefault(int(m.group(2)), i)
    i = 0
    while i < len(region):
        j = i
        while (j < len(region) and _WORD_STORE.match(region[j] or "")
               and int(_WORD_STORE.match(region[j]).group(1)) in load_at):
            j += 1
        if j - i > 1:
            region[i:j] = sorted(
                region[i:j], reverse=True,
                key=lambda t: load_at[int(_WORD_STORE.match(t).group(1))])
        i = max(j, i + 1)
    changed = 0
    for n in sorted(offsets):
        refs = []
        ok = True
        for i, ln in enumerate(region):
            if ln is None:
                continue
            hit = False
            for m in _AUTO_ANY.finditer(ln.split(";", 1)[0]):
                if int(m.group(1) or 0) in (n, n + 1):
                    hit = True
            if hit:
                refs.append(i)
        if len(refs) != 2:
            continue
        s, l = refs
        ms, ml = _WORD_STORE.match(region[s]), _WORD_LOAD.match(region[l])
        if not (ms and ml and int(ms.group(1)) == n and int(ml.group(2)) == n):
            continue
        depth = 0
        for k in range(s + 1, l):
            ln = region[k]
            if ln is None:
                continue
            if not ln or ln[0] not in " \t":
                ok = False                       # a label or a directive
                break
            text = ln.split(";", 1)[0].strip()
            if not text:
                continue
            parts = text.split(None, 1)
            mn = parts[0].lower()
            opnd = parts[1] if len(parts) > 1 else ""
            if mn == "push":
                depth += 2
                continue
            if mn == "pop":
                depth -= 2
                if depth < 0:
                    ok = False
                    break
                continue
            if mn == "call" and "," not in opnd:
                if opnd.strip().lower() in _STACK_RUNTIME:
                    ok = False                   # leaves or takes bytes
                    break
                continue
            if re.search(r"\bsp\b", opnd, re.IGNORECASE):
                ok = False
                break
            try:
                eff = _effect(mn, opnd)
            except Exception:
                ok = False
                break
            if eff.flow != "next" or eff.stack != 0:
                ok = False
                break
        if not ok or depth != 0:
            continue
        region[s] = f"\tpush\t{ms.group(2).upper()}"
        region[l] = f"\tpop\t{ml.group(1).upper()}"
        changed += 1
    if changed:
        lines[start:] = region
    return changed


# ---------------------------------------------------------------------------
# A byte parameter in a spare register (uc80 --no-param-reg turns it off)
# ---------------------------------------------------------------------------
#
# After forward_slots, a byte parameter (A, or E for the second parameter)
# can still be stored in a slot and loaded back after a label or a write of
# the register it came in.  In a function that holds no call, no register
# stays alive across anything but the function's own instructions.  A
# register that no instruction of the function names then keeps the value
# from entry to return.  The store becomes ``ld R,A`` (13 T-states less)
# and each load ``ld X,R`` (9 T-states less).  A parameter that arrives in
# E and that the function never writes needs no store at all: it stays in E.

_FREE_ORDER = ("d", "c", "b", "e")


def keep_params_in_regs(lines: list, start: int, slots: dict, init: dict,
                        incoming: set) -> set:
    """Rewrite ``lines[start:]`` in place.  ``slots`` and ``init`` are the
    arguments of forward_slots; ``incoming`` holds the registers in which
    a caller passes the parameters (a, e, d, h, l).  Returns the names of
    the parameters that no longer have a slot."""
    if _effect is None or not slots:
        return set()
    region = lines[start:]
    if any("__UC80_ASM" in ln for ln in region):
        return set()
    labels = {ln.split(":", 1)[0].strip() for ln in region
              if ln and ln[0] not in " \t;" and ":" in ln}
    mentioned: set = set()
    written: set = set()
    for ln in region:
        ins = _ins(ln)
        if ins is None:
            continue
        mn, ops = ins
        if mn in ("call", "rst", "exx", "halt") or mn == "ex" and (
                ops and ops[0].lower().startswith("af")):
            return set()
        try:
            eff = _effect(mn, ",".join(ops))
        except Exception:
            return set()
        if eff.flow in ("jump", "branch") and mn in ("jp", "jr", "djnz"):
            if not ops or ops[-1] not in labels:
                return set()            # a jump out of the function
        if eff.flow in ("stop", "call", "data"):
            return set()
        names = set(eff.reads) | set(eff.writes)
        if eff.swap_de_hl:
            names |= {"d", "e", "h", "l"}
        mentioned |= names
        written |= set(eff.writes)
        if eff.swap_de_hl:
            written |= {"d", "e", "h", "l"}
    owners: dict = {}
    for key, name in slots.items():
        owners.setdefault(name, []).append(key)
    taken = set(incoming)
    changed_owners: set = set()
    for name, keys in sorted(owners.items()):
        keys.sort()
        if any(k[0] != "a" for k in keys):
            continue
        first = keys[0]
        in_reg = init.get(first)
        if in_reg not in ("a", "e") or len(keys) > 2:
            continue
        if len(keys) == 2 and (in_reg != "e" or init.get(keys[1]) != "d"
                               or keys[1][1] != first[1] + 1):
            continue
        n = first[1]
        # every mention of the slot must be a byte load or store (or the
        # entry store of the word DE); the high byte of a word is junk and
        # must not be read
        loads, stores, bad = [], [], False
        for i, ln in enumerate(region):
            ins = _ins(ln)
            if ins is None:
                continue
            text = ln.split(";", 1)[0]
            offs = [int(m.group(1) or 0) for m in _AUTO_ANY.finditer(text)]
            if not any(o in (n, n + 1) for o in offs):
                continue
            mn, ops = ins
            if mn == "ld" and len(ops) == 2:
                sl = [_slot(o) for o in ops]
                if sl[1] == ("a", n) and ops[0].lower() in _R8:
                    loads.append(i)
                    continue
                if sl[0] == ("a", n) and ops[1].lower() in _R8:
                    stores.append((i, ops[1].lower()))
                    continue
                if (sl[0] == ("a", n) and ops[1].lower() == "de"
                        and in_reg == "e" and len(keys) == 2):
                    stores.append((i, "e"))
                    continue
            bad = True
            break
        if bad or not loads:
            continue
        # a register for the value
        r = None
        if in_reg == "e" and "e" not in written:
            r = "e"
        elif in_reg == "a" and "a" not in written:
            r = "a"
        else:
            for c in _FREE_ORDER:
                if c not in mentioned and c not in taken:
                    r = c
                    break
        if r is None:
            continue
        # a store of the entry register itself is dropped; every other
        # store becomes a move
        if r in ("a", "e") and any(src != r for _, src in stores):
            continue            # a write of the entry register
        for i, src in stores:
            region[i] = None if src == r else f"\tld\t{r.upper()},{src.upper()}"
        for i in loads:
            mn, ops = _ins(region[i])
            region[i] = (None if ops[0].lower() == r
                         else f"\tld\t{ops[0].upper()},{r.upper()}")
        taken.add(r)
        mentioned.add(r)
        if r not in incoming:
            written.add(r)
        changed_owners.add(name)
    if not changed_owners:
        return set()
    lines[start:] = [ln for ln in region if ln is not None]
    return changed_owners
