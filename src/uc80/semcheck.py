"""Constraint checks that run on the parsed file, before code generation.

C23 has no implicit declarations, and a const object is not modifiable.
The code generator accepted both silently: an undeclared name became an
implicit external that failed only at link time (or never, for a name
that is only assigned), and ``*p--`` with ``T *const p`` modified p.

The checks walk each translation unit with its own scope stack:

  - a name that no scope declares is an error: a variable, or a function
    that is called without a declaration (6.5.1, 6.5.2.2 and 6.7.1 in
    the C23 text; there is no implicit int function any more);
  - ``=``, ``op=``, ``++`` and ``--`` need a modifiable lvalue (6.5.16,
    6.5.2.4, 6.5.3.1): an object with a const-qualified type, a member of
    a const structure, an object reached through a pointer to const, and
    a structure with a const member (6.3.2.1) are errors;
  - a variable length array is an error (__STDC_NO_VLA__ is 1).

Every message starts with ``file:line:`` of the source line (the
preprocessor map in ``srcmap`` turns a line of the preprocessed text
into the file and line of the source).  The pass reports every problem
that it finds, so one run shows all of them.
"""

from __future__ import annotations

import dataclasses

from uc_core import ast
from uc_core import _dcfast
from uc_core import ast_legacy as lt
from uc_core import codegen_helpers as ch
from uc_core.codegen_helpers import ResolvedType

from . import srcmap

_ASSIGN_OPS = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
                         "^=", "<<=", ">>="))
_SPECIAL_NAMES = frozenset(("__func__", "__FUNCTION__",
                            "__PRETTY_FUNCTION__", "nullptr"))


def _is_node(x) -> bool:
    return _dcfast.is_dataclass(x) and not isinstance(x, type)


def _children(node):
    for f in _dcfast.fields(node):
        if f.name == "pos":
            continue
        v = getattr(node, f.name, None)
        if isinstance(v, list):
            for c in v:
                if _is_node(c):
                    yield c
        elif _is_node(v):
            yield v


def _from_legacy(t):
    """A ResolvedType for the legacy type that a cast carries, or None."""
    if t is None:
        return None
    if isinstance(t, ResolvedType):
        return t
    if isinstance(t, lt.BasicType):
        return ResolvedType(kind="basic", name=t.name, is_const=t.is_const,
                            is_volatile=t.is_volatile)
    if isinstance(t, lt.PointerType):
        return ResolvedType(kind="pointer", pointee=_from_legacy(t.base_type),
                            is_const=t.is_const, is_volatile=t.is_volatile)
    if isinstance(t, lt.ArrayType):
        return ResolvedType(kind="array", element=_from_legacy(t.base_type))
    return None


class Scope:
    __slots__ = ("names", "tags")

    def __init__(self):
        self.names = {}     # name -> ("var" | "typedef" | "enum", type)
        self.tags = {}      # struct or union tag -> member tuple


