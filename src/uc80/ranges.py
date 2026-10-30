"""Value ranges of integer locals, for the 16-bit compare (docs/range_compare.md).

A signed 16-bit compare of ``x`` with a constant costs 44 T-states, an
unsigned compare costs 22.  The two give the same answer when both operands
are in 0..32767.  The loop variable of ``for (x = 0; x < 80; x++)`` is in
that range, but the compare does not know this.  This pass finds the
ranges.  It has two parts.

Function-wide ranges.  A local variable is tracked when its type is a
signed 16-bit integer, it is not volatile, it has automatic storage, it is
not a parameter and its address is never taken.  Every store to the
variable is one of these:

  - ``v = e`` or an initializer ``e``: the range of ``e``;
  - the update clause of a ``for`` statement: ``v++``, ``++v`` or
    ``v += s``, where the condition of the statement has the conjunct
    ``v < L`` or ``v <= L``, the body and the condition store nothing to
    ``v``, and ``s`` is not negative.  The update runs only when the
    condition was true, so the new value is at most ``top + max(s)``,
    where ``top`` is the largest value that the condition lets through.

Any other store (``v--``, ``v -= e``, ``v++`` outside such a update) gives
the full range.  The ranges are the least solution of these equations,
found by iteration; the result is checked once more at the end and a
variable that fails the check gets the full range.

The range of an expression comes from the ranges of the variables and
from the operators + - * << >> & | ^ % / and unary - ~ on integer
constants.  A value that leaves the range of the 16-bit int gives no range
(the code wraps).  Anything else (a call, a load, a variable that is not
tracked) gives no range.

Loop ranges.  Inside a ``for`` statement of the form above, whose init
clause stores ``v = e`` or declares ``v`` with an initializer, the
variable ``v`` is in ``min(e) .. max(max(e), top + max(s))`` whatever other
stores to ``v`` the function has.  This holds for the condition, the body
and the update, and only there.  It needs a loop body that stores nothing to
``v``, declares no other ``v``, and has no label and no case label of a
switch outside the loop (a jump into the loop could skip the condition).

The code generator asks the range of both operands of a compare of two
16-bit signed values.  When both are in 0..32767 it uses the unsigned
compare.

No label, no inline assembly and no statement expression may be in the
function for the function-wide ranges.
"""

from __future__ import annotations

import os
import sys

from uc_core import ast
from uc_core import ast_legacy as lt

from .addrcse import _fields, _is_node

_ASSIGN = ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=")
LO, HI = -32768, 32767
_FULL = None        # no range known


def _op(n) -> str:
    return getattr(n.op, "text", n.op)


def _join(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), max(a[1], b[1]))


def _fits(r) -> bool:
    return r is not None and LO <= r[0] and r[1] <= HI


def _stores_to(node, v) -> bool:
    """``node`` stores to the variable named ``v`` somewhere inside."""
    stack = [node]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
            continue
        if not _is_node(x):
            continue
        tgt = None
        if isinstance(x, ast.BinaryOp) and _op(x) in _ASSIGN:
            tgt = x.left
        elif isinstance(x, (ast.UnaryOp, ast.PostfixOp)) and _op(x) in (
                "++", "--"):
            tgt = x.operand
        if isinstance(tgt, ast.Identifier) and tgt.name.text == v:
            return True
        for f in _fields(x):
            y = getattr(x, f, None)
            if y is not None and not isinstance(y, (str, int)):
                stack.append(y)
    return False


