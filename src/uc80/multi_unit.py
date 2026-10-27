"""Several C files on one command line: keep their ``static`` names apart.

``uc80 a.c b.c`` parses each file and merges the declarations into one
translation unit.  A ``static`` function or variable has internal linkage,
so ``a.c`` and ``b.c`` may each define their own ``helper``.  After the
merge the two definitions would be one name, and every call would reach the
same function.  ``uniquify_statics`` gives each such file-scope static a
name that is private to its file (``helper__s0``, ``helper__s1``) before the
merge.  A static whose name no other file uses keeps its name.

Typedef names and struct, union and enum tags are also file-wide in the
merged unit, so two files that give the same typedef or tag different
definitions are told apart by ``uniquify_types`` (``T__t0``, ``T__t1``).
Files that define a name identically (the same header) keep the shared name.

A name in a block (a parameter, a local, a ``for`` declaration) hides the
file-scope name, so the pass follows block scope and renames only the
references that reach the file-scope static.
"""

from __future__ import annotations

import dataclasses

from uc_core import ast


def _declarator_holder(decl):
    """The innermost ``Declarator`` node (the one that holds the name)."""
    seen = 0
    while decl is not None and seen < 64:
        seen += 1
        if isinstance(decl, ast.Declarator):
            return decl
        decl = getattr(decl, "inner", None)
    return None


def _declarator_token(decl):
    """The name token of a declarator, through pointer/array/function parts."""
    holder = _declarator_holder(decl)
    return holder.name if holder is not None else None


def _storage(specs) -> set[str]:
    return {s.kw.text for s in specs or ()
            if isinstance(s, ast.StorageClass)}


def _init_declarator(d):
    """(declarator, initialiser or None) of one entry of a Declaration."""
    return getattr(d, "declarator", None), getattr(d, "init", None)


def _file_scope_names(unit):
    """(all file-scope names, names with internal linkage) of one unit."""
    every: set[str] = set()
    statics: set[str] = set()
    for item in unit.items:
        if isinstance(item, ast.FunctionDef):
            tok = _declarator_token(item.declarator)
            if tok is not None:
                every.add(tok.text)
                if "static" in _storage(item.decl_specs):
                    statics.add(tok.text)
        elif isinstance(item, ast.Declaration):
            sc = _storage(item.decl_specs)
            if "typedef" in sc:
                continue
            for d in item.declarators or ():
                tok = _declarator_token(_init_declarator(d)[0])
                if tok is None:
                    continue
                every.add(tok.text)
                if "static" in sc:
                    statics.add(tok.text)
    return every, statics


class _Renamer:
    def __init__(self, mapping: dict[str, str]):
        self.mapping = mapping
        self.scopes: list[set[str]] = []

    def hidden(self, name: str) -> bool:
        return any(name in s for s in self.scopes)

    def rename_declarator(self, decl, file_scope: bool) -> None:
        holder = _declarator_holder(decl)
        if holder is None:
            return
        name = holder.name.text
        if name not in self.mapping:
            return
        if file_scope:
            object.__setattr__(holder, "name", dataclasses.replace(
                holder.name, text=self.mapping[name]))
        else:
            self.scopes[-1].add(name)

    def children(self, node):
        for f in dataclasses.fields(node):
            if f.name == "pos":
                continue
            v = getattr(node, f.name, None)
            if isinstance(v, (list, tuple)):
                for x in v:
                    if dataclasses.is_dataclass(x) and not isinstance(x, ast.Token):
                        yield x
            elif dataclasses.is_dataclass(v) and not isinstance(v, ast.Token):
                yield v

    def walk(self, node) -> None:
        if isinstance(node, ast.Identifier):
            name = node.name.text
            if name in self.mapping and not self.hidden(name):
                object.__setattr__(node, "name", dataclasses.replace(
                    node.name, text=self.mapping[name]))
            return
        if isinstance(node, ast.CompoundStmt):
            self.scopes.append(set())
            for item in node.items or ():
                self.walk(item)
            self.scopes.pop()
            return
        if isinstance(node, ast.ForStmt):
            self.scopes.append(set())
            for c in self.children(node):
                self.walk(c)
            self.scopes.pop()
            return
        if isinstance(node, ast.Declaration):
            self.declaration(node, file_scope=not self.scopes)
            return
        for c in self.children(node):
            self.walk(c)

    def declaration(self, node, file_scope: bool) -> None:
        sc = _storage(node.decl_specs)
        for s in node.decl_specs or ():
            self.walk(s)
        for d in node.declarators or ():
            decl, init = _init_declarator(d)
            # The scope of a name starts at its declarator, before the
            # initialiser: ``int x = x;`` reads the new x.
            if not file_scope:
                self.rename_declarator(decl, False)
            elif "static" in sc and "typedef" not in sc:
                self.rename_declarator(decl, True)
            self.walk_declarator(decl)
            if init is not None:
                self.walk(init)

    def walk_declarator(self, decl) -> None:
        # Array sizes may name a constant; parameter names are tokens.
        if decl is None:
            return
        for c in self.children(decl):
            if not isinstance(c, ast.Declarator):
                self.walk(c)

    def function(self, fn) -> None:
        self.scopes.append(set())
        node = fn.declarator
        seen = 0
        while node is not None and seen < 64:
            seen += 1
            if isinstance(node, ast.FnDeclarator):
                for p in node.params or ():
                    tok = _declarator_token(getattr(p, "declarator", None))
                    if tok is not None and tok.text in self.mapping:
                        self.scopes[-1].add(tok.text)
                break
            node = getattr(node, "inner", None)
        for item in fn.body.items or ():
            self.walk(item)
        self.scopes.pop()