class Checker:
    def __init__(self, path: str, allow_implicit_functions: bool = False,
                 allow_vla: bool = False):
        self.path = path
        self.allow_implicit_functions = allow_implicit_functions
        self.allow_vla = allow_vla
        self.errors = []        # (line, message), in the order found
        self.warnings = []
        self.scopes = [Scope()]
        self.reported = set()   # (function, name) already reported
        self.function = None
        self.in_sizeof = 0

    # ----- diagnostics ----------------------------------------------------

    def where(self, node, token=None) -> str:
        if token is None:
            token = _first_token(node)
        if token is not None:
            return srcmap.locate(token, self.path)
        pos = getattr(node, "pos", None)
        line = getattr(pos, "start_line", 0) if pos is not None else 0
        return srcmap.locate_line(self.path, line)

    def error(self, node, message, token=None):
        self.errors.append(f"{self.where(node, token)}: {message}")

    def warn(self, node, message, token=None):
        self.warnings.append(f"{self.where(node, token)}: warning: {message}")

    # ----- scopes ---------------------------------------------------------

    def push(self):
        self.scopes.append(Scope())

    def pop(self):
        self.scopes.pop()

    def declare(self, name, kind, ty, constexpr=False):
        if name:
            self.scopes[-1].names[name] = (kind, ty, constexpr)

    def lookup(self, name):
        for s in reversed(self.scopes):
            e = s.names.get(name)
            if e is not None:
                return e
        return None

    def lookup_tag(self, tag):
        for s in reversed(self.scopes):
            if tag in s.tags:
                return s.tags[tag]
        return None

    def _typedef(self, name):
        e = self.lookup(name)
        if e is not None and e[0] == "typedef" and e[1] is not None:
            return dataclasses.replace(e[1])
        return None

    # ----- entry ----------------------------------------------------------

    def run(self, unit):
        with ch.typedef_resolver_scope(self._typedef):
            for item in unit.items or []:
                self.external(item)
        return self.errors

    def external(self, item):
        if isinstance(item, ast.FunctionDef):
            self.function_def(item)
        elif isinstance(item, ast.Declaration):
            self.declaration(item)
        else:
            self.visit(item)

    # ----- declarations ---------------------------------------------------

    def specs(self, decl_specs):
        """Tags and enumerators that the specifiers define, and the
        expressions inside them."""
        for sp in decl_specs or []:
            if isinstance(sp, (ast.StructDef, ast.StructAnon)):
                rt = ch.resolve_base_type([sp])
                name = getattr(getattr(sp, "name", None), "text", None)
                if name:
                    self.scopes[-1].tags[name] = rt.members
                for m in sp.members or []:
                    if isinstance(m, (ast.StructMember, ast.StructAnonMember)):
                        self.specs(m.decl_specs)
                        for d in getattr(m, "declarators", None) or []:
                            self.declarator(d, member=True)
            elif isinstance(sp, (ast.EnumDef, ast.EnumAnon)):
                for v in sp.values or []:
                    if isinstance(v, ast.EnumValueWithInit):
                        self.visit(v.value)
                    self.declare(v.name.text, "enum",
                                 ResolvedType(kind="basic", name="int"))
            elif isinstance(sp, (ast.StructEmpty,)):
                name = sp.name.text
                self.scopes[-1].tags.setdefault(name, ())
            elif isinstance(sp, (ast.TypeofExpr, ast.TypeofUnqualExpr,
                                 ast.AlignasValue, ast.BitIntSpec)):
                self.visit(sp)
            elif isinstance(sp, ast.AlignasType):
                self.visit(sp)

    def declaration(self, decl):
        self.specs(decl.decl_specs)
        storage = ch.decl_storage_class(decl.decl_specs)
        base = ch.resolve_base_type(decl.decl_specs)
        constexpr = any(isinstance(sp, ast.StorageClass)
                        and sp.kw.text == "constexpr"
                        for sp in decl.decl_specs or [])
        for d in decl.declarators or []:
            if isinstance(d, ast.InitDeclaratorWithInit):
                inner, init = d.declarator, d.init
            elif isinstance(d, ast.InitDeclarator):
                inner, init = d.declarator, None
            else:
                continue
            name, full = ch._wrap_declarator(inner, dataclasses.replace(base))
            self.declarator(inner)
            if storage == "typedef":
                self.declare(name, "typedef", full)
                continue
            self.declare(name, "var", full, constexpr)
            if init is not None:
                self.visit(init)
                if len(self.scopes) < 2 or storage == "static" or constexpr:
                    bad = self.nonconstant_variable(init)
                    if bad is not None:
                        self.error(bad, "initializer element is not "
                                   "constant: '%s' is a variable (use an "
                                   "enumerator, a constexpr object or a "
                                   "macro)" % bad.name.text)
            self.check_vla(full, inner, storage)

    def check_vla(self, full, declarator, storage):
        """A block-scope array whose size is not a constant."""
        if self.allow_vla or len(self.scopes) < 2 or storage in (
                "extern", "typedef"):
            return
        t = full
        while t is not None and t.kind == "array":
            if t.size_expr is not None and not self.is_constant(t.size_expr):
                self.error(declarator, "variable length array: C23 makes "
                           "VLAs optional and uc80 does not implement them "
                           "(__STDC_NO_VLA__ is 1)")
                return
            t = t.element

    def declarator(self, node, member=False):
        """Visit the expressions in a declarator (array sizes, the
        parameters of a function declarator)."""
        if node is None:
            return
        if isinstance(node, (ast.FnDeclarator, ast.AbstractFn)):
            self.declarator(getattr(node, "inner", None))
            self.push()
            params = node.params
            if isinstance(params, ast.VariadicParams):
                params = params.params
            for p in params or []:
                self.param(p)
            self.pop()
            return
        if isinstance(node, (ast.PlainDeclarator, ast.BitFieldDecl,
                             ast.AnonBitFieldDecl)):
            if isinstance(node, ast.PlainDeclarator):
                self.declarator(node.declarator)
            elif isinstance(node, ast.BitFieldDecl):
                self.declarator(node.declarator)
                self.visit(node.bit_width)
            else:
                self.visit(node.bit_width)
            return
        if isinstance(node, (ast.InitDeclarator, ast.InitDeclaratorWithInit)):
            self.declarator(node.declarator)
            return
        if isinstance(node, ast.Declarator):
            return
        for c in _children(node):
            if isinstance(c, ast.PointerDeclarator) or "Declarator" in type(
                    c).__name__ or type(c).__name__.startswith("Abstract") \
                    or type(c).__name__ in ("PointerOne", "PointerNested",
                                            "GroupDeclarator"):
                self.declarator(c)
            else:
                self.visit(c)

    def param(self, p):
        if isinstance(p, ast.ParamDecl):
            self.specs(p.decl_specs)
            _, pt = ch.resolve_type_from_decl(p.decl_specs, p.declarator)
            self.declarator(p.declarator)
            self.declare(ch.declarator_ident(p.declarator), "var",
                         _decay_param(pt))
        elif isinstance(p, ast.ParamDeclAbstract):
            self.specs(p.decl_specs)
            self.declarator(p.declarator)
        elif isinstance(p, ast.ParamDeclTypeOnly):
            self.specs(p.decl_specs)

    def function_def(self, fd):
        self.specs(fd.decl_specs)
        base = ch.resolve_base_type(fd.decl_specs)
        name, full = ch._wrap_declarator(fd.declarator, base)
        self.declare(name, "var", full)
        fn = ch._outermost_fn_declarator(fd.declarator)
        self.function = name
        self.push()
        if isinstance(fn, ast.FnDeclarator):
            params = fn.params
            if isinstance(params, ast.VariadicParams):
                params = params.params
            for p in params or []:
                self.param(p)
        self.visit(fd.body)
        self.pop()
        self.function = None

    # ----- expressions and statements ------------------------------------

    def visit(self, node):
        if node is None or not _is_node(node):
            return
        m = getattr(self, "v_" + type(node).__name__, None)
        if m is not None:
            m(node)
            return
        for c in _children(node):
            self.visit(c)

    def v_CompoundStmt(self, node):
        self.push()
        for it in node.items or []:
            if isinstance(it, ast.Declaration):
                self.declaration(it)
            else:
                self.visit(it)
        self.pop()

    def v_StmtExpr(self, node):
        self.visit(node.stmt)

    def v_ForStmt(self, node):
        self.push()
        if isinstance(node.init, ast.Declaration):
            self.declaration(node.init)
        else:
            self.visit(node.init)
        self.visit(node.condition)
        self.visit(node.update)
        self.visit(node.body)
        self.pop()

    def v_Declaration(self, node):
        self.declaration(node)

    def v_FunctionDef(self, node):
        self.function_def(node)

    def v_Identifier(self, node):
        self.use(node, node.name.text, called=False)

    def use(self, node, name, called):
        if (name in _SPECIAL_NAMES or name.startswith("__builtin_")
                or self.lookup(name) is not None):
            return
        key = (self.function, name, called)
        if key in self.reported:
            return
        self.reported.add(key)
        if called:
            msg = (f"implicit declaration of function '{name}': C23 has no "
                   f"implicit declarations, declare the function first")
            if self.allow_implicit_functions:
                self.warn(node, msg)
                return
            self.error(node, msg)
        else:
            self.error(node, f"'{name}' undeclared (first use in "
                       + (f"function '{self.function}')" if self.function
                          else "this file)"))

    def v_Call(self, node):
        f = node.func
        if isinstance(f, ast.Identifier):
            self.use(f, f.name.text, called=True)
        else:
            self.visit(f)
        for a in node.args or []:
            self.visit(a)

    def v_CallNoArgs(self, node):
        self.v_Call(node)

    def v_UnaryOp(self, node):
        op = node.op.text
        if op == "&&":
            return                      # the address of a label
        self.visit(node.operand)
        if op in ("++", "--"):
            self.modify(node.operand, op)

    def v_PostfixOp(self, node):
        self.visit(node.operand)
        self.modify(node.operand, node.op.text)

    def v_BinaryOp(self, node):
        self.visit(node.left)
        self.visit(node.right)
        op = node.op.text
        if op in _ASSIGN_OPS:
            self.modify(node.left, op)

    def v_OffsetofExpr(self, node):
        for c in _children(node):
            if isinstance(c, (ast.TypeName, ast.TypeNameWithDeclarator)):
                continue
            self.visit(c)

    # ----- types of expressions ------------------------------------------

    def etype(self, e):
        """ResolvedType of an lvalue-ish expression, or None if unknown."""
        if isinstance(e, ast.Identifier):
            ent = self.lookup(e.name.text)
            return ent[1] if ent and ent[0] == "var" else None
        if isinstance(e, ast.UnaryOp):
            op = e.op.text
            if op == "*":
                return _deref(self.etype(e.operand))
            if op == "&":
                t = self.etype(e.operand)
                return (ResolvedType(kind="pointer", pointee=t)
                        if t is not None else None)
            if op in ("+", "-", "++", "--"):
                t = self.etype(e.operand)
                return t if t is not None and t.kind == "pointer" else None
            return None
        if isinstance(e, ast.PostfixOp):
            t = self.etype(e.operand)
            return t if t is not None and t.kind == "pointer" else None
        if isinstance(e, ast.Index):
            for a in (e.array, e.index):
                t = self.etype(a)
                if t is not None and t.kind in ("pointer", "array"):
                    return _deref(t)
            return None
        if isinstance(e, ast.BinaryOp):
            if e.op.text in ("+", "-"):
                t = self.etype(e.left)
                if t is not None and t.kind in ("pointer", "array"):
                    return (t if t.kind == "pointer" else ResolvedType(
                        kind="pointer", pointee=t.element))
                t = self.etype(e.right)
                if (e.op.text == "+" and t is not None
                        and t.kind in ("pointer", "array")):
                    return (t if t.kind == "pointer" else ResolvedType(
                        kind="pointer", pointee=t.element))
            return None
        if isinstance(e, (ast.Member, ast.ArrowMember)):
            ot = self.etype(e.obj)
            if isinstance(e, ast.ArrowMember):
                ot = _deref(ot)
            return self.member_type(ot, e.member.text)
        if isinstance(e, ast.Cast):
            return _from_legacy(e.target_type)
        if isinstance(e, (ast.CompoundLiteral, ast.CompoundLiteralEmpty)):
            return _from_legacy(e.target_type)
        if isinstance(e, (ast.Call, ast.CallNoArgs)):
            ft = self.etype(e.func)
            if ft is not None and ft.kind == "pointer":
                ft = ft.pointee
            if ft is not None and ft.kind == "function":
                return ft.return_type
            return None
        return None

    def members_of(self, t):
        if t is None or t.kind != "struct":
            return None
        if t.members or not t.name:
            return t.members
        return self.lookup_tag(t.name) or ()

    def member_type(self, ot, name):
        mem = self.members_of(ot)
        if mem is None:
            return None
        for nm, mt, _bw in mem:
            if nm == name:
                return _with_const(mt, ot.is_const)
            if nm is None and mt is not None and mt.kind == "struct":
                sub = self.member_type(_with_const(mt, ot.is_const), name)
                if sub is not None:
                    return sub
        return None

    def has_const_member(self, t, depth=0):
        if depth > 8 or t is None:
            return False
        if t.kind == "array":
            return t.is_const or self.has_const_member(t.element, depth + 1)
        if t.kind != "struct":
            return False
        for _nm, mt, _bw in self.members_of(t) or ():
            if mt is None:
                continue
            if mt.is_const or self.has_const_member(mt, depth + 1):
                return True
        return False

    # ----- modification ---------------------------------------------------

    def modify(self, lvalue, op):
        t = self.etype(lvalue)
        if t is None or t.kind == "array" or t.kind == "function":
            return
        if t.is_const:
            what = "read-only " + self.describe(lvalue)
        elif t.kind == "struct" and op == "=" and self.has_const_member(t):
            what = "a structure with a read-only member, " + self.describe(
                lvalue, bare=True)
        else:
            return
        if op == "++":
            verb = "increment of"
        elif op == "--":
            verb = "decrement of"
        else:
            verb = "assignment of"
        self.error(lvalue, f"{verb} {what} (C23 6.5.16, 6.5.2.4, 6.5.3.1: "
                   f"the operand must be a modifiable lvalue)")

    def describe(self, e, bare=False):
        text = _expr_text(e)
        if bare:
            return f"'{text}'"
        if isinstance(e, ast.Identifier):
            return f"variable '{text}'"
        if isinstance(e, (ast.Member, ast.ArrowMember)):
            return f"member '{e.member.text}' ('{text}')"
        return f"location '{text}'"

    # ----- constants ------------------------------------------------------

    def is_null_member_address(self, e):
        """True for ((T *)constant)->m, .m and [constant] chains."""
        while True:
            if isinstance(e, (ast.Member, ast.ArrowMember)):
                e = e.obj
            elif isinstance(e, ast.Index):
                if not self.is_constant(e.index):
                    return False
                e = e.array
            else:
                break
        return isinstance(e, ast.Cast) and self.is_constant(e.expr)

    def nonconstant_variable(self, e):
        """The Identifier of a variable that the initializer ``e`` of a
        static-storage object reads as a value (6.7.10p5: the initializer
        has to be a constant expression), or None.  The walk is
        conservative: it looks only through operators, casts and braces,
        so an address, a sizeof, a call or a member never counts."""
        if isinstance(e, list):
            for x in e:
                r = self.nonconstant_variable(x)
                if r is not None:
                    return r
            return None
        if isinstance(e, ast.Identifier):
            ent = self.lookup(e.name.text)
            if ent is None or ent[0] != "var" or (len(ent) > 2 and ent[2]):
                return None
            t = ent[1]
            if t is None or t.kind in ("array", "function"):
                return None
            return e
        if isinstance(e, ast.Cast):
            return self.nonconstant_variable(e.expr)
        if isinstance(e, ast.TernaryOp):
            return self.nonconstant_variable(
                [e.condition, e.true_expr, e.false_expr])
        if isinstance(e, ast.BinaryOp):
            return self.nonconstant_variable([e.left, e.right])
        if isinstance(e, ast.UnaryOp) and e.op.text in ("-", "+", "~", "!"):
            return self.nonconstant_variable(e.operand)
        if isinstance(e, ast.InitializerList):
            return self.nonconstant_variable(list(e.values or []))
        if isinstance(e, ast.DesignatedInit):
            return self.nonconstant_variable(e.value)
        return None

    def is_constant(self, e):
        """True for an integer constant expression as far as the walk can
        tell: literals, enumerators, sizeof, casts and operators of
        those."""
        if e is None:
            return True
        if isinstance(e, (ast.IntLiteral, ast.CharLiteral, ast.SizeofType,
                          ast.AlignofType, ast.SizeofExpr, ast.OffsetofExpr,
                          ast.FloatLiteral, ast.NullptrLiteral)):
            return True
        if isinstance(e, ast.Identifier):
            ent = self.lookup(e.name.text)
            return ent is None or ent[0] == "enum" or (
                ent[0] == "var" and len(ent) > 2 and ent[2])
        if isinstance(e, ast.Cast):
            return self.is_constant(e.expr)
        if isinstance(e, ast.TernaryOp):
            return all(self.is_constant(x) for x in
                       (e.condition, e.true_expr, e.false_expr))
        if isinstance(e, ast.BinaryOp):
            return self.is_constant(e.left) and self.is_constant(e.right)
        if isinstance(e, ast.SequenceExpr):
            return False
        if isinstance(e, ast.UnaryOp):
            if e.op.text == "&" and self.is_null_member_address(e.operand):
                return True     # the offsetof idiom &((T *)0)->m
            return (e.op.text not in ("*", "&", "++", "--")
                    and self.is_constant(e.operand))
        if isinstance(e, (ast.Call, ast.CallNoArgs)):
            f = e.func
            return (isinstance(f, ast.Identifier)
                    and f.name.text.startswith("__builtin_")
                    and all(self.is_constant(a) for a in e.args or []))
        if isinstance(e, (ast.GenericSelection, ast.StmtExpr)):
            return True     # the optimizer or the code generator decides
        return False


