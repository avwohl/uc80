"""``-g``: the variables of a program (phase 8 of the debugger plan).

The code generator notes each global, static, parameter and local variable
here while it hands out the storage.  Nothing in this module changes the
text of the code.  ``varinfo.add_vars`` writes the ``;#var`` and ``;#type``
markers (docs/debug_info.md of um80_and_friends) after the last text pass,
because only then the final text says where each variable lives.

What the generator knows when it hands out a slot:

- the name, the C type (typedef names kept when the declaration has them),
  the kind (global, static, local, param), the block that scopes the name,
  and the line of the declaration
- the slot: an IX displacement, an offset into the shared area ``??AUTO``,
  or the label of a static object

What only the final text knows: whether the function kept its IX frame,
whether the slot is still used, and where a register local lives
(``varinfo.py``).
"""

from __future__ import annotations

import functools
import os
import sys

from . import srcmap

_BUILTIN = {
    "void", "bool", "_Bool", "char", "signed char", "unsigned char",
    "short", "unsigned short", "int", "unsigned int", "long",
    "unsigned long", "long long", "unsigned long long", "float", "double",
    "long double",
}


def base_name(name: str, is_signed) -> str:
    """The name of a base type in the ``;#type`` grammar (``int``, ``uchar``,
    ``ullong``, ...), without the size."""
    words = (name or "int").split()
    unsigned = "unsigned" in words or is_signed is False
    explicit = "signed" in words or is_signed is True
    core = " ".join(w for w in words if w not in ("signed", "unsigned"))
    core = core or "int"
    if core in ("_Bool", "bool"):
        return "bool"
    if core == "void":
        return "void"
    if core == "char":
        return "uchar" if unsigned else ("schar" if explicit else "char")
    if core in ("float", "double"):
        return core
    if core == "long double":
        return "ldouble"
    if core == "long long":
        core = "llong"
    return ("u" + core) if unsigned else core


def _safe(fn):
    """A fault in the variable records must not stop a build: the records are
    a debugging aid.  The first fault writes a warning, and the records of
    the program are then incomplete.  UC80_DEBUG_VARS_STRICT=1 raises."""
    @functools.wraps(fn)
    def run(self, *args, **kw):
        try:
            return fn(self, *args, **kw)
        except Exception as e:           # noqa: BLE001
            if os.environ.get("UC80_DEBUG_VARS_STRICT"):
                raise
            if not getattr(self, "_dbgv_warned", False):
                self._dbgv_warned = True
                print(f"uc80: warning: variable records for -g are incomplete "
                      f"({type(e).__name__}: {e})", file=sys.stderr)
    return run


