"""A call of a function with an empty body is deleted (docs/family_l.md).

A debug function such as ``static void dbg(const char *fmt, ...) { }``
does nothing when the debug switch is off, but every call still pushes its
arguments.  The pass finds the functions whose body has no code, and
deletes each call that is a whole expression statement, when every argument
is free of side effects.  The function then has no caller, and the dead
function removal drops it, with the string literals that only its calls
used.

A call is kept when

- an argument has a call, an assignment, ``++``, ``--``, a statement
  expression or a compound literal in it, or may access a volatile object;
- the function body has any code (a declaration, a statement, an
  expression with a side effect);
- a local variable or a parameter of the calling function has the name of
  the function (the call then calls something else);
- the unit has more than one definition of the name.

The function itself stays when its address is taken.
"""

from __future__ import annotations

from uc_core import _dcfast
from uc_core import ast
from uc_core.volatility import VolatileInfo


def _fname(func) -> str | None:
    from .codegen import function_name
    return function_name(func)


_PURE_LEAVES = ("Identifier", "IntLiteral", "CharLiteral", "FloatLiteral",
                "StringLiteral", "SizeofType")
_PURE_INNER = ("Cast", "Index", "Member", "ArrowMember", "TernaryOp",
               "SizeofExpr")
_ASSIGN_OPS = frozenset(("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=",
                         "^=", "<<=", ">>=", ","))


def _optext(n) -> str:
    o = n.op
    return o.text if hasattr(o, "text") else o


def _children(node):
    for f in _dcfast.fields(node):
        if f.name == "pos":
            continue
        v = getattr(node, f.name, None)
        if isinstance(v, (list, tuple)):
            for x in v:
                yield x
        else:
            yield v


def _is_node(x) -> bool:
    return _dcfast.is_dataclass(x) and not isinstance(x, type)


def is_pure(e) -> bool:
    """No side effect and no call.  A positive list of node types."""
    if isinstance(e, (list, tuple)):          # adjacent string pieces
        return all(is_pure(x) for x in e)
    if not _is_node(e):
        return e is None or isinstance(e, (str, int))
    cls = type(e).__name__
    if cls in _PURE_LEAVES:
        return True
    if cls in ("UnaryOp", "PostfixOp"):
        if _optext(e) in ("++", "--"):
            return False
        return is_pure(e.operand)
    if cls == "BinaryOp":
        return _optext(e) not in _ASSIGN_OPS and is_pure(e.left) \
            and is_pure(e.right)
    if cls in _PURE_INNER:
        if cls in ("Cast", "SizeofExpr"):
            inner = e.expr if cls == "Cast" else e.operand
            return is_pure(inner)
        return all(is_pure(c) for c in _children(e)
                   if _is_node(c) and _is_expr_child(e, c))
    return False


def _is_expr_child(parent, child) -> bool:
    # a type name inside a node (a Member name token, a cast type) is not
    # an expression; only the value children matter
    cls = type(parent).__name__
    if cls == "Index":
        return child is parent.array or child is parent.index
    if cls in ("Member", "ArrowMember"):
        return child is parent.obj
    if cls == "TernaryOp":
        return child in (parent.condition, parent.true_expr,
                         parent.false_expr)
    return True


def _body_is_empty(func) -> bool:
    body = func.body
    if not isinstance(body, ast.CompoundStmt):
        return False
    return all(_stmt_is_empty(s) for s in body.items or [])


def _stmt_is_empty(s) -> bool:
    if isinstance(s, ast.EmptyStmt):
        return True
    if isinstance(s, ast.CompoundStmt):
        return all(_stmt_is_empty(i) for i in s.items or [])
    if isinstance(s, ast.ExpressionStmt):
        e = s.expr
        if e is None:
            return True
        if type(e).__name__ == "Cast":
            e = e.expr
        return is_pure(e)
    return False


def _declared_in(func) -> set[str]:
    """Every name that a declarator inside ``func`` declares."""
    names: set[str] = set()
    stack = [func.declarator, func.body]
    while stack:
        n = stack.pop()
        if isinstance(n, (list, tuple)):
            stack.extend(n)
            continue
        if not _is_node(n):
            continue
        if isinstance(n, ast.Declarator):
            nm = getattr(n.name, "text", None)
            if nm:
                names.add(nm)
        stack.extend(_children(n))
    return names


def run(unit, keep=frozenset()) -> int:
    """Delete the calls in ``unit``.  Returns the number of calls.  The
    functions in ``keep`` (entry points, interrupt functions) stay."""
    defs: dict[str, list] = {}
    for item in unit.items or []:
        if isinstance(item, ast.FunctionDef) and item.body is not None:
            nm = _fname(item)
            if nm is not None:
                defs.setdefault(nm, []).append(item)
    empty = {nm for nm, fs in defs.items()
             if len(fs) == 1 and nm != "main" and nm not in keep
             and _body_is_empty(fs[0])}
    if not empty:
        return 0
    vol = VolatileInfo(unit)
    vol_names = vol.all_names()
    count = 0

    def target(stmt) -> str | None:
        if not isinstance(stmt, ast.ExpressionStmt):
            return None
        e = stmt.expr
        if type(e).__name__ == "Cast":
            e = e.expr
        if not isinstance(e, (ast.Call, ast.CallNoArgs)):
            return None
        if not isinstance(e.func, ast.Identifier):
            return None
        nm = e.func.name.text
        if nm not in empty:
            return None
        args = list(getattr(e, "args", None) or [])
        if not all(is_pure(a) and not vol.touches(a, vol_names)
                   for a in args):
            return None
        return nm

    def walk(node, shadow):
        nonlocal count
        if type(node).__name__ == "StmtExpr":
            return
        for f in _dcfast.fields(node):
            if f.name == "pos":
                continue
            v = getattr(node, f.name, None)
            if isinstance(v, list):
                keep = []
                for x in v:
                    nm = target(x)
                    if nm is not None and nm not in shadow:
                        count += 1
                        if isinstance(node, ast.CompoundStmt):
                            continue
                        keep.append(ast.EmptyStmt(pos=x.pos))
                        continue
                    if _is_node(x):
                        walk(x, shadow)
                    keep.append(x)
                v[:] = keep
            elif _is_node(v):
                nm = target(v)
                if nm is not None and nm not in shadow:
                    count += 1
                    setattr(node, f.name, ast.EmptyStmt(pos=v.pos))
                else:
                    walk(v, shadow)

    for item in unit.items or []:
        if isinstance(item, ast.FunctionDef) and item.body is not None:
            walk(item.body, _declared_in(item) & empty)
    return count