def _decay_param(t):
    if t is not None and t.kind == "array":
        return ResolvedType(kind="pointer", pointee=t.element)
    if t is not None and t.kind == "function":
        return ResolvedType(kind="pointer", pointee=t)
    return t


def _deref(t):
    if t is None:
        return None
    if t.kind == "pointer":
        return t.pointee
    if t.kind == "array":
        return _with_const(t.element, t.is_const)
    return None


def _with_const(t, const):
    if t is None or not const or t.is_const:
        return t
    return dataclasses.replace(t, is_const=True)


def _first_token(node):
    """The first token that a node holds (its leftmost name or literal)."""
    if node is None:
        return None
    for name in ("name", "value", "kw", "op", "label", "member"):
        v = getattr(node, name, None)
        if v is not None and hasattr(v, "line") and hasattr(v, "file_id"):
            return v
    for f in _dcfast.fields(node):
        if f.name == "pos":
            continue
        v = getattr(node, f.name, None)
        cands = v if isinstance(v, list) else [v]
        for c in cands:
            if _is_node(c):
                t = _first_token(c)
                if t is not None:
                    return t
    return None


def _expr_text(e, depth=0):
    if depth > 6:
        return "..."
    if isinstance(e, ast.Identifier):
        return e.name.text
    if isinstance(e, ast.UnaryOp):
        return e.op.text + _expr_text(e.operand, depth + 1)
    if isinstance(e, ast.PostfixOp):
        return _expr_text(e.operand, depth + 1) + e.op.text
    if isinstance(e, ast.Member):
        return _expr_text(e.obj, depth + 1) + "." + e.member.text
    if isinstance(e, ast.ArrowMember):
        return _expr_text(e.obj, depth + 1) + "->" + e.member.text
    if isinstance(e, ast.Index):
        return (_expr_text(e.array, depth + 1) + "["
                + _expr_text(e.index, depth + 1) + "]")
    if isinstance(e, ast.IntLiteral):
        return e.value.text
    if isinstance(e, ast.BinaryOp):
        return (_expr_text(e.left, depth + 1) + " " + e.op.text + " "
                + _expr_text(e.right, depth + 1))
    if isinstance(e, ast.Cast):
        return "(cast)" + _expr_text(e.expr, depth + 1)
    if isinstance(e, (ast.Call, ast.CallNoArgs)):
        return _expr_text(e.func, depth + 1) + "(...)"
    return "expression"


def check(unit, path: str, allow_implicit_functions: bool = False,
          allow_vla: bool = False):
    """Check one parsed file.  Returns (errors, warnings): lists of
    ready-to-print messages that start with file:line."""
    c = Checker(path, allow_implicit_functions, allow_vla)
    c.run(unit)
    return c.errors, c.warnings
