"""A static pointer that holds one address becomes that address.

See docs/family_l.md.  A program often keeps its state in one object and
reaches the object through a pointer:

    static State state_object;
    static State *g = NULL;
    int main(void) { g = &state_object; ... g->field ... }

Every ``g->field`` loads the pointer from memory and adds the offset.  The
pass replaces ``g`` by ``&state_object`` and ``(&state_object)->field`` by
``state_object.field``, a fixed address.

The pointer ``p`` qualifies when

- it is a plain pointer variable of file scope with internal linkage
  (``static``), not volatile, declared once, and no inline assembly, no
  assembly file and no option names it;
- its address is never taken, and it is never changed by ``++``, ``--`` or
  an operator like ``+=``;
- it points to one static object ``obj`` of the same type (the object has
  file scope, or is a ``static`` of a function; the pass moves the second
  kind to file scope under a new name), and one of two cases holds:
  (a) the declaration is ``static T *p = &obj;`` and no statement assigns
  ``p``; or (b) the declaration has no initializer or a null one, and the
  only assignment is the statement ``p = &obj;`` among the first items of
  ``main``, where every item before the assignment is a declaration with
  initializers that make no call and do not mention ``p``;
- ``main`` is the only entry (no interrupt function, no ``--entry``, no
  assembly file, no ROM) and no function calls ``main``.

In case (b), no code reads ``p`` before the assignment: ``main`` starts
the program and the items before the assignment make no call.
"""

from __future__ import annotations

from uc_core import _dcfast
from uc_core import ast
from uc_core.ast_optimizer import make_identifier, _optok
from uc_core.volatility import children as _children

_ASSIGN = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                     "<<=", ">>="))
_TAINT = ("Typeof", "Generic", "Alignof")


def _is_node(x) -> bool:
    return _dcfast.is_dataclass(x) and not isinstance(x, type)


def _opt(n) -> str:
    o = n.op
    return o.text if hasattr(o, "text") else o


def _tok_text(t):
    return getattr(t, "text", t)


def _spec_key(specs):
    """A key for the type of a declaration, or None when the type is not a
    plain one (basic keywords, a typedef name or a struct/union tag).
    Storage classes and qualifiers do not count; volatile gives None."""
    kws = []
    for sp in specs or []:
        n = type(sp).__name__
        if n == "BasicTypeSpec":
            kws.append("b:" + _tok_text(sp.kw))
        elif n == "TypedefNameSpec":
            kws.append("t:" + _tok_text(sp.name))
        elif n == "StorageClass":
            continue
        elif n == "TypeQualifier":
            if _tok_text(sp.kw) in ("volatile", "_Atomic"):
                return None
        elif n == "StructRef":
            kws.append("s:" + _tok_text(sp.kind.kw) + ":" + _tok_text(sp.name))
        else:
            return None
    return tuple(sorted(kws)) if kws else None


def _storage(specs):
    for sp in specs or []:
        if type(sp).__name__ == "StorageClass":
            return _tok_text(sp.kw)
    return None


def _plain_name(declarator):
    if isinstance(declarator, ast.Declarator):
        return _tok_text(declarator.name)
    return None


def _pointer_name(declarator):
    """Name of ``T *p`` (one plain pointer level without qualifiers)."""
    if type(declarator).__name__ != "PointerDeclarator":
        return None
    ptr = declarator.pointer
    if type(ptr).__name__ != "PointerOne" or (ptr.quals or []):
        return None
    return _plain_name(declarator.inner)


def _is_null(e) -> bool:
    if type(e).__name__ == "Cast":
        return _is_null(e.expr)
    if isinstance(e, ast.IntLiteral):
        try:
            return int(_tok_text(e.value).rstrip("uUlL"), 0) == 0
        except ValueError:
            return False
    return isinstance(e, ast.Identifier) and _tok_text(e.name) == "NULL"


def _addr_of(e):
    """The name when ``e`` is ``&name``, else None."""
    if isinstance(e, ast.UnaryOp) and _opt(e) == "&" \
            and isinstance(e.operand, ast.Identifier):
        return _tok_text(e.operand.name)
    return None


