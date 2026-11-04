"""One divide for x / y and x % y (family L, L4; docs/family_l.md).

Two statements often divide the same operands, once for the quotient and
once for the remainder:

    a[n] = x % n;
    x = 10 * a[n - 1] + x / n;

The runtime routine that divides leaves both results (__div16 returns the
quotient in HL and the remainder in DE).  The pass finds such pairs and
rewrites the first operation to the intrinsic call

    __uc80_dm_first(E1, T)

and notes where E2 stands.  The code generator (codegen.py,
``_gen_divmod_first``) generates E1 with the call that leaves both
results, stores the other result in ``T`` and keeps E1 in HL.  Then the
generator puts ``T`` in place of E2 (``take_second``).  When E1 does not end
in such a call (an inline divide of a byte, for example), E2 stays as it
is.  So the rewrite is right whichever code E1 gets.

A pair is two operations in the same block, one after the other, with

- operands that are integer variables of 16 bits (int, short, unsigned,
  unsigned short) or integer constants, named by plain identifiers; the
  variables are not volatile and their address is never taken;
- the first operation always runs when its statement runs (not on the
  right of ``&&`` or ``||``, not in an arm of ``?:``);
- no change of an operand between the two operations, no call between
  them when an operand is a global, and no label or other statement in
  between that is not an expression statement, a return or a declaration;
  the second statement may be the assignment ``x = ... x / n ...`` to an
  operand (the divide happens before the assignment).
"""

from __future__ import annotations

from uc_core import _dcfast
from uc_core import ast
from uc_core.ast_optimizer import _decl_type_key, _function_param_typekeys
from uc_core.codegen_helpers import make_identifier, _make_synthetic_token

_SIGNED = frozenset(("int", "short", "int short", "signed", "int signed",
                     "short signed", "int short signed"))
_UNSIGNED = frozenset(("unsigned", "int unsigned", "short unsigned",
                       "int short unsigned"))
_ASSIGN = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                     "<<=", ">>="))
INTRINSIC = "__uc80_dm_first"


_TD: dict = {}          # typedef name -> type key, set by run()


def _norm(key):
    """Resolve a typedef-name key ("td:uint16_t") to the key of its base
    type, when the unit declares the typedef as a bare scalar."""
    n = 0
    while key is not None and key.startswith("td:") and n < 8:
        key = _TD.get(key[3:])
        n += 1
    return key


def _is_node(x) -> bool:
    return _dcfast.is_dataclass(x) and not isinstance(x, type)


def _opt(n) -> str:
    o = n.op
    return o.text if hasattr(o, "text") else o


def _tok(t):
    return getattr(t, "text", t)


def _children_fields(node):
    for f in _dcfast.fields(node):
        if f.name == "pos":
            continue
        yield f.name, getattr(node, f.name, None)


class _Effects:
    """What a statement changes: names assigned, calls, odd nodes."""

    def __init__(self):
        self.mods: list = []        # (name, node)
        self.call = False
        self.odd = False
        self.addr: set = set()


def _effects(node, eff, cond=False):
    if isinstance(node, (list, tuple)):
        for x in node:
            _effects(x, eff)
        return
    if not _is_node(node):
        return
    cls = type(node).__name__
    if cls in ("Call", "CallNoArgs"):
        eff.call = True
    elif cls in ("StmtExpr", "AsmStmt", "AsmDeclaration", "VaArgExpr",
                 "LabelStmt"):
        eff.odd = True
    elif cls == "BinaryOp" and _opt(node) in _ASSIGN:
        if isinstance(node.left, ast.Identifier):
            eff.mods.append((_tok(node.left.name), node))
    elif cls in ("UnaryOp", "PostfixOp"):
        o = _opt(node)
        if o in ("++", "--") and isinstance(node.operand, ast.Identifier):
            eff.mods.append((_tok(node.operand.name), node))
        elif o == "&" and isinstance(node.operand, ast.Identifier):
            eff.addr.add(_tok(node.operand.name))
    for _n, v in _children_fields(node):
        if isinstance(v, (list, tuple)) or _is_node(v):
            _effects(v, eff)


