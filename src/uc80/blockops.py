"""Inline block operations (docs/blockops.md).

``memset(p, v, N)`` and ``memcpy(d, s, N)`` with a constant N, whose
result the program does not use, become inline code instead of a library
call:

* a copy is ``LDI`` N times (N <= UNROLL) or ``LD BC,N`` and ``LDIR``;
* a fill stores the first byte and copies it to the rest with ``LDIR``
  (``ld (hl),v / ld de,hl+1 / ldir``), or stores N times (N <= UNROLL);
* an address that is a constant (``buf``, ``&g``, a string) is loaded
  straight into HL or DE, and a fill at such an address uses ``LD (nn),A``
  and ``LD (nn),HL`` for the small sizes.

The library call stays when N is not constant, N is 0 or over 65535, the
result is used, an argument names a volatile object, or the call is
inside a function with the name of the routine.  ``memmove`` is never
changed: its areas may overlap and ``LDIR`` copies forward.

``LDIR`` and ``LDI`` can be interrupted between two bytes, as the
library routine can.  They use BC, DE and HL, and no memory.
"""

from __future__ import annotations

import os
import re

from uc_core import ast
from uc_core import ast_legacy as lt
from uc_core._const import int_value

from .addrcse import _fields, _is_node

# up to this size a copy or fill is unrolled
UNROLL = max(1, int(os.environ.get("UC80_BLOCKOP_UNROLL", 4)))
# a fill of device memory is unrolled up to this size, then a store loop
DEV_UNROLL = 6
_LD_HL_CONST = re.compile(r"\tld\tHL,([^(\s;][^;\s]*)$", re.IGNORECASE)
_NAMES = ("memset", "memcpy")


def _vol_type(t, depth: int = 0) -> bool:
    """Does the type name a volatile thing at any level?"""
    while t is not None and depth < 8:
        if getattr(t, "is_volatile", False):
            return True
        t = getattr(t, "base_type", None)
        depth += 1
    return False