class DebugVarsMixin:
    """Collects the variable records of ``-g`` (see the module text)."""

    # -- state ---------------------------------------------------------

    def _dbgv_reset(self) -> None:
        self._dbgv_funcs: dict[str, list] = {}   # label -> variables
        self._dbgv_globals: list = []
        self._dbgv_types: dict[str, dict] = {}
        self._dbgv_anon: dict[int, str] = {}
        self._dbgv_busy: set = set()
        self._dbgv_keep: list = []
        self._dbgv_enums: dict[str, list] = {}
        self._dbgv_cur: list | None = None
        self._dbgv_blocks: list = []
        self._dbgv_nrl = 0
        self._dbgv_decl = None
        self._dbgv_gdecl: dict = {}

    # -- the scope of a name --------------------------------------------

    def _dbgv_range(self, node):
        """(path, first line, last line) of the source lines of ``node``, or
        None."""
        pos = getattr(node, "pos", None)
        fid = getattr(self, "_dbg_fid", 0)
        if pos is None or not fid:
            return None
        first = getattr(pos, "start_line", None)
        last = getattr(pos, "end_line", None) or first
        if not first:
            return None
        a = srcmap.locate_at(fid, first)
        b = srcmap.locate_at(fid, last)
        if a is None or b is None or a[0] != b[0]:
            return None
        return (a[0], a[1], max(a[1], b[1]))

    @_safe
    def dbgv_block_push(self, node) -> None:
        if not self.debug_info:
            return
        self._dbgv_blocks.append(self._dbgv_range(node))

    def dbgv_block_pop(self) -> None:
        if self.debug_info and self._dbgv_blocks:
            self._dbgv_blocks.pop()

    def dbgv_decl(self, decl) -> None:
        """The declaration whose variables ``dbgv_local`` is about to get."""
        if self.debug_info:
            self._dbgv_decl = decl

    # -- types -----------------------------------------------------------

    def _dbgv_anon_tag(self, t) -> str:
        key = id(t)
        tag = self._dbgv_anon.get(key)
        if tag is None:
            tag = "." + str(len(self._dbgv_anon) + 1)
            self._dbgv_anon[key] = tag
            self._dbgv_keep.append(t)
        return tag

    @_safe
    def note_enum(self, tag, entries) -> None:
        """An enumeration was registered (``_register_enum_entries``)."""
        if self.debug_info and tag:
            self._dbgv_enums[tag] = list(entries)

    def dbgv_type(self, t) -> str:
        """The ``;#type`` grammar text of type ``t`` (a ResolvedType or a
        legacy type).  Definitions of named types go into the table."""
        try:
            return self._dbgv_describe(t)
        except Exception:
            return "raw:0"

    def _dbgv_size(self, t) -> int:
        try:
            return int(self._type_size(t))
        except Exception:
            return 0

    def _dbgv_describe(self, t) -> str:
        from . import codegen as cg
        lt = cg.lt
        if t is None:
            return "void"
        if cg._is_resolved_type(t):
            kind = t.kind
            if kind == "typedef":
                return self._dbgv_typedef(t.name)
            if kind == "basic":
                if t.name and t.name not in _BUILTIN:
                    return self._dbgv_typedef(t.name)
                b = base_name(t.name, t.is_signed)
                return b if b == "void" else f"{b}:{self._dbgv_size(t)}"
            if kind == "pointer":
                return "*" + self._dbgv_describe(t.pointee)
            if kind == "array":
                return self._dbgv_array(t, t.element)
            if kind == "function":
                return "func(" + self._dbgv_describe(t.return_type) + ")"
            if kind == "enum":
                return self._dbgv_enum(t.name, t)
            t = cg._to_legacy(t)
        if isinstance(t, lt.BasicType):
            if t.name and t.name not in _BUILTIN:
                return self._dbgv_typedef(t.name)
            b = base_name(t.name, t.is_signed)
            return b if b == "void" else f"{b}:{self._dbgv_size(t)}"
        if isinstance(t, lt.PointerType):
            return "*" + self._dbgv_describe(t.base_type)
        if isinstance(t, lt.ArrayType):
            return self._dbgv_array(t, t.base_type)
        if isinstance(t, lt.FunctionType):
            return "func(" + self._dbgv_describe(t.return_type) + ")"
        if isinstance(t, lt.StructType):
            return self._dbgv_struct(t)
        if isinstance(t, lt.EnumType):
            return self._dbgv_enum(t.name, t)
        return f"raw:{self._dbgv_size(t)}"

    def _dbgv_array(self, t, element) -> str:
        total = self._dbgv_size(t)
        each = self._dbgv_size(element)
        n = total // each if each and total else None
        inner = self._dbgv_describe(element)
        return f"[{n}]" + inner if n else "[]" + inner

    def _dbgv_typedef(self, name) -> str:
        full = "typedef:" + name
        if full in self._dbgv_types or full in self._dbgv_busy:
            return full
        from . import codegen as cg
        target = self.ctx.typedefs.get(name)
        if target is None:
            reg = cg._ACTIVE_TYPEDEFS
            target = reg.get(name) if reg else None
        if target is None:
            return "raw:0"
        self._dbgv_busy.add(full)
        try:
            text = self._dbgv_describe(target)
        finally:
            self._dbgv_busy.discard(full)
        self._dbgv_types[full] = {"kind": "typedef", "size": None,
                                  "target": text}
        return full

    def _dbgv_enum(self, tag, t) -> str:
        size = self._dbgv_size(t) or self.type_config.int_size
        entries = self._dbgv_enums.get(tag) if tag else None
        if not tag or not entries:
            return f"int:{size}"
        full = "enum:" + tag
        if full not in self._dbgv_types:
            self._dbgv_types[full] = {"kind": "enum", "size": size,
                                      "values": [[n, v] for n, v in entries]}
        return full

    def _dbgv_struct(self, t) -> str:
        from . import codegen as cg
        tag = t.name or self._dbgv_anon_tag(t)
        full = ("union:" if t.is_union else "struct:") + tag
        if full in self._dbgv_types or full in self._dbgv_busy:
            return full
        self._dbgv_busy.add(full)
        try:
            members = []
            for name, mtype, offset in self._get_struct_members(t):
                row = [name, self._dbgv_describe(mtype), offset]
                bf = self.ctx.bitfield_info.get((tag, name))
                if bf is not None:
                    row += [bf.bit_width, bf.bit_offset]
                members.append(row)
            size = self._dbgv_size(t)
        finally:
            self._dbgv_busy.discard(full)
        self._dbgv_types[full] = {"kind": "union" if t.is_union else "struct",
                                  "size": size, "members": members}
        return full

    # -- functions --------------------------------------------------------

    @_safe
    def dbgv_begin(self, func) -> None:
        """A function starts; its parameters and locals follow."""
        if not self.debug_info:
            return
        self._dbgv_cur = []
        self._dbgv_blocks = [self._dbgv_range(func.body)]
        self._dbgv_nrl = len(getattr(self, "_rl_vars", ()))
        self._dbgv_decl = None

    def _dbgv_candidate(self) -> bool:
        """True when ``_rl_note`` took the variable it was just given."""
        n = len(getattr(self, "_rl_vars", ()))
        took = n > self._dbgv_nrl
        self._dbgv_nrl = n
        return took

    def _dbgv_add(self, name, raw_type, kind, sym, cand, where=None) -> None:
        if self._dbgv_cur is None or not name:
            return
        if sym.uses_shared_storage:
            loc = ("auto", sym.shared_offset)
            key = ("a", sym.shared_offset)
        else:
            loc = ("ix", sym.offset)
            key = ("x", sym.offset)
        size = self._dbgv_size(sym.sym_type)
        blocks = self._dbgv_blocks
        self._dbgv_cur.append({
            "name": name, "type": self.dbgv_type(raw_type), "kind": kind,
            "loc": loc, "size": size, "key": key if cand else None,
            "scope": blocks[-1] if blocks else None,
            "decl": where, "reg": None})

    @_safe
    def dbgv_param(self, name, raw_type, sym, cand, node=None) -> None:
        """A parameter has its slot (``sym``)."""
        if not self.debug_info:
            return
        cand = self._dbgv_candidate() if cand else False
        where = None
        if node is not None:
            try:
                where = self._dbg_where(node)
            except Exception:
                where = None
        self._dbgv_add(name, raw_type, "param", sym, cand, where)

    @_safe
    def dbgv_local(self, name, raw_type, sym) -> None:
        """A local variable has its slot (``sym``), after ``_rl_note``."""
        if not self.debug_info:
            return
        cand = self._dbgv_candidate()
        where = None
        decl = self._dbgv_decl
        if decl is not None:
            try:
                where = self._dbg_where(decl.declarators, decl)
            except Exception:
                where = None
        self._dbgv_add(name, raw_type, "local", sym, cand, where)

    @_safe
    def dbgv_static(self, name, var_type, label) -> None:
        """A static local variable with the label ``label``."""
        if not self.debug_info or self._dbgv_cur is None:
            return
        blocks = self._dbgv_blocks
        where = None
        decl = self._dbgv_decl
        if decl is not None:
            try:
                where = self._dbg_where(decl.declarators, decl)
            except Exception:
                where = None
        self._dbgv_cur.append({
            "name": name, "type": self.dbgv_type(var_type), "kind": "static",
            "loc": ("label", label), "size": self._dbgv_size(var_type),
            "key": None, "scope": blocks[-1] if blocks else None,
            "decl": where, "reg": None})

    @_safe
    def dbgv_end(self, label: str, assigned: dict) -> None:
        """The function ``label`` is complete.  ``assigned`` maps a slot
        key to the register that ``reglocals`` gave the variable."""
        if not self.debug_info or self._dbgv_cur is None:
            return
        for v in self._dbgv_cur:
            if v["key"] is not None:
                v["reg"] = assigned.get(v["key"])
        self._dbgv_funcs[label] = self._dbgv_cur
        self._dbgv_cur = None

    # -- file scope --------------------------------------------------------

    @_safe
    def dbgv_note_global_decl(self, name, node) -> None:
        if not self.debug_info:
            return
        try:
            self._dbgv_gdecl.setdefault(name, self._dbg_where(
                getattr(node, "declarators", None), node))
        except Exception:
            pass

    @_safe
    def dbgv_globals(self, items, raw_types) -> None:
        """``items``: [(name, label, legacy type, static?)]; ``raw_types``
        maps a name to the type as the declaration wrote it."""
        if not self.debug_info:
            return
        for name, label, vtype, static in items:
            raw = raw_types.get(name, vtype)
            self._dbgv_globals.append({
                "name": name, "type": self.dbgv_type(raw),
                "kind": "static" if static else "global",
                "loc": ("label", label),
                "size": self._dbgv_size(vtype), "key": None, "scope": None,
                "decl": self._dbgv_gdecl.get(name), "reg": None})

    def dbg_var_info(self) -> dict:
        """What ``varinfo.add_vars`` writes."""
        return {"funcs": self._dbgv_funcs, "globals": self._dbgv_globals,
                "types": self._dbgv_types}