def uniquify_statics(units) -> int:
    """Rename conflicting file-scope statics of UNITS in place; return how
    many names were renamed."""
    if len(units) < 2:
        return 0
    names = [_file_scope_names(u) for u in units]
    count = 0
    for i, unit in enumerate(units):
        others: set[str] = set()
        for j, (every, _st) in enumerate(names):
            if j != i:
                others |= every
        mapping = {n: f"{n}__s{i}" for n in names[i][1] if n in others}
        if not mapping:
            continue
        count += len(mapping)
        rn = _Renamer(mapping)
        for item in unit.items:
            if isinstance(item, ast.FunctionDef):
                if "static" in _storage(item.decl_specs):
                    rn.rename_declarator(item.declarator, True)
                rn.function(item)
            else:
                rn.walk(item)
    return count


# ---------------------------------------------------------------- types

def _children(node):
    for f in dataclasses.fields(node):
        if f.name == "pos":
            continue
        v = getattr(node, f.name, None)
        if isinstance(v, (list, tuple)):
            for x in v:
                if dataclasses.is_dataclass(x):
                    yield x
        elif dataclasses.is_dataclass(v):
            yield v


def _iter(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, ast.Token):
            stack.extend(_children(n))


def _shape(node, refs: set) -> tuple:
    """A hashable image of NODE without positions; ``refs`` collects the
    type names (tag or typedef) that NODE mentions."""
    if isinstance(node, ast.Token):
        return ("tok", node.text)
    if isinstance(node, ast.TypedefNameSpec):
        refs.add(("td", node.name.text))
    elif isinstance(node, (ast.StructDef, ast.StructRef, ast.StructEmpty,
                           ast.EnumDef, ast.EnumRef)):
        if node.name is not None:
            refs.add(("tag", node.name.text))
    out = [type(node).__name__]
    for f in dataclasses.fields(node):
        if f.name == "pos":
            continue
        v = getattr(node, f.name, None)
        if isinstance(v, (list, tuple)):
            out.append(tuple(_shape(x, refs) if dataclasses.is_dataclass(x)
                             else repr(x) for x in v))
        elif dataclasses.is_dataclass(v):
            out.append(_shape(v, refs))
        else:
            out.append(repr(v))
    return tuple(out)


def _type_definitions(unit):
    """{("tag"|"td", name): [(shape, refs)]} of the definitions in UNIT."""
    defs: dict[tuple, list] = {}
    for item in unit.items:
        for n in _iter(item):
            if isinstance(n, (ast.StructDef, ast.EnumDef)):
                key = ("tag", n.name.text) if n.name is not None else None
            elif (isinstance(n, ast.Declaration)
                  and "typedef" in _storage(n.decl_specs)):
                for d in n.declarators or ():
                    tok = _declarator_token(_init_declarator(d)[0])
                    if tok is not None:
                        refs: set = set()
                        shape = _shape(n, refs)
                        # The name itself is not a reference.
                        defs.setdefault(("td", tok.text), []).append(
                            (shape, refs - {("td", tok.text)}))
                continue
            else:
                continue
            if key is not None and \
                    (getattr(n, "members", None) or getattr(n, "values", None)):
                refs = set()
                shape = _shape(n, refs)
                defs.setdefault(key, []).append((shape, refs - {key}))
    return defs