class BlockOpsMixin:
    enable_blockops = True
    blockops_stats: dict

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _bo_count(self, key: str) -> None:
        st = self.__dict__.setdefault("blockops_stats", {})
        st[key] = st.get(key, 0) + 1

    def _bo_volatile(self, node) -> bool:
        """An argument that names a volatile object, pointer or type.  A
        value that is not an AST node (a saved address) counts as volatile,
        so the call stays."""
        if not _is_node(node):
            return True
        if isinstance(node, ast.Identifier):
            sym = self.ctx.lookup(node.name.text)
            if sym is not None and _vol_type(
                    self._to_legacy_type(sym.sym_type)):
                return True
        elif isinstance(node, ast.Cast):
            tt = getattr(node, "target_type", None) or getattr(
                node, "type", None)
            if tt is not None and _vol_type(self._to_legacy_type(tt)):
                return True
        if isinstance(node, (ast.Identifier, ast.Cast, ast.UnaryOp,
                             ast.Member, ast.ArrowMember, ast.Index,
                             ast.BinaryOp)):
            try:
                if _vol_type(self._to_legacy_type(self._get_expr_type(node))):
                    return True
            except Exception:
                return True
        for f in _fields(node):
            v = getattr(node, f, None)
            if _is_node(v):
                if self._bo_volatile(v):
                    return True
            elif isinstance(v, (list, tuple)):
                for x in v:
                    if _is_node(x) and self._bo_volatile(x):
                        return True
        return False

    @staticmethod
    def _to_legacy_type(t):
        from .codegen import _to_legacy
        return _to_legacy(t) if t is not None else None

    def _bo_dev_dest(self, node) -> bool:
        """Does ``memset`` fill device memory?  The destination argument
        points to a volatile object, or it is a constant address.  A fill
        of such memory must not read the destination: ``LDIR`` reads each
        byte it copies, and a device returns something else than the
        value written.  The test looks at the argument as the program
        wrote it, through every pointer cast (a cast to ``void *`` or to
        ``unsigned char *`` hides the pointee type), through ``+``, ``-``,
        ``&``, ``[]``, ``.`` and ``->`` to the object, and into both
        branches of a conditional: a member of a volatile structure is
        volatile.  A fill of RAM that is not volatile keeps ``LDIR``."""
        if not _is_node(node):
            return False
        if isinstance(node, ast.TernaryOp):
            return (self._bo_dev_dest(node.true_expr)
                    or self._bo_dev_dest(node.false_expr))
        if isinstance(node, ast.Cast) and self._bo_dev_dest(node.expr):
            return True
        return self._bo_ptr_vol(node) or self._bo_is_const_addr(node)

    def _bo_type(self, node):
        try:
            return self._to_legacy_type(self._get_expr_type(node))
        except Exception:
            return None

    @staticmethod
    def _bo_top_vol(t) -> bool:
        if isinstance(t, lt.ArrayType):
            return getattr(t.base_type, "is_volatile", False)
        return getattr(t, "is_volatile", False)

    def _bo_ptr_vol(self, node, depth: int = 0) -> bool:
        """The pointer value ``node`` points to a volatile object."""
        if not _is_node(node) or depth > 12:
            return False
        t = self._bo_type(node)
        if isinstance(t, lt.PointerType) and getattr(
                t.base_type, "is_volatile", False):
            return True
        if isinstance(t, lt.ArrayType) and self._bo_top_vol(t):
            return True
        if isinstance(node, ast.Cast):
            return self._bo_ptr_vol(node.expr, depth + 1)
        if isinstance(node, ast.TernaryOp):
            return (self._bo_ptr_vol(node.true_expr, depth + 1)
                    or self._bo_ptr_vol(node.false_expr, depth + 1))
        if isinstance(node, ast.BinaryOp):
            op = node.op.text if hasattr(node.op, "text") else str(node.op)
            if op in ("+", "-"):
                return (self._bo_ptr_vol(node.left, depth + 1)
                        or (op == "+"
                            and self._bo_ptr_vol(node.right, depth + 1)))
            return False
        if isinstance(node, ast.UnaryOp):
            op = node.op.text if hasattr(node.op, "text") else str(node.op)
            if op == "&":
                return self._bo_lval_vol(node.operand, depth + 1)
            return False
        if isinstance(node, (ast.Member, ast.ArrowMember, ast.Index)):
            # an array member or element decays to its address
            return (isinstance(t, lt.ArrayType)
                    and self._bo_lval_vol(node, depth + 1))
        return False

    def _bo_lval_vol(self, node, depth: int = 0) -> bool:
        """The lvalue ``node`` is, or is part of, a volatile object."""
        if not _is_node(node) or depth > 12:
            return False
        if self._bo_top_vol(self._bo_type(node)):
            return True
        if isinstance(node, ast.Member):
            return self._bo_lval_vol(node.obj, depth + 1)
        if isinstance(node, ast.ArrowMember):
            t = self._bo_type(node.obj)
            return (isinstance(t, lt.PointerType)
                    and getattr(t.base_type, "is_volatile", False))
        if isinstance(node, ast.Index):
            if isinstance(self._bo_type(node.array), lt.ArrayType):
                return self._bo_lval_vol(node.array, depth + 1)
            return self._bo_ptr_vol(node.array, depth + 1)
        if isinstance(node, ast.UnaryOp):
            op = node.op.text if hasattr(node.op, "text") else str(node.op)
            if op == "*":
                return self._bo_ptr_vol(node.operand, depth + 1)
        return False

    def _bo_int_expr(self, node) -> bool:
        """An expression of integer type (no pointer, no array)."""
        if not _is_node(node):
            return False
        try:
            it = self._to_legacy_type(self._get_expr_type(node))
        except Exception:
            return False
        if isinstance(it, lt.BasicType):
            return it.name not in ("void", "float", "double", "long double")
        return type(it).__name__ == "EnumType"

    def _bo_int_const(self, node) -> bool:
        """An integer constant expression (no pointer in it)."""
        try:
            if self._eval_const_expr(node) is None:
                return False
            it = self._to_legacy_type(self._get_expr_type(node))
        except Exception:
            return False
        return not isinstance(it, (lt.PointerType, lt.ArrayType))

    def _bo_is_const_addr(self, node) -> bool:
        """The value of ``node`` is a constant address: an integer constant
        or a cast of one, such an address plus or minus an integer
        constant or any integer expression (``(unsigned char *) REG_BASE
        + 2``, ``(unsigned char *) 0x4F00 + y * 32``), the address of a
        member or element of an object at such an address
        (``&((struct dev *) 0x4B00)->ctl``), or an array member or element
        there (``((struct dev *) 0x4B00)->buf``)."""
        if not _is_node(node):
            return False
        if isinstance(node, ast.Cast):
            return self._bo_is_const_addr(node.expr)
        if isinstance(node, ast.Identifier):
            return False
        if self._bo_int_const(node):
            return True
        if isinstance(node, ast.BinaryOp):
            op = node.op.text if hasattr(node.op, "text") else str(node.op)
            if op in ("+", "-"):
                # the offset is any integer expression: the base is the
                # constant address, and the base is what makes a device
                if self._bo_is_const_addr(node.left) \
                        and self._bo_int_expr(node.right):
                    return True
                if op == "+" and self._bo_is_const_addr(node.right) \
                        and self._bo_int_expr(node.left):
                    return True
            return False
        if isinstance(node, ast.UnaryOp):
            op = node.op.text if hasattr(node.op, "text") else str(node.op)
            if op == "&":
                return self._bo_const_lvalue(node.operand)
            return False
        if isinstance(node, (ast.Member, ast.ArrowMember, ast.Index)):
            # an array member or element decays to its address
            try:
                t = self._to_legacy_type(self._get_expr_type(node))
            except Exception:
                return False
            return (isinstance(t, lt.ArrayType)
                    and self._bo_const_lvalue(node))
        return False

    def _bo_const_lvalue(self, node) -> bool:
        """``node`` is an lvalue at a constant address."""
        if not _is_node(node):
            return False
        if isinstance(node, ast.Cast):
            return False
        if isinstance(node, ast.UnaryOp):
            op = node.op.text if hasattr(node.op, "text") else str(node.op)
            return op == "*" and self._bo_is_const_addr(node.operand)
        if isinstance(node, ast.ArrowMember):
            return self._bo_is_const_addr(node.obj)
        if isinstance(node, ast.Member):
            return self._bo_const_lvalue(node.obj)
        if isinstance(node, ast.Index):
            if not self._bo_int_expr(node.index):
                return False
            try:
                t = self._to_legacy_type(self._get_expr_type(node.array))
            except Exception:
                return False
            if isinstance(t, lt.ArrayType):
                return self._bo_const_lvalue(node.array)
            return self._bo_is_const_addr(node.array)
        return False

    def _bo_dev_call(self, expr):
        """``memset (dest, v, n)`` with a device destination, not inline:
        the call goes to ``__memset_dev``, which only stores.  Returns the
        new call, or ``expr`` when nothing changes."""
        # no test of --no-blockops here: a fill that reads a device is a
        # wrong result, not a matter of code size
        if (not isinstance(expr.func, ast.Identifier)
                or expr.func.name.text != "memset"
                or len(expr.args or []) != 3
                or "memset" in getattr(self, "user_defined_funcs", ())
                or self.ctx.current_function in ("memset", "_memset")
                or self.ctx.lookup("__memset_dev") is None
                or not self._bo_dev_dest(expr.args[0])):
            return expr
        self._bo_count("memset_dev_call")
        from .codegen import make_identifier
        return ast.Call(func=make_identifier("__memset_dev"),
                        args=expr.args, pos=expr.pos)

    def _bo_const_addr(self, mark: int) -> "str | None":
        """The lines emitted since ``mark`` are one ``ld HL,const``:
        remove them and return the constant."""
        lines = self.ctx.lines
        if len(lines) == mark + 1:
            m = _LD_HL_CONST.match(lines[mark])
            if m is not None:
                del lines[mark:]
                return m.group(1)
        return None

    def _bo_to_de(self, gen) -> None:
        """HL holds a value.  Run ``gen`` (it leaves an address in HL), and
        leave the old HL value in HL and the new address in DE."""
        e = self.ctx.emit_instr
        lines = self.ctx.lines
        mark = len(lines)
        gen()
        c = self._bo_const_addr(mark)
        if c is not None:
            e("ld", f"DE,{c}")
        else:
            lines.insert(mark, "\tpush\tHL")
            e("ex", "DE,HL")
            e("pop", "HL")

    def _bo_copy_hl_de(self, n: int) -> None:
        """Copy N bytes from (HL) to (DE), forward."""
        e = self.ctx.emit_instr
        if n <= UNROLL and self.enable_blockops:
            for _ in range(n):
                e("ldi")
        else:
            e("ld", f"BC,{n}")
            e("ldir")

    @staticmethod
    def _bo_off(sym: str, k: int) -> str:
        return sym if k == 0 else f"{sym}+{k}"

    def _bo_fill(self, sym: "str | None", n: int, val: "int | None") -> None:
        """Fill N bytes.  ``sym`` is the constant address, or None when HL
        holds the address.  ``val`` is the byte, or None when A holds it."""
        e = self.ctx.emit_instr
        off = self._bo_off
        if sym is not None:
            if val is not None and n <= UNROLL:
                i = 0
                if n >= 2:
                    w = val | (val << 8)
                    e("ld", f"HL,{w}")
                    while n - i >= 2:
                        e("ld", f"({off(sym, i)}),HL")
                        i += 2
                if n - i == 1:
                    if val == 0:
                        e("xor", "A")
                    else:
                        e("ld", f"A,{val}")
                    e("ld", f"({off(sym, i)}),A")
                return
            if val is not None:
                e("ld", f"HL,{sym}")
                e("ld", f"(HL),{val}")
            elif n <= UNROLL:
                for i in range(n):
                    e("ld", f"({off(sym, i)}),A")
                return
            else:
                e("ld", f"({sym}),A")
                e("ld", f"HL,{sym}")
            e("ld", f"DE,{off(sym, 1)}")
            e("ld", f"BC,{n - 1}")
            e("ldir")
            return
        src = f"{val}" if val is not None else "A"
        if n <= UNROLL:
            for i in range(n):
                e("ld", f"(HL),{src}")
                if i != n - 1:
                    e("inc", "HL")
            return
        e("ld", f"(HL),{src}")
        e("ld", "D,H")
        e("ld", "E,L")
        e("inc", "DE")
        e("ld", f"BC,{n - 1}")
        e("ldir")

    def _bo_fill_dev(self, sym: "str | None", n: int,
                     val: "int | None") -> None:
        """Fill N bytes of device memory by stores only, from the low
        address up.  ``sym`` is the constant address, or None when HL holds
        the address; ``val`` is the byte, or None when A holds it."""
        e = self.ctx.emit_instr
        if sym is not None:
            e("ld", f"HL,{sym}")
        if val is not None:
            if val:
                e("ld", f"A,{val}")
            else:
                e("xor", "A")
        if n <= DEV_UNROLL:
            for i in range(n):
                e("ld", "(HL),A")
                if i != n - 1:
                    e("inc", "HL")
            return
        loop = self.ctx.new_label("MSD")
        lo, hi = n & 0xFF, n >> 8
        if hi == 0:
            e("ld", f"B,{lo}")
            self.ctx.lines.append(f"{loop}:")
            e("ld", "(HL),A")
            e("inc", "HL")
            e("djnz", loop)
            return
        e("ld", f"C,{lo}")
        e("ld", f"B,{hi + (1 if lo else 0)}")
        self.ctx.lines.append(f"{loop}:")
        e("ld", "(HL),A")
        e("inc", "HL")
        e("dec", "C")
        e("jr", f"NZ,{loop}")
        e("djnz", loop)

    # ------------------------------------------------------------------
    # the two calls
    # ------------------------------------------------------------------

    def _bo_try(self, name: str, args: list) -> bool:
        """Emit ``name(args)`` inline when the rules allow.  False means
        nothing was emitted."""
        if (not self.enable_blockops or os.environ.get("UC80_NO_BLOCKOPS")
                or name not in _NAMES or len(args) != 3):
            return False
        if self.ctx.current_function == name or self.ctx.current_function == "_" + name:
            return False
        # A program that defines its own memset or memcpy (static or
        # not) means that function, whatever its parameters are.
        if name in getattr(self, "user_defined_funcs", ()):
            return False
        sym = self.ctx.lookup(name)
        if sym is None or not isinstance(
                self._to_legacy_type(sym.sym_type), lt.FunctionType):
            return False
        if len(self._to_legacy_type(sym.sym_type).param_types) != 3:
            return False
        # The library declaration: (pointer, pointer, size) or (pointer, int, size).
        pts = self._to_legacy_type(sym.sym_type).param_types
        if not all(isinstance(t, (lt.PointerType, lt.ArrayType))
                   for t in (pts[:2] if name == "memcpy" else pts[:1])):
            return False
        n = self._eval_const_expr(args[2])
        if n is None or not (1 <= n <= 0xFFFF):
            return False
        only = os.environ.get("UC80_BLOCKOP_ONLY")
        if only and self.ctx.current_function not in only.split(","):
            return False
        # a device destination is no reason to keep the call: the fill
        # below never reads it (the other arguments are still checked)
        dev_dest = name == "memset" and self._bo_dev_dest(args[0])
        if any(self._bo_volatile(a) for a in (args[1:] if dev_dest
                                              else args)):
            return False
        e = self.ctx.emit_instr
        lines = self.ctx.lines
        if name == "memcpy":
            self.gen_expr(args[1])
            self._bo_to_de(lambda: self.gen_expr(args[0]))
            self._bo_copy_hl_de(n)
            self._bo_count("memcpy")
            return True
        # memset(d, v, n)
        dev = n > UNROLL and dev_dest
        v = self._eval_const_expr(args[1])
        val = None if v is None else v & 0xFF
        mark = len(lines)
        self.gen_expr(args[0])
        const = self._bo_const_addr(mark)
        if val is None:
            if const is None:
                e("push", "HL")
            self.gen_expr(args[1])
            e("ld", "A,L")
            if const is None:
                e("pop", "HL")
        if dev:
            self._bo_fill_dev(const, n, val)
            self._bo_count("memset_dev")
            return True
        self._bo_fill(const, n, val)
        self._bo_count("memset")
        return True