def _has_call_or_asm(node) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not _is_node(n):
            continue
        cls = type(n).__name__
        if cls in ("Call", "CallNoArgs", "StmtExpr", "AsmStmt",
                   "AsmDeclaration", "VaArgExpr"):
            return True
        stack.extend(_children(n))
    return False


def _mentions(node, name) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not _is_node(n):
            continue
        if isinstance(n, ast.Identifier) and _tok_text(n.name) == name:
            return True
        stack.extend(_children(n))
    return False


def run(unit, *, asm_names=frozenset(), name_of=None,
        params_of=None) -> int:
    """Apply the pass to ``unit`` (in place).  ``asm_names`` are the
    lower-case tokens of all assembly.  Returns the number of pointers
    removed."""
    items = unit.items
    funcs = [i for i in items
             if isinstance(i, ast.FunctionDef) and i.body is not None]
    by_name: dict[str, list] = {}
    for f in funcs:
        by_name.setdefault(name_of(f), []).append(f)
    mains = by_name.get("main", [])
    if len(mains) != 1:
        return 0
    main_fn = mains[0]

    # file-scope variables: name -> list of (Declaration, declarator node)
    gdecl: dict[str, list] = {}
    for it in items:
        if isinstance(it, ast.Declaration) and _storage(it.decl_specs) != "typedef":
            for d in it.declarators or []:
                inner = getattr(d, "declarator", d)
                nm = _pointer_name(inner) or _plain_name(inner)
                if nm is None:
                    # an array, function or other shape: count the name
                    nm = _deep_name(inner)
                if nm is not None:
                    gdecl.setdefault(nm, []).append((it, d))

    # names declared inside functions (parameters and locals)
    local_names: set[str] = set()
    local_decl_count: dict[tuple, int] = {}
    for f in funcs:
        for nm in _local_decl_names(f, params_of):
            local_names.add(nm)

    # candidate pointers
    cands: dict[str, dict] = {}
    for nm, lst in gdecl.items():
        if len(lst) != 1:
            continue
        decl, d = lst[0]
        if _storage(decl.decl_specs) != "static" or len(decl.declarators) != 1:
            continue
        inner = getattr(d, "declarator", d)
        if _pointer_name(inner) != nm:
            continue
        key = _spec_key(decl.decl_specs)
        if key is None or nm in local_names:
            continue
        if nm.lower() in asm_names or ("_" + nm).lower() in asm_names:
            continue
        init = getattr(d, "init", None) if type(d).__name__ == "InitDeclaratorWithInit" else None
        if init is None or _is_null(init):
            form = "store"
            target = None
        else:
            target = _addr_of(init)
            if target is None:
                continue
            form = "init"
        cands[nm] = dict(decl=decl, key=key, form=form, target=target,
                         stores=[], bad=False, reads=0)
    if not cands:
        return 0

    # one scan of the functions: classify each mention
    calls_main = False

    def scan(node, parent, taint):
        nonlocal calls_main
        if isinstance(node, (list, tuple)):
            for x in node:
                scan(x, parent, taint)
            return
        if not _is_node(node):
            return
        cls = type(node).__name__
        if isinstance(node, ast.Identifier):
            nm = _tok_text(node.name)
            if nm == "main":
                calls_main = True
            c = cands.get(nm)
            if c is not None:
                if taint:
                    c["bad"] = True
                elif isinstance(parent, ast.UnaryOp) and _opt(parent) == "&":
                    c["bad"] = True
                elif isinstance(parent, (ast.UnaryOp, ast.PostfixOp)) \
                        and _opt(parent) in ("++", "--"):
                    c["bad"] = True
                elif isinstance(parent, ast.BinaryOp) and parent.left is node \
                        and _opt(parent) in _ASSIGN:
                    if _opt(parent) == "=":
                        c["stores"].append(parent)
                    else:
                        c["bad"] = True
                else:
                    c["reads"] += 1
            return
        t = taint or any(k in cls for k in _TAINT)
        for f in _dcfast.fields(node):
            if f.name == "pos":
                continue
            v = getattr(node, f.name, None)
            if isinstance(v, (list, tuple)) or _is_node(v):
                scan(v, node, t)

    for f in funcs:
        scan(f.body, f, False)
    # mentions outside functions (initializers): the pointer would be
    # read before main runs
    for it in items:
        if isinstance(it, ast.FunctionDef):
            continue
        for nm, c in cands.items():
            if _mentions_outside_decl(it, nm, c["decl"]):
                c["bad"] = True
    if calls_main:
        return 0

    removed = 0
    plan = []
    for nm, c in cands.items():
        if c["bad"]:
            continue
        if c["form"] == "init":
            if c["stores"]:
                continue
            obj = c["target"]
            store_stmt = None
        else:
            if len(c["stores"]) != 1:
                continue
            st = c["stores"][0]
            obj = _addr_of(st.right)
            if obj is None:
                continue
            store_stmt = _store_item(main_fn, st, nm)
            if store_stmt is None:
                continue
        res = _resolve_object(obj, items, gdecl, by_name, funcs, local_names,
                              params_of, name_of, asm_names, c["key"])
        if res is None:
            continue
        plan.append((nm, c, obj, res, store_stmt))

    for nm, c, obj, res, store_stmt in plan:
        new_obj = obj
        if res[0] == "local":
            new_obj = _hoist(unit, res[1], res[2], res[3], obj, funcs, name_of)
            if new_obj is None:
                continue
        _rewrite_all(funcs, nm, new_obj)
        if store_stmt is not None:
            parent_list, stmt = store_stmt
            for k, x in enumerate(parent_list):
                if x is stmt:
                    parent_list[k] = ast.EmptyStmt(pos=stmt.pos)
        # drop the declaration of the pointer
        items.remove(c["decl"])
        removed += 1
    return removed


