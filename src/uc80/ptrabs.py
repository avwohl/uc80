"""A pointer that holds a constant address becomes a direct access.

``absaddr.py`` turns ``*(volatile T *)K`` into a named object at address K.
This pass does the same for a pointer VARIABLE whose value is a constant:

    volatile uint8_t *const port = (volatile uint8_t *)0x4000;   *port = x;
    static volatile uint8_t *const ym = (void *)0x4000;           ym[1] = x;
    static struct ym *const chip = (struct ym *)0x5000;           chip->d0 = x;
    static volatile uint8_t *p;  ...  p = (volatile uint8_t *)0x4000;  *p = x;

Each access through the pointer is replaced with a name at the address
K + the offset of the member (``LD (nn),A`` and ``LD A,(nn)`` in place of a
load of the pointer and an access through HL).  Only accesses of a volatile
integer object change, so the volatile rules of the compiler (every access
happens, in order, with the right width) apply to the new name.

A pointer variable qualifies when
  - it is a pointer (one level) to an integer type or to a struct,
  - it is not itself volatile, and not a parameter,
  - it is `T *const` with a constant non-zero address as initial value, or
    it has internal linkage or is local, its address is never taken, it is
    never modified by `++`, `+=` and so on, and every `=` and the
    initialiser give the same constant non-zero address,
  - its name does not occur in the text of an asm statement, in a
    `#pragma uc80 public` or `keep`.
A use that stays (the pointer passed to a function, `p + i` with a variable
i) keeps the variable.  When no use stays, the variable and its assignments
go away.

Limits: a non-const pointer is taken as holding its constant from the
start, so a read through the pointer before the first assignment (a null
pointer read in the C sense) reads the constant address.  A local
struct or typedef (declared inside a function) is not resolved.
"""

import re

from uc_core import ast
from uc_core import ast_legacy as lt

from . import absaddr

_ASSIGN = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                     "<<=", ">>="))
_SIZES = absaddr._SIZES


def ResolvedType_struct(rt, members):
    from .codegen import ResolvedType
    return ResolvedType(kind="struct", name=rt.name, is_union=rt.is_union,
                        members=tuple(members))


def _op(node):
    op = node.op
    return op.text if hasattr(op, "text") else op


class _Var:
    def __init__(self, name, decl, decl_node, scope_kind,
                 pointee, is_const, static_file, addr):
        self.name = name
        self.decl = decl                # the InitDeclarator node
        self.decl_node = decl_node      # the Declaration
        self.scope_kind = scope_kind    # "file" or "local"
        self.pointee = pointee          # legacy type
        self.is_const = is_const
        self.static_file = static_file  # internal linkage or local
        self.init_addr = addr
        self.addrs = set()              # constants it is given
        self.bad = False
        self.value_uses = 0
        self.sites = 0
        self.kept_assigns = 0

    def addr(self):
        if self.bad:
            return None
        s = set(self.addrs)
        if self.init_addr is not None:
            s.add(self.init_addr)
        if len(s) != 1:
            return None
        k = next(iter(s))
        return k if 0 < k <= 0xFFFF else None


