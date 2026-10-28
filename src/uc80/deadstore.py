"""Dead-local elimination (docs/dead_store.md).

A local variable that no expression ever reads is dead: every store to
the variable can go.  The pass finds such variables on the AST of each
function, removes the stores and the declaration, and repeats until
nothing changes (removing ``count -= !flags[k]`` can make a name inside
the removed expression dead too).

What counts as a store of a variable ``x``, with the value not used::

    x = e;   x op= e;   x++;   --x;   T x = e;

in a statement, in the init or update part of ``for`` and in either half
of a comma expression whose value is not used.  Any other occurrence of
the name is a read, so ``y = x``, ``a[x] = 0``, ``f(x)``, ``sizeof x``
and ``z = (x = 1)`` all keep ``x``.

A candidate is a plain scalar or pointer local, declared once in the
function, with no storage class but ``register``/``auto``, not volatile,
and with no ``&`` on its name.  Parameters, statics, arrays and the
names of globals are never candidates.  A function with inline assembly,
a statement expression or ``setjmp`` is left alone.

An expression of a removed store that may have an effect (a call, ``++``,
a store, a volatile access) stays as an expression statement.  Only an
expression that has no effect at all goes.

The second part removes a store that a later store of the same list of
statements overwrites, when no statement between the two names the
variable and none holds a label or a jump (``int i = 0; ...; for (i = 0;
...)``).  A candidate variable only, so no call and no pointer store can
read the value.
"""

from __future__ import annotations

import copy
import os
import re
import sys

from uc_core import ast

from .addrcse import _fields, _is_node

_ASSIGN = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=",
           ">>=")
_PURE_UNARY = ("+", "-", "~", "!", "*", "&")
_BAD_SPECS = ("StructDef", "StructRef", "StructEmpty", "StructAnon",
              "StructAnonEmpty", "EnumDef", "EnumRef", "EnumAnon",
              "TypeofExpr", "TypeofUnqualExpr", "BitIntSpec", "AlignasType",
              "AlignasValue")
_ARRAYISH = ("Array", "Fn", "Abstract")


def _op(n) -> str:
    o = n.op
    return o.text if hasattr(o, "text") else o


def _walk(x):
    """Every AST node under ``x`` (``x`` included), preorder."""
    stack = [x]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(reversed(n))
            continue
        if not _is_node(n):
            continue
        yield n
        kids = []
        for f in _fields(n):
            v = getattr(n, f, None)
            if v is not None and not isinstance(v, (str, int)):
                kids.append(v)
        stack.extend(reversed(kids))


def _unsafe(body) -> bool:
    """A function the pass leaves alone: inline assembly (a label alone
    is no code), a statement expression or setjmp."""
    for n in _walk(body):
        cls = type(n).__name__
        if cls == "StmtExpr":
            return True
        if cls == "AsmDeclaration":
            if n.clauses or not _label_only(n):
                return True
        elif cls == "Identifier" and "setjmp" in n.name.text.lower():
            return True
    return False


def _label_only(asm) -> bool:
    """``asm("name::")``: a label, no instruction."""
    text = ""
    for lit in asm.template or []:
        if not isinstance(lit, ast.StringLiteral):
            return False
        text += lit.value.text[1:-1]
    return re.fullmatch(r"\s*[A-Za-z_][A-Za-z_0-9]*::?\s*", text) is not None


def _decl_name(d):
    """The name of an init-declarator, and whether the declarator is a
    plain scalar or pointer (no array, no function)."""
    x = d.declarator if hasattr(d, "declarator") else d
    simple = True
    while x is not None:
        cls = type(x).__name__
        if cls == "Declarator":
            return x.name.text, simple
        if cls.startswith(_ARRAYISH):
            simple = False
        x = getattr(x, "inner", None) or getattr(x, "declarator", None)
    return None, False


