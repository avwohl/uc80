"""qsort of 16-bit elements with a register comparator (family L, L2).

See docs/family_l.md.  A call ``qsort(base, n, 2, cmp)`` calls the library
routine __qsort_wr (lc_qsort_w.mac) in place of qsort when

- the build is a CP/M whole-program build with the register convention
  and 16-bit int, and the program has no interrupt function and no
  function named qsort;
- the element size is a constant of 2;
- ``cmp`` is a function of the program that no other code can reach:
  every use of its name in the program is the comparator argument of such
  a call, and no assembly, option or pragma names it;
- ``cmp`` has two parameters of one or two words, and it returns an int
  (not a char);
- ``cmp`` cannot reach qsort or bsearch: the function and every function
  that it calls directly make no indirect call, call no library function
  that calls back (qsort, bsearch, raise, signal and the like), have no
  assembly, and make no qsort call.  __qsort_wr keeps its state in static
  storage and so is not reentrant.

The comparator of such a call takes its two pointers in HL and DE
(docs/calling_convention.md) and returns the result in HL.  The call is
``ld BC,cmp`` / ``ld DE,n`` / ``ld HL,base`` / ``call __qsort_wr``.
"""

from __future__ import annotations

from uc_core import _dcfast
from uc_core import ast
from uc_core.volatility import children as _children

# library functions that call a function of the program and return
_CALLBACKS = frozenset((
    "qsort", "qsort_s", "bsearch", "bsearch_s", "raise", "signal",
    "call_once", "thrd_create", "atexit", "at_quick_exit", "__qsort_s",
    "__qsort_wr"))


def _is_node(x) -> bool:
    return _dcfast.is_dataclass(x) and not isinstance(x, type)


def _decl_names(func) -> set[str]:
    """Every name that a declarator in the function declares (parameters
    and locals)."""
    from .codegen import function_params
    names: set[str] = set()
    stack = [function_params(func), func.body]
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


def _unwrap_cmp(arg):
    """The Identifier of the comparator argument (``cmp`` or ``&cmp``)."""
    if isinstance(arg, ast.UnaryOp):
        op = arg.op.text if hasattr(arg.op, "text") else arg.op
        if op == "&":
            arg = arg.operand
    return arg if isinstance(arg, ast.Identifier) else None


