"""Z80 hardware intrinsics: port I/O, interrupt control, critical sections.

See docs/rom.md ("Hardware access").  The intrinsics are the compiler
builtins that <z80.h> wraps:

    __builtin_z80_in(port)          IN A,(n)  or  LD C,port / IN A,(C)
    __builtin_z80_out(port, value)  OUT (n),A or  LD C,port / OUT (C),A
    __builtin_z80_in16(port)        LD BC,port / IN A,(C)
    __builtin_z80_out16(port, v)    LD BC,port / OUT (C),A
    __builtin_z80_di() / _ei()      DI / EI
    __builtin_z80_irq_save()        LD A,I / DI; 1 if interrupts were on
    __builtin_z80_irq_restore(s)    EI when s is not zero
    __builtin_z80_crit_enter()      LD A,I / DI / PUSH AF   (__critical {)
    __builtin_z80_crit_leave()      POP AF / JP PO,L / EI / L:   (})
    __builtin_z80_load16(p)         read a 16-bit object that an interrupt
                                    handler writes, with no torn value

Each machine instruction that touches a port or the interrupt state is
between two BARRIER lines.  The peephole pass never matches across such a
region (main.py), so no rule can merge, move or delete the instruction,
the same as for volatile accesses.
"""

from contextlib import contextmanager

from uc_core import ast
from uc_core import ast_legacy as lt

BARRIER_BEGIN = "; __UC80_BARRIER_BEGIN__"
BARRIER_END = "; __UC80_BARRIER_END__"
# main.py replaces these lines: with the code that saves and restores the
# alternate register set, or with nothing (isr_check.py decides).
ISR_ALT_SAVE = "; __UC80_ISR_ALT_SAVE__"
ISR_ALT_RESTORE = "; __UC80_ISR_ALT_RESTORE__"
# the same for the save of the work areas of the runtime (--isr-save-workareas)
ISR_WA_SAVE = "; __UC80_ISR_WA_SAVE__"
ISR_WA_RESTORE = "; __UC80_ISR_WA_RESTORE__"

# name -> (return type name or None for void, number of arguments)
Z80_BUILTINS = {
    "__builtin_z80_in": ("unsigned char", 1),
    "__builtin_z80_out": ("unsigned char", 2),
    "__builtin_z80_in16": ("unsigned char", 1),
    "__builtin_z80_out16": ("unsigned char", 2),
    "__builtin_z80_di": ("void", 0),
    "__builtin_z80_ei": ("void", 0),
    "__builtin_z80_irq_save": ("unsigned char", 0),
    "__builtin_z80_irq_restore": ("void", 1),
    "__builtin_z80_crit_enter": ("void", 0),
    "__builtin_z80_crit_leave": ("void", 0),
    "__builtin_z80_load16": ("unsigned int", 1),
}


def z80_builtin_type(name: str):
    """The legacy return type of a z80 builtin, or None."""
    info = Z80_BUILTINS.get(name)
    if info is None:
        return None
    return lt.BasicType(name=info[0], is_signed=(
        None if info[0] == "void" else False))