def _specs_ok(decl) -> bool:
    for sp in decl.decl_specs or []:
        cls = type(sp).__name__
        if cls == "StorageClass":
            if sp.kw.text not in ("register", "auto"):
                return False
        elif cls == "TypeQualifier":
            if sp.kw.text != "const":
                return False
        elif cls in _BAD_SPECS:
            return False
    return True


class _Fn:
    def __init__(self, gen, func, file_names, stats):
        from .reglocals_gen import address_taken_names
        self.g = gen
        self.func = func
        self.stats = stats
        self.vol = gen._vol
        self.vol_names = gen._vol.names_for(func)
        self.addr_taken = address_taken_names(func.body)
        self.file_names = file_names
        self.debug = bool(os.environ.get("UC80_DSE_DEBUG"))
        self.cands: set = set()
        self.reads: dict = {}
        self.skip_name = None

    # -- purity -----------------------------------------------------------

    def pure(self, e) -> bool:
        """The evaluation of ``e`` changes nothing and reads nothing
        volatile."""
        if e is None:
            return True
        for n in _walk(e):
            cls = type(n).__name__
            if cls in ("Call", "CallNoArgs", "PostfixOp", "StmtExpr",
                       "VaArgExpr", "CompoundLiteral",
                       "CompoundLiteralEmpty", "GenericSelection",
                       "AsmDeclaration", "InitializerList"):
                return False
            if cls == "UnaryOp" and _op(n) not in _PURE_UNARY:
                return False
            if cls == "BinaryOp" and _op(n) in _ASSIGN:
                return False
        return not self.vol.touches(e, self.vol_names)

    # -- candidates ---------------------------------------------------------

    def find_candidates(self) -> None:
        from . import codegen as cg
        counts: dict = {}
        ok: dict = {}
        for p in cg.function_params(self.func):
            nm = None
            if isinstance(p, ast.ParamDecl):
                nm = cg.declarator_ident(p.declarator)
            if nm:
                counts[nm] = counts.get(nm, 0) + 1
                ok[nm] = False
        for n in _walk(self.func.body):
            cls = type(n).__name__
            if cls == "Declaration":
                specs_ok = _specs_ok(n)
                for d in n.declarators or []:
                    nm, simple = _decl_name(d)
                    if nm is None:
                        continue
                    counts[nm] = counts.get(nm, 0) + 1
                    ok[nm] = specs_ok and simple
            elif cls in ("EnumValue", "EnumValueWithInit"):
                counts[n.name.text] = counts.get(n.name.text, 0) + 2
            elif cls in ("StructMember", "StructAnonMember"):
                pass
        for nm, c in counts.items():
            if (c == 1 and ok.get(nm) and nm not in self.file_names
                    and nm not in self.vol_names
                    and nm not in self.addr_taken):
                self.cands.add(nm)

    # -- counting -----------------------------------------------------------

    def count(self) -> None:
        self.reads = {nm: 0 for nm in self.cands}
        self.skip_name = None
        self.visit(self.func.body)

    def visit(self, n) -> None:
        if isinstance(n, (list, tuple)):
            for x in n:
                self.visit(x)
            return
        if not _is_node(n):
            return
        cls = type(n).__name__
        if cls == "Identifier":
            nm = n.name.text
            if nm in self.reads and nm != self.skip_name:
                self.reads[nm] += 1
            return
        if cls == "ExpressionStmt":
            self.discarded(n.expr)
            return
        if cls == "ForStmt":
            init = n.init
            if isinstance(init, ast.Declaration):
                for d in init.declarators or []:
                    nm, _s = _decl_name(d)
                    if nm in self.reads:
                        self.reads[nm] += 1     # pinned
                self.visit(init)
            else:
                self.discarded(init)
            self.visit(n.condition)
            self.discarded(n.update)
            self.visit(n.body)
            return
        if cls == "Declaration":
            multi = len(n.declarators or []) > 1
            for d in n.declarators or []:
                nm, _s = _decl_name(d)
                init = getattr(d, "init", None)
                if nm in self.reads and init is not None:
                    if isinstance(init, ast.InitializerList):
                        self.reads[nm] += 1
                    elif multi and not self.pure(init):
                        self.reads[nm] += 1
            self.visit(n.declarators)
            self.visit(n.decl_specs)
            return
        for f in _fields(n):
            v = getattr(n, f, None)
            if v is not None and not isinstance(v, (str, int)):
                self.visit(v)

    def store_target(self, e):
        """The candidate name that the discarded expression ``e`` only
        stores into, or None."""
        cls = type(e).__name__
        if cls == "BinaryOp" and _op(e) in _ASSIGN:
            if isinstance(e.left, ast.Identifier) and e.left.name.text in self.reads:
                return e.left.name.text
        elif cls in ("UnaryOp", "PostfixOp") and _op(e) in ("++", "--"):
            if (isinstance(e.operand, ast.Identifier)
                    and e.operand.name.text in self.reads):
                return e.operand.name.text
        return None

    def discarded(self, e) -> None:
        if e is None:
            return
        if isinstance(e, ast.SequenceExpr):
            self.discarded(e.left)
            self.discarded(e.right)
            return
        nm = self.store_target(e)
        if nm is None:
            self.visit(e)
            return
        if isinstance(e, ast.BinaryOp):
            # A read of the name inside a pure right side is no read:
            # the right side goes with the store.
            old = self.skip_name
            if self.pure(e.right):
                self.skip_name = nm
            try:
                self.visit(e.right)
            finally:
                self.skip_name = old

    # -- rewriting ------------------------------------------------------------

    def simplify(self, e, dead):
        """The discarded expression ``e`` without its stores to the dead
        names; None when nothing is left."""
        if e is None:
            return None
        if isinstance(e, ast.ExpressionStmt):
            inner = self.simplify(e.expr, dead)
            if inner is e.expr:
                return e
            return None if inner is None else ast.ExpressionStmt(expr=inner)
        if isinstance(e, ast.SequenceExpr):
            l = self.simplify(e.left, dead)
            r = self.simplify(e.right, dead)
            if l is e.left and r is e.right:
                return e
            if l is None:
                return r
            if r is None:
                return l
            return ast.SequenceExpr(left=l, right=r)
        nm = self.store_target(e)
        if nm is None or nm not in dead:
            return e
        self.stats["stores"] = self.stats.get("stores", 0) + 1
        if isinstance(e, ast.BinaryOp):
            return None if self.pure(e.right) else e.right
        return None

    def stmts(self, items: list) -> list:
        out = []
        for it in items:
            out.extend(self.stmt(it))
        return out

    def slot(self, node, field) -> None:
        v = getattr(node, field, None)
        if v is None:
            return
        new = self.stmt(v)
        if len(new) == 1:
            setattr(node, field, new[0])
        elif not new:
            setattr(node, field, ast.EmptyStmt())
        else:
            setattr(node, field, ast.CompoundStmt(items=new))

    def stmt(self, s) -> list:
        dead = self.dead
        cls = type(s).__name__
        if cls == "ExpressionStmt":
            e = self.simplify(s.expr, dead)
            if e is s.expr:
                return [s]
            if e is None:
                return []
            s.expr = e
            return [s]
        if cls == "CompoundStmt":
            s.items[:] = self.stmts(s.items)
            return [s]
        if cls == "Declaration":
            return self.decl(s)
        if cls == "ForStmt":
            if not isinstance(s.init, ast.Declaration):
                s.init = self.simplify(s.init, dead)
            s.update = self.simplify(s.update, dead)
            self.slot(s, "body")
            return [s]
        if cls in ("IfStmt", "IfStmtElse"):
            self.slot(s, "then_branch")
            if cls == "IfStmtElse":
                self.slot(s, "else_branch")
            return [s]
        if cls in ("WhileStmt", "DoWhileStmt", "SwitchStmt"):
            self.slot(s, "body")
            return [s]
        if cls in ("LabelStmt", "CaseStmt", "DefaultStmt"):
            self.slot(s, "stmt")
            return [s]
        return [s]

    def decl(self, s) -> list:
        dead = self.dead
        keep = []
        extra = []
        changed = False
        for d in s.declarators or []:
            nm, _simple = _decl_name(d)
            if nm in dead:
                changed = True
                init = getattr(d, "init", None)
                if init is not None and not self.pure(init):
                    extra.append(ast.ExpressionStmt(expr=init))
                self.stats["decls"] = self.stats.get("decls", 0) + 1
                continue
            keep.append(d)
        if not changed:
            return [s]
        out = []
        if keep:
            s.declarators = keep
            out.append(s)
        out.extend(extra)
        return out

    def mentions(self, names) -> bool:
        """Does an expression or a declaration still name one of ``names``?"""
        for n in _walk(self.func.body):
            if isinstance(n, ast.Identifier) and n.name.text in names:
                return True
            if isinstance(n, ast.Declaration):
                for d in n.declarators or []:
                    if _decl_name(d)[0] in names:
                        return True
        return False

    # -- a store that a later store overwrites -----------------------------------

    @staticmethod
    def refs(node, nm) -> bool:
        for n in _walk(node):
            if isinstance(n, ast.Identifier) and n.name.text == nm:
                return True
        return False

    @staticmethod
    def jumps(node) -> bool:
        """A label or a jump: the flow may enter or leave in the middle."""
        for n in _walk(node):
            if type(n).__name__ in ("LabelStmt", "CaseStmt", "DefaultStmt",
                                    "GotoStmt", "BreakStmt", "ContinueStmt"):
                return True
        return False

    def overwrite_of(self, item):
        """The name that ``item`` sets without a read of the name, when the
        statement runs first on the path: ``x = e;`` or ``for (x = e; ...``."""
        e = None
        if isinstance(item, ast.ExpressionStmt):
            e = item.expr
        elif isinstance(item, ast.ForStmt) and not isinstance(
                item.init, ast.Declaration):
            e = item.init
            while isinstance(e, ast.ExpressionStmt):
                e = e.expr
        if (isinstance(e, ast.BinaryOp) and _op(e) == "="
                and isinstance(e.left, ast.Identifier)):
            nm = e.left.name.text
            if nm in self.cands and not self.refs(e.right, nm):
                return nm
        return None

    def note(self, nm) -> None:
        if self.debug:
            print(f"dse: {self.g_name}: overwritten {nm}", file=sys.stderr)

    def kill(self, items, i, nm) -> bool:
        """Is the store at ``items[i]`` to ``nm`` overwritten by a later
        item of the list before anything reads it?"""
        for k in range(i + 1, len(items)):
            it = items[k]
            if self.overwrite_of(it) == nm:
                return True
            if isinstance(it, ast.ForStmt) and isinstance(it.init, ast.Declaration):
                if self.refs(it, nm) or self.jumps(it):
                    return False
                continue
            if self.refs(it, nm) or self.jumps(it):
                return False
            if isinstance(it, ast.Declaration):
                continue
        return False

    def overwritten(self, items: list) -> bool:
        """Remove each store in ``items`` that a later store of the same
        list overwrites.  True when something went."""
        changed = False
        i = 0
        while i < len(items):
            it = items[i]
            nm = None
            if isinstance(it, ast.ExpressionStmt):
                e = it.expr
                if (isinstance(e, ast.BinaryOp) and _op(e) == "="
                        and isinstance(e.left, ast.Identifier)
                        and e.left.name.text in self.cands):
                    nm = e.left.name.text
                    if self.kill(items, i, nm):
                        self.note(nm)
                        new = self.simplify(e, {nm})
                        if new is None:
                            del items[i]
                            changed = True
                            continue
                        it.expr = new
                        changed = True
            elif isinstance(it, ast.Declaration):
                ds = it.declarators or []
                for di, d in enumerate(ds):
                    if not isinstance(d, ast.InitDeclaratorWithInit):
                        continue
                    nm, _s = _decl_name(d)
                    init = d.init
                    if nm not in self.cands or isinstance(init, ast.InitializerList):
                        continue
                    # an effect of the initializer stays, in place, only
                    # when the declaration has no other declarator
                    if len(ds) > 1 and not self.pure(init):
                        continue
                    # a later declarator of the declaration may read nm
                    if any(self.refs(o, nm) for o in ds[di + 1:]):
                        continue
                    if self.kill(items, i, nm):
                        self.note(nm)
                        ds[di] = ast.InitDeclarator(declarator=d.declarator)
                        self.stats["stores"] = self.stats.get("stores", 0) + 1
                        changed = True
                        if not self.pure(init):
                            items.insert(i + 1, ast.ExpressionStmt(expr=init))
            for f in ("body", "then_branch", "else_branch", "stmt"):
                sub = getattr(it, f, None)
                if sub is not None:
                    changed |= self.overwritten_in(sub)
            if isinstance(it, ast.CompoundStmt):
                changed |= self.overwritten(it.items)
            i += 1
        return changed

    def overwritten_in(self, node) -> bool:
        if isinstance(node, ast.CompoundStmt):
            return self.overwritten(node.items)
        changed = False
        for f in ("body", "then_branch", "else_branch", "stmt"):
            sub = getattr(node, f, None)
            if sub is not None:
                changed |= self.overwritten_in(sub)
        return changed

    # -- driver -----------------------------------------------------------------

    def run(self) -> None:
        self.find_candidates()
        for _ in range(20):
            if not self.cands:
                return
            changed = False
            self.count()
            self.dead = {nm for nm, r in self.reads.items() if r == 0}
            backup = copy.deepcopy(self.func.body.items)
            try:
                if self.dead:
                    if self.debug:
                        print(f"dse: {self.g_name}: dead {sorted(self.dead)}",
                              file=sys.stderr)
                    self.func.body.items[:] = self.stmts(self.func.body.items)
                    if self.mentions(self.dead):
                        # a store that the rewrite did not reach: give up
                        self.func.body.items[:] = backup
                        if self.debug:
                            print(f"dse: {self.g_name}: dangling name, undone",
                                  file=sys.stderr)
                        return
                    self.cands -= self.dead
                    changed = True
                if (not os.environ.get("UC80_NO_DSE_OVERWRITE")
                        and self.overwritten(self.func.body.items)):
                    changed = True
            except RecursionError:
                self.func.body.items[:] = backup
                return
            if not changed:
                return


