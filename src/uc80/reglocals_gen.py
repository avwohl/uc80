"""The part of the register-locals pass that needs the code generator.

``reglocals`` does the work on the assembly text of one function.  This
mixin collects the slots while the code generator hands them out, decides
which functions the pass may touch, and gives the pass its model of the
callees.  See docs/register_locals.md.
"""

from __future__ import annotations

import dataclasses
import re

from uc_core import ast
from uc_core.volatility import children as _children

from . import reglocals

_SETJMP = re.compile(r"setjmp", re.IGNORECASE)


def address_taken_names(body) -> set:
    """Names that occur under an ``&`` in the body (a local named there
    must stay in memory)."""
    out: set = set()
    stack = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.UnaryOp):
            op = getattr(node.op, "text", node.op)
            if op == "&":
                sub = [node.operand]
                while sub:
                    x = sub.pop()
                    if isinstance(x, ast.Identifier):
                        out.add(x.name.text)
                    elif dataclasses.is_dataclass(x) and not isinstance(x, type):
                        sub.extend(_children(x))
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            stack.extend(_children(node))
    return out


class RegLocalsMixin:
    """State and hooks for ``reglocals`` (see gen_function)."""

    _rl_on = False
    _rl_fn_ok = False
    _rl_vars: list = []
    _rl_skip: set = frozenset()

    # -- program level -----------------------------------------------------

    def _rl_plan(self, unit, enabled: bool) -> None:
        """Decide whether the pass runs at all, and for which functions."""
        self._rl_on = False
        self._rl_fn_w: dict = {}
        self._rl_wrap_id = [0]
        self._rl_reads_cache = None
        scan = getattr(self, "_regcc_scan", None)
        if (not enabled or not reglocals.enabled() or scan is None
                or not self.register_cc or not self.whole_program
                or self.type_config.int_size != 2):
            return
        from . import regcc
        skip = set(self.keep_names) | set(self.public_names) | set(self.isr_funcs)
        skip |= set(self.absolute) | set(scan.value_refs & self._rl_defined(unit))
        libnames = regcc.lib_asm_names()
        asm_names = scan.asm_tokens | self.foreign_asm_names
        for nm in self._rl_defined(unit):
            if nm == "main":
                continue         # crt0 calls main and keeps nothing in BC
            low = nm.lower()
            if (low in asm_names or f"_{low}" in asm_names
                    or low in libnames or f"_{low}" in libnames):
                skip.add(nm)
        # a function that a plan reaches only by direct calls is not in
        # value_refs; an address-taken one is
        self._rl_skip = skip
        self._rl_on = True

    @staticmethod
    def _rl_defined(unit) -> set:
        from .codegen import function_name
        out = set()
        for item in unit.items:
            if isinstance(item, ast.FunctionDef) and item.body is not None:
                nm = function_name(item)
                if nm:
                    out.add(nm)
        return out

    def _rl_label(self, name: str) -> str:
        sym = self.ctx.globals.get(name)
        return sym.label() if sym else f"_{name}"

    def _rl_env(self) -> "reglocals.Env":
        if self._rl_reads_cache is None:
            reads = {}
            for nm, plan in self._regcc.items():
                m = 0
                for loc in plan.loc:
                    if loc == "E":
                        m |= reglocals.E
                    elif loc == "DE":
                        m |= reglocals.D | reglocals.E
                    elif loc == "A":
                        m |= reglocals.A
                    elif loc == "HL":
                        m |= reglocals.H | reglocals.L
                reads[self._rl_label(nm)] = m
            self._rl_reads_cache = reads
        reads = self._rl_reads_cache
        fn_w = self._rl_fn_w

        def callee_reads(t):
            if t in reads:
                return reads[t]
            if t == "__callhl":
                return reglocals.H | reglocals.L
            if t.startswith("__"):
                return reglocals.helper_reads(t)
            return 0

        def callee_writes(t):
            return reglocals.ALL7

        def callee_est(t):
            if t in fn_w:
                return fn_w[t]
            if t == "__callhl":
                return reglocals.ALL7
            if t.startswith("__"):
                return reglocals.helper_writes(t)
            return reglocals.ALL7

        return reglocals.Env(callee_reads, callee_writes, callee_est)

    # -- function level ------------------------------------------------------

    def _rl_begin(self, name: str, func) -> None:
        self._rl_vars = []
        self._rl_fn_ok = False
        if not self._rl_on or name in self._rl_skip:
            return
        self._rl_excl = address_taken_names(func.body)
        self._rl_vol = self._vol_names
        self._rl_fn_ok = True

    def _rl_note(self, sym, loc=None) -> None:
        """A local or a parameter slot was handed out."""
        if not self._rl_fn_ok or sym is None or not sym.name:
            return
        from . import codegen as cg
        t = sym.sym_type
        if not isinstance(t, (cg.lt.BasicType, cg.lt.PointerType, cg.lt.EnumType)):
            return
        if (getattr(t, "is_volatile", False) or sym.name in self._rl_excl
                or sym.name in self._rl_vol):
            return
        try:
            if isinstance(t, cg.lt.BasicType) and (
                    self._is_float_type(t) or t.name == "void"):
                return
            size = self._type_size(t)
        except Exception:
            return
        if size not in (1, 2):
            return
        if sym.uses_shared_storage:
            kind, base = "a", sym.shared_offset
            nbytes = 2 if loc == "E" else size
        else:
            kind, base = "x", sym.offset
            nbytes = size
        self._rl_vars.append(reglocals.Var(
            sym.name, kind, base, size, nbytes, is_param=loc is not None,
            param_loc=loc))

    def _rl_finish(self, name: str, region_start: int, entry_idx: int,
                   frame_mode: bool) -> None:
        """The body is generated: let the pass move locals into BC and DE."""
        if not self._rl_fn_ok or not self._rl_vars or self._stmt_expr_seen:
            return
        lines = self.ctx.lines
        region = lines[region_start:]
        if any(_SETJMP.search(ln) for ln in region if ln.startswith("\tcall")):
            return
        res = reglocals.allocate(region, self._rl_vars, self._rl_env(),
                                 frame_mode, self._rl_wrap_id)
        if res is None:
            return
        lines[region_start:] = res.lines
        if res.prologue:
            lines[entry_idx:entry_idx] = res.prologue
        if self._rl_debug:
            import sys
            print(f"reglocals: {name}: "
                  + ", ".join(f"{v.name}->{v.reg}" for v in res.assigned)
                  + f" (gain {res.gain:.0f})", file=sys.stderr)

    _rl_debug = False

    def _rl_end(self, name: str, text_start: int) -> None:
        """The function is complete: remember what it writes."""
        if not self._rl_on:
            return
        lbl = self._rl_label(name)
        self._rl_fn_w[lbl] = reglocals.function_writes(
            self.ctx.lines[text_start:], self._rl_env(), lbl)