def _spine_ops(node, parent, field, idx, out, conditional=False):
    """Collect (op, parent, field, idx, conditional) for every / and %."""
    if isinstance(node, list):
        for i, x in enumerate(node):
            _spine_ops(x, parent, field, i, out, conditional)
        return
    if not _is_node(node):
        return
    cls = type(node).__name__
    if cls in ("SizeofExpr", "SizeofType", "StmtExpr", "GenericSelection"):
        return
    if cls == "BinaryOp" and _opt(node) in ("/", "%"):
        out.append((node, parent, field, idx, conditional))
    if cls == "BinaryOp" and _opt(node) in ("&&", "||"):
        _spine_ops(node.left, node, "left", None, out, conditional)
        _spine_ops(node.right, node, "right", None, out, True)
        return
    if cls == "TernaryOp":
        _spine_ops(node.condition, node, "condition", None, out, conditional)
        _spine_ops(node.true_expr, node, "true_expr", None, out, True)
        _spine_ops(node.false_expr, node, "false_expr", None, out, True)
        return
    for name, v in _children_fields(node):
        if isinstance(v, list) or _is_node(v):
            _spine_ops(v, node, name, None, out, conditional)


class _Func:
    def __init__(self, f, gtypes, gaddr, params_of):
        self.types: dict = {}
        self.addr: set = set()
        self.declared: dict = {}
        self.static_names: set = set()      # static or extern locals
        for name, key in _function_param_typekeys(f.declarator):
            self._decl(name, key)
        self._scan(f.body)
        self.gtypes = gtypes
        self.gaddr = gaddr

    def _decl(self, name, key):
        if name in self.declared:
            self.types[name] = None         # declared twice: unknown
        else:
            self.types[name] = key
        self.declared[name] = True

    def _scan(self, node):
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not _is_node(n):
                continue
            if isinstance(n, ast.Declaration):
                for d in n.declarators or []:
                    dd = getattr(d, "declarator", d)
                    nm = _deep_name(dd)
                    if nm is not None:
                        self._decl(nm, _decl_type_key(n.decl_specs, dd))
                        for sp in n.decl_specs or []:
                            if (type(sp).__name__ == "StorageClass"
                                    and _tok(sp.kw) in ("static", "extern")):
                                self.static_names.add(nm)
            elif (isinstance(n, ast.UnaryOp) and _opt(n) == "&"
                  and isinstance(n.operand, ast.Identifier)):
                self.addr.add(_tok(n.operand.name))
            for _nm, v in _children_fields(n):
                if isinstance(v, (list, tuple)) or _is_node(v):
                    stack.append(v)

    def kind(self, e):
        """('s'|'u', names, key) for an operand, or None.  names is the
        set of variable names (empty for a constant)."""
        if isinstance(e, ast.IntLiteral):
            text = _tok(e.value)
            try:
                v = int(text.rstrip("uUlL"), 0)
            except ValueError:
                return None
            if text[-1:] in "lL" or text.rstrip("lL")[-1:] in "lL":
                return None
            if not 0 <= v <= 32767 and "u" not in text.lower():
                return None
            if v > 65535:
                return None
            return ("u" if "u" in text.lower() else "s", frozenset(), v)
        if isinstance(e, ast.Identifier):
            nm = _tok(e.name)
            if nm in self.declared:
                key = _norm(self.types.get(nm))
                glob = nm in self.static_names
            else:
                key = _norm(self.gtypes.get(nm))
                glob = True
            if key is None or nm in self.addr or nm in self.gaddr:
                return None
            if key in _SIGNED:
                return ("s", frozenset((nm,)), glob)
            if key in _UNSIGNED:
                return ("u", frozenset((nm,)), glob)
        return None