def _file_names(unit) -> set:
    """Names that the unit declares at file scope or as an enumerator
    anywhere: a local of the same name may hide one of these."""
    from . import codegen as cg
    out: set = set()
    for item in unit.items:
        if isinstance(item, ast.FunctionDef):
            nm = cg.function_name(item)
            if nm:
                out.add(nm)
        elif isinstance(item, ast.Declaration):
            for d in item.declarators or []:
                nm, _s = _decl_name(d)
                if nm:
                    out.add(nm)
        for n in _walk(item):
            if type(n).__name__ in ("EnumValue", "EnumValueWithInit"):
                out.add(n.name.text)
    return out


class DeadStoreMixin:
    """The hook of the pass in the code generator."""

    enable_dead_store = True

    def _dse_run(self, unit) -> dict:
        stats: dict = {}
        if not self.enable_dead_store or os.environ.get("UC80_NO_DSE"):
            return stats
        from . import codegen as cg
        names = _file_names(unit)
        only = os.environ.get("UC80_DSE_ONLY")
        only = set(only.split(",")) if only else None
        for item in unit.items:
            if not isinstance(item, ast.FunctionDef) or item.body is None:
                continue
            name = cg.function_name(item)
            if name is None or (only is not None and name not in only):
                continue
            if name in getattr(self, "naked", ()) or _unsafe(item.body):
                continue
            fn = _Fn(self, item, names, stats)
            fn.g_name = name
            try:
                fn.run()
            except RecursionError:
                pass  # the edits so far each keep the meaning
        return stats