class Z80IntrinsicsMixin:
    """Methods of CodeGenerator for the __builtin_z80_* functions."""

    @contextmanager
    def _barrier(self):
        self.ctx.emit(BARRIER_BEGIN)
        try:
            yield
        finally:
            self.ctx.emit(BARRIER_END)

    def _z80_port_const(self, expr, limit):
        k = self._const_int_operand(expr)
        if k is not None and 0 <= k < limit:
            return k
        return None

    def _gen_z80_builtin(self, expr, name: str) -> None:
        """Code for a call of a __builtin_z80_* function.  The value, if
        any, is left in HL like any other call."""
        want, nargs = Z80_BUILTINS[name]
        args = list(expr.args or [])
        if len(args) != nargs:
            from .codegen import CodegenError
            raise CodegenError(
                f"{name} takes {nargs} argument(s), not {len(args)}", expr)
        e = self.ctx.emit_instr
        discard = self._discard_expr is expr
        short = name[len("__builtin_z80_"):]
        # An intrinsic is a barrier for the register cache as well.
        self.ctx.regs.reset()

        if short == "di":
            with self._barrier():
                e("di")
        elif short == "ei":
            with self._barrier():
                e("ei")
        elif short == "irq_save":
            lab = self.ctx.new_label("IRQ")
            with self._barrier():
                e("ld", "A,I")
                e("di")
                e("ld", "HL,0")
                e("jp", f"PO,{lab}")
                e("inc", "L")
                self.ctx.emit_label(lab)
        elif short == "irq_restore":
            self.gen_expr(args[0])
            lab = self.ctx.new_label("IRQ")
            with self._barrier():
                e("ld", "A,L")
                e("or", "A")
                e("jr", f"Z,{lab}")
                e("ei")
                self.ctx.emit_label(lab)
        elif short == "crit_enter":
            with self._barrier():
                e("ld", "A,I")
                e("di")
                e("push", "AF")
        elif short == "crit_leave":
            lab = self.ctx.new_label("CRIT")
            with self._barrier():
                e("pop", "AF")
                e("jp", f"PO,{lab}")
                e("ei")
                self.ctx.emit_label(lab)
        elif short in ("in", "in16"):
            self._gen_z80_in(expr, to_hl=not discard)
        elif short in ("out", "out16"):
            wide = short == "out16"
            k = self._z80_port_const(args[0], 65536 if wide else 256)
            if k is not None:
                self._z80_value_to_a(args[1])
                with self._barrier():
                    if wide:
                        e("ld", f"BC,{k}")
                        e("out", "(C),A")
                    else:
                        e("out", f"({k}),A")
            else:
                # Port first, then the value: C evaluates the arguments in
                # an unspecified order, and the port is usually simple.
                self.gen_expr(args[0])
                e("push", "HL")
                self._z80_value_to_a(args[1])
                e("pop", "BC" if wide else "HL")
                with self._barrier():
                    if not wide:
                        e("ld", "C,L")
                    e("out", "(C),A")
            if not discard:
                self._z80_a_to_hl()
        elif short == "load16":
            self._gen_z80_load16(args[0])
        else:  # pragma: no cover - the table above is the list
            from .codegen import CodegenError
            raise CodegenError(f"unknown builtin {name}", expr)

    def _z80_in_call(self, expr) -> bool:
        """``expr`` is a call of __builtin_z80_in or __builtin_z80_in16."""
        return (isinstance(expr, (ast.Call, ast.CallNoArgs))
                and isinstance(expr.func, ast.Identifier)
                and expr.func.name.text in ("__builtin_z80_in",
                                            "__builtin_z80_in16")
                and len(expr.args or ()) == 1)

    def _gen_z80_in(self, expr, to_hl: bool) -> None:
        """The byte read from the port, in A; and in HL when ``to_hl``."""
        e = self.ctx.emit_instr
        wide = expr.func.name.text.endswith("16")
        arg = expr.args[0]
        self.ctx.regs.reset()
        k = self._z80_port_const(arg, 65536 if wide else 256)
        if k is not None:
            with self._barrier():
                if wide:
                    e("ld", f"BC,{k}")
                    e("in", "A,(C)")
                else:
                    e("in", f"A,({k})")
        else:
            self.gen_expr(arg)
            with self._barrier():
                if wide:
                    e("ld", "B,H")
                e("ld", "C,L")
                e("in", "A,(C)")
        if to_hl:
            self._z80_a_to_hl()

    def _z80_value_to_a(self, value) -> None:
        """A = low byte of the integer expression ``value``."""
        if self._a_profitable(value):
            self._gen_expr_a(value)
        else:
            self.gen_expr(value)
            self.ctx.emit_instr("ld", "A,L")

    def _z80_a_to_hl(self) -> None:
        """HL = the unsigned byte in A."""
        self.ctx.emit_instr("ld", "L,A")
        self.ctx.emit_instr("ld", "H,0")

    def _gen_z80_load16(self, ptr) -> None:
        """HL = the 16-bit object at ``ptr``, read so that an interrupt
        handler cannot change one byte between the two reads.  LD HL,(nn)
        is one instruction, and an interrupt is taken only between
        instructions, so a known address needs no DI."""
        e = self.ctx.emit_instr
        p = ptr
        while isinstance(p, ast.Cast):
            p = p.expr
        direct = None
        k = self._const_int_operand(p)
        if k is not None:
            direct = str(k & 0xFFFF)
        elif (isinstance(p, ast.UnaryOp)
              and getattr(p.op, "text", p.op) == "&"
              and isinstance(p.operand, ast.Identifier)):
            sym = self.ctx.lookup(p.operand.name.text)
            if sym is not None and sym.is_global:
                direct = sym.label()
        if direct is not None:
            with self._barrier():
                e("ld", f"HL,({direct})")
            return
        self.gen_expr(ptr)
        lab = self.ctx.new_label("LD16")
        with self._barrier():
            e("ld", "A,I")
            e("di")
            e("ld", "E,(HL)")
            e("inc", "HL")
            e("ld", "D,(HL)")
            e("ex", "DE,HL")
            e("jp", f"PO,{lab}")
            e("ei")
            self.ctx.emit_label(lab)