def _deep_name(d):
    while d is not None and not isinstance(d, ast.Declarator):
        nxt = None
        for attr in ("inner", "declarator"):
            nxt = getattr(d, attr, None)
            if nxt is not None:
                break
        d = nxt
    return _tok(d.name) if d is not None else None


def run(unit, *, int_size=2, name_of=None, params_of=None,
        skip=frozenset()) -> int:
    """``skip``: functions under optnone, which the pass leaves alone."""
    if int_size != 2:
        return 0
    gtypes: dict = {}
    gaddr: set = set()
    _TD.clear()
    for it in unit.items or []:
        if isinstance(it, ast.Declaration) and any(
                type(sp).__name__ == "StorageClass"
                and _tok(sp.kw) == "typedef" for sp in it.decl_specs or []):
            for d in it.declarators or []:
                dd = getattr(d, "declarator", d)
                nm = _deep_name(dd)
                key = _decl_type_key(it.decl_specs, dd)
                if nm is not None and key is not None:
                    _TD[nm] = key
    for it in unit.items or []:
        if isinstance(it, ast.Declaration):
            for d in it.declarators or []:
                dd = getattr(d, "declarator", d)
                nm = _deep_name(dd)
                if nm is None:
                    continue
                key = _decl_type_key(it.decl_specs, dd)
                if nm in gtypes and gtypes[nm] != key:
                    key = None
                gtypes[nm] = key
    # a global whose address is taken anywhere
    stack = [it for it in unit.items or []]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not _is_node(n):
            continue
        if (isinstance(n, ast.UnaryOp) and _opt(n) == "&"
                and isinstance(n.operand, ast.Identifier)):
            gaddr.add(_tok(n.operand.name))
        for _nm, v in _children_fields(n):
            if isinstance(v, (list, tuple)) or _is_node(v):
                stack.append(v)
    total = 0
    counter = [0]
    pending: dict = {}
    funcs = []
    for it in unit.items or []:
        if (isinstance(it, ast.FunctionDef) and it.body is not None
                and name_of(it) not in skip):
            fx = _Func(it, gtypes, gaddr, params_of)
            pairs = []
            _block_items_walk(it.body, fx, pairs)
            funcs.append((it, fx, pairs))
    # A signed pair pays in bytes only when the program has no other signed
    # 16-bit divide: __sdivmod16 (58 bytes) replaces __sdiv16 and __smod16
    # (70 bytes), and it costs bytes beside any of the two.  The shared
    # body of family K (49 bytes) wins in main outside every loop.
    keep = []
    for it, fx, pairs in funcs:
        is_main = name_of(it) == "main"
        keep.append([p for p in pairs
                     if not (p[0][4] == "s" and is_main and p[2] == 0)])
    lone_signed = _program_has_lone_signed(unit, funcs, keep)
    for (it, fx, _pairs), pairs in zip(funcs, keep):
        for (e1, e2, depth) in pairs:
            if e1[4] == "s" and lone_signed:
                continue
            _apply(it, e1, e2, counter, pending)
            total += 1
    unit._dm_pending = pending
    return total


# libc routines that call __sdiv16 or __smod16
_SIGNED_LIBC = frozenset(("div", "cbrt", "cbrtf", "cbrtl"))


def _program_has_lone_signed(unit, funcs, keep) -> bool:
    """Is there a divide or remainder that may call __sdiv16 or __smod16
    and is not in a pair that stays?  Unknown types count as signed."""
    in_pair = set()
    for pairs in keep:
        for e1, e2, _d in pairs:
            in_pair.add(id(e1[0]))
            in_pair.add(id(e2[0]))
    for it, fx, _pairs in funcs:
        stack = [it.body]
        while stack:
            n = stack.pop()
            if isinstance(n, (list, tuple)):
                stack.extend(n)
                continue
            if not _is_node(n):
                continue
            if isinstance(n, ast.Identifier) and _tok(n.name) in _SIGNED_LIBC:
                return True
            if isinstance(n, ast.BinaryOp) and id(n) not in in_pair:
                o = _opt(n)
                if o in ("/", "%", "/=", "%=") and not _benign(n, fx):
                    return True
            for _nm, v in _children_fields(n):
                if isinstance(v, (list, tuple)) or _is_node(v):
                    stack.append(v)
    return False