class RangeMixin:
    enable_range_cmp = True
    _range_env: dict = {}
    _range_scoped: dict = {}
    _range_taken: frozenset = frozenset()
    _range_aborted: bool = True
    _range_vol = frozenset()
    _range_analysing = False

    # -- expression ranges ------------------------------------------------

    def _const_val(self, e):
        try:
            c = self._const_int_operand(e)
        except Exception:
            return None
        return c if isinstance(c, int) else None

    def _type_bounds(self, t):
        if not isinstance(t, lt.BasicType) or self._is_float_type(t):
            return None
        if t.name in ("void", "bool", "_Bool"):
            return None
        try:
            size = self._type_size(t)
        except Exception:
            return None
        if size not in (1, 2, 4):
            return None
        if t.is_signed is False:
            return (0, (1 << (8 * size)) - 1)
        if t.is_signed is None and t.name == "char":
            return None
        return (-(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1)

    def _var_range(self, name):
        if name in self._range_scoped:
            ok_param = True
            r = self._range_scoped[name]
        elif name in self._range_env:
            ok_param = False
            r = self._range_env[name]
        else:
            return None
        if not self._range_analysing:
            sym = self.ctx.lookup(name)
            if (sym is None or sym.is_global or sym.is_static
                    or (sym.is_param and not ok_param)
                    or self._type_size(sym.sym_type) != 2):
                return None
        return r

    def _ival(self, e):
        """The range (lo, hi) of the integer expression ``e``, or None."""
        r = self._ival_1(e)
        return r if _fits(r) else None

    def _ival_1(self, e):
        c = self._const_val(e)
        if c is not None:
            return (c, c)
        if isinstance(e, ast.Identifier):
            return self._var_range(e.name.text)
        if isinstance(e, ast.UnaryOp):
            a = self._ival(e.operand)
            if a is None:
                return None
            op = _op(e)
            if op == "-":
                return (-a[1], -a[0])
            if op == "~":
                return (-a[1] - 1, -a[0] - 1)
            if op == "+":
                return a
            return None
        if isinstance(e, ast.Cast):
            a = self._ival(e.expr)
            if a is None:
                return None
            try:
                tb = self._type_bounds(self._resolve_typename(e.target_type))
            except Exception:
                return None
            if tb is None or a[0] < tb[0] or a[1] > tb[1]:
                return None
            return a
        if not isinstance(e, ast.BinaryOp):
            return None
        op = _op(e)
        if op not in ("+", "-", "*", "<<", ">>", "&", "|", "^", "%", "/"):
            return None
        kr = self._const_val(e.right)
        kl = self._const_val(e.left)
        if op == "&":
            # a nonnegative mask bounds the result whatever the other side
            for k, other in ((kr, e.left), (kl, e.right)):
                if k is not None and k >= 0:
                    o = self._ival(other)
                    return (0, min(k, o[1]) if o and o[0] >= 0 else k)
            a, b = self._ival(e.left), self._ival(e.right)
            if a and b and a[0] >= 0 and b[0] >= 0:
                return (0, min(a[1], b[1]))
            return None
        a = self._ival(e.left)
        if a is None:
            return None
        if op in ("<<", ">>"):
            if kr is None or not 0 <= kr <= 15:
                return None
            if op == "<<":
                return (a[0] << kr, a[1] << kr)
            return (a[0] >> kr, a[1] >> kr)
        if op in ("%", "/"):
            if kr is None or kr <= 0 or a[0] < 0:
                return None
            if op == "%":
                return (0, min(kr - 1, a[1]))
            return (a[0] // kr, a[1] // kr)
        b = self._ival(e.right)
        if b is None:
            return None
        if op == "+":
            return (a[0] + b[0], a[1] + b[1])
        if op == "-":
            return (a[0] - b[1], a[1] - b[0])
        if op == "*":
            p = (a[0] * b[0], a[0] * b[1], a[1] * b[0], a[1] * b[1])
            return (min(p), max(p))
        if a[0] >= 0 and b[0] >= 0:             # | and ^
            return (0, (1 << max(a[1], b[1]).bit_length()) - 1)
        return None

    # -- the analysis of a function ---------------------------------------

    def _range_facts(self, func) -> None:
        self._range_env = {}
        self._range_scoped = {}
        self._range_taken = frozenset()
        self._range_vol = frozenset()
        if not self.enable_range_cmp:
            return
        self._range_aborted = True
        try:
            from .reglocals_gen import address_taken_names
            self._range_taken = frozenset(address_taken_names(func.body))
            self._range_vol = self._vol.names_for(func)
        except Exception:
            return
        try:
            self._range_analysing = True
            try:
                self._range_early = False
                self._range_facts_1(func)
                self._range_aborted = self._range_early
            finally:
                self._range_analysing = False
        except Exception as ex:
            if os.environ.get("UC80_RANGE_DEBUG"):
                import traceback
                traceback.print_exc()
            self._range_env = {}
            return
        if os.environ.get("UC80_RANGE_DEBUG"):
            from . import codegen as cg
            names = sorted(n for n, r in self._range_env.items()
                           if r[0] >= 0)
            print(f"range: {cg.function_name(func)}: {names}",
                  file=sys.stderr)

    def _guard_of(self, n):
        """(v, top_expr, step_expr) of a for statement whose update runs
        only after the condition limited v, or None.  ``top_expr`` is
        (rel, expr): v < expr or v <= expr."""
        u = n.update
        v = step = None
        if (isinstance(u, (ast.UnaryOp, ast.PostfixOp)) and _op(u) == "++"
                and isinstance(u.operand, ast.Identifier)):
            v, step = u.operand.name.text, None
        elif (isinstance(u, ast.BinaryOp) and _op(u) == "+="
                and isinstance(u.left, ast.Identifier)):
            v, step = u.left.name.text, u.right
        if v is None or n.condition is None:
            return None
        conj = []
        todo = [n.condition]
        while todo:
            c = todo.pop()
            if isinstance(c, ast.BinaryOp) and _op(c) == "&&":
                todo += [c.left, c.right]
            else:
                conj.append(c)
        limits = []
        for c in conj:
            if not (isinstance(c, ast.BinaryOp)
                    and _op(c) in ("<", "<=", ">", ">=")):
                continue
            op, l, r = _op(c), c.left, c.right
            if op in (">", ">="):
                l, r, op = r, l, "<" if op == ">" else "<="
            if isinstance(l, ast.Identifier) and l.name.text == v:
                limits.append((op, r))
        if not limits:
            return None
        # the condition and the body store nothing to v
        if _stores_to(n.condition, v) or _stores_to(n.body, v):
            return None
        return v, limits, step

    def _guard_values(self, limits, step):
        """(top, smin, smax) of a guard under the current ranges, or None.
        top is the largest value the condition lets through."""
        top = None
        for op, k in limits:
            kr = self._ival(k)
            if kr is None:
                continue
            t = kr[1] - 1 if op == "<" else kr[1]
            top = t if top is None else min(top, t)
        if top is None:
            return None
        if step is None:
            smin = smax = 1
        else:
            sr = self._ival(step)
            if sr is None or sr[0] < 0:
                return None
            smin, smax = sr
        if top + smax > HI:
            return None
        return top, smin, smax

    def _range_facts_1(self, func) -> None:
        from . import codegen as cg
        from .reglocals_gen import address_taken_names
        decl_ok: dict = {}
        bad: set = set()
        vals: dict = {}             # name -> [expr] of the plain stores
        guards: dict = {}           # name -> [(limits, step)]
        accounted: set = set()      # ids of update nodes a guard covers
        for p in cg.function_params(func):
            if isinstance(p, ast.ParamDecl):
                nm = cg.declarator_ident(p.declarator)
                if nm:
                    bad.add(nm)
        stack = [(func.body, 0, 0)]
        fors = []
        while stack:
            x, ld, sw = stack.pop()
            if isinstance(x, list):
                stack.extend((y, ld, sw) for y in x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, (ast.LabelStmt, ast.AsmDeclaration,
                              ast.StmtExpr)):
                self._range_early = True
                return
            if (isinstance(x, (ast.CaseStmt, ast.DefaultStmt)) and sw == 0
                    and ld > 0):
                self._range_early = True
                return
            if isinstance(x, ast.Declaration):
                self._range_decl(x, decl_ok, bad, vals)
            if isinstance(x, ast.ForStmt):
                fors.append(x)
            nld, nsw = ld, sw
            if isinstance(x, (ast.ForStmt, ast.WhileStmt, ast.DoWhileStmt)):
                nld, nsw = ld + 1, 0
            if isinstance(x, ast.SwitchStmt):
                nsw = sw + 1
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append((v, nld, nsw))
        for n in fors:
            g = self._guard_of(n)
            if g is not None:
                v, limits, step = g
                guards.setdefault(v, []).append((limits, step))
                accounted.add(id(n.update))
        # every store
        stack = [func.body]
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                stack.extend(x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, ast.BinaryOp) and _op(x) in _ASSIGN \
                    and isinstance(x.left, ast.Identifier) \
                    and id(x) not in accounted:
                nm = x.left.name.text
                if _op(x) == "=":
                    vals.setdefault(nm, []).append(x.right)
                else:
                    bad.add(nm)
            elif isinstance(x, (ast.UnaryOp, ast.PostfixOp)) and _op(x) in (
                    "++", "--") and isinstance(x.operand, ast.Identifier) \
                    and id(x) not in accounted:
                bad.add(x.operand.name.text)
            for f in _fields(x):
                v = getattr(x, f, None)
                if v is not None and not isinstance(v, (str, int)):
                    stack.append(v)
        taken = address_taken_names(func.body)
        vol = self._vol.names_for(func)
        self._range_taken = frozenset(taken)
        self._range_vol = vol
        tracked = [nm for nm, ok in decl_ok.items()
                   if ok and nm not in bad and nm not in taken
                   and nm not in vol]
        env: dict = {nm: None for nm in tracked}      # None: not computed
        full: set = set()

        def eval_all(cur):
            new = {}
            for nm in tracked:
                if nm in full:
                    continue
                r = None
                for e in vals.get(nm, ()):
                    x = self._ival(e)
                    if x is None:
                        r = "full"
                        break
                    r = _join(r, x)
                if r == "full":
                    new[nm] = "full"
                    continue
                for limits, step in guards.get(nm, ()):
                    gv = self._guard_values(limits, step)
                    if gv is None:
                        r = "full"
                        break
                    top, _smin, smax = gv
                    base = r if r is not None else (top, top)
                    r = _join(r, (base[0], top + smax))
                new[nm] = r if r is not None else (0, 0)
            return new

        # iterate: ranges only grow; after a few rounds a growing
        # variable gets the full range
        def publish(cur):
            self._range_env = {k: v for k, v in cur.items()
                               if isinstance(v, tuple)}
        cur = {nm: (0, 0) for nm in tracked}
        for rnd in range(12):
            publish(cur)
            new = eval_all(cur)
            changed = False
            for nm, r in new.items():
                if r == "full":
                    full.add(nm)
                    cur.pop(nm, None)
                    changed = True
                    continue
                old = cur.get(nm)
                j = _join(old, r)
                if j != old:
                    changed = True
                    if rnd >= 6:
                        full.add(nm)
                        cur.pop(nm, None)
                    else:
                        cur[nm] = j
            if not changed:
                break
        else:
            self._range_env = {}
            return
        # a last check: the result must contain what the stores give
        for _ in range(6):
            publish(cur)
            new = eval_all(cur)
            bad_now = [nm for nm, r in new.items()
                       if nm in cur and (r == "full" or r[0] < cur[nm][0]
                                         or r[1] > cur[nm][1])]
            if not bad_now:
                break
            for nm in bad_now:
                cur.pop(nm, None)
                full.add(nm)
        else:
            self._range_env = {}
            return
        publish(cur)

    def _range_decl(self, n, decl_ok, bad, vals) -> None:
        from . import codegen as cg
        sc = cg.decl_storage_class(n.decl_specs)
        if sc == "typedef":
            return
        for nm, full, init, is_fn in cg.iter_var_decls(n):
            if nm is None:
                continue
            ok = sc not in ("static", "extern") and not is_fn
            t = cg._to_legacy(full)
            ok = (ok and isinstance(t, lt.BasicType)
                  and t.name in ("int", "short")
                  and t.is_signed is not False
                  and not t.is_volatile and self._type_size(t) == 2)
            if init is not None:
                if isinstance(init, ast.InitializerList):
                    ok = False
                else:
                    vals.setdefault(nm, []).append(init)
            decl_ok[nm] = decl_ok.get(nm, True) and ok

    # -- the loop ranges ----------------------------------------------------

    def _range_loop_facts(self, n) -> dict:
        """Ranges that hold for the variable of a counted ``for`` statement
        inside the statement (see the module comment).  {} when none."""
        if not self.enable_range_cmp or self._range_aborted:
            return {}
        try:
            return self._range_loop_facts_1(n)
        except Exception:
            return {}

    def _range_loop_facts_1(self, n) -> dict:
        from . import codegen as cg
        g = self._guard_of(n)
        if g is None:
            return {}
        v, limits, step = g
        if v in self._range_taken or v in self._range_vol:
            return {}
        sym = self.ctx.lookup(v)
        if (sym is None or sym.is_global or sym.is_static
                or self._type_size(sym.sym_type) != 2):
            return {}
        t = sym.sym_type
        if not (isinstance(t, lt.BasicType) and t.name in ("int", "short")
                and t.is_signed is not False and not t.is_volatile):
            return {}
        # the init clause stores v from a range
        init = n.init
        if isinstance(init, ast.ExpressionStmt):
            init = init.expr
        rhs = []
        if isinstance(init, ast.Declaration):
            for nm, _f, e, _fn in cg.iter_var_decls(init):
                if nm == v and e is not None \
                        and not isinstance(e, ast.InitializerList):
                    rhs.append(e)
                elif nm == v:
                    return {}
        else:
            todo = [init]
            while todo:
                x = todo.pop()
                if isinstance(x, ast.SequenceExpr):
                    todo += [x.left, x.right]
                elif (isinstance(x, ast.BinaryOp) and _op(x) == "="
                        and isinstance(x.left, ast.Identifier)
                        and x.left.name.text == v):
                    rhs.append(x.right)
                elif x is not None and _stores_to(x, v):
                    return {}
        if not rhs:
            return {}
        r0 = None
        for e in rhs:
            x = self._ival(e)
            if x is None:
                return {}
            r0 = _join(r0, x)
        # jumps into the loop and a second v in the body
        stack = [(n, 0)]
        while stack:
            x, sw = stack.pop()
            if isinstance(x, list):
                stack.extend((y, sw) for y in x)
                continue
            if not _is_node(x):
                continue
            if isinstance(x, (ast.LabelStmt, ast.AsmDeclaration,
                              ast.StmtExpr)):
                return {}
            if isinstance(x, (ast.CaseStmt, ast.DefaultStmt)) and sw == 0:
                return {}
            if isinstance(x, ast.Declaration) and x is not n.init:
                try:
                    if any(nm == v for nm, _f, _i, _fn in
                           cg.iter_var_decls(x)):
                        return {}
                except Exception:
                    return {}
            if isinstance(x, ast.SwitchStmt):
                sw += 1
            for f in _fields(x):
                y = getattr(x, f, None)
                if y is not None and not isinstance(y, (str, int)):
                    stack.append((y, sw))
        gv = self._guard_values(limits, step)
        if gv is None:
            return {}
        top, _smin, smax = gv
        res = {v: (r0[0], max(r0[1], top + smax))}
        if os.environ.get("UC80_RANGE_DEBUG"):
            print(f"range: loop {v} {res[v]}", file=sys.stderr)
        return res

    # -- the use ---------------------------------------------------------

    def _is_nonneg_operand(self, e) -> bool:
        """``e`` is in 0..32767."""
        r = self._ival(e)
        return r is not None and r[0] >= 0 and r[1] <= HI

    def _both_nonneg(self, left, right) -> bool:
        return (self.enable_range_cmp
                and (self._range_env or self._range_scoped
                     or self._const_val(left) is not None
                     or self._const_val(right) is not None)
                and self._is_nonneg_operand(left)
                and self._is_nonneg_operand(right))