def _deep_name(d):
    while d is not None and not isinstance(d, ast.Declarator):
        nxt = None
        for attr in ("inner", "declarator"):
            nxt = getattr(d, attr, None)
            if nxt is not None:
                break
        d = nxt
    return _tok_text(d.name) if d is not None else None


def _local_decl_names(func, params_of) -> set[str]:
    names: set[str] = set()
    stack = [params_of(func), func.body]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not _is_node(n):
            continue
        if isinstance(n, ast.Declarator):
            names.add(_tok_text(n.name))
        stack.extend(_children(n))
    return names


def _mentions_outside_decl(item, name, own_decl) -> bool:
    if item is own_decl:
        return False
    return _mentions(item, name)


def _store_item(main_fn, st, pname):
    """(items list, statement) when the assignment node ``st`` is an expression
    statement among the first items of main and the items before it are
    declarations that do not call and do not mention the pointer."""
    body = main_fn.body
    if not isinstance(body, ast.CompoundStmt):
        return None
    its = body.items
    for i, it in enumerate(its):
        if isinstance(it, ast.ExpressionStmt) and it.expr is st:
            for pre in its[:i]:
                if isinstance(pre, ast.EmptyStmt):
                    continue
                if not isinstance(pre, ast.Declaration):
                    return None
                if _has_call_or_asm(pre) or _mentions(pre, pname):
                    return None
            return its, it
    return None


def _resolve_object(obj, items, gdecl, by_name, funcs, local_names,
                    params_of, name_of, asm_names, key):
    """How to reach ``obj``: ("global",) or ("local", function, Declaration,
    declarator); None when it does not qualify."""
    if obj.lower() in asm_names or ("_" + obj).lower() in asm_names:
        return None
    lst = gdecl.get(obj)
    if lst:
        if len(lst) != 1 or obj in local_names:
            return None
        decl, d = lst[0]
        inner = getattr(d, "declarator", d)
        if _plain_name(inner) != obj or _spec_key(decl.decl_specs) != key:
            return None
        if len(decl.declarators) != 1:
            return None
        return ("global",)
    # a static of one function
    found = []
    for f in funcs:
        stack = [f.body]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not _is_node(n):
                continue
            if isinstance(n, ast.Declaration):
                for d in n.declarators or []:
                    inner = getattr(d, "declarator", d)
                    if _deep_name(inner) == obj:
                        found.append((f, n, d))
            stack.extend(_children(n))
    if len(found) != 1:
        return None
    f, decl, d = found[0]
    inner = getattr(d, "declarator", d)
    if (_storage(decl.decl_specs) != "static" or len(decl.declarators) != 1
            or _plain_name(inner) != obj or _spec_key(decl.decl_specs) != key):
        return None
    if _defines_types(f.body):
        return None
    init = getattr(d, "init", None)
    if init is not None:
        # the initializer moves to file scope: it may not name a local
        locs = _local_decl_names(f, params_of) - {obj}
        if any(_mentions(init, nm) for nm in locs):
            return None
    # the name must not be a parameter of f or declared in another function
    if any(_deep_name_in_params(params_of(f), obj)):
        return None
    for g in funcs:
        if g is not f and obj in _local_decl_names(g, params_of):
            return None
    return ("local", f, decl, d)


