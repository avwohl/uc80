"""Volatile objects at constant addresses become absolute symbols.

``*(volatile unsigned char *)0xF900`` is a hardware register.  The code
generator knows how to load, store and update a global variable in the
best way (``LD A,(nn)``, ``LD (nn),A``, ``LD HL,(nn)``), but a pointer
dereference goes through HL.  This pass replaces each such dereference
with a name, and the name gets an EQU with the address and no storage, the
same as an ``__at`` object (docs/rom.md).  Only a volatile integer object
is changed: the volatile rules of the compiler then apply to the name.

    *(volatile T *)K            -> name at K
    ((volatile T *)K)[i]        -> name at K + i * sizeof (T)   (i constant)
"""

from uc_core import ast
from uc_core import ast_legacy as lt
from uc_core._const import int_value

_SIZES = {"char": 1, "short": 2, "int": 2, "long": 4, "long long": 8}


def _op(node):
    op = node.op
    return op.text if hasattr(op, "text") else op


def _tok(text, name="IDENT"):
    from uc_core.c23_parser import Token
    return Token(name=name, text=text, line=0, column=0, offset=0, file_id=0)


def _const(node, const_int):
    """The value of an integer constant expression, else None."""
    k = const_int(node)
    return k


class AbsoluteRewriter:
    def __init__(self, const_int, type_size):
        self.const_int = const_int
        self.type_size = type_size
        self.symbols = {}       # name -> address
        self.decls = []         # new Declaration nodes
        self._by_key = {}

    def _cast_address(self, node):
        """(K, pointee) when node is (volatile T *)K, T an integer type."""
        if not isinstance(node, ast.Cast):
            return None
        t = node.target_type
        if not isinstance(t, lt.PointerType):
            return None
        base = t.base_type
        if not (isinstance(base, lt.BasicType) and base.is_volatile
                and base.name in _SIZES):
            return None
        k = self.const_int(node.expr)
        if k is None:
            return None
        return k & 0xFFFF, base

    def _symbol(self, addr, base):
        key = (addr, base.name, base.is_signed, base.is_const)
        name = self._by_key.get(key)
        if name is None:
            tag = {"char": "c", "short": "s", "int": "i", "long": "l",
                   "long long": "q"}[base.name]
            sg = "u" if base.is_signed is False else (
                "s" if base.is_signed else "")
            name = f"__abs{addr:04X}{sg}{tag}"
            self._by_key[key] = name
            self.symbols[name] = addr
            specs = [ast.TypeQualifier(kw=_tok("volatile", "KW_VOLATILE"))]
            if base.is_const:
                specs.insert(0, ast.TypeQualifier(
                    kw=_tok("const", "KW_CONST")))
            if base.is_signed is False:
                specs.append(ast.BasicTypeSpec(
                    kw=_tok("unsigned", "KW_UNSIGNED")))
            elif base.is_signed is True:
                specs.append(ast.BasicTypeSpec(kw=_tok("signed", "KW_SIGNED")))
            for w in base.name.split():
                specs.append(ast.BasicTypeSpec(kw=_tok(w, "KW_" + w.upper())))
            self.decls.append(ast.Declaration(
                decl_specs=specs,
                declarators=[ast.InitDeclarator(
                    declarator=ast.Declarator(name=_tok(name)))]))
        return name

    def replacement(self, node):
        if isinstance(node, ast.UnaryOp) and _op(node) == "*":
            found = self._cast_address(node.operand)
            if found is not None:
                addr, base = found
                return ast.Identifier(name=_tok(self._symbol(addr, base)),
                                      pos=node.pos)
        if isinstance(node, ast.Index):
            found = self._cast_address(node.array)
            i = self.const_int(node.index)
            if found is not None and i is not None:
                addr, base = found
                addr = (addr + i * _SIZES[base.name]) & 0xFFFF
                return ast.Identifier(name=_tok(self._symbol(addr, base)),
                                      pos=node.pos)
        return None

    def visit(self, node):
        if isinstance(node, list):
            return [self.visit(x) for x in node]
        if not hasattr(node, "__dataclass_fields__"):
            return node
        r = self.replacement(node)
        if r is not None:
            return r
        for f in node.__dataclass_fields__:
            if f == "pos":
                continue
            v = getattr(node, f)
            if isinstance(v, list) or hasattr(v, "__dataclass_fields__"):
                nv = self.visit(v)
                if nv is not v:
                    setattr(node, f, nv)
        return node


def rewrite(unit, const_int, type_size=None):
    """Rewrite ``unit`` in place.  Returns {name: address}."""
    rw = AbsoluteRewriter(const_int, type_size)
    for item in unit.items:
        if isinstance(item, ast.FunctionDef) and item.body is not None:
            rw.visit(item.body)
        elif isinstance(item, ast.Declaration):
            for d in item.declarators or []:
                init = getattr(d, "init", None)
                if init is not None:
                    nv = rw.visit(init)
                    if nv is not init:
                        d.init = nv
    unit.items = rw.decls + list(unit.items)
    return rw.symbols