def _benign(op, fx) -> bool:
    """The operation cannot call __sdiv16 or __smod16: a power of two, or an
    operand that is unsigned (16 bits), long or floating."""
    if isinstance(op.right, ast.IntLiteral):
        k = fx.kind(op.right)
        if k is not None and k[2] > 0 and k[2] & (k[2] - 1) == 0:
            return True
    for o in (op.left, op.right):
        if type(o).__name__ in ("SizeofExpr", "SizeofType"):
            return True                     # size_t: unsigned
        if isinstance(o, ast.IntLiteral):
            low = _tok(o.value).lower()
            if "u" in low or "l" in low:
                return True
        elif isinstance(o, ast.Identifier):
            nm = _tok(o.name)
            key = _norm(fx.types.get(nm) if nm in fx.declared
                        else fx.gtypes.get(nm))
            if key is None:
                continue
            if key in _UNSIGNED:
                return True
            if any(w in key for w in ("long", "float", "double")):
                return True
    return False


def _block_items_walk(node, fx, pairs, depth=0):
    """Find the blocks (statement lists) below ``node`` and scan each.
    ``depth`` counts the loops around the block."""
    if isinstance(node, ast.CompoundStmt):
        _scan_block(node.items, fx, pairs, depth)
        return
    if not _is_node(node):
        return
    cls = type(node).__name__
    if cls in ("StmtExpr",):
        return
    if cls in ("WhileStmt", "ForStmt", "DoWhileStmt"):
        depth += 1
    for _nm, v in _children_fields(node):
        if isinstance(v, list):
            for x in v:
                if _is_node(x) and not isinstance(x, (ast.Declaration,)):
                    _block_items_walk(x, fx, pairs, depth)
        elif _is_node(v):
            _block_items_walk(v, fx, pairs, depth)


def _item_exprs(item):
    """(holder, field) pairs of the expressions of a simple item."""
    if isinstance(item, ast.ExpressionStmt):
        return [(item, "expr")]
    if isinstance(item, ast.ReturnStmtValue):
        return [(item, "value")]
    if isinstance(item, ast.Declaration):
        return [(d, "init") for d in item.declarators or []
                if type(d).__name__ == "InitDeclaratorWithInit"]
    return []


def _scan_block(items, fx, pairs, depth=0):
    open_: list = []        # candidates: dict(op, parent, field, idx, kind, names, glob, o)
    for item in items:
        if isinstance(item, ast.EmptyStmt):
            continue
        simple = isinstance(item, (ast.ExpressionStmt, ast.ReturnStmtValue,
                                   ast.Declaration))
        if not simple:
            open_.clear()
            _block_items_walk(item, fx, pairs, depth)
            continue
        eff = _Effects()
        _effects(item, eff)
        if eff.odd:
            open_.clear()
            continue
        holders = _item_exprs(item)
        ops = []
        for h, fld in holders:
            _spine_ops(getattr(h, fld), h, fld, None, ops)
        # does the item assign an operand once, as its top assignment?
        top_assign = None
        if (isinstance(item, ast.ExpressionStmt)
                and isinstance(item.expr, ast.BinaryOp)
                and _opt(item.expr) == "="
                and isinstance(item.expr.left, ast.Identifier)):
            top_assign = item.expr
        matched_nodes = set()
        for (op, parent, field, idx, cond) in ops:
            info = _op_info(op, fx)
            if info is None:
                continue
            for c in list(open_):
                if (c["o"] == _opt(op) or c["sig"] != info["sig"]
                        or c["u"] != info["u"]):
                    continue
                mods = [m for m in eff.mods if m[0] in c["names"]]
                if mods:
                    # only the top assignment x = ... op ... may change an
                    # operand, and it takes effect after the operation
                    if not (len(mods) == 1 and top_assign is not None
                            and mods[0][1] is top_assign
                            and _within(top_assign.right, op)):
                        continue
                if eff.call and c["glob"]:
                    continue
                pairs.append(((c["op"], c["parent"], c["field"], c["idx"], c["u"]),
                              (op, parent, field, idx), depth))
                open_.remove(c)
                matched_nodes.add(id(op))
                break
        # close the candidates that this item changes
        changed = {m[0] for m in eff.mods}
        open_[:] = [c for c in open_
                    if not (c["names"] & changed)
                    and not (eff.call and c["glob"])]
        # new candidates
        for (op, parent, field, idx, cond) in ops:
            if cond or id(op) in matched_nodes:
                continue
            info = _op_info(op, fx)
            if info is None or (info["names"] & changed):
                continue
            if eff.call and info["glob"]:
                continue
            open_.append(dict(op=op, parent=parent, field=field, idx=idx,
                              o=_opt(op), sig=info["sig"], u=info["u"],
                              names=info["names"], glob=info["glob"]))