def _defines_types(node) -> bool:
    """A struct, union, enum or typedef is defined inside the body."""
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not _is_node(n):
            continue
        if type(n).__name__ in ("StructDef", "UnionDef", "EnumDef") \
                or (isinstance(n, ast.Declaration)
                    and _storage(n.decl_specs) == "typedef"):
            return True
        stack.extend(_children(n))
    return False


def _deep_name_in_params(params, name):
    for p in params or []:
        yield _deep_name(getattr(p, "declarator", None)) == name


def _hoist(unit, func, decl, d, obj, funcs, name_of):
    """Move the static ``obj`` of ``func`` to file scope under a new name."""
    used = set()
    for it in unit.items:
        if isinstance(it, ast.Declaration):
            for dd in it.declarators or []:
                nm = _deep_name(getattr(dd, "declarator", dd))
                if nm:
                    used.add(nm.lower())
        elif isinstance(it, ast.FunctionDef):
            nm = name_of(it)
            if nm:
                used.add(nm.lower())
    new = f"__ls_{name_of(func)}_{obj}"
    while new.lower() in used:
        new += "_"
    # remove the declaration from its block
    holder = _find_holder(func.body, decl)
    if holder is None:
        return None
    holder.remove(decl)
    # rename the mentions in func
    _rename(func.body, obj, new)
    inner = getattr(d, "declarator", d)
    inner.name = _renamed_token(inner.name, new)
    idx = unit.items.index(func)
    unit.items.insert(idx, decl)
    return new


def _find_holder(node, decl):
    """The list (of statements) that holds ``decl``."""
    stack = [node]
    while stack:
        n = stack.pop()
        if not _is_node(n):
            continue
        for f in _dcfast.fields(n):
            if f.name == "pos":
                continue
            v = getattr(n, f.name, None)
            if isinstance(v, list):
                if any(x is decl for x in v):
                    return v
                stack.extend(v)
            elif _is_node(v):
                stack.append(v)
    return None


def _renamed_token(tok, text):
    import dataclasses
    return dataclasses.replace(tok, text=text)


def _rename(node, old, new):
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not _is_node(n):
            continue
        if isinstance(n, ast.Identifier) and _tok_text(n.name) == old:
            n.name = _renamed_token(n.name, new)
        stack.extend(_children(n))


def _rewrite_all(funcs, pname, obj):
    for f in funcs:
        f.body = _rw(f.body, pname, obj)


def _rw(node, pname, obj):
    if isinstance(node, list):
        for i, x in enumerate(node):
            node[i] = _rw(x, pname, obj)
        return node
    if isinstance(node, tuple):
        return node
    if not _is_node(node):
        return node
    for f in _dcfast.fields(node):
        if f.name == "pos":
            continue
        v = getattr(node, f.name, None)
        if isinstance(v, (list, tuple)) or _is_node(v):
            nv = _rw(v, pname, obj)
            if nv is not v:
                setattr(node, f.name, nv)
    if isinstance(node, ast.Identifier) and _tok_text(node.name) == pname:
        return ast.UnaryOp(op=_optok("&"), operand=make_identifier(obj),
                           pos=node.pos)
    if isinstance(node, ast.ArrowMember) and _addr_of(node.obj) == obj:
        return ast.Member(obj=node.obj.operand, member=node.member,
                          pos=node.pos)
    if (isinstance(node, ast.UnaryOp) and _opt(node) == "*"
            and _addr_of(node.operand) == obj):
        return node.operand.operand
    return node