def _conflicting_types(units) -> list[set]:
    """Per unit, the type names that need a private name."""
    per_unit = [_type_definitions(u) for u in units]
    names = set()
    for d in per_unit:
        names |= set(d)
    conflicts: set = set()
    changed = True
    while changed:
        changed = False
        for name in names:
            if name in conflicts:
                continue
            owners = [d[name] for d in per_unit if name in d]
            if len(owners) < 2:
                continue
            shapes = {sh for defs in owners for sh, _r in defs}
            reaches = any(r & conflicts for defs in owners for _s, r in defs)
            if len(shapes) > 1 or reaches:
                conflicts.add(name)
                changed = True
    return [{n for n in conflicts if n in d} for d in per_unit]


def uniquify_types(units) -> int:
    """Give a typedef or tag that two files define differently a private
    name in each file; return how many (unit, name) pairs were renamed."""
    if len(units) < 2:
        return 0
    count = 0
    for i, (unit, mine) in enumerate(zip(units, _conflicting_types(units))):
        if not mine:
            continue
        count += len(mine)
        td = {n: f"{n}__t{i}" for k, n in mine if k == "td"}
        tag = {n: f"{n}__t{i}" for k, n in mine if k == "tag"}

        def rename(owner, attr, table):
            tok = getattr(owner, attr, None)
            if tok is not None and tok.text in table:
                object.__setattr__(owner, attr, dataclasses.replace(
                    tok, text=table[tok.text]))

        for item in unit.items:
            for n in _iter(item):
                if isinstance(n, ast.TypedefNameSpec):
                    rename(n, "name", td)
                elif isinstance(n, (ast.StructDef, ast.StructRef,
                                    ast.StructEmpty, ast.EnumDef,
                                    ast.EnumRef)):
                    rename(n, "name", tag)
                elif (isinstance(n, ast.Declaration)
                      and "typedef" in _storage(n.decl_specs)):
                    for d in n.declarators or ():
                        holder = _declarator_holder(_init_declarator(d)[0])
                        if holder is not None:
                            rename(holder, "name", td)
    return count


# ----------------------------------------------------------- enumerators

def _enumerator_definitions(unit) -> dict[str, set]:
    """{enumerator name: {shape of each enum definition that declares it}}.
    The shape is that of the whole enum, because an enumerator's value
    depends on the enumerators before it."""
    defs: dict[str, set] = {}
    for item in unit.items:
        for n in _iter(item):
            if isinstance(n, (ast.EnumDef, ast.EnumAnon)):
                shape = _shape(n, set())
                for v in n.values or ():
                    tok = getattr(v, "name", None)
                    if tok is not None:
                        defs.setdefault(tok.text, set()).add(shape)
    return defs


def uniquify_enumerators(units) -> int:
    """Give an enumerator that two files define with different enums a
    private name in each file (``RED__e0``); return how many (unit, name)
    pairs were renamed.  Constant initialisers of static data, array sizes
    and code then take the values of their own file."""
    if len(units) < 2:
        return 0
    per_unit = [_enumerator_definitions(u) for u in units]
    count = 0
    for i, unit in enumerate(units):
        mapping = {}
        for name, shapes in per_unit[i].items():
            for j, other in enumerate(per_unit):
                if j != i and name in other and (other[name] != shapes):
                    mapping[name] = f"{name}__e{i}"
                    break
        if not mapping:
            continue
        count += len(mapping)
        for item in unit.items:
            for n in _iter(item):
                if isinstance(n, (ast.EnumDef, ast.EnumAnon)):
                    for v in n.values or ():
                        tok = getattr(v, "name", None)
                        if tok is not None and tok.text in mapping:
                            object.__setattr__(v, "name", dataclasses.replace(
                                tok, text=mapping[tok.text]))
        rn = _Renamer(mapping)
        for item in unit.items:
            if isinstance(item, ast.FunctionDef):
                rn.function(item)
            else:
                rn.walk(item)
    return count