class QsortWrMixin:
    enable_qsort_wr = True
    _wr_cmps: frozenset = frozenset()

    # ---- the test shared by the planning pass and the call site

    def _wr_call_shape(self, expr):
        """The comparator name when ``expr`` has the shape of a call that
        __qsort_wr takes: ``qsort(b, n, 2, name)``; else None.  Nothing
        about the program is tested here."""
        if not isinstance(expr, ast.Call):
            return None
        fn = expr.func
        if not isinstance(fn, ast.Identifier) or fn.name.text != "qsort":
            return None
        args = expr.args or []
        if len(args) != 4:
            return None
        cid = _unwrap_cmp(args[3])
        if cid is None:
            return None
        if self._const_int_operand(args[2]) != 2:
            return None
        return cid.name.text

    def _wr_globals_ok(self) -> bool:
        return (self.enable_qsort_wr and self.whole_program
                and not self.rom_data and not self.sdas_output
                and not self.has_mac_files and not self.isr_funcs
                and self.type_config.int_size == 2)

    def _qsort_wr_ok(self, expr) -> bool:
        name = self._wr_call_shape(expr)
        if name is None:
            return False
        if name in self._wr_cmps:
            return True
        return False

    def _wr_guard(self, expr) -> None:
        """A qsort call that names a register comparator must be a wr call.
        The planning pass made the comparator a register function only
        because every use is such a call."""
        fn = getattr(expr, "func", None)
        if (isinstance(fn, ast.Identifier) and fn.name.text == "qsort"
                and len(expr.args or []) == 4 and self._wr_cmps):
            cid = _unwrap_cmp(expr.args[3])
            if cid is not None and cid.name.text in self._wr_cmps:
                if not self._qsort_wr_ok(expr):
                    from .codegen import CodegenError
                    raise CodegenError(
                        "internal error: qsort call with a register "
                        "comparator is not a __qsort_wr call")

    # ---- the planning pass

    def _wr_candidates(self, unit, scan, name_of) -> set[str]:
        """Names of the comparators that qualify (before the register plan
        of the function itself is built)."""
        self._wr_cmps = frozenset()
        if not self._wr_globals_ok():
            return set()
        defs: dict[str, list] = {}
        for item in unit.items or []:
            if isinstance(item, ast.FunctionDef) and item.body is not None:
                nm = name_of(item)
                if nm is not None:
                    defs.setdefault(nm, []).append(item)
        if "qsort" in defs:
            return set()
        local_names: set[str] = set()
        for fs in defs.values():
            for f in fs:
                local_names |= _decl_names(f)
        if "qsort" in local_names:
            return set()
        from .codegen import iter_var_decls, decl_storage_class
        declared_fns: set[str] = set()
        for item in unit.items or []:
            if isinstance(item, ast.Declaration):
                try:
                    for nm, _full, _init, is_fn in iter_var_decls(item):
                        if nm is not None and is_fn:
                            declared_fns.add(nm)
                except Exception:
                    pass
        # uses of each name as a value, and as the comparator of a wr call
        refs: dict[str, int] = {}
        wr_uses: dict[str, int] = {}
        calls: dict[str, set] = {}          # function -> direct callees
        bad_body: set[str] = set()          # an indirect call, or qsort
        for fname, fs in defs.items():
            cs = calls.setdefault(fname, set())
            for f in fs:
                stack = [f.body]
                while stack:
                    n = stack.pop()
                    if isinstance(n, (list, tuple)):
                        stack.extend(n)
                        continue
                    if not _is_node(n):
                        continue
                    if isinstance(n, (ast.Call, ast.CallNoArgs)):
                        fn = n.func
                        if isinstance(fn, ast.Identifier):
                            cs.add(fn.name.text)
                            if (fn.name.text not in defs
                                    and fn.name.text not in declared_fns):
                                bad_body.add(fname)   # a pointer variable
                            stack.extend(getattr(n, "args", None) or [])
                            if fn.name.text == "qsort":
                                nm = self._wr_call_shape(n)
                                if nm is not None:
                                    wr_uses[nm] = wr_uses.get(nm, 0) + 1
                            continue
                        bad_body.add(fname)           # an indirect call
                    if isinstance(n, ast.Identifier):
                        k = n.name.text
                        refs[k] = refs.get(k, 0) + 1
                        continue
                    stack.extend(_children(n))
        # value uses outside function bodies (initializers)
        for item in unit.items or []:
            if isinstance(item, ast.FunctionDef):
                continue
            stack = [item]
            while stack:
                n = stack.pop()
                if isinstance(n, (list, tuple)):
                    stack.extend(n)
                    continue
                if not _is_node(n):
                    continue
                if isinstance(n, ast.Identifier):
                    k = n.name.text
                    refs[k] = refs.get(k, 0) + 1
                    continue
                stack.extend(_children(n))
        out: set[str] = set()
        for name, k in wr_uses.items():
            if (refs.get(name, 0) != k or name not in defs
                    or len(defs[name]) != 1 or name in local_names
                    or name in scan.asm_funcs
                    or name == "main"):
                continue
            if self._wr_reaches_qsort(name, defs, calls, bad_body,
                                      scan.asm_funcs):
                continue
            out.add(name)
        return out

    def _wr_reaches_qsort(self, start, defs, calls, bad_body, asm_funcs):
        """Can the function ``start`` reach a callback routine, an indirect
        call or assembly through direct calls?"""
        seen = set()
        todo = [start]
        while todo:
            f = todo.pop()
            if f in seen:
                continue
            seen.add(f)
            if f in bad_body or f in asm_funcs:
                return True
            for c in calls.get(f, ()):
                if c in _CALLBACKS:
                    return True
                if c in defs:
                    todo.append(c)
        return False

    # ---- the call

    def _gen_qsort_wr(self, expr) -> None:
        """``ld BC,cmp / ld DE,n / ld HL,base / call __qsort_wr``."""
        e = self.ctx.emit_instr
        base, n, _size, cmp_arg = expr.args
        cid = _unwrap_cmp(cmp_arg)
        self.ctx.implicit_externs.add("__qsort_wr")
        gsym = self.ctx.globals.get(cid.name.text)
        label = gsym.label() if gsym is not None else f"_{cid.name.text}"
        k = self._const_int_operand(n)
        if k is None:
            self.gen_expr(n)
            e("push", "HL")
            self.gen_expr(base)
            e("pop", "DE")
        else:
            self.gen_expr(base)
            e("ld", f"DE,{k & 0xFFFF}")
        e("ld", f"BC,{label}")
        e("call", "__qsort_wr")