class PointerRewriter:
    def __init__(self, cg, unit, protected):
        self.cg = cg
        self.unit = unit
        self.protected = {p.lower() for p in protected}
        self.rw = absaddr.AbsoluteRewriter(cg._const_int_operand,
                                           cg._type_size)
        self.vars = {}                  # id(InitDeclarator) -> _Var
        self.scopes = []                # list of dict name -> _Var | None
        self.pass_no = 1
        self.asm_names = set()
        self._defs = None
        self.count = 0
        from .codegen import _to_legacy
        self._legacy = _to_legacy

    # ---- helpers -----------------------------------------------------
    def _lookup(self, name):
        for sc in reversed(self.scopes):
            if name in sc:
                return sc[name]
        return None

    def _declare(self, name, var):
        self.scopes[-1][name] = var

    def _const_addr(self, init):
        """K for ``(T *)K`` (casts to pointer or integer types)."""
        n = init
        while isinstance(n, ast.Cast):
            n = n.expr
        if n is init:
            return None            # a bare integer is not a pointer value
        k = self.cg._const_int_operand(n)
        if k is None:
            return None
        return k & 0xFFFF

    # ---- types (ResolvedType: it keeps `volatile` through a typedef) ----
    def _norm(self, rt):
        """(the type without typedef layers, volatile)."""
        vol = False
        seen = 0
        while rt is not None and rt.kind == "typedef" and seen < 16:
            vol = vol or rt.is_volatile
            rt = self.cg.ctx.typedefs.get(rt.name)
            seen += 1
        if rt is None:
            return None, vol
        return rt, vol or rt.is_volatile

    def _size(self, rt):
        return self.cg._type_size(self._legacy(rt))

    def _tag_defs(self):
        if self._defs is None:
            from .codegen import _resolve_struct_spec
            self._defs = {}
            stack = [self.unit.items]
            while stack:
                n = stack.pop()
                if isinstance(n, list):
                    stack.extend(n)
                    continue
                if not hasattr(n, "__dataclass_fields__"):
                    continue
                if isinstance(n, ast.StructDef) and n.name is not None:
                    try:
                        self._defs[n.name.text] = _resolve_struct_spec(n)
                    except Exception:
                        pass
                for f in n.__dataclass_fields__:
                    if f != "pos":
                        v = getattr(n, f)
                        if isinstance(v, list) or hasattr(
                                v, "__dataclass_fields__"):
                            stack.append(v)
        return self._defs

    def _struct_fields(self, rt):
        """{member: (ResolvedType, offset)} of a struct type, or None when
        the layout has a bit-field or an anonymous member."""
        cg = self.cg
        members = rt.members
        if not members and rt.name:
            d = self._tag_defs().get(rt.name)
            members = d.members if d is not None else ()
        if not members:
            return None
        if any(bw is not None or nm is None for nm, _mt, bw in members):
            return None
        if rt.name and rt.name in cg.ctx.structs:
            offs = {nm: off for nm, _t, off in cg.ctx.structs[rt.name]}
        else:
            try:
                st = self._legacy(ResolvedType_struct(rt, members))
                lay, anon, _sz = cg._compute_struct_layout(
                    f"__ptrabs_{id(rt)}", st.members, st.is_union)
            except Exception:
                return None
            if anon:
                return None
            offs = {nm: off for nm, _t, off in lay}
        out = {}
        for nm, mt, _bw in members:
            if nm not in offs:
                return None
            out[nm] = (mt, offs[nm])
        return out

    @staticmethod
    def _scalar_type(rt):
        return rt is not None and rt.kind == "basic" and rt.name in _SIZES

    def _path(self, node):
        """(var, address offset, type, volatile) for an lvalue that starts
        with the deref of a candidate variable, else None.  The index of an
        array is a constant."""
        cg = self.cg
        if isinstance(node, ast.UnaryOp) and _op(node) == "*":
            return self._root(node.operand, 0)
        if isinstance(node, ast.Index):
            i = cg._const_int_operand(node.index)
            if i is None:
                return None
            r = self._root(node.array, i)
            if r is not None:
                return r
            base = self._path(node.array)
            if base is None:
                return None
            var, off, rt, vol = base
            if rt.kind != "array" or rt.element is None:
                return None
            et, evol = self._norm(rt.element)
            if et is None:
                return None
            return (var, off + i * self._size(et), et, vol or evol)
        if isinstance(node, ast.ArrowMember):
            r = self._root(node.obj, 0)
            if r is None:
                return None
            return self._field(r, node.member)
        if isinstance(node, ast.Member):
            base = self._path(node.obj)
            if base is None:
                return None
            return self._field(base, node.member)
        return None

    def _field(self, base, member):
        var, off, rt, vol = base
        if rt.kind != "struct":
            return None
        name = member.text if hasattr(member, "text") else member
        fields = self._struct_fields(rt)
        if fields is None or name not in fields:
            return None
        mt, moff = fields[name]
        mt, mvol = self._norm(mt)
        if mt is None:
            return None
        return var, off + moff, mt, vol or mvol

    def _root(self, ident, index):
        """The pointee of an Identifier that names a candidate variable,
        reached by *p or p[index]: (var, offset, type, volatile)."""
        if not isinstance(ident, ast.Identifier):
            return None
        var = self._lookup(ident.name.text)
        if var is None or var.bad:
            return None
        rt, vol = var.pointee
        return (var, index * self._size(rt), rt, vol)

    def _scalar(self, p):
        var, off, rt, vol = p
        if not vol or not self._scalar_type(rt):
            return None
        k = var.addr()
        if k is None:
            return None
        a = k + off
        if a < 0 or a + _SIZES[rt.name] > 0x10000:
            return None
        return a, rt

    # ---- declarations ------------------------------------------------
    def _candidate(self, decl_node, init_decl, kind):
        cg = self.cg
        from .codegen import (decl_storage_class, resolve_base_type,
                              _wrap_declarator)
        storage = decl_storage_class(decl_node.decl_specs)
        if storage in ("typedef", "extern"):
            return None
        base = resolve_base_type(decl_node.decl_specs)
        inner = init_decl.declarator
        name, full = _wrap_declarator(inner, base)
        if name is None or full.kind not in ("pointer", "typedef"):
            return None
        outer_const = full.is_const
        full, ptr_vol = self._norm(full)
        if full is None or full.kind != "pointer" or ptr_vol:
            return None
        full_const = outer_const or full.is_const
        pointee, pvol = self._norm(full.pointee)
        if pointee is None:
            return None
        if self._scalar_type(pointee):
            pass
        elif pointee.kind == "struct":
            if self._struct_fields(pointee) is None:
                return None
        else:
            return None
        init = getattr(init_decl, "init", None)
        k = None
        if init is not None:
            k = self._const_addr(init)
            if k is None:
                return None
        static_file = kind == "local" or storage == "static"
        if not full_const and not static_file:
            return None
        if not full_const and k is None and init is not None:
            return None
        return _Var(name, init_decl, decl_node, kind, (pointee, pvol),
                    full_const, static_file, k)

    # ---- the walk ----------------------------------------------------
    def _fn_params(self, decl):
        names = []
        stack = [decl]
        while stack:
            x = stack.pop()
            if x is None:
                continue
            if isinstance(x, ast.FnDeclarator):
                params = x.params
                if isinstance(params, ast.VariadicParams):
                    params = params.params
                for p in params or []:
                    d = getattr(p, "declarator", None)
                    while d is not None and not isinstance(d, ast.Declarator):
                        d = getattr(d, "inner", None)
                    if d is not None:
                        names.append(d.name.text)
            stack.append(getattr(x, "inner", None))
        return names

    def _declaration(self, decl, kind):
        from .codegen import (decl_storage_class, _wrap_declarator,
                              resolve_base_type)
        storage = decl_storage_class(decl.decl_specs)
        if self.pass_no == 1:
            self.visit(decl.decl_specs)
        for d in list(decl.declarators or []):
            try:
                name, _ = _wrap_declarator(
                    d.declarator, resolve_base_type(decl.decl_specs))
            except Exception:
                name = None
            init = getattr(d, "init", None)
            if self.pass_no == 1:
                # a use of a pointer in a typeof or an array bound counts
                self.visit(d.declarator)
            if name is None:
                if init is not None:
                    self.visit(init)
                continue
            if self.pass_no == 1:
                var = self._candidate(decl, d, kind)
                self.vars[id(d)] = var
                if kind == "file" and name in self.scopes[0]:
                    # a second declaration of the name: no certainty
                    old = self.scopes[0][name]
                    if old is not None:
                        old.bad = True
                    var = None
                    self.vars[id(d)] = None
                elif storage == "extern" and kind == "local":
                    old = self._lookup(name)
                    if old is not None:
                        old.bad = True
            else:
                var = self.vars.get(id(d))
            if init is not None and not (var is not None
                                         and var.init_addr is not None):
                self.visit(init)
            if self.pass_no == 2 and var is not None and self._removable(var):
                decl.declarators = [x for x in decl.declarators
                                    if x is not d]
            self.scopes[-1][name] = var

    def visit(self, node, parent=None, slot=None):
        """Walk ``node``.  In pass 2 return the replacement of ``node``."""
        if isinstance(node, list):
            for i, x in enumerate(node):
                nx = self.visit(x, node, i)
                if self.pass_no == 2 and nx is not x:
                    node[i] = nx
            return node
        if not hasattr(node, "__dataclass_fields__"):
            return node
        if isinstance(node, ast.FunctionDef):
            self.scopes.append({n: None for n in self._fn_params(
                node.declarator)})
            if node.body is not None:
                self.visit_block(node.body, push=False)
            self.scopes.pop()
            return node
        if isinstance(node, ast.CompoundStmt):
            self.visit_block(node)
            return node
        if isinstance(node, ast.ForStmt):
            self.scopes.append({})
            for f in ("init", "condition", "update", "body"):
                v = getattr(node, f)
                if isinstance(v, ast.Declaration):
                    self._declaration(v, "local")
                elif v is not None:
                    nv = self.visit(v, node, f)
                    if self.pass_no == 2 and nv is not v:
                        setattr(node, f, nv)
            self.scopes.pop()
            return node
        if isinstance(node, ast.Declaration):
            self._declaration(node, "local" if len(self.scopes) > 1
                              else "file")
            return node
        if isinstance(node, ast.AsmDeclaration) and self.pass_no == 1:
            from .codegen import _asm_template_text
            self.asm_names.update(
                w.lower() for w in re.findall(
                    r"[A-Za-z_]\w*", _asm_template_text(node)))
        if isinstance(node, ast.ExpressionStmt):
            e = node.expr
            if isinstance(e, ast.BinaryOp) and _op(e) == "=":
                var = self._ident_var(e.left)
                if var is not None:
                    e._ptrabs_stmt = True
                    if self.pass_no == 2 and self._removable(var):
                        self.visit(e.right)
                        return ast.EmptyStmt(pos=node.pos)
        if self.pass_no == 1:
            if self._scan(node):
                return node
        else:
            r = self._replace(node)
            if r is not None:
                return r
            if self._is_owned(node):
                return node
        for f in node.__dataclass_fields__:
            if f == "pos":
                continue
            v = getattr(node, f)
            if isinstance(v, list) or hasattr(v, "__dataclass_fields__"):
                nv = self.visit(v, node, f)
                if self.pass_no == 2 and nv is not v:
                    setattr(node, f, nv)
        return node

    def visit_block(self, block, push=True):
        if push:
            self.scopes.append({})
        items = block.items
        for i, it in enumerate(list(items)):
            if isinstance(it, ast.Declaration):
                self._declaration(it, "local")
            else:
                nv = self.visit(it, items, i)
                if self.pass_no == 2 and nv is not it:
                    items[i] = nv
        if push:
            self.scopes.pop()

    def _ident_var(self, n):
        if isinstance(n, ast.Identifier):
            return self._lookup(n.name.text)
        return None

    def _is_owned(self, node):
        """The nodes whose children pass 1 has accounted for: a path, an
        assignment to a candidate, ``&p``, ``p++``."""
        if isinstance(node, ast.BinaryOp) and _op(node) in _ASSIGN:
            return self._ident_var(node.left) is not None
        if isinstance(node, (ast.UnaryOp, ast.PostfixOp)) and \
                _op(node) in ("++", "--", "&") and \
                self._ident_var(node.operand) is not None:
            return True
        return self._path(node) is not None

    # ---- pass 1: look at one expression node ---------------------------
    def _scan(self, node):
        """Account for ``node``.  True when its children are done."""
        if isinstance(node, ast.BinaryOp) and _op(node) in _ASSIGN:
            var = self._ident_var(node.left)
            if var is not None:
                if _op(node) == "=" and not var.is_const:
                    k = self._const_addr(node.right)
                    if k is None:
                        var.bad = True
                    else:
                        var.addrs.add(k)
                else:
                    var.bad = True
                if not getattr(node, "_ptrabs_stmt", False):
                    var.value_uses += 1       # the value of the assignment
                self.visit(node.right)
                return True
        if isinstance(node, (ast.UnaryOp, ast.PostfixOp)):
            op = _op(node)
            if op in ("++", "--", "&"):
                var = self._ident_var(node.operand)
                if var is not None:
                    if not (op == "&" and var.is_const):
                        var.bad = True
                    var.value_uses += 1
                    return True
        p = self._path(node)
        if p is not None:
            var, off, t, vol = p
            if vol and self._scalar_type(t):
                var.sites += 1
            else:
                var.value_uses += 1
            return True
        if isinstance(node, ast.Identifier):
            var = self._lookup(node.name.text)
            if var is not None:
                var.value_uses += 1
        return False

    # ---- pass 2 --------------------------------------------------------
    def _replace(self, node):
        if isinstance(node, ast.BinaryOp) and _op(node) in _ASSIGN:
            var = self._ident_var(node.left)
            if var is not None:
                nv = self.visit(node.right)
                node.right = nv
                return node
        p = self._path(node)
        if p is None:
            return None
        sc = self._scalar(p)
        if sc is None or not self._qualifies(p[0]):
            return None
        addr, t = sc
        base = lt.BasicType(name=t.name, is_signed=t.is_signed,
                            is_const=False, is_volatile=True)
        name = self.rw._symbol(addr, base)
        self.count += 1
        return ast.Identifier(name=absaddr._tok(name), pos=node.pos)

    def _qualifies(self, var):
        low = var.name.lower()
        # asm text names a C object with a leading underscore
        return (var.addr() is not None
                and low not in self.asm_names
                and "_" + low not in self.asm_names
                and low not in self.protected
                and "_" + low not in self.protected)

    def _removable(self, var):
        """No read of the pointer is left after the rewrite."""
        return (var.static_file and self._qualifies(var)
                and var.value_uses == 0 and var.sites > 0)


def rewrite(cg, unit, protected=()):
    """Rewrite ``unit`` in place.  Returns (the names that the pass made,
    {name: address}, the number of accesses changed)."""
    pr = PointerRewriter(cg, unit, protected)
    pr.pass_no = 1
    pr.scopes = [{}]
    pr.visit(unit.items)
    if not any(v is not None and pr._qualifies(v) and v.sites
               for v in pr.vars.values()):
        return [], {}, 0
    pr.scopes = [{}]
    pr.pass_no = 2
    pr.visit(unit.items)
    new = [d for d in pr.rw.decls
           if d.declarators[0].declarator.name.text not in cg.absolute]
    syms = {k: v for k, v in pr.rw.symbols.items() if k not in cg.absolute}
    unit.items = new + list(unit.items)
    return new, syms, pr.count