def _within(root, target) -> bool:
    stack = [root]
    while stack:
        n = stack.pop()
        if n is target:
            return True
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if _is_node(n):
            for _nm, v in _children_fields(n):
                if isinstance(v, (list, tuple)) or _is_node(v):
                    stack.append(v)
    return False


def _op_info(op, fx):
    a = fx.kind(op.left)
    b = fx.kind(op.right)
    if a is None or b is None:
        return None
    # a constant divisor: zero or a power of two has its own code
    if isinstance(op.right, ast.IntLiteral):
        v = b[2]
        if v == 0 or v & (v - 1) == 0:
            return None
    if isinstance(op.left, ast.IntLiteral) and isinstance(op.right,
                                                          ast.IntLiteral):
        return None
    names = a[1] | b[1]
    glob = (a[2] is True if a[1] else False) or (b[2] is True if b[1] else False)
    unsigned = "u" if "u" in (a[0], b[0]) else "s"
    sig = (_sig(op.left), _sig(op.right))
    return dict(names=names, glob=glob, u=unsigned, sig=sig)


def _sig(e):
    if isinstance(e, ast.IntLiteral):
        return ("c", _tok(e.value))
    return ("v", _tok(e.name))


def _set(parent, field, idx, new):
    if idx is None:
        setattr(parent, field, new)
    else:
        getattr(parent, field)[idx] = new


def _apply(func, e1, e2, counter, pending):
    op1, p1, f1, i1, unsigned = e1
    op2, p2, f2, i2 = e2
    name = f"__dm{counter[0]}"
    counter[0] += 1
    call1 = ast.Call(func=make_identifier(INTRINSIC),
                     args=[op1, make_identifier(name)], pos=op1.pos)
    _set(p1, f1, i1, call1)
    # the code generator puts T in place of E2 when E1 has set T
    pending[name] = (op2, p2, f2, i2)
    kw = "unsigned" if unsigned == "u" else "int"
    spec = ast.BasicTypeSpec(kw=_make_synthetic_token(
        "KW_UNSIGNED" if unsigned == "u" else "KW_INT", kw))
    decl = ast.Declaration(
        decl_specs=[spec],
        declarators=[ast.InitDeclarator(declarator=ast.Declarator(
            name=_make_synthetic_token("IDENT", name)))])
    func.body.items.insert(0, decl)


def take_second(pending, name):
    """Put the variable ``name`` in place of the second operation, when the
    operation still stands where the pass saw the operation.  Returns True
    when it did."""
    site = pending.pop(name, None)
    if site is None:
        return False
    op2, parent, field, idx = site
    cur = getattr(parent, field)
    if idx is None:
        if cur is not op2:
            return False
        setattr(parent, field, make_identifier(name))
        return True
    if idx >= len(cur) or cur[idx] is not op2:
        return False
    cur[idx] = make_identifier(name)
    return True
