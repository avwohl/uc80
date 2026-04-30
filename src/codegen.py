"""Z80 code generator for C24 compiler.

Generates MACRO-80 compatible assembly (.mac files) for the um80 assembler.
Uses IX as frame pointer, following the calling convention in implementation_plan.md.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
import struct
from typing import Callable, Iterator, Optional
from uc_core import ast
from uc_core.type_config import TypeConfig, Z80_CPM


def float_to_ieee754(f: float) -> int:
    """Convert a Python float to IEEE 754 single-precision (32-bit) integer representation."""
    packed = struct.pack('>f', f)  # Big-endian single precision
    return struct.unpack('>I', packed)[0]


def ix_off(offset: int) -> str:
    """Format an IX offset for assembly, always including the sign."""
    if offset >= 0:
        return f"IX+{offset}"
    else:
        return f"IX{offset}"


class RegState(Enum):
    """State of a register in the allocator."""
    FREE = auto()      # Available for use
    BUSY = auto()      # Contains live value, in use
    SPILLED = auto()   # Value saved to stack, register reused


@dataclass
class BitfieldInfo:
    """Describes a bitfield member's position within its storage unit."""
    bit_offset: int      # Bit position within the storage unit (0 = LSB)
    bit_width: int       # Width of the bitfield in bits
    storage_size: int    # Size of the storage unit in bytes (1, 2, or 4)
    is_signed: bool      # Whether the bitfield is signed


@dataclass
class RegDescriptor:
    """Descriptor tracking state of a single register."""
    state: RegState = RegState.FREE
    owner: str = ""           # Debug: what claimed this register
    spill_depth: int = 0      # Stack depth when spilled (for nested spills)


@dataclass
class RegisterAllocator:
    """
    Tracks register state and manages allocation with automatic spilling.

    When code needs a register that's busy, it's automatically saved to the
    stack and restored when released.
    """

    # Register descriptors
    hl: RegDescriptor = field(default_factory=RegDescriptor)
    de: RegDescriptor = field(default_factory=RegDescriptor)
    bc: RegDescriptor = field(default_factory=RegDescriptor)

    # Stack tracking for spilled registers
    spill_stack: list[str] = field(default_factory=list)

    # Statistics
    stats: dict[str, int] = field(default_factory=dict)

    def get_reg(self, name: str) -> RegDescriptor:
        """Get descriptor by register name."""
        return getattr(self, name.lower())

    def is_busy(self, reg: str) -> bool:
        """Check if a register is currently busy."""
        return self.get_reg(reg).state == RegState.BUSY

    def is_free(self, reg: str) -> bool:
        """Check if a register is currently free."""
        return self.get_reg(reg).state == RegState.FREE

    def need_reg(self, reg: str, owner: str,
                 emit_fn: Callable[[str, str], None]) -> str:
        """
        Request a register. Returns the register name.
        If busy, automatically spills it first.
        """
        reg = reg.lower()
        desc = self.get_reg(reg)

        if desc.state == RegState.BUSY:
            # Must spill - save current contents to stack
            self._spill_reg(reg, emit_fn)

        # Mark as busy with new owner
        desc.state = RegState.BUSY
        desc.owner = owner
        self.stats['claims'] = self.stats.get('claims', 0) + 1
        return reg

    def _spill_reg(self, reg: str, emit_fn: Callable[[str, str], None]) -> None:
        """Spill a register to the stack."""
        desc = self.get_reg(reg)
        emit_fn("push", reg.upper())
        self.spill_stack.append(reg)
        desc.spill_depth = len(self.spill_stack)
        desc.state = RegState.SPILLED
        self.stats['spills'] = self.stats.get('spills', 0) + 1

    def release_reg(self, reg: str, emit_fn: Callable[[str, str], None]) -> None:
        """
        Release a register. If it was spilled, restore it.
        """
        reg = reg.lower()
        desc = self.get_reg(reg)

        # Check if we need to restore from spill
        if self.spill_stack and self.spill_stack[-1] == reg:
            # This register was spilled and is top of stack - restore it
            emit_fn("pop", reg.upper())
            self.spill_stack.pop()
            self.stats['restores'] = self.stats.get('restores', 0) + 1

        desc.state = RegState.FREE
        desc.owner = ""
        desc.spill_depth = 0

    @contextmanager
    def with_reg(self, reg: str, owner: str,
                 emit_fn: Callable[[str, str], None]) -> Iterator[str]:
        """Context manager for scoped register use."""
        self.need_reg(reg, owner, emit_fn)
        try:
            yield reg
        finally:
            self.release_reg(reg, emit_fn)

    def mark_busy(self, reg: str, owner: str = "") -> None:
        """Mark a register as busy without spilling."""
        desc = self.get_reg(reg.lower())
        desc.state = RegState.BUSY
        desc.owner = owner

    def mark_free(self, reg: str) -> None:
        """Mark a register as free."""
        desc = self.get_reg(reg.lower())
        desc.state = RegState.FREE
        desc.owner = ""
        desc.spill_depth = 0

    def reset(self) -> None:
        """Reset all registers to free state."""
        for reg in ['hl', 'de', 'bc']:
            desc = self.get_reg(reg)
            desc.state = RegState.FREE
            desc.owner = ""
            desc.spill_depth = 0
        self.spill_stack.clear()

    def pick_free_alt(self) -> str:
        """Pick a free alternate register (DE or BC), prefer DE."""
        if self.is_free('de'):
            return 'de'
        if self.is_free('bc'):
            return 'bc'
        return 'de'  # Will be spilled


@dataclass
class Symbol:
    """Symbol table entry."""
    name: str
    sym_type: ast.TypeNode
    offset: int = 0  # Stack offset for locals (negative from IX)
    is_global: bool = False
    is_param: bool = False
    is_static: bool = False  # Static local - name already has underscore prefix
    uses_shared_storage: bool = False  # True if using shared automatic storage
    shared_offset: int = 0  # Offset within shared storage area
    label_override: Optional[str] = None  # Mangled label (set when an
                                           # earlier global already case-folds
                                           # to this one, since um80 is
                                           # case-insensitive).

    def label(self) -> str:
        """Get the assembly label for this symbol."""
        # Static locals already have __ prefix, don't add another _
        if self.is_static:
            return self.name
        # Global symbols get _ prefix
        return f"_{self.label_override or self.name}"


@dataclass
class CallGraphAnalyzer:
    """Analyzes call relationships between functions for shared storage optimization.

    Builds a call graph and determines which functions can share automatic storage
    by identifying functions that cannot be on the call stack simultaneously.

    When whole_program=True (default), assumes no other C files will be linked,
    enabling aggressive optimizations. When False, treats all PUBLIC (non-static)
    functions as potential entry points and is conservative about external calls.
    """

    call_graph: dict[str, set[str]] = field(default_factory=dict)  # func -> set of called funcs
    func_storage: dict[str, int] = field(default_factory=dict)  # func -> total local storage bytes
    can_be_active_together: dict[str, set[str]] = field(default_factory=dict)  # func -> concurrent funcs
    address_taken: set[str] = field(default_factory=set)  # functions whose address is taken
    func_signatures: dict[str, tuple] = field(default_factory=dict)  # func -> (ret_type, param_types)
    indirect_call_sigs: dict[str, set[tuple]] = field(default_factory=dict)  # func -> indirect call sigs
    storage_offsets: dict[str, int] = field(default_factory=dict)  # func -> base offset in shared storage
    total_shared_storage: int = 0  # Total size of shared storage area
    is_variadic: dict[str, bool] = field(default_factory=dict)  # func -> is variadic
    has_body: set[str] = field(default_factory=set)  # functions with definitions (not just declarations)
    is_static: dict[str, bool] = field(default_factory=dict)  # func -> is static (internal linkage)
    whole_program: bool = True  # True = no other C files at link time
    struct_sizes: dict[str, int] = field(default_factory=dict)  # struct name -> size in bytes
    type_config: TypeConfig = field(default_factory=lambda: Z80_CPM)

    def build_call_graph(self, unit: ast.TranslationUnit) -> None:
        """Build call graph by analyzing all function bodies."""
        # Pass 0: Collect struct definitions for accurate size calculation
        for decl in unit.declarations:
            self._collect_struct_defs(decl)

        # Pass 1: Collect all function names, signatures, and storage sizes
        for decl in unit.declarations:
            self._collect_function_info(decl)

        # Pass 2: For each function, find all calls and address-taken
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                self._analyze_function_body(decl)

        # Pass 3: Analyze global variable initializers for address-taken functions
        for decl in unit.declarations:
            self._analyze_global_init(decl)

    def _analyze_global_init(self, decl: ast.Declaration) -> None:
        """Analyze global variable initializers for address-taken functions."""
        if isinstance(decl, ast.VarDecl) and decl.init:
            address_taken: set[str] = set()
            self._analyze_expr(decl.init, set(), address_taken, set())
            # Filter to only known functions
            self.address_taken.update(address_taken & self.has_body)
        elif isinstance(decl, ast.DeclarationList):
            for d in decl.declarations:
                self._analyze_global_init(d)

    def _collect_struct_defs(self, decl: ast.Declaration) -> None:
        """Collect struct definitions for accurate size calculation."""
        if isinstance(decl, ast.StructDecl) and decl.is_definition and decl.name:
            size = 0
            if decl.is_union:
                for m in decl.members:
                    size = max(size, self._var_size(m.member_type))
            else:
                for m in decl.members:
                    size += self._var_size(m.member_type)
            self.struct_sizes[decl.name] = size
        elif isinstance(decl, ast.VarDecl):
            # Collect struct defs from variable type declarations
            self._collect_struct_from_type(decl.var_type)
        elif isinstance(decl, ast.DeclarationList):
            for d in decl.declarations:
                self._collect_struct_defs(d)
        elif isinstance(decl, ast.FunctionDecl):
            # Collect struct defs from parameters and return type
            self._collect_struct_from_type(decl.return_type)
            for p in decl.params:
                self._collect_struct_from_type(p.param_type)
            # Collect struct defs from local variables
            if decl.body:
                self._collect_struct_defs_from_body(decl.body)

    def _collect_struct_from_type(self, t: ast.TypeNode) -> None:
        """Collect struct definition from a type node."""
        if isinstance(t, ast.StructType) and t.name and t.members:
            if t.name not in self.struct_sizes:
                size = 0
                if t.is_union:
                    for m in t.members:
                        size = max(size, self._var_size(m.member_type))
                else:
                    for m in t.members:
                        size += self._var_size(m.member_type)
                self.struct_sizes[t.name] = size
        elif isinstance(t, ast.PointerType):
            self._collect_struct_from_type(t.base_type)
        elif isinstance(t, ast.ArrayType):
            self._collect_struct_from_type(t.base_type)

    def _collect_struct_defs_from_body(self, body: ast.CompoundStmt) -> None:
        """Collect struct definitions from function body statements."""
        for item in body.items:
            if isinstance(item, ast.VarDecl):
                self._collect_struct_from_type(item.var_type)
            elif isinstance(item, ast.DeclarationList):
                for d in item.declarations:
                    if isinstance(d, ast.VarDecl):
                        self._collect_struct_from_type(d.var_type)
                    elif isinstance(d, ast.StructDecl) and d.is_definition and d.name:
                        size = 0
                        if d.is_union:
                            for m in d.members:
                                size = max(size, self._var_size(m.member_type))
                        else:
                            for m in d.members:
                                size += self._var_size(m.member_type)
                        self.struct_sizes[d.name] = size
            elif isinstance(item, ast.CompoundStmt):
                self._collect_struct_defs_from_body(item)
            elif isinstance(item, ast.ForStmt):
                if isinstance(item.init, ast.VarDecl):
                    self._collect_struct_from_type(item.init.var_type)
                if isinstance(item.body, ast.CompoundStmt):
                    self._collect_struct_defs_from_body(item.body)
            elif isinstance(item, ast.StructDecl) and item.is_definition and item.name:
                size = 0
                if item.is_union:
                    for m in item.members:
                        size = max(size, self._var_size(m.member_type))
                else:
                    for m in item.members:
                        size += self._var_size(m.member_type)
                self.struct_sizes[item.name] = size

    def _collect_function_info(self, decl: ast.Declaration) -> None:
        """Collect function names, signatures, and storage requirements."""
        if isinstance(decl, ast.FunctionDecl):
            self.call_graph[decl.name] = set()
            self.is_variadic[decl.name] = decl.is_variadic
            self.is_static[decl.name] = (decl.storage_class == "static")

            # Build signature tuple
            ret_type = self._type_signature(decl.return_type)
            param_types = tuple(self._type_signature(p.param_type) for p in decl.params)
            self.func_signatures[decl.name] = (ret_type, param_types)

            if decl.body:
                self.has_body.add(decl.name)
                self.func_storage[decl.name] = self._calc_locals_size(decl.body)
            else:
                self.func_storage[decl.name] = 0

        elif isinstance(decl, ast.DeclarationList):
            for d in decl.declarations:
                self._collect_function_info(d)

    def _type_signature(self, t: ast.TypeNode) -> str:
        """Convert a type to a simple signature string for comparison."""
        if isinstance(t, ast.BasicType):
            prefix = "u" if t.is_signed is False else ""
            return prefix + t.name
        elif isinstance(t, ast.PointerType):
            return "ptr"
        elif isinstance(t, ast.ArrayType):
            return "ptr"  # Arrays decay to pointers
        elif isinstance(t, ast.FunctionType):
            return "func"
        elif isinstance(t, ast.StructType):
            return f"struct_{t.name or 'anon'}"
        elif isinstance(t, ast.EnumType):
            return "int"
        return "unknown"

    def _calc_locals_size(self, body: ast.CompoundStmt) -> int:
        """Calculate total size needed for local variables."""
        size = 0
        for item in body.items:
            if isinstance(item, ast.VarDecl):
                if item.storage_class != "static":  # Static vars use global storage
                    size += self._var_size_with_init(item)
            elif isinstance(item, ast.DeclarationList):
                for decl in item.declarations:
                    if isinstance(decl, ast.VarDecl) and decl.storage_class != "static":
                        size += self._var_size_with_init(decl)
            elif isinstance(item, ast.CompoundStmt):
                size += self._calc_locals_size(item)
            elif isinstance(item, ast.ForStmt):
                if isinstance(item.init, ast.VarDecl):
                    size += self._var_size_with_init(item.init)
                elif isinstance(item.init, ast.DeclarationList):
                    for decl in item.init.declarations:
                        if isinstance(decl, ast.VarDecl):
                            size += self._var_size_with_init(decl)
                if isinstance(item.body, ast.CompoundStmt):
                    size += self._calc_locals_size(item.body)
            elif isinstance(item, ast.IfStmt):
                if isinstance(item.then_branch, ast.CompoundStmt):
                    size += self._calc_locals_size(item.then_branch)
                if isinstance(item.else_branch, ast.CompoundStmt):
                    size += self._calc_locals_size(item.else_branch)
            elif isinstance(item, (ast.WhileStmt, ast.DoWhileStmt)):
                if isinstance(item.body, ast.CompoundStmt):
                    size += self._calc_locals_size(item.body)
            elif isinstance(item, ast.SwitchStmt):
                if isinstance(item.body, ast.CompoundStmt):
                    size += self._calc_locals_size(item.body)
            elif isinstance(item, ast.CaseStmt):
                if isinstance(item.stmt, ast.CompoundStmt):
                    size += self._calc_locals_size(item.stmt)
                elif isinstance(item.stmt, ast.CaseStmt):
                    fake = ast.CompoundStmt(items=[item.stmt])
                    size += self._calc_locals_size(fake)
        return size

    def _var_size_with_init(self, decl: ast.VarDecl) -> int:
        """Return the size of a variable, inferring unsized array size from initializer."""
        t = decl.var_type
        if isinstance(t, ast.ArrayType) and t.size is None and decl.init:
            # Infer array size from initializer
            init = decl.init
            if isinstance(init, ast.Compound):
                init = init.init
            if isinstance(init, ast.StringLiteral):
                is_wide = getattr(init, 'is_wide', False)
                elem_size = 2 if is_wide else 1
                return (len(init.value) + 1) * elem_size
            elif isinstance(init, ast.InitializerList):
                # Braced string literal: char x[] = {"XXX"} -> size is string length + 1
                if (len(init.values) == 1 and isinstance(init.values[0], ast.StringLiteral)
                        and isinstance(t.base_type, ast.BasicType)
                        and t.base_type.name in ("char", "signed char", "unsigned char")):
                    return len(init.values[0].value) + 1
                return self._var_size(t.base_type) * len(init.values)
        return self._var_size(t)

    def _var_size(self, t: ast.TypeNode) -> int:
        """Return the size of a type in bytes."""
        if isinstance(t, ast.BasicType):
            if t.name == "void":
                return 0
            size = self.type_config.sizeof_basic(t.name)
            if size is not None:
                return size
            return self.type_config.int_size
        elif isinstance(t, ast.PointerType):
            return self.type_config.ptr_size
        elif isinstance(t, ast.ArrayType):
            base_size = self._var_size(t.base_type)
            if t.size:
                if isinstance(t.size, ast.IntLiteral):
                    return base_size * t.size.value
                size_val = self._eval_const_expr(t.size)
                if size_val is not None:
                    return base_size * size_val
            return base_size
        elif isinstance(t, ast.StructType):
            # Check inline members first
            if t.members:
                if t.is_union:
                    return max((self._var_size(m.member_type) for m in t.members), default=0)
                else:
                    return sum(self._var_size(m.member_type) for m in t.members)
            # Look up from collected struct definitions
            if t.name and t.name in self.struct_sizes:
                return self.struct_sizes[t.name]
            return 4  # Fallback estimate
        return 2

    def _analyze_function_body(self, func: ast.FunctionDecl) -> None:
        """Analyze a function body for calls and address-taken functions."""
        if not func.body:
            return

        calls = set()
        address_taken = set()
        indirect_sigs: set[tuple] = set()

        self._analyze_stmt(func.body, calls, address_taken, indirect_sigs)

        self.call_graph[func.name] = calls
        self.address_taken.update(address_taken)
        self.indirect_call_sigs[func.name] = indirect_sigs

    def _analyze_stmt(self, stmt: ast.Statement, calls: set[str],
                      address_taken: set[str], indirect_sigs: set[tuple]) -> None:
        """Recursively analyze a statement for calls and address-taken."""
        if isinstance(stmt, ast.CompoundStmt):
            for item in stmt.items:
                if isinstance(item, ast.Statement):
                    self._analyze_stmt(item, calls, address_taken, indirect_sigs)
                elif isinstance(item, ast.VarDecl) and item.init:
                    self._analyze_expr(item.init, calls, address_taken, indirect_sigs)
                elif isinstance(item, ast.DeclarationList):
                    for decl in item.declarations:
                        if isinstance(decl, ast.VarDecl) and decl.init:
                            self._analyze_expr(decl.init, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.ExpressionStmt) and stmt.expr:
            self._analyze_expr(stmt.expr, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.ReturnStmt) and stmt.value:
            self._analyze_expr(stmt.value, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.IfStmt):
            self._analyze_expr(stmt.condition, calls, address_taken, indirect_sigs)
            self._analyze_stmt(stmt.then_branch, calls, address_taken, indirect_sigs)
            if stmt.else_branch:
                self._analyze_stmt(stmt.else_branch, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.WhileStmt):
            self._analyze_expr(stmt.condition, calls, address_taken, indirect_sigs)
            self._analyze_stmt(stmt.body, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.DoWhileStmt):
            self._analyze_stmt(stmt.body, calls, address_taken, indirect_sigs)
            self._analyze_expr(stmt.condition, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.ForStmt):
            if stmt.init:
                if isinstance(stmt.init, ast.Expression):
                    self._analyze_expr(stmt.init, calls, address_taken, indirect_sigs)
                elif isinstance(stmt.init, ast.VarDecl) and stmt.init.init:
                    self._analyze_expr(stmt.init.init, calls, address_taken, indirect_sigs)
                elif isinstance(stmt.init, ast.DeclarationList):
                    for decl in stmt.init.declarations:
                        if isinstance(decl, ast.VarDecl) and decl.init:
                            self._analyze_expr(decl.init, calls, address_taken, indirect_sigs)
            if stmt.condition:
                self._analyze_expr(stmt.condition, calls, address_taken, indirect_sigs)
            if stmt.update:
                self._analyze_expr(stmt.update, calls, address_taken, indirect_sigs)
            self._analyze_stmt(stmt.body, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.SwitchStmt):
            self._analyze_expr(stmt.expr, calls, address_taken, indirect_sigs)
            self._analyze_stmt(stmt.body, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.CaseStmt) and stmt.stmt:
            self._analyze_stmt(stmt.stmt, calls, address_taken, indirect_sigs)
        elif isinstance(stmt, ast.LabelStmt):
            self._analyze_stmt(stmt.stmt, calls, address_taken, indirect_sigs)

    def _analyze_expr(self, expr: ast.Expression, calls: set[str],
                      address_taken: set[str], indirect_sigs: set[tuple]) -> None:
        """Recursively analyze an expression for calls and address-taken."""
        if isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Identifier):
                # Direct call
                calls.add(expr.func.name)
            else:
                # Indirect call through pointer - track signature if possible
                # For now, mark as having indirect calls
                pass
            # Analyze arguments
            for arg in expr.args:
                self._analyze_expr(arg, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.UnaryOp):
            if expr.op == "&" and isinstance(expr.operand, ast.Identifier):
                # Address-of operator on identifier
                address_taken.add(expr.operand.name)
            self._analyze_expr(expr.operand, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.BinaryOp):
            self._analyze_expr(expr.left, calls, address_taken, indirect_sigs)
            self._analyze_expr(expr.right, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.TernaryOp):
            self._analyze_expr(expr.condition, calls, address_taken, indirect_sigs)
            self._analyze_expr(expr.true_expr, calls, address_taken, indirect_sigs)
            self._analyze_expr(expr.false_expr, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.Index):
            self._analyze_expr(expr.array, calls, address_taken, indirect_sigs)
            self._analyze_expr(expr.index, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.Member):
            self._analyze_expr(expr.obj, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.Cast):
            self._analyze_expr(expr.expr, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.SizeofExpr):
            self._analyze_expr(expr.expr, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.InitializerList):
            for val in expr.values:
                if isinstance(val, ast.Expression):
                    self._analyze_expr(val, calls, address_taken, indirect_sigs)
                elif isinstance(val, ast.DesignatedInit):
                    self._analyze_expr(val.value, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.Compound):
            self._analyze_expr(expr.init, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.StmtExpr):
            # Statement expression - analyze all items in the body
            for item in expr.body.items:
                if isinstance(item, ast.ExpressionStmt) and item.expr:
                    self._analyze_expr(item.expr, calls, address_taken, indirect_sigs)
                # Other statement types are handled by _analyze_stmt

        elif isinstance(expr, ast.GenericSelection):
            # Analyze controlling expression and all association expressions
            self._analyze_expr(expr.controlling_expr, calls, address_taken, indirect_sigs)
            for _, value_expr in expr.associations:
                self._analyze_expr(value_expr, calls, address_taken, indirect_sigs)

        elif isinstance(expr, ast.Identifier):
            # An identifier used as a value (not in a call context) might be
            # a function whose address is taken (function name decays to pointer)
            # We'll record it; the caller should filter to only known functions
            address_taken.add(expr.name)

    def compute_active_together(self) -> None:
        """Compute which functions can be on stack simultaneously.

        Two functions can be active together if:
        1. One calls the other (directly or transitively), OR
        2. Both can be called from a common ancestor
        3. When not in whole_program mode: both are PUBLIC (external code could
           call them on the same stack)
        """
        # For each function, compute reachable set (transitive closure)
        reachable: dict[str, set[str]] = {}
        for func in self.call_graph:
            reachable[func] = self._get_reachable(func, set())

        # Two funcs are active together if one is reachable from the other
        for func in self.call_graph:
            self.can_be_active_together[func] = {func}
            self.can_be_active_together[func].update(reachable[func])
            # Also add callers
            for other in self.call_graph:
                if func in reachable.get(other, set()):
                    self.can_be_active_together[func].add(other)

        # When not in whole_program mode, all PUBLIC functions can be active together
        # since external code could call any of them on the same stack
        if not self.whole_program:
            public_funcs = {f for f in self.call_graph
                          if f in self.has_body and not self.is_static.get(f, False)}
            for func in public_funcs:
                self.can_be_active_together[func].update(public_funcs)

    def _get_reachable(self, func: str, visited: set[str]) -> set[str]:
        """Get all functions reachable from func via calls."""
        if func in visited:
            return set()  # Cycle detected (recursive)
        visited = visited | {func}
        result = set(self.call_graph.get(func, set()))
        for callee in list(result):
            result.update(self._get_reachable(callee, visited))
        return result

    def is_recursive(self, func: str) -> bool:
        """Check if function is recursive (directly or indirectly)."""
        # Check direct/indirect recursion via call graph
        reachable = self._get_reachable(func, set())
        if func in reachable:
            return True

        # Check potential recursion via function pointers
        return self._can_recurse_via_pointer(func)

    def _can_recurse_via_pointer(self, func: str) -> bool:
        """Check if function can recurse through indirect calls."""
        if func not in self.address_taken:
            return False

        sig = self.func_signatures.get(func)
        if not sig:
            return True  # Conservative: assume recursive if unknown

        # Get all functions reachable from func
        reachable = self._get_reachable(func, set())

        # Check if any reachable function makes indirect calls
        # that could potentially call back to func
        for callee in reachable:
            if self.indirect_call_sigs.get(callee):
                return True  # Conservative: any indirect call could recurse

        return False

    def can_use_shared_storage(self, func: str) -> bool:
        """Check if function can use shared storage optimization."""
        # Must have a body (not just declaration)
        if func not in self.has_body:
            return False

        # Variadic functions use stack frames
        if self.is_variadic.get(func, False):
            return False

        # Must not be recursive
        if self.is_recursive(func):
            return False

        # Must have local storage to share
        if self.func_storage.get(func, 0) == 0:
            return False

        return True

    def allocate_shared_storage(self) -> None:
        """Allocate shared storage using graph coloring.

        Functions that cannot be active together share the same memory.
        """
        # Get functions that can use shared storage
        shareable_funcs = [(f, s) for f, s in self.func_storage.items()
                          if self.can_use_shared_storage(f) and s > 0]

        # Sort by storage size (descending) for better packing
        shareable_funcs.sort(key=lambda x: -x[1])

        # Track allocated intervals: (start, end, func)
        allocated: list[tuple[int, int, str]] = []

        for func, total_size in shareable_funcs:
            # Find lowest offset without conflict
            offset = 0
            while True:
                conflict = False
                for start, end, other in allocated:
                    if other in self.can_be_active_together.get(func, set()):
                        if not (offset + total_size <= start or offset >= end):
                            conflict = True
                            offset = max(offset, end)
                            break
                if not conflict:
                    break

            self.storage_offsets[func] = offset
            allocated.append((offset, offset + total_size, func))

        self.total_shared_storage = max((end for _, end, _ in allocated), default=0)

    def find_live_functions(self, entry_points: set[str] | None = None) -> set[str]:
        """Find all functions reachable from entry points.

        Args:
            entry_points: Set of entry point function names. If None, uses 'main'
                         plus any function whose address is taken. When not in
                         whole_program mode, all PUBLIC (non-static) functions
                         are also entry points.

        Returns:
            Set of function names that are reachable (live).
        """
        if entry_points is None:
            entry_points = set()
            # 'main' is always an entry point if it exists
            if "main" in self.call_graph:
                entry_points.add("main")
            # Functions whose addresses are taken are also entry points
            # (they could be called via function pointers)
            entry_points.update(self.address_taken)

            # When not in whole_program mode, all PUBLIC functions are entry points
            # since external code might call them
            if not self.whole_program:
                for func in self.call_graph:
                    if func in self.has_body and not self.is_static.get(func, False):
                        entry_points.add(func)

        live: set[str] = set()
        worklist = list(entry_points)

        while worklist:
            func = worklist.pop()
            if func in live:
                continue
            if func not in self.call_graph:
                continue  # External function or declaration only
            live.add(func)
            # Add all callees to worklist
            for callee in self.call_graph.get(func, set()):
                if callee not in live:
                    worklist.append(callee)

        return live

    def eliminate_dead_functions(self, unit: ast.TranslationUnit) -> ast.TranslationUnit:
        """Remove functions that are never called.

        Returns a new TranslationUnit with dead functions removed.
        """
        live = self.find_live_functions()

        # Filter declarations, keeping:
        # - All non-function declarations (variables, types, etc.)
        # - Function declarations that are live
        # - Function declarations without bodies (prototypes) - keep for linking
        new_decls = []
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl):
                # Keep if live, or if it's just a prototype (no body)
                if decl.name in live or decl.body is None:
                    new_decls.append(decl)
            elif isinstance(decl, ast.DeclarationList):
                # Filter function declarations within a list
                filtered = []
                for d in decl.declarations:
                    if isinstance(d, ast.FunctionDecl):
                        if d.name in live or d.body is None:
                            filtered.append(d)
                    else:
                        filtered.append(d)
                if filtered:
                    new_decls.append(ast.DeclarationList(declarations=filtered))
            else:
                # Keep all other declarations
                new_decls.append(decl)

        return ast.TranslationUnit(declarations=new_decls)

    def count_calls(self) -> dict[str, int]:
        """Count how many times each function is called."""
        call_counts: dict[str, int] = {}
        for func, callees in self.call_graph.items():
            for callee in callees:
                call_counts[callee] = call_counts.get(callee, 0) + 1
        return call_counts

    def _count_statements(self, stmt: ast.Statement) -> int:
        """Count the number of statements in a statement/block."""
        if isinstance(stmt, ast.CompoundStmt):
            count = 0
            for item in stmt.items:
                if isinstance(item, ast.Statement):
                    count += self._count_statements(item)
                else:
                    count += 1  # Declaration counts as 1
            return count
        elif isinstance(stmt, ast.IfStmt):
            count = 1
            count += self._count_statements(stmt.then_branch)
            if stmt.else_branch:
                count += self._count_statements(stmt.else_branch)
            return count
        elif isinstance(stmt, (ast.WhileStmt, ast.DoWhileStmt)):
            return 1 + self._count_statements(stmt.body)
        elif isinstance(stmt, ast.ForStmt):
            return 1 + self._count_statements(stmt.body)
        elif isinstance(stmt, ast.SwitchStmt):
            return 1 + self._count_statements(stmt.body)
        elif isinstance(stmt, ast.CaseStmt):
            return 1 + (self._count_statements(stmt.stmt) if stmt.stmt else 0)
        elif isinstance(stmt, ast.LabelStmt):
            return 1 + (self._count_statements(stmt.stmt) if stmt.stmt else 0)
        else:
            return 1

    def should_inline(self, func_name: str, func_bodies: dict[str, ast.FunctionDecl],
                     call_counts: dict[str, int]) -> bool:
        """Determine if a function should be inlined.

        Inline if:
        - Not recursive
        - Not variadic
        - Address not taken (could be called indirectly)
        - In whole_program mode OR function is static
        - Body is very small (< 4 statements) OR
          (body <= 10 statements AND called <= 2 times)
        """
        if func_name not in func_bodies:
            return False

        func = func_bodies[func_name]
        if func.body is None:
            return False

        # Don't inline recursive functions
        if self.is_recursive(func_name):
            return False

        # Don't inline variadic functions
        if func.is_variadic:
            return False

        # Don't inline if address is taken (could be called via pointer)
        if func_name in self.address_taken:
            return False

        # When not in whole_program mode, don't inline PUBLIC functions
        # (external code might call them)
        if not self.whole_program and not self.is_static.get(func_name, False):
            return False

        # Count statements
        stmt_count = self._count_statements(func.body)

        # Always inline tiny functions
        if stmt_count <= 3:
            return True

        # Inline small functions called infrequently
        calls = call_counts.get(func_name, 0)
        if stmt_count <= 10 and calls <= 2:
            return True

        return False

    def _is_trivial_function(self, func: ast.FunctionDecl) -> bool:
        """Check if function is trivial (single return statement with expression)."""
        if func.body is None:
            return False

        items = func.body.items
        if len(items) != 1:
            return False

        if not isinstance(items[0], ast.ReturnStmt):
            return False

        if items[0].value is None:
            return False

        # Don't inline functions with 64-bit params or return type -
        # the compiler can't do 64-bit arithmetic, and inlining loses
        # the type widening needed for correct results
        if self._is_long_long_type_node(func.return_type):
            return False
        for param in func.params:
            if self._is_long_long_type_node(param.param_type):
                return False

        return True

    def _is_long_long_type_node(self, t: ast.TypeNode) -> bool:
        """Check if a type node is a 64-bit integer type (byte-width dispatch)."""
        if isinstance(t, ast.BasicType):
            size = self.type_config.sizeof_basic(t.name)
            return size == 8 and t.name not in ("float", "double", "long double")
        return False

    def _substitute_params(self, expr: ast.Expression,
                          param_map: dict[str, ast.Expression]) -> ast.Expression:
        """Substitute parameter references with argument expressions."""
        if isinstance(expr, ast.Identifier):
            if expr.name in param_map:
                return param_map[expr.name]
            return expr

        elif isinstance(expr, ast.BinaryOp):
            return ast.BinaryOp(
                op=expr.op,
                left=self._substitute_params(expr.left, param_map),
                right=self._substitute_params(expr.right, param_map),
                location=expr.location
            )

        elif isinstance(expr, ast.UnaryOp):
            return ast.UnaryOp(
                op=expr.op,
                operand=self._substitute_params(expr.operand, param_map),
                is_prefix=expr.is_prefix,
                location=expr.location
            )

        elif isinstance(expr, ast.TernaryOp):
            return ast.TernaryOp(
                condition=self._substitute_params(expr.condition, param_map),
                true_expr=self._substitute_params(expr.true_expr, param_map),
                false_expr=self._substitute_params(expr.false_expr, param_map),
                location=expr.location
            )

        elif isinstance(expr, ast.Call):
            return ast.Call(
                func=self._substitute_params(expr.func, param_map),
                args=[self._substitute_params(a, param_map) for a in expr.args],
                location=expr.location
            )

        elif isinstance(expr, ast.Index):
            return ast.Index(
                array=self._substitute_params(expr.array, param_map),
                index=self._substitute_params(expr.index, param_map),
                location=expr.location
            )

        elif isinstance(expr, ast.Member):
            return ast.Member(
                obj=self._substitute_params(expr.obj, param_map),
                member=expr.member,
                is_arrow=expr.is_arrow,
                location=expr.location
            )

        elif isinstance(expr, ast.Cast):
            return ast.Cast(
                target_type=expr.target_type,
                expr=self._substitute_params(expr.expr, param_map),
                location=expr.location
            )

        elif isinstance(expr, ast.SizeofExpr):
            return ast.SizeofExpr(
                expr=self._substitute_params(expr.expr, param_map),
                location=expr.location
            )

        # Literals and other expressions don't need substitution
        return expr

    def _inline_expr(self, expr: ast.Expression,
                    func_bodies: dict[str, ast.FunctionDecl],
                    inlineable: set[str]) -> ast.Expression:
        """Recursively inline function calls in an expression."""
        if isinstance(expr, ast.Call):
            # First, inline any calls in the arguments
            new_args = [self._inline_expr(a, func_bodies, inlineable) for a in expr.args]

            # Check if this is a direct call to an inlineable function
            if isinstance(expr.func, ast.Identifier) and expr.func.name in inlineable:
                func = func_bodies[expr.func.name]
                if self._is_trivial_function(func):
                    # Build parameter -> argument map
                    param_map: dict[str, ast.Expression] = {}
                    for i, param in enumerate(func.params):
                        if param.name and i < len(new_args):
                            arg = new_args[i]
                            # Wrap in Cast for _Bool parameters (C99 6.3.1.2)
                            if (isinstance(param.param_type, ast.BasicType)
                                    and param.param_type.name == 'bool'):
                                arg = ast.Cast(target_type=param.param_type, expr=arg)
                            param_map[param.name] = arg

                    # Get the return expression and substitute parameters
                    ret_stmt = func.body.items[0]
                    assert isinstance(ret_stmt, ast.ReturnStmt)
                    return self._substitute_params(ret_stmt.value, param_map)

            # Return call with inlined arguments
            return ast.Call(
                func=self._inline_expr(expr.func, func_bodies, inlineable),
                args=new_args,
                location=expr.location
            )

        elif isinstance(expr, ast.BinaryOp):
            return ast.BinaryOp(
                op=expr.op,
                left=self._inline_expr(expr.left, func_bodies, inlineable),
                right=self._inline_expr(expr.right, func_bodies, inlineable),
                location=expr.location
            )

        elif isinstance(expr, ast.UnaryOp):
            return ast.UnaryOp(
                op=expr.op,
                operand=self._inline_expr(expr.operand, func_bodies, inlineable),
                is_prefix=expr.is_prefix,
                location=expr.location
            )

        elif isinstance(expr, ast.TernaryOp):
            return ast.TernaryOp(
                condition=self._inline_expr(expr.condition, func_bodies, inlineable),
                true_expr=self._inline_expr(expr.true_expr, func_bodies, inlineable),
                false_expr=self._inline_expr(expr.false_expr, func_bodies, inlineable),
                location=expr.location
            )

        elif isinstance(expr, ast.Index):
            return ast.Index(
                array=self._inline_expr(expr.array, func_bodies, inlineable),
                index=self._inline_expr(expr.index, func_bodies, inlineable),
                location=expr.location
            )

        elif isinstance(expr, ast.Member):
            return ast.Member(
                obj=self._inline_expr(expr.obj, func_bodies, inlineable),
                member=expr.member,
                is_arrow=expr.is_arrow,
                location=expr.location
            )

        elif isinstance(expr, ast.Cast):
            return ast.Cast(
                target_type=expr.target_type,
                expr=self._inline_expr(expr.expr, func_bodies, inlineable),
                location=expr.location
            )

        return expr

    def _inline_stmt(self, stmt: ast.Statement,
                    func_bodies: dict[str, ast.FunctionDecl],
                    inlineable: set[str]) -> ast.Statement:
        """Recursively inline function calls in a statement."""
        if isinstance(stmt, ast.ExpressionStmt):
            if stmt.expr:
                return ast.ExpressionStmt(
                    expr=self._inline_expr(stmt.expr, func_bodies, inlineable),
                    location=stmt.location
                )
            return stmt

        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value:
                return ast.ReturnStmt(
                    value=self._inline_expr(stmt.value, func_bodies, inlineable),
                    location=stmt.location
                )
            return stmt

        elif isinstance(stmt, ast.CompoundStmt):
            new_items = []
            for item in stmt.items:
                if isinstance(item, ast.Statement):
                    new_items.append(self._inline_stmt(item, func_bodies, inlineable))
                elif isinstance(item, ast.VarDecl) and item.init:
                    new_items.append(ast.VarDecl(
                        name=item.name,
                        var_type=item.var_type,
                        init=self._inline_expr(item.init, func_bodies, inlineable),
                        storage_class=item.storage_class,
                        location=item.location
                    ))
                else:
                    new_items.append(item)
            return ast.CompoundStmt(items=new_items, location=stmt.location)

        elif isinstance(stmt, ast.IfStmt):
            return ast.IfStmt(
                condition=self._inline_expr(stmt.condition, func_bodies, inlineable),
                then_branch=self._inline_stmt(stmt.then_branch, func_bodies, inlineable),
                else_branch=self._inline_stmt(stmt.else_branch, func_bodies, inlineable) if stmt.else_branch else None,
                location=stmt.location
            )

        elif isinstance(stmt, ast.WhileStmt):
            return ast.WhileStmt(
                condition=self._inline_expr(stmt.condition, func_bodies, inlineable),
                body=self._inline_stmt(stmt.body, func_bodies, inlineable),
                location=stmt.location
            )

        elif isinstance(stmt, ast.DoWhileStmt):
            return ast.DoWhileStmt(
                body=self._inline_stmt(stmt.body, func_bodies, inlineable),
                condition=self._inline_expr(stmt.condition, func_bodies, inlineable),
                location=stmt.location
            )

        elif isinstance(stmt, ast.ForStmt):
            new_init = stmt.init
            if isinstance(stmt.init, ast.Expression):
                new_init = self._inline_expr(stmt.init, func_bodies, inlineable)
            return ast.ForStmt(
                init=new_init,
                condition=self._inline_expr(stmt.condition, func_bodies, inlineable) if stmt.condition else None,
                update=self._inline_expr(stmt.update, func_bodies, inlineable) if stmt.update else None,
                body=self._inline_stmt(stmt.body, func_bodies, inlineable),
                location=stmt.location
            )

        elif isinstance(stmt, ast.SwitchStmt):
            return ast.SwitchStmt(
                expr=self._inline_expr(stmt.expr, func_bodies, inlineable),
                body=self._inline_stmt(stmt.body, func_bodies, inlineable),
                location=stmt.location
            )

        elif isinstance(stmt, ast.CaseStmt):
            return ast.CaseStmt(
                value=stmt.value,
                stmt=self._inline_stmt(stmt.stmt, func_bodies, inlineable) if stmt.stmt else None,
                location=stmt.location
            )

        elif isinstance(stmt, ast.LabelStmt):
            return ast.LabelStmt(
                label=stmt.label,
                stmt=self._inline_stmt(stmt.stmt, func_bodies, inlineable),
                location=stmt.location
            )

        return stmt

    def inline_functions(self, unit: ast.TranslationUnit) -> tuple[ast.TranslationUnit, int]:
        """Inline trivial functions into their call sites.

        Iterates until no more inlining is possible.
        Returns the modified AST and total count of inlined calls.
        """
        total_inlined = 0
        max_iterations = 10  # Prevent infinite loops

        for _ in range(max_iterations):
            # Build function body map
            func_bodies: dict[str, ast.FunctionDecl] = {}
            for decl in unit.declarations:
                if isinstance(decl, ast.FunctionDecl) and decl.body:
                    func_bodies[decl.name] = decl

            # Rebuild call graph for accurate counts
            self.call_graph.clear()
            self.address_taken.clear()
            self.build_call_graph(unit)

            # Count calls
            call_counts = self.count_calls()

            # Find inlineable functions (trivial functions that should be inlined)
            inlineable: set[str] = set()
            for name, func in func_bodies.items():
                if self.should_inline(name, func_bodies, call_counts):
                    if self._is_trivial_function(func):
                        inlineable.add(name)

            if not inlineable:
                break

            # Count inlined calls for this iteration
            inlined_count = sum(call_counts.get(f, 0) for f in inlineable)
            total_inlined += inlined_count

            # Transform the AST
            new_decls = []
            for decl in unit.declarations:
                if isinstance(decl, ast.FunctionDecl):
                    if decl.body:
                        new_body = self._inline_stmt(decl.body, func_bodies, inlineable)
                        new_decls.append(ast.FunctionDecl(
                            name=decl.name,
                            return_type=decl.return_type,
                            params=decl.params,
                            body=new_body,
                            is_variadic=decl.is_variadic,
                            storage_class=decl.storage_class,
                            is_inline=decl.is_inline,
                            location=decl.location
                        ))
                    else:
                        new_decls.append(decl)
                else:
                    new_decls.append(decl)

            unit = ast.TranslationUnit(declarations=new_decls)

        return unit, total_inlined

    def _collect_call_args(self, unit: ast.TranslationUnit) -> dict[str, list[list[ast.Expression]]]:
        """Collect all argument lists for each function call.

        Returns: dict mapping function name to list of argument lists from all call sites.
        """
        call_args: dict[str, list[list[ast.Expression]]] = {}

        def collect_from_expr(expr: ast.Expression) -> None:
            if isinstance(expr, ast.Call):
                if isinstance(expr.func, ast.Identifier):
                    func_name = expr.func.name
                    if func_name not in call_args:
                        call_args[func_name] = []
                    call_args[func_name].append(list(expr.args))
                # Recurse into function expression and arguments
                collect_from_expr(expr.func)
                for arg in expr.args:
                    collect_from_expr(arg)
            elif isinstance(expr, ast.BinaryOp):
                collect_from_expr(expr.left)
                collect_from_expr(expr.right)
            elif isinstance(expr, ast.UnaryOp):
                collect_from_expr(expr.operand)
            elif isinstance(expr, ast.TernaryOp):
                collect_from_expr(expr.condition)
                collect_from_expr(expr.true_expr)
                collect_from_expr(expr.false_expr)
            elif isinstance(expr, ast.Index):
                collect_from_expr(expr.array)
                collect_from_expr(expr.index)
            elif isinstance(expr, ast.Member):
                collect_from_expr(expr.obj)
            elif isinstance(expr, ast.Cast):
                collect_from_expr(expr.expr)
            elif isinstance(expr, ast.SizeofExpr):
                collect_from_expr(expr.expr)
            elif isinstance(expr, ast.InitializerList):
                for val in expr.values:
                    if isinstance(val, ast.Expression):
                        collect_from_expr(val)
                    elif isinstance(val, ast.DesignatedInit):
                        collect_from_expr(val.value)

        def collect_from_stmt(stmt: ast.Statement) -> None:
            if isinstance(stmt, ast.ExpressionStmt) and stmt.expr:
                collect_from_expr(stmt.expr)
            elif isinstance(stmt, ast.ReturnStmt) and stmt.value:
                collect_from_expr(stmt.value)
            elif isinstance(stmt, ast.CompoundStmt):
                for item in stmt.items:
                    if isinstance(item, ast.Statement):
                        collect_from_stmt(item)
                    elif isinstance(item, ast.VarDecl) and item.init:
                        collect_from_expr(item.init)
                    elif isinstance(item, ast.DeclarationList):
                        for d in item.declarations:
                            if isinstance(d, ast.VarDecl) and d.init:
                                collect_from_expr(d.init)
            elif isinstance(stmt, ast.IfStmt):
                collect_from_expr(stmt.condition)
                collect_from_stmt(stmt.then_branch)
                if stmt.else_branch:
                    collect_from_stmt(stmt.else_branch)
            elif isinstance(stmt, ast.WhileStmt):
                collect_from_expr(stmt.condition)
                collect_from_stmt(stmt.body)
            elif isinstance(stmt, ast.DoWhileStmt):
                collect_from_stmt(stmt.body)
                collect_from_expr(stmt.condition)
            elif isinstance(stmt, ast.ForStmt):
                if isinstance(stmt.init, ast.Expression):
                    collect_from_expr(stmt.init)
                if stmt.condition:
                    collect_from_expr(stmt.condition)
                if stmt.update:
                    collect_from_expr(stmt.update)
                collect_from_stmt(stmt.body)
            elif isinstance(stmt, ast.SwitchStmt):
                collect_from_expr(stmt.expr)
                collect_from_stmt(stmt.body)
            elif isinstance(stmt, ast.CaseStmt) and stmt.stmt:
                collect_from_stmt(stmt.stmt)
            elif isinstance(stmt, ast.LabelStmt):
                collect_from_stmt(stmt.stmt)

        # Collect from all function bodies
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                collect_from_stmt(decl.body)

        return call_args

    def _eval_const_expr(self, expr: ast.Expression) -> int | None:
        """Evaluate a constant expression. Returns None if not constant."""
        if isinstance(expr, ast.IntLiteral):
            return expr.value
        elif isinstance(expr, ast.CharLiteral):
            # Character constants have type int (C 6.4.4.4)
            val = expr.value
            if val >= 0x80:
                val = val - 0x100  # Sign extend signed char to int
            return val
        elif isinstance(expr, ast.BoolLiteral):
            return 1 if expr.value else 0
        elif isinstance(expr, ast.UnaryOp):
            operand = self._eval_const_expr(expr.operand)
            if operand is None:
                return None
            if expr.op == "-":
                return -operand
            elif expr.op == "+":
                return operand
            elif expr.op == "~":
                return ~operand
            elif expr.op == "!":
                return 0 if operand else 1
        elif isinstance(expr, ast.BinaryOp):
            left = self._eval_const_expr(expr.left)
            right = self._eval_const_expr(expr.right)
            if left is None or right is None:
                return None
            if expr.op == "+":
                return left + right
            elif expr.op == "-":
                return left - right
            elif expr.op == "*":
                return left * right
            elif expr.op == "/" and right != 0:
                return left // right
            elif expr.op == "%" and right != 0:
                return left % right
            elif expr.op == "&":
                return left & right
            elif expr.op == "|":
                return left | right
            elif expr.op == "^":
                return left ^ right
            elif expr.op == "<<":
                return left << right
            elif expr.op == ">>":
                return left >> right
            elif expr.op == "==":
                return 1 if left == right else 0
            elif expr.op == "!=":
                return 1 if left != right else 0
            elif expr.op == "<":
                return 1 if left < right else 0
            elif expr.op == ">":
                return 1 if left > right else 0
            elif expr.op == "<=":
                return 1 if left <= right else 0
            elif expr.op == ">=":
                return 1 if left >= right else 0
            elif expr.op == "&&":
                return 1 if left and right else 0
            elif expr.op == "||":
                return 1 if left or right else 0
        elif isinstance(expr, ast.Cast):
            return self._eval_const_expr(expr.expr)
        elif isinstance(expr, ast.SizeofType):
            return self._var_size(expr.target_type)
        elif isinstance(expr, ast.SizeofExpr):
            # For sizeof(expr), we need to infer the expression type
            # This is a simplified version - handles common cases
            return None
        elif isinstance(expr, ast.Identifier):
            # Check if this is an enum constant (only available in CodeGenerator context)
            if hasattr(self, 'ctx') and expr.name in self.ctx.enum_constants:
                return self.ctx.enum_constants[expr.name]
            # Also check if CallGraphAnalyzer has collected enum values
            if hasattr(self, 'enum_values') and expr.name in self.enum_values:
                return self.enum_values[expr.name]
        return None

    def _find_constant_params(self, unit: ast.TranslationUnit) -> dict[str, dict[int, int]]:
        """Find parameters that are always the same constant at all call sites.

        Returns: dict mapping function name to dict of (param_index -> constant_value).
        """
        call_args = self._collect_call_args(unit)
        constant_params: dict[str, dict[int, int]] = {}

        # Build function info map
        func_info: dict[str, ast.FunctionDecl] = {}
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                func_info[decl.name] = decl

        for func_name, all_args in call_args.items():
            if func_name not in func_info:
                continue  # External function

            func = func_info[func_name]
            if not all_args:
                continue

            # Don't propagate if function's address is taken
            if func_name in self.address_taken:
                continue

            # When not in whole_program mode, don't propagate to PUBLIC functions
            # (external code might pass different values)
            if not self.whole_program and not self.is_static.get(func_name, False):
                continue

            num_params = len(func.params)
            param_constants: dict[int, int] = {}

            for param_idx in range(num_params):
                # Check if this parameter is the same constant at all call sites
                first_value: int | None = None
                all_same = True

                for args in all_args:
                    if param_idx >= len(args):
                        all_same = False
                        break
                    const_val = self._eval_const_expr(args[param_idx])
                    if const_val is None:
                        all_same = False
                        break
                    if first_value is None:
                        first_value = const_val
                    elif const_val != first_value:
                        all_same = False
                        break

                if all_same and first_value is not None:
                    param_constants[param_idx] = first_value

            if param_constants:
                constant_params[func_name] = param_constants

        return constant_params

    def _substitute_param_constants(self, expr: ast.Expression,
                                    param_names: list[str],
                                    constants: dict[int, int]) -> ast.Expression:
        """Substitute constant values for parameters in an expression."""
        if isinstance(expr, ast.Identifier):
            # Check if this is a parameter with a known constant
            if expr.name in param_names:
                param_idx = param_names.index(expr.name)
                if param_idx in constants:
                    return ast.IntLiteral(value=constants[param_idx], location=expr.location)
            return expr

        elif isinstance(expr, ast.BinaryOp):
            return ast.BinaryOp(
                op=expr.op,
                left=self._substitute_param_constants(expr.left, param_names, constants),
                right=self._substitute_param_constants(expr.right, param_names, constants),
                location=expr.location
            )

        elif isinstance(expr, ast.UnaryOp):
            # Don't substitute inside address-of - &(param) needs the actual parameter
            if expr.op == "&":
                return expr
            return ast.UnaryOp(
                op=expr.op,
                operand=self._substitute_param_constants(expr.operand, param_names, constants),
                is_prefix=expr.is_prefix,
                location=expr.location
            )

        elif isinstance(expr, ast.TernaryOp):
            return ast.TernaryOp(
                condition=self._substitute_param_constants(expr.condition, param_names, constants),
                true_expr=self._substitute_param_constants(expr.true_expr, param_names, constants),
                false_expr=self._substitute_param_constants(expr.false_expr, param_names, constants),
                location=expr.location
            )

        elif isinstance(expr, ast.Call):
            return ast.Call(
                func=self._substitute_param_constants(expr.func, param_names, constants),
                args=[self._substitute_param_constants(a, param_names, constants) for a in expr.args],
                location=expr.location
            )

        elif isinstance(expr, ast.Index):
            return ast.Index(
                array=self._substitute_param_constants(expr.array, param_names, constants),
                index=self._substitute_param_constants(expr.index, param_names, constants),
                location=expr.location
            )

        elif isinstance(expr, ast.Member):
            return ast.Member(
                obj=self._substitute_param_constants(expr.obj, param_names, constants),
                member=expr.member,
                is_arrow=expr.is_arrow,
                location=expr.location
            )

        elif isinstance(expr, ast.Cast):
            return ast.Cast(
                target_type=expr.target_type,
                expr=self._substitute_param_constants(expr.expr, param_names, constants),
                location=expr.location
            )

        elif isinstance(expr, ast.SizeofExpr):
            return ast.SizeofExpr(
                expr=self._substitute_param_constants(expr.expr, param_names, constants),
                location=expr.location
            )

        return expr

    def _substitute_stmt_constants(self, stmt: ast.Statement,
                                   param_names: list[str],
                                   constants: dict[int, int]) -> ast.Statement:
        """Substitute constant values for parameters in a statement."""
        if isinstance(stmt, ast.ExpressionStmt):
            if stmt.expr:
                return ast.ExpressionStmt(
                    expr=self._substitute_param_constants(stmt.expr, param_names, constants),
                    location=stmt.location
                )
            return stmt

        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value:
                return ast.ReturnStmt(
                    value=self._substitute_param_constants(stmt.value, param_names, constants),
                    location=stmt.location
                )
            return stmt

        elif isinstance(stmt, ast.CompoundStmt):
            new_items = []
            for item in stmt.items:
                if isinstance(item, ast.Statement):
                    new_items.append(self._substitute_stmt_constants(item, param_names, constants))
                elif isinstance(item, ast.VarDecl) and item.init:
                    new_items.append(ast.VarDecl(
                        name=item.name,
                        var_type=item.var_type,
                        init=self._substitute_param_constants(item.init, param_names, constants),
                        storage_class=item.storage_class,
                        location=item.location
                    ))
                else:
                    new_items.append(item)
            return ast.CompoundStmt(items=new_items, location=stmt.location)

        elif isinstance(stmt, ast.IfStmt):
            return ast.IfStmt(
                condition=self._substitute_param_constants(stmt.condition, param_names, constants),
                then_branch=self._substitute_stmt_constants(stmt.then_branch, param_names, constants),
                else_branch=self._substitute_stmt_constants(stmt.else_branch, param_names, constants) if stmt.else_branch else None,
                location=stmt.location
            )

        elif isinstance(stmt, ast.WhileStmt):
            return ast.WhileStmt(
                condition=self._substitute_param_constants(stmt.condition, param_names, constants),
                body=self._substitute_stmt_constants(stmt.body, param_names, constants),
                location=stmt.location
            )

        elif isinstance(stmt, ast.DoWhileStmt):
            return ast.DoWhileStmt(
                body=self._substitute_stmt_constants(stmt.body, param_names, constants),
                condition=self._substitute_param_constants(stmt.condition, param_names, constants),
                location=stmt.location
            )

        elif isinstance(stmt, ast.ForStmt):
            new_init = stmt.init
            if isinstance(stmt.init, ast.Expression):
                new_init = self._substitute_param_constants(stmt.init, param_names, constants)
            return ast.ForStmt(
                init=new_init,
                condition=self._substitute_param_constants(stmt.condition, param_names, constants) if stmt.condition else None,
                update=self._substitute_param_constants(stmt.update, param_names, constants) if stmt.update else None,
                body=self._substitute_stmt_constants(stmt.body, param_names, constants),
                location=stmt.location
            )

        elif isinstance(stmt, ast.SwitchStmt):
            return ast.SwitchStmt(
                expr=self._substitute_param_constants(stmt.expr, param_names, constants),
                body=self._substitute_stmt_constants(stmt.body, param_names, constants),
                location=stmt.location
            )

        elif isinstance(stmt, ast.CaseStmt):
            return ast.CaseStmt(
                value=stmt.value,
                stmt=self._substitute_stmt_constants(stmt.stmt, param_names, constants) if stmt.stmt else None,
                location=stmt.location
            )

        elif isinstance(stmt, ast.LabelStmt):
            return ast.LabelStmt(
                label=stmt.label,
                stmt=self._substitute_stmt_constants(stmt.stmt, param_names, constants),
                location=stmt.location
            )

        return stmt

    def propagate_constants(self, unit: ast.TranslationUnit) -> tuple[ast.TranslationUnit, int]:
        """Propagate constant arguments into function bodies.

        Returns the modified AST and count of propagated constants.
        """
        constant_params = self._find_constant_params(unit)

        if not constant_params:
            return unit, 0

        total_propagated = sum(len(v) for v in constant_params.values())

        # Transform the AST
        new_decls = []
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                if decl.name in constant_params:
                    constants = constant_params[decl.name]
                    param_names = [p.name for p in decl.params if p.name]

                    new_body = self._substitute_stmt_constants(decl.body, param_names, constants)
                    new_decls.append(ast.FunctionDecl(
                        name=decl.name,
                        return_type=decl.return_type,
                        params=decl.params,
                        body=new_body,
                        is_variadic=decl.is_variadic,
                        storage_class=decl.storage_class,
                        is_inline=decl.is_inline,
                        location=decl.location
                    ))
                else:
                    new_decls.append(decl)
            else:
                new_decls.append(decl)

        return ast.TranslationUnit(declarations=new_decls), total_propagated


@dataclass
class CodeGenContext:
    """Context for code generation."""
    # Output lines
    lines: list[str] = field(default_factory=list)

    # Register allocator
    regs: RegisterAllocator = field(default_factory=RegisterAllocator)

    # Symbol tables
    globals: dict[str, Symbol] = field(default_factory=dict)
    locals: dict[str, Symbol] = field(default_factory=dict)

    # String literals: label -> string value
    strings: dict[str, str] = field(default_factory=dict)
    wide_strings: set[str] = field(default_factory=set)  # Labels of wide string literals
    string_counter: int = 0

    # Label generation
    label_counter: int = 0

    # Current function info
    current_function: Optional[str] = None
    current_return_type: Optional[ast.TypeNode] = None  # Return type of current function
    local_offset: int = 0  # Current stack offset for locals

    # Loop context for break/continue
    break_labels: list[str] = field(default_factory=list)
    continue_labels: list[str] = field(default_factory=list)

    # Runtime functions used (need EXTRN)
    runtime_used: set[str] = field(default_factory=set)

    # Struct definitions: name -> list of (member_name, member_type, offset)
    structs: dict[str, list[tuple[str, ast.TypeNode, int]]] = field(default_factory=dict)

    # Anonymous struct/union members: struct_name -> list of (anon_struct_type, offset)
    anon_members: dict[str, list[tuple[ast.StructType, int]]] = field(default_factory=dict)

    # Bitfield info: (struct_name, member_name) -> BitfieldInfo
    bitfield_info: dict[tuple[str, str], BitfieldInfo] = field(default_factory=dict)

    # Cached struct sizes (for structs with bitfields where _type_size can't compute from members alone)
    struct_sizes: dict[str, int] = field(default_factory=dict)

    # Function names (for distinguishing functions from variables)
    function_names: set[str] = field(default_factory=set)

    # Implicitly-declared external symbols: any `_name` referenced in code
    # without a matching declaration (e.g. abort()/exit() called in code that
    # never #include'd stdlib.h).  Recorded so we can emit EXTRN at the end.
    implicit_externs: set[str] = field(default_factory=set)

    # Enum constants: name -> integer value
    enum_constants: dict[str, int] = field(default_factory=dict)

    # Static local variables: label -> (type, init_value, resolved_addr)
    static_locals: dict[str, tuple] = field(default_factory=dict)
    static_counter: int = 0
    # Map from original variable name (func:varname) to static local label
    static_local_labels: dict[str, str] = field(default_factory=dict)

    def emit(self, line: str = "") -> None:
        """Emit a line of assembly."""
        self.lines.append(line)

    def emit_label(self, label: str) -> None:
        """Emit a label."""
        self.lines.append(f"{label}:")

    def emit_instr(self, instr: str, operands: str = "") -> None:
        """Emit an instruction with optional operands."""
        if operands:
            self.lines.append(f"\t{instr}\t{operands}")
        else:
            self.lines.append(f"\t{instr}")

    def new_label(self, prefix: str = "L") -> str:
        """Generate a unique label."""
        self.label_counter += 1
        return f"@{prefix}{self.label_counter}"

    def new_string_label(self) -> str:
        """Generate a unique string literal label."""
        self.string_counter += 1
        return f"@STR{self.string_counter}"

    def add_string(self, value: str, is_wide: bool = False) -> str:
        """Add a string literal and return its label."""
        # Check if string already exists (wide and narrow are distinct)
        for label, s in self.strings.items():
            is_existing_wide = label in self.wide_strings
            if s == value and is_existing_wide == is_wide:
                return label
        label = self.new_string_label()
        self.strings[label] = value
        if is_wide:
            self.wide_strings.add(label)
        return label

    def lookup(self, name: str) -> Optional[Symbol]:
        """Look up a symbol in local then global scope.
        If 'name' was declared extern in the current block scope,
        skip the local and go straight to globals (C99 6.2.2).
        """
        if name in self.locals and name not in getattr(self, 'block_externs', ()):
            return self.locals[name]
        if name in self.globals:
            return self.globals[name]
        return None


class CodeGenerator:
    """Z80 code generator."""

    def __init__(self, module_name: str = "main", enable_shared_storage: bool = True,
                 enable_dead_elimination: bool = True, enable_inlining: bool = True,
                 enable_const_propagation: bool = True, whole_program: bool = True,
                 embed_runtime: bool = False,
                 printf_features: set[str] | None = None,
                 scanf_features: set[str] | None = None,
                 type_config: TypeConfig | None = None):
        self.module_name = module_name
        self.ctx = CodeGenContext()
        self.enable_shared_storage = enable_shared_storage
        self.enable_dead_elimination = enable_dead_elimination
        self.enable_inlining = enable_inlining
        self.enable_const_propagation = enable_const_propagation
        self.whole_program = whole_program
        self.embed_runtime = embed_runtime
        self.printf_features = printf_features  # None = no pragma (emit default all table)
        self.scanf_features = scanf_features    # None = no pragma
        self.type_config = type_config if type_config is not None else Z80_CPM
        self.call_graph_analyzer: Optional[CallGraphAnalyzer] = None
        self.dead_functions_removed: int = 0
        self.inlined_calls: int = 0
        self.constants_propagated: int = 0
        # Switch statement context
        self._switch_cases: list[tuple[int, str]] = []
        self._switch_default: str | None = None

    def _infer_array_size(self, var_type: ast.TypeNode,
                          init: ast.Expression | None) -> ast.TypeNode:
        """Infer array size from initializer for unsized arrays."""
        if not isinstance(var_type, ast.ArrayType):
            return var_type
        if var_type.size is not None:
            return var_type

        # String literal initializing char/wchar_t array
        if isinstance(init, ast.StringLiteral):
            array_size = len(init.value) + 1  # +1 for null terminator
            return ast.ArrayType(
                base_type=var_type.base_type,
                size=ast.IntLiteral(value=array_size, is_long=False, is_unsigned=False)
            )

        if not isinstance(init, ast.InitializerList):
            return var_type

        # C standard: char x[] = {"string"} is equivalent to char x[] = "string"
        # A braced string literal initializer for a char array
        base_type = var_type.base_type
        if (len(init.values) == 1 and isinstance(init.values[0], ast.StringLiteral)
                and isinstance(base_type, ast.BasicType)
                and base_type.name in ("char", "signed char", "unsigned char")):
            array_size = len(init.values[0].value) + 1  # +1 for null terminator
            return ast.ArrayType(
                base_type=base_type,
                size=ast.IntLiteral(value=array_size, is_long=False, is_unsigned=False)
            )

        # Check for designated initializers with array index - size = max_index + 1
        max_desig_index = -1
        for v in init.values:
            if isinstance(v, ast.DesignatedInit) and v.designators:
                d = v.designators[0]
                if isinstance(d, int):
                    max_desig_index = max(max_desig_index, d)
                elif isinstance(d, ast.IntLiteral):
                    max_desig_index = max(max_desig_index, d.value)
                elif isinstance(d, tuple) and len(d) == 2:
                    # Range designator [start...end]
                    _, end = d
                    end_val = end.value if isinstance(end, ast.IntLiteral) else end
                    max_desig_index = max(max_desig_index, end_val)

        # Count elements in initializer
        if isinstance(base_type, ast.StructType):
            # Check if initializer uses braced sub-initializers
            # e.g. {{1,2}, {3,4}} - each brace group is one element
            if init.values and isinstance(init.values[0], ast.InitializerList):
                array_size = len(init.values)
            else:
                # Flat init - count struct elements to determine array size
                flat_count = self._count_struct_init_values(base_type)
                if flat_count > 0:
                    array_size = (len(init.values) + flat_count - 1) // flat_count
                else:
                    array_size = len(init.values)
        else:
            array_size = len(init.values)

        # Ensure size accounts for designated indices
        if max_desig_index >= 0:
            array_size = max(array_size, max_desig_index + 1)

        # Create new ArrayType with inferred size
        return ast.ArrayType(
            base_type=base_type,
            size=ast.IntLiteral(value=array_size, is_long=False, is_unsigned=False)
        )

    def _merge_array_size(self, name: str, var_type: ast.TypeNode) -> ast.TypeNode:
        """If a prior extern declared a larger array size, use it (C99 6.9.2).

        e.g., extern char arr[3]; char arr[] = {1,}; → arr has size 3 with zero-padding.
        """
        if not isinstance(var_type, ast.ArrayType):
            return var_type
        prev = self.ctx.globals.get(name)
        if not prev or not isinstance(prev.sym_type, ast.ArrayType):
            return var_type
        prev_size = prev.sym_type.size
        cur_size = var_type.size
        if prev_size is not None and isinstance(prev_size, ast.IntLiteral):
            if cur_size is None or (isinstance(cur_size, ast.IntLiteral)
                                    and cur_size.value < prev_size.value):
                return ast.ArrayType(
                    base_type=var_type.base_type,
                    size=prev_size
                )
        return var_type

    def _count_struct_init_values(self, struct_type: ast.StructType) -> int:
        """Count flat values needed to init a struct (for size inference)."""
        # Get members from inline definition or registered structs
        if struct_type.members:
            members = struct_type.members
        elif struct_type.name and struct_type.name in self.ctx.structs:
            # Convert registered format to member format
            registered = self.ctx.structs[struct_type.name]
            count = 0
            for name, member_type, offset in registered:
                count += self._count_member_init_values(member_type)
            return count
        else:
            return 1

        count = 0
        for m in members:
            count += self._count_member_init_values(m.member_type)
        return count

    def _count_member_init_values(self, member_type: ast.TypeNode) -> int:
        """Count flat values for a member type."""
        if isinstance(member_type, ast.ArrayType):
            array_size = 1
            if member_type.size:
                if isinstance(member_type.size, ast.IntLiteral):
                    array_size = member_type.size.value
                else:
                    sz = self._eval_const_expr(member_type.size)
                    if sz is not None:
                        array_size = sz
            base_type = member_type.base_type
            if isinstance(base_type, ast.StructType):
                return array_size * self._count_struct_init_values(base_type)
            return array_size
        elif isinstance(member_type, ast.StructType):
            return self._count_struct_init_values(member_type)
        return 1

    def generate(self, unit: ast.TranslationUnit) -> str:
        """Generate assembly for a translation unit."""
        # Build call graph for optimizations
        needs_call_graph = (self.enable_shared_storage or self.enable_dead_elimination or
                           self.enable_inlining or self.enable_const_propagation)
        if needs_call_graph:
            self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program, type_config=self.type_config)
            self.call_graph_analyzer.build_call_graph(unit)

        # Inline expansion (before dead elimination so inlined functions can be removed)
        if self.enable_inlining and self.call_graph_analyzer:
            unit, self.inlined_calls = self.call_graph_analyzer.inline_functions(unit)
            # Rebuild call graph after inlining
            if self.inlined_calls > 0:
                self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program, type_config=self.type_config)
                self.call_graph_analyzer.build_call_graph(unit)

        # Interprocedural constant propagation (after inlining, before dead elimination)
        if self.enable_const_propagation and self.call_graph_analyzer:
            unit, self.constants_propagated = self.call_graph_analyzer.propagate_constants(unit)
            # Rebuild call graph after constant propagation if any changes
            if self.constants_propagated > 0:
                self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program, type_config=self.type_config)
                self.call_graph_analyzer.build_call_graph(unit)

        # Dead function elimination
        if self.enable_dead_elimination and self.call_graph_analyzer:
            original_count = sum(1 for d in unit.declarations
                                if isinstance(d, ast.FunctionDecl) and d.body)
            unit = self.call_graph_analyzer.eliminate_dead_functions(unit)
            new_count = sum(1 for d in unit.declarations
                           if isinstance(d, ast.FunctionDecl) and d.body)
            self.dead_functions_removed = original_count - new_count

            # Rebuild call graph after elimination for accurate shared storage
            if self.enable_shared_storage and self.dead_functions_removed > 0:
                self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program, type_config=self.type_config)
                self.call_graph_analyzer.build_call_graph(unit)

        # Shared storage allocation
        if self.enable_shared_storage and self.call_graph_analyzer:
            self.call_graph_analyzer.compute_active_together()
            self.call_graph_analyzer.allocate_shared_storage()

        # Header
        self.ctx.emit(f"; C24 Compiler Output - {self.module_name}")
        self.ctx.emit("; Target: Z80")
        self.ctx.emit("; Generated by uc80")
        self.ctx.emit()
        self.ctx.emit("\t.z80")
        self.ctx.emit()

        # First pass: collect global declarations
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl):
                # Create FunctionType with return type and parameter types
                func_type = ast.FunctionType(
                    return_type=decl.return_type,
                    param_types=[p.param_type for p in decl.params],
                    is_variadic=decl.is_variadic
                )
                self.ctx.globals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=func_type,
                    is_global=True
                )
                self.ctx.function_names.add(decl.name)
            elif isinstance(decl, ast.VarDecl):
                var_type = self._infer_array_size(decl.var_type, decl.init)
                # If a prior extern declared a larger array size, use it (C99 6.9.2)
                var_type = self._merge_array_size(decl.name, var_type)
                self.ctx.globals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=var_type,
                    is_global=True
                )
            elif isinstance(decl, ast.DeclarationList):
                for d in decl.declarations:
                    if isinstance(d, ast.VarDecl):
                        var_type = self._infer_array_size(d.var_type, d.init)
                        var_type = self._merge_array_size(d.name, var_type)
                        self.ctx.globals[d.name] = Symbol(
                            name=d.name,
                            sym_type=var_type,
                            is_global=True
                        )

        # Disambiguate globals whose case-folded names collide.  The MACRO-80
        # assembler we target is case-insensitive, so e.g. `int sprite` and
        # `const int SPRITE` both resolve to `_SPRITE` and the assembler
        # rejects the second definition.  Append a numeric suffix to all
        # but the first occurrence of each lowercase form.
        lower_seen: dict[str, int] = {}
        for name, sym in self.ctx.globals.items():
            key = name.lower()
            count = lower_seen.get(key, 0)
            if count > 0:
                sym.label_override = f"{name}_{count}"
            lower_seen[key] = count + 1

        # Auto-detect printf features if not explicitly specified.
        # Run in both whole-program and separate-compilation modes so that the
        # compiled unit always emits its own __printf_format_table when it
        # calls printf.  Without the codegen-emitted table, libc.rel's
        # concat-baked default table wins, and that default only knows the
        # 16-bit-int handlers — breaking --int=32 and --long=64.
        if self.printf_features is None:
            detected = self._auto_detect_printf_features(unit)
            if detected is not None:
                self.printf_features = detected
                # If no format specifiers used, convert printf("...\n") to puts("...")
                # (only safe in whole-program mode where we see all callers).
                if not detected and self.whole_program:
                    if self._rewrite_printf_to_puts(unit):
                        self._needs_puts_extern = True
            else:
                # Non-literal format string somewhere — must include every handler.
                self.printf_features = {"all"}

        # Under --int=32, scanf's %d/%u/%x/%i store only 2 bytes into the
        # (4-byte) int pointed at, leaving the upper half garbage.  The
        # scanf library does have %ld/%lu/%li/%lx which store 4 bytes, so
        # rewrite literal format strings here: %d → %ld, etc.  Non-literal
        # format strings are left alone (rare; documented limitation).
        if self.type_config.int_size == 4:
            self._rewrite_scanf_formats_for_int32(unit)

        # Code segment
        self.ctx.emit("\tcseg")
        self.ctx.emit()

        # Emit EXTRN for puts if printf→puts rewrite needs it
        if getattr(self, '_needs_puts_extern', False):
            self.ctx.emit_instr("extrn", "_puts")

        # Generate code for each declaration
        for decl in unit.declarations:
            self.gen_declaration(decl)

        # Emit printf/scanf dispatch tables if features are known
        if self.printf_features is not None:
            self._emit_printf_format_tables()

        # Emit EXTRN for runtime functions used (unless embedding runtime)
        if self.ctx.runtime_used and not self.embed_runtime:
            self.ctx.emit()
            self.ctx.emit("; Runtime library functions")
            for name in sorted(self.ctx.runtime_used):
                self.ctx.emit_instr("extrn", name)

        # Emit EXTRN for any implicit externals discovered during codegen
        # (functions called/referenced without an explicit prototype, e.g.
        # abort() / exit() in pre-C99 source).
        if self.ctx.implicit_externs:
            already = set(self.ctx.runtime_used)
            new_externs = sorted(n for n in self.ctx.implicit_externs
                                 if n not in already)
            if new_externs:
                self.ctx.emit()
                self.ctx.emit("; Implicit external functions")
                for name in new_externs:
                    self.ctx.emit_instr("extrn", name)

        # Data segment for global variables
        # Collect global variable declarations, merging tentative definitions
        # In C, multiple declarations of the same variable are allowed (tentative definitions)
        # Only one can have an initializer
        global_vars: dict[str, ast.VarDecl] = {}  # name -> decl with init (or first decl)
        extern_only: set[str] = set()  # Names that only have extern declarations

        for d in unit.declarations:
            decls_to_check = []
            if isinstance(d, ast.VarDecl) and not isinstance(d.var_type, ast.FunctionType):
                decls_to_check.append(d)
            elif isinstance(d, ast.DeclarationList):
                for inner in d.declarations:
                    if isinstance(inner, ast.VarDecl) and not isinstance(inner.var_type, ast.FunctionType):
                        decls_to_check.append(inner)
            for decl in decls_to_check:
                if decl.storage_class == "extern" and not decl.init:
                    # Pure extern declaration - track it but don't define yet
                    if decl.name not in global_vars:
                        extern_only.add(decl.name)
                else:
                    # This is a definition (not extern, or extern with init)
                    extern_only.discard(decl.name)  # Remove from extern-only
                    if decl.name in global_vars:
                        # Already seen - prefer the one with initializer
                        if decl.init and not global_vars[decl.name].init:
                            global_vars[decl.name] = decl
                    else:
                        global_vars[decl.name] = decl

        # Emit EXTRN for symbols that are extern-only (declared but not defined)
        for name in sorted(extern_only):
            sym = self.ctx.globals.get(name)
            self.ctx.emit_instr("extrn", sym.label() if sym else f"_{name}")

        # Separate globals into initialized (DSEG) and uninitialized (COMMON/BSS)
        init_globals = []
        uninit_globals = []
        if global_vars:
            self.ctx.emit()
            self.ctx.emit("; Global variables")
            for name, decl in global_vars.items():
                if decl.storage_class != "static":
                    sym = self.ctx.globals.get(decl.name)
                    label = sym.label() if sym else f"_{decl.name}"
                    self.ctx.emit_instr("public", label)
            for name, decl in global_vars.items():
                if decl.init:
                    init_globals.append((name, decl))
                else:
                    uninit_globals.append((name, decl))

        # Separate statics into initialized (DSEG) and uninitialized (COMMON/BSS)
        init_statics = []
        uninit_statics = []
        for label, entry in self.ctx.static_locals.items():
            var_type, init = entry[0], entry[1]
            resolved_addr = entry[2] if len(entry) > 2 else None
            if resolved_addr and isinstance(var_type, ast.PointerType):
                init_statics.append((label, entry, True))  # has resolved addr
            elif init:
                init_statics.append((label, entry, False))
            else:
                uninit_statics.append((label, entry))

        # === DSEG: initialized data only (goes into binary) ===
        in_dseg = False

        if init_globals:
            self.ctx.emit("\tdseg")
            in_dseg = True
            for name, decl in init_globals:
                sym = self.ctx.globals.get(name)
                var_type = sym.sym_type if sym else decl.var_type
                label = sym.label() if sym else f"_{decl.name}"
                self.ctx.emit_label(label)
                self._emit_initializer(decl.init, var_type)

        if init_statics:
            if not in_dseg:
                self.ctx.emit("\tdseg")
                in_dseg = True
            self.ctx.emit("; Static local variables")
            for label, entry, has_resolved in init_statics:
                var_type, init = entry[0], entry[1]
                resolved_addr = entry[2] if len(entry) > 2 else None
                self.ctx.emit_label(label)
                if has_resolved:
                    self._emit_address_const(*resolved_addr)
                else:
                    self._emit_initializer(init, var_type)

        # Data segment with string literals (emitted after globals so that
        # strings created during global initializer emission are included)
        if self.ctx.strings:
            if not in_dseg:
                self.ctx.emit("\tdseg")
                in_dseg = True
            self.ctx.emit()
            self.ctx.emit("; String literals")
            for label, value in self.ctx.strings.items():
                self.ctx.emit_label(label)
                if label in self.ctx.wide_strings:
                    # Wide string: emit each character as a 16-bit word (little-endian)
                    for ch in value:
                        self.ctx.emit_instr("dw", str(ord(ch)))
                    self.ctx.emit_instr("dw", "0")  # 16-bit null terminator
                else:
                    # Narrow string: emit as bytes with null terminator
                    escaped = self._escape_string(value)
                    self.ctx.emit_instr("db", f"'{escaped}',0")

        # Compound literals materialized during code generation
        if hasattr(self.ctx, 'compound_literals') and self.ctx.compound_literals:
            if not in_dseg:
                self.ctx.emit("\tdseg")
                in_dseg = True
            self.ctx.emit()
            self.ctx.emit("; Compound literals")
            for label, init, target_type, size in self.ctx.compound_literals:
                self.ctx.emit_label(label)
                if isinstance(init, ast.InitializerList):
                    self._emit_initializer(init, target_type)
                else:
                    self.ctx.emit_instr("ds", str(size))

        # === COMMON: uninitialized data (BSS - zeroed by crt0, not in binary) ===
        has_bss = uninit_globals or uninit_statics or (
            self.call_graph_analyzer and self.call_graph_analyzer.total_shared_storage > 0)

        if has_bss:
            self.ctx.emit()
            self.ctx.emit("; BSS - uninitialized static storage (zeroed by crt0)")
            self.ctx.emit("\tcommon\t//")

        if uninit_globals:
            for name, decl in uninit_globals:
                sym = self.ctx.globals.get(name)
                var_type = sym.sym_type if sym else decl.var_type
                size = self._type_size(var_type)
                label = sym.label() if sym else f"_{decl.name}"
                self.ctx.emit_label(label)
                self.ctx.emit_instr("ds", str(size))

        if uninit_statics:
            for label, entry in uninit_statics:
                var_type = entry[0]
                size = self._type_size(var_type)
                self.ctx.emit_label(label)
                self.ctx.emit_instr("ds", str(size))

        # Shared automatic storage for non-recursive functions
        if self.call_graph_analyzer and self.call_graph_analyzer.total_shared_storage > 0:
            self.ctx.emit("; Shared automatic storage")
            self.ctx.emit_label("??AUTO")
            self.ctx.emit_instr("ds", str(self.call_graph_analyzer.total_shared_storage))

        self.ctx.emit()
        self.ctx.emit("\tend")

        return "\n".join(self.ctx.lines)

    def gen_declaration(self, decl: ast.Declaration) -> None:
        """Generate code for a declaration."""
        if isinstance(decl, ast.FunctionDecl):
            self.gen_function(decl)
        elif isinstance(decl, ast.VarDecl):
            # Register any inline types (enums, etc.)
            self._register_inline_types(decl.var_type)
            # Check if this is a function declaration (parsed as VarDecl with FunctionType)
            if isinstance(decl.var_type, ast.FunctionType):
                # This is a function declaration without body - emit EXTRN
                # (but not for static functions or functions defined in this file)
                if decl.storage_class != "static" and decl.name not in self.ctx.function_names:
                    sym = self.ctx.globals.get(decl.name)
                    self.ctx.emit_instr("extrn", sym.label() if sym else f"_{decl.name}")
            # Other global variables are handled in data segment
            pass
        elif isinstance(decl, ast.DeclarationList):
            for d in decl.declarations:
                self.gen_declaration(d)
        elif isinstance(decl, ast.StructDecl):
            self._register_struct(decl)
        elif isinstance(decl, ast.EnumDecl):
            self._register_enum(decl)
        elif isinstance(decl, ast.TypedefDecl):
            self._register_typedef(decl)

    def _compute_struct_layout(self, struct_name: str, ast_members: list[ast.StructMember],
                                is_union: bool) -> tuple[list[tuple[str, ast.TypeNode, int]],
                                                          list[tuple[ast.StructType, int]], int]:
        """Compute struct layout with bitfield packing.

        Returns (members, anon_list, total_size) where members is a list of
        (name, type, byte_offset) tuples and anon_list is anonymous struct/union members.
        Bitfield info is registered in ctx.bitfield_info as a side effect.
        """
        members = []
        anon_list = []
        offset = 0
        # Bitfield packing state
        bit_pos = 0              # Current bit position within storage unit
        storage_unit_start = 0   # Byte offset where current storage unit begins
        storage_unit_size = 0    # Size of current storage unit in bytes

        for member in ast_members:
            bf_width = None
            if member.bit_width is not None:
                bf_width = self._eval_const_expr(member.bit_width)
                if bf_width is None:
                    bf_width = self._eval_enum_expr(member.bit_width)

            if bf_width is not None:
                # Bitfield member
                type_size = self._type_size(member.member_type)
                max_bits = type_size * 8
                # Enum bitfields are unsigned (matches GCC; C leaves it impl-defined)
                bf_signed = (not isinstance(member.member_type, ast.EnumType)
                             and self._is_signed_type(member.member_type))

                if bf_width == 0:
                    # Zero-width: force alignment to the next multiple of
                    # the zero-width bitfield's own type-size (in bits),
                    # not the current storage unit.  `char :0` advances
                    # only to the next byte boundary — it doesn't have to
                    # kick subsequent bitfields out of a shared int
                    # storage unit, which the "close storage unit" rule
                    # used to do.  Stays compatible with `int :0` because
                    # int's type_size happens to equal storage_unit_size
                    # for int-based bitfield groups.
                    if is_union:
                        continue
                    align_bits = type_size * 8
                    if align_bits > 0 and bit_pos % align_bits != 0:
                        bit_pos = ((bit_pos + align_bits - 1)
                                   // align_bits) * align_bits
                    # If we've crossed the end of the current storage unit,
                    # close it and advance to the next aligned byte.
                    if storage_unit_size > 0 and bit_pos >= storage_unit_size * 8:
                        offset = storage_unit_start + storage_unit_size
                        bit_pos = 0
                        storage_unit_size = 0
                    continue

                # Clamp width to type size
                if bf_width > max_bits:
                    bf_width = max_bits

                if is_union:
                    # Union: all at offset 0
                    if member.name:
                        members.append((member.name, member.member_type, 0))
                        self.ctx.bitfield_info[(struct_name, member.name)] = BitfieldInfo(
                            bit_offset=0, bit_width=bf_width,
                            storage_size=type_size,
                            is_signed=bf_signed)
                else:
                    # Check if fits in current storage unit (same size type and room)
                    if (bit_pos > 0 and storage_unit_size == type_size
                            and bit_pos + bf_width <= max_bits):
                        # Fits in current unit
                        if member.name:
                            members.append((member.name, member.member_type, storage_unit_start))
                            self.ctx.bitfield_info[(struct_name, member.name)] = BitfieldInfo(
                                bit_offset=bit_pos, bit_width=bf_width,
                                storage_size=type_size,
                                is_signed=bf_signed)
                        bit_pos += bf_width
                    else:
                        # Start new storage unit
                        if bit_pos > 0:
                            offset = storage_unit_start + storage_unit_size
                        storage_unit_start = offset
                        storage_unit_size = type_size
                        if member.name:
                            members.append((member.name, member.member_type, offset))
                            self.ctx.bitfield_info[(struct_name, member.name)] = BitfieldInfo(
                                bit_offset=0, bit_width=bf_width,
                                storage_size=type_size,
                                is_signed=bf_signed)
                        bit_pos = bf_width
            else:
                # Non-bitfield member: close any open bitfield group
                if bit_pos > 0 and not is_union:
                    offset = storage_unit_start + storage_unit_size
                    bit_pos = 0

                if member.name:
                    members.append((member.name, member.member_type, offset))
                elif isinstance(member.member_type, ast.StructType):
                    anon_list.append((member.member_type, offset))

                if not is_union:
                    offset += self._type_size(member.member_type)

        # Close final bitfield group
        if bit_pos > 0 and not is_union:
            offset = storage_unit_start + storage_unit_size

        # Compute total size
        if is_union:
            total_size = 0
            for member in ast_members:
                total_size = max(total_size, self._type_size(member.member_type))
            offset = total_size

        return members, anon_list, offset

    def _register_struct(self, decl: ast.StructDecl) -> None:
        """Register a struct definition for later use."""
        if not decl.is_definition or not decl.name:
            return

        members, anon_list, total_size = self._compute_struct_layout(
            decl.name, decl.members, decl.is_union)
        self.ctx.structs[decl.name] = members
        self.ctx.struct_sizes[decl.name] = total_size
        if anon_list:
            self.ctx.anon_members[decl.name] = anon_list

    def _eval_enum_expr(self, expr: ast.Expression) -> int | None:
        """Evaluate a constant expression for enum values, supporting references
        to previously defined enum constants and basic arithmetic."""
        if isinstance(expr, ast.IntLiteral):
            return expr.value
        if isinstance(expr, ast.Identifier):
            if expr.name in self.ctx.enum_constants:
                return self.ctx.enum_constants[expr.name]
            return None
        if isinstance(expr, ast.UnaryOp):
            val = self._eval_enum_expr(expr.operand)
            if val is None:
                return None
            if expr.op == '-':
                return -val
            if expr.op == '+':
                return val
            if expr.op == '~':
                return ~val
            return None
        if isinstance(expr, ast.BinaryOp):
            left = self._eval_enum_expr(expr.left)
            right = self._eval_enum_expr(expr.right)
            if left is None or right is None:
                return None
            if expr.op == '+': return left + right
            if expr.op == '-': return left - right
            if expr.op == '*': return left * right
            if expr.op == '/': return left // right if right != 0 else None
            if expr.op == '%': return left % right if right != 0 else None
            if expr.op == '<<': return left << right
            if expr.op == '>>': return left >> right
            if expr.op == '&': return left & right
            if expr.op == '|': return left | right
            if expr.op == '^': return left ^ right
            return None
        if isinstance(expr, ast.Cast):
            return self._eval_enum_expr(expr.expr)
        return None

    def _register_enum_type_values(self, enum_type: ast.EnumType) -> None:
        """Register enum constants from an inline EnumType."""
        if not enum_type.values:
            return

        next_value = 0
        for enum_val in enum_type.values:
            if enum_val.value is not None:
                evaluated = self._eval_enum_expr(enum_val.value)
                if evaluated is not None:
                    next_value = evaluated
                else:
                    next_value = 0
            self.ctx.enum_constants[enum_val.name] = next_value
            next_value += 1

    def _register_enum(self, decl: ast.EnumDecl) -> None:
        """Register enum constants for later use."""
        if not decl.is_definition:
            return

        next_value = 0
        for enum_val in decl.values:
            if enum_val.value is not None:
                evaluated = self._eval_enum_expr(enum_val.value)
                if evaluated is not None:
                    next_value = evaluated
                else:
                    next_value = 0
            self.ctx.enum_constants[enum_val.name] = next_value
            next_value += 1

    def _register_inline_types(self, type_node: ast.TypeNode) -> None:
        """Recursively register inline type definitions (enums in structs, etc.)."""
        if isinstance(type_node, ast.EnumType):
            self._register_enum_type_values(type_node)
        elif isinstance(type_node, ast.StructType):
            # Register the struct with member offsets if it has a name and members
            if type_node.name and type_node.members and type_node.name not in self.ctx.structs:
                members, anon_list, total_size = self._compute_struct_layout(
                    type_node.name, type_node.members, type_node.is_union)
                self.ctx.structs[type_node.name] = members
                self.ctx.struct_sizes[type_node.name] = total_size
                if anon_list:
                    self.ctx.anon_members[type_node.name] = anon_list
            # Also register any nested inline types
            for member in type_node.members:
                self._register_inline_types(member.member_type)
        elif isinstance(type_node, ast.PointerType):
            self._register_inline_types(type_node.base_type)
        elif isinstance(type_node, ast.ArrayType):
            self._register_inline_types(type_node.base_type)
        elif isinstance(type_node, ast.FunctionType):
            self._register_inline_types(type_node.return_type)
            for param_type in type_node.param_types:
                self._register_inline_types(param_type)

    def _register_typedef(self, decl: ast.TypedefDecl) -> None:
        """Register typedef, especially for anonymous structs."""
        # Register any inline types first
        self._register_inline_types(decl.target_type)

        if isinstance(decl.target_type, ast.StructType):
            struct_type = decl.target_type
            # If it's an anonymous struct with inline members, register under typedef name
            if struct_type.members:
                struct_name = decl.name
                struct_type.name = struct_name  # Set name for later lookup
                members, anon_list, total_size = self._compute_struct_layout(
                    struct_name, struct_type.members, struct_type.is_union)
                self.ctx.structs[struct_name] = members
                self.ctx.struct_sizes[struct_name] = total_size
                if anon_list:
                    self.ctx.anon_members[struct_name] = anon_list
        # For enum types, nothing special needed - enum values are already constants

    def _emit_printf_format_tables(self) -> None:
        """Emit printf format dispatch tables based on #pragma printf features.

        Tables are (DB specifier_char, DW handler_addr) entries terminated by DB 0.
        The linker pulls in only the handler modules that are referenced.
        """
        features = self.printf_features
        assert features is not None

        self.ctx.emit()
        self.ctx.emit("; Printf format dispatch tables (#pragma printf)")

        # With --int=32, `%d` etc. should consume 4 bytes off the stack.
        # The library already ships 32-bit handlers (__printf_handle_ld etc.)
        # for the 'l' length modifier, so we route `%d`→ld, `%u`→lu, etc.
        # when int_size == 4.  `%X` maps to lx (lowercase) since there is
        # no uppercase long-hex handler — matches existing %lX behavior.
        int_is_32 = self.type_config.int_size == 4
        if int_is_32:
            int_d = '__printf_handle_ld'
            int_u = '__printf_handle_lu'
            int_o = '__printf_handle_lo'
            int_x = '__printf_handle_lx'
            int_X = '__printf_handle_lxu'
            # char is variadic-widened to int under --int=32, so %c reads
            # its 1 byte from a 4-byte slot and must advance accordingly.
            int_c = '__printf_handle_c32'
        else:
            int_d = '__printf_handle_d'
            int_u = '__printf_handle_u'
            int_o = '__printf_handle_o'
            int_x = '__printf_handle_x'
            int_X = '__printf_handle_xu'
            int_c = '__printf_handle_c'

        # Collect all handlers we'll reference, then emit EXTRNs
        handlers: set[str] = set()

        if "int" in features or "all" in features:
            handlers.update([int_d, int_u, int_o, int_x, int_X,
                           '__printf_handle_s',
                           int_c, '__printf_handle_p'])
        if "long" in features or "llong" in features or "all" in features:
            handlers.add('__printf_handle_l')
        if "long" in features or "all" in features:
            handlers.update(['__printf_handle_ld', '__printf_handle_lu',
                           '__printf_handle_lo', '__printf_handle_lx',
                           '__printf_handle_lxu'])
        if "float" in features or "all" in features:
            handlers.add('__printf_handle_f')
        if "llong" in features or "all" in features:
            handlers.update(['__printf_handle_lld', '__printf_handle_llu',
                           '__printf_handle_llx'])

        for h in sorted(handlers):
            self.ctx.emit_instr("extrn", h)
        self.ctx.emit()

        # Base format table
        self.ctx.emit_instr("public", "__printf_format_table")
        self.ctx.emit("__printf_format_table:")

        if "int" in features or "all" in features:
            for spec, handler in [('d', int_d),
                                  ('i', int_d),
                                  ('u', int_u),
                                  ('o', int_o),
                                  ('x', int_x),
                                  ('X', int_X),
                                  ('s', '__printf_handle_s'),
                                  ('c', int_c),
                                  ('p', '__printf_handle_p')]:
                self.ctx.emit(f"\tdb\t'{spec}'")
                self.ctx.emit(f"\tdw\t{handler}")
                self.ctx.runtime_used.add(handler)

        if "long" in features or "llong" in features or "all" in features:
            self.ctx.emit(f"\tdb\t'l'")
            self.ctx.emit(f"\tdw\t__printf_handle_l")
            self.ctx.runtime_used.add("__printf_handle_l")

        if "float" in features or "all" in features:
            self.ctx.emit(f"\tdb\t'f'")
            self.ctx.emit(f"\tdw\t__printf_handle_f")
            self.ctx.runtime_used.add("__printf_handle_f")

        self.ctx.emit("\tdb\t0\t\t; sentinel")

        # Long format table - always emitted to satisfy lc_printf_l EXTRN
        self.ctx.emit()
        self.ctx.emit_instr("public", "__printf_long_table")
        self.ctx.emit("__printf_long_table:")

        if "long" in features or "all" in features:
            for spec, handler in [('d', '__printf_handle_ld'),
                                  ('i', '__printf_handle_ld'),
                                  ('u', '__printf_handle_lu'),
                                  ('o', '__printf_handle_lo'),
                                  ('x', '__printf_handle_lx'),
                                  ('X', '__printf_handle_lxu')]:
                self.ctx.emit(f"\tdb\t'{spec}'")
                self.ctx.emit(f"\tdw\t{handler}")
                self.ctx.runtime_used.add(handler)

            # %lf same as %f
            if "float" in features or "all" in features:
                self.ctx.emit(f"\tdb\t'f'")
                self.ctx.emit(f"\tdw\t__printf_handle_f")

        if "llong" in features or "all" in features:
            self.ctx.emit(f"\tdb\t'l'")
            self.ctx.emit(f"\tdw\t__printf_ll_entry")

        self.ctx.emit("\tdb\t0\t\t; sentinel")

        # Long long format table - always emitted to satisfy lc_printf_l EXTRN
        self.ctx.emit()
        self.ctx.emit_instr("public", "__printf_ll_table")
        self.ctx.emit("__printf_ll_table:")

        if "llong" in features or "all" in features:
            for spec, handler in [('d', '__printf_handle_lld'),
                                  ('i', '__printf_handle_lld'),
                                  ('u', '__printf_handle_llu'),
                                  ('x', '__printf_handle_llx')]:
                self.ctx.emit(f"\tdb\t'{spec}'")
                self.ctx.emit(f"\tdw\t{handler}")
                self.ctx.runtime_used.add(handler)

        self.ctx.emit("\tdb\t0\t\t; sentinel")

        # The 'll' dispatch entry point (always needed)
        self.ctx.emit("__printf_ll_entry:")
        self.ctx.emit("\tret")

    def _auto_detect_printf_features(self, unit: ast.TranslationUnit) -> set[str] | None:
        """Scan AST for printf-family calls and detect which format specifiers are used.

        Returns a feature set (possibly empty) if all format strings are literals,
        or None if a non-literal format string is found (must use 'all').
        """
        # Printf-family functions and the index of their format string argument
        printf_funcs = {
            'printf': 0, 'fprintf': 1, 'sprintf': 1, 'snprintf': 2,
            'vprintf': 0, 'vfprintf': 1, 'vsprintf': 1,
        }
        features: set[str] = set()
        uses_printf = False

        def scan_expr(expr: ast.Expression) -> bool:
            """Scan expression for printf calls. Returns False if non-literal format found."""
            nonlocal uses_printf
            if isinstance(expr, ast.Call):
                # Check if this is a printf-family call
                func_name = None
                if isinstance(expr.func, ast.Identifier):
                    func_name = expr.func.name
                if func_name in printf_funcs:
                    uses_printf = True
                    fmt_idx = printf_funcs[func_name]
                    if fmt_idx < len(expr.args):
                        fmt_arg = expr.args[fmt_idx]
                        if isinstance(fmt_arg, ast.StringLiteral):
                            self._extract_printf_specifiers(fmt_arg.value, features)
                        else:
                            return False  # Non-literal format string
                # Scan arguments too (could have nested printf calls)
                for arg in expr.args:
                    if not scan_expr(arg):
                        return False
            elif isinstance(expr, ast.BinaryOp):
                if not scan_expr(expr.left) or not scan_expr(expr.right):
                    return False
            elif isinstance(expr, ast.UnaryOp):
                if not scan_expr(expr.operand):
                    return False
            elif isinstance(expr, ast.TernaryOp):
                if not scan_expr(expr.condition) or not scan_expr(expr.true_expr) or not scan_expr(expr.false_expr):
                    return False
            elif isinstance(expr, ast.Cast):
                if not scan_expr(expr.expr):
                    return False
            elif isinstance(expr, ast.Compound):
                if expr.init and not scan_expr(expr.init):
                    return False
            return True

        def scan_stmt(stmt) -> bool:
            """Scan statement for printf calls. Returns False if non-literal format found."""
            if isinstance(stmt, ast.ExpressionStmt):
                return scan_expr(stmt.expr)
            elif isinstance(stmt, ast.CompoundStmt):
                for s in stmt.items:
                    if not scan_stmt(s):
                        return False
            elif isinstance(stmt, ast.IfStmt):
                if not scan_expr(stmt.condition):
                    return False
                if not scan_stmt(stmt.then_branch):
                    return False
                if stmt.else_branch and not scan_stmt(stmt.else_branch):
                    return False
            elif isinstance(stmt, ast.WhileStmt):
                if not scan_expr(stmt.condition):
                    return False
                if not scan_stmt(stmt.body):
                    return False
            elif isinstance(stmt, ast.DoWhileStmt):
                if not scan_stmt(stmt.body):
                    return False
                if not scan_expr(stmt.condition):
                    return False
            elif isinstance(stmt, ast.ForStmt):
                if stmt.init:
                    if isinstance(stmt.init, ast.Expression):
                        if not scan_expr(stmt.init):
                            return False
                    else:
                        if not scan_stmt(stmt.init):
                            return False
                if stmt.condition and not scan_expr(stmt.condition):
                    return False
                if stmt.update and not scan_expr(stmt.update):
                    return False
                if not scan_stmt(stmt.body):
                    return False
            elif isinstance(stmt, ast.ReturnStmt):
                if stmt.value and not scan_expr(stmt.value):
                    return False
            elif isinstance(stmt, ast.SwitchStmt):
                if not scan_expr(stmt.expr):
                    return False
                if not scan_stmt(stmt.body):
                    return False
            elif isinstance(stmt, ast.CaseStmt):
                if not scan_stmt(stmt.stmt):
                    return False
            elif isinstance(stmt, ast.LabelStmt):
                if not scan_stmt(stmt.stmt):
                    return False
            elif isinstance(stmt, ast.VarDecl):
                if stmt.init and not scan_expr(stmt.init):
                    return False
            elif isinstance(stmt, ast.DeclarationList):
                for d in stmt.declarations:
                    if not scan_stmt(d):
                        return False
            return True

        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                if not scan_stmt(decl.body):
                    return None  # Non-literal format → fall back to all

        if not uses_printf:
            return set()  # No printf calls → empty features (minimal tables)

        return features

    @staticmethod
    def _extract_printf_specifiers(fmt: str, features: set[str]) -> None:
        """Parse a printf format string and add required feature flags."""
        i = 0
        while i < len(fmt):
            if fmt[i] != '%':
                i += 1
                continue
            i += 1
            if i >= len(fmt):
                break
            # Skip %%
            if fmt[i] == '%':
                i += 1
                continue
            # Skip flags: -, +, space, #, 0
            while i < len(fmt) and fmt[i] in '-+ #0':
                i += 1
            # Skip width (digits or *)
            if i < len(fmt) and fmt[i] == '*':
                i += 1
            else:
                while i < len(fmt) and fmt[i].isdigit():
                    i += 1
            # Skip precision
            if i < len(fmt) and fmt[i] == '.':
                i += 1
                if i < len(fmt) and fmt[i] == '*':
                    i += 1
                else:
                    while i < len(fmt) and fmt[i].isdigit():
                        i += 1
            # Length modifier
            length = ''
            if i < len(fmt) and fmt[i] in 'hlLzjt':
                length = fmt[i]
                i += 1
                if i < len(fmt) and fmt[i] == length and length in 'hl':
                    length += fmt[i]
                    i += 1
            # Conversion specifier
            if i < len(fmt):
                spec = fmt[i]
                i += 1
                if spec in 'dDiuUoOxXcCsSpn':
                    if length == 'll':
                        features.add('llong')
                        features.add('long')
                        features.add('int')
                    elif length == 'l' or spec in 'DUO':
                        features.add('long')
                        features.add('int')
                    else:
                        features.add('int')
                elif spec in 'fFeEgGaA':
                    features.add('float')
                    features.add('int')

    def _rewrite_printf_to_puts(self, unit: ast.TranslationUnit) -> bool:
        """Rewrite printf("...\n") to puts("...") when no format specifiers are used.

        Only called in whole-program mode when auto-detection found no specifiers.
        This eliminates the printf dependency entirely.
        Returns True if any rewrites were made.
        """
        rewrote = False

        def rewrite_expr(expr: ast.Expression) -> ast.Expression:
            nonlocal rewrote
            if isinstance(expr, ast.Call):
                if (isinstance(expr.func, ast.Identifier) and
                    expr.func.name == 'printf' and
                    len(expr.args) == 1 and
                    isinstance(expr.args[0], ast.StringLiteral)):
                    s = expr.args[0].value
                    if s.endswith('\n'):
                        # printf("...\n") → puts("...")
                        new_str = ast.StringLiteral(value=s[:-1], location=expr.args[0].location)
                        new_id = ast.Identifier(name='puts', location=expr.func.location)
                        rewrote = True
                        return ast.Call(func=new_id, args=[new_str], location=expr.location)
                    # printf("...") without \n — leave as printf
            return expr

        def rewrite_stmt(stmt):
            if isinstance(stmt, ast.ExpressionStmt):
                stmt.expr = rewrite_expr(stmt.expr)
            elif isinstance(stmt, ast.CompoundStmt):
                for s in stmt.items:
                    rewrite_stmt(s)
            elif isinstance(stmt, ast.IfStmt):
                rewrite_stmt(stmt.then_branch)
                if stmt.else_branch:
                    rewrite_stmt(stmt.else_branch)
            elif isinstance(stmt, ast.WhileStmt):
                rewrite_stmt(stmt.body)
            elif isinstance(stmt, ast.DoWhileStmt):
                rewrite_stmt(stmt.body)
            elif isinstance(stmt, ast.ForStmt):
                rewrite_stmt(stmt.body)
            elif isinstance(stmt, ast.SwitchStmt):
                rewrite_stmt(stmt.body)
            elif isinstance(stmt, ast.CaseStmt):
                rewrite_stmt(stmt.stmt)
            elif isinstance(stmt, ast.LabelStmt):
                rewrite_stmt(stmt.stmt)

        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                rewrite_stmt(decl.body)

        return rewrote

    def _rewrite_scanf_formats_for_int32(self, unit: ast.TranslationUnit) -> None:
        """When int_size == 4, rewrite literal scanf format strings so that
        %d/%i/%u/%x become their %l-prefixed variants.

        The scanf library's 16-bit handlers store only 2 bytes, which truncates
        writes into a 4-byte int.  The %ld/%li/%lu/%lx handlers store 4 bytes.
        %o is left alone (no library support either 16- or 32-bit).
        """
        import re
        # Matches a single format conversion: optional flags, width, precision,
        # optional length modifier already present (h/hh/l/ll/L/z/j/t), then
        # the conversion char.  We only rewrite if no length modifier is set.
        pat = re.compile(r'%([-+ #0]*\d*(?:\.\d+)?)([diux])')

        def rewrite_format(s: str) -> str:
            out = []
            i = 0
            while i < len(s):
                if s[i] != '%':
                    out.append(s[i])
                    i += 1
                    continue
                # '%%' — literal percent
                if i + 1 < len(s) and s[i + 1] == '%':
                    out.append('%%')
                    i += 2
                    continue
                m = pat.match(s, i)
                if m:
                    flags_width, conv = m.group(1), m.group(2)
                    out.append(f'%{flags_width}l{conv}')
                    i = m.end()
                else:
                    # Unrecognized / already has length modifier — leave intact
                    out.append(s[i])
                    i += 1
            return ''.join(out)

        scanf_funcs = {'scanf': 0, 'fscanf': 1, 'sscanf': 1}

        def visit_expr(expr):
            if expr is None:
                return
            if isinstance(expr, ast.Call):
                fname = expr.func.name if isinstance(expr.func, ast.Identifier) else None
                if fname in scanf_funcs:
                    idx = scanf_funcs[fname]
                    if idx < len(expr.args) and isinstance(expr.args[idx], ast.StringLiteral):
                        lit = expr.args[idx]
                        new_value = rewrite_format(lit.value)
                        if new_value != lit.value:
                            lit.value = new_value
                visit_expr(expr.func)
                for a in expr.args:
                    visit_expr(a)
            elif isinstance(expr, ast.BinaryOp):
                visit_expr(expr.left)
                visit_expr(expr.right)
            elif isinstance(expr, ast.UnaryOp):
                visit_expr(expr.operand)
            elif isinstance(expr, ast.TernaryOp):
                visit_expr(expr.condition)
                visit_expr(expr.true_expr)
                visit_expr(expr.false_expr)
            elif isinstance(expr, ast.Cast):
                visit_expr(expr.expr)
            elif isinstance(expr, ast.Member):
                visit_expr(expr.obj)
            elif isinstance(expr, ast.Index):
                visit_expr(expr.array)
                visit_expr(expr.index)
            elif isinstance(expr, ast.Compound):
                for it in expr.items:
                    visit_expr(it) if isinstance(it, ast.Expression) else visit_stmt(it)

        def visit_stmt(stmt):
            if isinstance(stmt, ast.ExpressionStmt):
                visit_expr(stmt.expr)
            elif isinstance(stmt, ast.CompoundStmt):
                for s in stmt.items:
                    visit_stmt(s)
            elif isinstance(stmt, ast.IfStmt):
                visit_expr(stmt.condition)
                visit_stmt(stmt.then_branch)
                if stmt.else_branch:
                    visit_stmt(stmt.else_branch)
            elif isinstance(stmt, ast.WhileStmt):
                visit_expr(stmt.condition)
                visit_stmt(stmt.body)
            elif isinstance(stmt, ast.DoWhileStmt):
                visit_stmt(stmt.body)
                visit_expr(stmt.condition)
            elif isinstance(stmt, ast.ForStmt):
                if stmt.init:
                    visit_stmt(stmt.init) if not isinstance(stmt.init, ast.Expression) else visit_expr(stmt.init)
                if stmt.condition:
                    visit_expr(stmt.condition)
                if stmt.update:
                    visit_expr(stmt.update)
                visit_stmt(stmt.body)
            elif isinstance(stmt, ast.SwitchStmt):
                visit_expr(stmt.expr)
                visit_stmt(stmt.body)
            elif isinstance(stmt, ast.CaseStmt):
                visit_stmt(stmt.stmt)
            elif isinstance(stmt, ast.LabelStmt):
                visit_stmt(stmt.stmt)
            elif isinstance(stmt, ast.ReturnStmt):
                if stmt.value:
                    visit_expr(stmt.value)
            elif isinstance(stmt, ast.VarDecl):
                if stmt.init:
                    visit_expr(stmt.init)

        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                visit_stmt(decl.body)

    def gen_function(self, func: ast.FunctionDecl) -> None:
        """Generate code for a function."""
        if func.body is None:
            # Just a declaration, emit EXTRN (but not for static or locally-defined functions)
            if func.storage_class != "static" and func.name not in self.ctx.function_names:
                sym = self.ctx.globals.get(func.name)
                self.ctx.emit_instr("extrn", sym.label() if sym else f"_{func.name}")
            return

        self.ctx.current_function = func.name
        self.ctx.current_return_type = func.return_type
        self.ctx.locals.clear()
        self.ctx.local_offset = 0
        self.ctx.regs.reset()  # Reset register allocator for new function

        # Check if this function uses shared storage optimization
        use_shared_storage = (
            self.call_graph_analyzer is not None and
            self.call_graph_analyzer.can_use_shared_storage(func.name)
        )

        # Track shared storage base offset for this function
        shared_base_offset = 0
        if use_shared_storage:
            shared_base_offset = self.call_graph_analyzer.storage_offsets.get(func.name, 0)

        # Store in context for local variable allocation
        self._use_shared_storage = use_shared_storage
        self._shared_base_offset = shared_base_offset
        self._shared_local_offset = 0  # Track offset within function's shared area

        # Make function public (unless static)
        sym = self.ctx.globals.get(func.name)
        func_label = sym.label() if sym else f"_{func.name}"
        if func.storage_class != "static":
            self.ctx.emit_instr("public", func_label)
        self.ctx.emit()
        if use_shared_storage:
            self.ctx.emit(f"; Function {func.name} (uses shared storage)")
        else:
            self.ctx.emit(f"; Function {func.name}")
        self.ctx.emit_label(func_label)

        # Function prologue: save IX, set up frame
        self.ctx.emit_instr("push", "IX")
        self.ctx.emit_instr("ld", "IX,0")
        self.ctx.emit_instr("add", "IX,SP")

        # Calculate space needed for locals (only for stack-based functions)
        if not use_shared_storage:
            local_size = self._calc_locals_size(func.body)
            if local_size > 0:
                self.ctx.emit_instr("ld", f"HL,-{local_size}")
                self.ctx.emit_instr("add", "HL,SP")
                self.ctx.emit_instr("ld", "SP,HL")

        # Set up parameters in symbol table
        # Parameters are at IX+4, IX+6, etc. (after saved IX and return address)
        param_offset = 4
        for param in func.params:
            if param.name:
                size = self._type_size(param.param_type)
                self.ctx.locals[param.name] = Symbol(
                    name=param.name,
                    sym_type=param.param_type,
                    offset=param_offset,
                    is_param=True
                )
                # Round up to even for stack alignment (PUSH works in 2-byte words)
                param_offset += (size + 1) & ~1

        # Generate function body
        self.gen_compound_stmt(func.body)

        # Epilogue label for early returns
        epilogue_label = f"@{func.name}_ret"
        self.ctx.emit_label(epilogue_label)

        # Function epilogue: restore SP, IX, return
        # Note: For shared storage functions, SP hasn't changed, but this is still safe
        self.ctx.emit_instr("ld", "SP,IX")
        self.ctx.emit_instr("pop", "IX")
        self.ctx.emit_instr("ret")
        self.ctx.emit()

        self.ctx.current_function = None
        self.ctx.current_return_type = None
        self._use_shared_storage = False

    def gen_compound_stmt(self, stmt: ast.CompoundStmt) -> None:
        """Generate code for a compound statement (block)."""
        # Save block-scoped extern names for scope restoration
        saved_block_externs = getattr(self.ctx, 'block_externs', set()).copy()
        for item in stmt.items:
            if isinstance(item, ast.Declaration):
                self.gen_local_decl(item)
            else:
                self.gen_statement(item)
        # Restore block_externs on scope exit
        self.ctx.block_externs = saved_block_externs

    def gen_local_decl(self, decl: ast.Declaration) -> None:
        """Generate code for a local declaration."""
        if isinstance(decl, ast.DeclarationList):
            # Handle multiple declarations (e.g., 'int a, b;')
            for d in decl.declarations:
                self.gen_local_decl(d)
            return

        if isinstance(decl, ast.StructDecl):
            # Register local struct/union type declarations
            self._register_struct(decl)
            return

        if isinstance(decl, ast.EnumDecl):
            # Register local enum constants
            self._register_enum(decl)
            return

        if isinstance(decl, ast.TypedefDecl):
            # Register local typedef (including inline enum types)
            self._register_typedef(decl)
            return

        if isinstance(decl, ast.VarDecl):
            # Register any inline types (e.g., struct defined in local variable decl)
            self._register_inline_types(decl.var_type)

            # Check for static local variable
            if decl.storage_class == "static":
                self._gen_static_local(decl)
                return

            # Handle extern declarations inside functions - reference the global symbol
            # rather than allocating local storage (e.g., extern int v1; inside main())
            if decl.storage_class == "extern":
                if decl.name not in self.ctx.globals:
                    # Not yet seen as a global - register it as an external global
                    self.ctx.globals[decl.name] = Symbol(
                        name=decl.name,
                        sym_type=decl.var_type,
                        is_global=True
                    )
                    # Track for EXTRN emission
                    if not hasattr(self.ctx, '_local_externs'):
                        self.ctx._local_externs = set()
                    self.ctx._local_externs.add(decl.name)
                # If a same-named local exists, mark this name to bypass local lookup
                if decl.name in self.ctx.locals:
                    if not hasattr(self.ctx, 'block_externs'):
                        self.ctx.block_externs = set()
                    self.ctx.block_externs.add(decl.name)
                return

            # Infer array size from initializer for unsized arrays (e.g., char s[] = "hello")
            if isinstance(decl.var_type, ast.ArrayType) and decl.var_type.size is None and decl.init:
                decl.var_type = self._infer_array_size(decl.var_type, decl.init)

            size = self._type_size(decl.var_type)

            # Check if using shared storage
            if getattr(self, '_use_shared_storage', False):
                # Allocate in shared storage area
                shared_offset = self._shared_base_offset + self._shared_local_offset
                self._shared_local_offset += size
                self.ctx.locals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=decl.var_type,
                    offset=0,  # Not used for shared storage
                    uses_shared_storage=True,
                    shared_offset=shared_offset
                )
            else:
                # Stack-based allocation
                self.ctx.local_offset -= size
                self.ctx.locals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=decl.var_type,
                    offset=self.ctx.local_offset
                )

            # Initialize if there's an initializer
            if decl.init:
                # Unwrap compound literal if needed
                init = decl.init
                init_type = decl.var_type
                if isinstance(decl.init, ast.Compound):
                    init = decl.init.init  # Get InitializerList from Compound
                    init_type = decl.init.target_type

                # Handle string literal initializing an array (char[] or wchar_t[])
                if isinstance(init_type, ast.ArrayType) and isinstance(init, ast.StringLiteral):
                    sym = self.ctx.locals[decl.name]
                    self._gen_local_string_array_init(sym, init_type, init)
                # Handle braced string literal: char x[] = {"XXX"}
                elif (isinstance(init_type, ast.ArrayType) and isinstance(init, ast.InitializerList)
                      and len(init.values) == 1 and isinstance(init.values[0], ast.StringLiteral)
                      and isinstance(init_type.base_type, ast.BasicType)
                      and init_type.base_type.name in ("char", "signed char", "unsigned char")):
                    sym = self.ctx.locals[decl.name]
                    self._gen_local_string_array_init(sym, init_type, init.values[0])
                # Handle array initialization specially
                elif isinstance(init_type, ast.ArrayType) and isinstance(init, ast.InitializerList):
                    # Temporarily replace decl.init with unwrapped init
                    old_init = decl.init
                    decl.init = init
                    self._gen_local_array_init(decl)
                    decl.init = old_init
                elif isinstance(init_type, ast.StructType) and isinstance(init, ast.InitializerList):
                    # Temporarily replace decl.init with unwrapped init
                    old_init = decl.init
                    decl.init = init
                    self._gen_local_struct_init(decl)
                    decl.init = old_init
                elif isinstance(decl.var_type, ast.StructType):
                    # Struct copy from expression (e.g., *ptr, or struct variable)
                    self._gen_struct_copy_from_expr(decl)
                else:
                    is_long = self._is_long_type(decl.var_type)
                    is_long_long = self._is_long_long_type(decl.var_type)
                    is_float = self._is_float_type(decl.var_type)
                    init_is_float = self._is_float_expr(decl.init)
                    target_is_int = not is_float and not is_long and not is_long_long

                    # Handle float-to-int conversion
                    if init_is_float and target_is_int:
                        if self._is_bool_type(decl.var_type):
                            # Float-to-bool: any non-zero float becomes 1 (C99 6.3.1.2)
                            if isinstance(decl.init, ast.FloatLiteral):
                                bool_val = 0 if decl.init.value == 0.0 else 1
                                self.ctx.emit_instr("ld", f"HL,{bool_val}")
                            else:
                                self.gen_expr(decl.init, force_long=True)
                                # Check all 4 bytes of DEHL for non-zero
                                self.ctx.emit_instr("ld", "A,D")
                                self.ctx.emit_instr("or", "E")
                                self.ctx.emit_instr("or", "H")
                                self.ctx.emit_instr("or", "L")
                                self.ctx.emit_instr("ld", "HL,0")
                                self.ctx.emit_instr("jr", "Z,$+3")
                                self.ctx.emit_instr("inc", "L")
                        elif isinstance(decl.init, ast.FloatLiteral):
                            # Compile-time conversion
                            int_val = int(decl.init.value)
                            self.ctx.emit_instr("ld", f"HL,{int_val}")
                        else:
                            # Runtime conversion
                            self.gen_expr(decl.init, force_long=True)
                            self._call_runtime("__ftoi")
                    elif is_long_long:
                        # 64-bit initialization
                        self._gen_64bit_operand(decl.init, to_tmp=False)
                    elif (is_long and not is_float and init_is_float):
                        # Under --int=32 a "long" target catches plain int too.
                        # Float-literal init: fold at compile time; otherwise
                        # generate the float and call __ftoi.
                        if isinstance(decl.init, ast.FloatLiteral):
                            int_val = int(decl.init.value)
                            val32 = int_val & 0xFFFFFFFF
                            self.ctx.emit_instr("ld", f"HL,{val32 & 0xFFFF}")
                            self.ctx.emit_instr("ld", f"DE,{(val32 >> 16) & 0xFFFF}")
                        else:
                            self.gen_expr(decl.init, force_long=True)
                            self._call_runtime("__ftoi")
                    else:
                        # Both long and float need 32-bit handling
                        need_32bit = is_long or is_float
                        self.gen_expr(decl.init, force_long=need_32bit)

                        # _Bool normalization for declaration initializer (C99 6.3.1.2)
                        if self._is_bool_type(decl.var_type):
                            self._emit_bool_normalize()

                        # Extend to 32-bit if target is long (not float) but source is not 32-bit
                        # (Don't extend for float targets - they're already 32-bit in DEHL)
                        if is_long and not is_float and not self._is_long_expr(decl.init) and not self._is_float_expr(decl.init):
                            is_signed = not self._is_unsigned_expr(decl.init)
                            self._extend_hl_to_dehl(is_signed)

                        # Convert int to float if target is float but source is integer
                        if is_float and not self._is_float_expr(decl.init):
                            # First extend integer to 32-bit if needed
                            if not self._is_long_expr(decl.init):
                                is_signed = not self._is_unsigned_expr(decl.init)
                                self._extend_hl_to_dehl(is_signed)
                            # Then convert to float
                            if self._is_unsigned_expr(decl.init):
                                self._call_runtime("__uitof")
                            else:
                                self._call_runtime("__itof")

                    sym = self.ctx.locals[decl.name]
                    if is_long_long:
                        self._store_local_64(sym)
                    elif is_long or is_float:
                        self._store_local_32(sym)
                    else:
                        self._store_local(sym)

    def _gen_static_local(self, decl: ast.VarDecl) -> None:
        """Handle static local variable."""
        # Infer array size from initializer for unsized arrays
        if isinstance(decl.var_type, ast.ArrayType) and decl.var_type.size is None and decl.init:
            decl.var_type = self._infer_array_size(decl.var_type, decl.init)

        # Generate unique label for this static variable
        # Use function name + counter to make globally unique, don't use @ prefix
        func_name = self.ctx.current_function or "global"
        label = f"__{func_name}_S{self.ctx.static_counter}"
        self.ctx.static_counter += 1

        # Record mapping from variable name to label for late-binding initializers
        self.ctx.static_local_labels[decl.name] = label

        # Register as a "global" for access purposes, but mark is_static=True
        # so we don't add another _ prefix when accessing
        self.ctx.locals[decl.name] = Symbol(
            name=label,  # Use label as name for global-style access
            sym_type=decl.var_type,
            is_global=True,
            is_static=True  # Mark as static to avoid double underscore
        )

        # Pre-resolve address constant inits while local scope is active,
        # since ctx.locals will be cleared when processing subsequent functions.
        resolved_addr = None
        if decl.init:
            addr = self._try_resolve_address_const(decl.init)
            if addr[0] is not None:
                resolved_addr = addr

        # Store type, init value, and pre-resolved address for data segment emission
        self.ctx.static_locals[label] = (decl.var_type, decl.init, resolved_addr)

    def _gen_local_string_array_init(self, sym: 'Symbol', array_type: ast.ArrayType,
                                      string_lit: ast.StringLiteral) -> None:
        """Initialize a local char/wchar_t array from a string literal."""
        is_wide = getattr(string_lit, 'is_wide', False)
        elem_size = self._type_size(array_type.base_type)
        value = string_lit.value

        if is_wide:
            # Wide string: store the string data in the data segment, then LDIR
            label = self.ctx.add_string(value, is_wide=True)
            total_size = (len(value) + 1) * elem_size  # +1 for null terminator
        else:
            # Narrow string: same approach - store in data segment, LDIR
            label = self.ctx.add_string(value)
            total_size = len(value) + 1  # +1 for null terminator

        # Source: string literal in data segment
        self.ctx.emit_instr("ld", f"HL,{label}")
        # Destination: local variable address
        if sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"DE,??AUTO+{sym.shared_offset}")
        else:
            self.ctx.emit_instr("push", "HL")
            self._gen_lea_ix_offset(sym.offset)
            self.ctx.emit_instr("ex", "DE,HL")
            self.ctx.emit_instr("pop", "HL")
        self.ctx.emit_instr("ld", f"BC,{total_size}")
        self.ctx.emit_instr("ldir")

    def _gen_local_array_init(self, decl: ast.VarDecl) -> None:
        """Generate code to initialize a local array from an initializer list."""
        sym = self.ctx.locals[decl.name]
        init_list = decl.init
        elem_type = decl.var_type.base_type
        elem_size = self._type_size(elem_type)
        is_long = self._is_long_type(elem_type)
        is_float = self._is_float_type(elem_type)
        is_32bit = is_long or is_float

        # Detect flat/mixed init for arrays of aggregates (sub-arrays or structs)
        # e.g., float y[4][3] = { 1, 3, 5, 2, 4, 6, 3, 5, 7 }
        if isinstance(elem_type, (ast.StructType, ast.ArrayType)) and init_list.values:
            has_flat = any(not isinstance(v, (ast.InitializerList, ast.DesignatedInit))
                          for v in init_list.values)
            if has_flat:
                # Use _gen_store_member_value to handle flat/mixed init
                # by delegating to the struct/array flat init path
                self._gen_store_member_value(sym, decl.var_type, 0, init_list)
                return

        # Determine declared array size (in elements).  C99 6.7.8/21:
        # "If there are fewer initializers in a brace-enclosed list than there
        # are elements ... in an aggregate, the remainder of the aggregate
        # shall be initialized implicitly the same as objects that have static
        # storage duration." — i.e., zero-filled even for an auto array.
        # Without this, a re-entry into a function whose ??AUTO slot was
        # touched by another function leaves stale bytes in the unspecified
        # tail of the array.
        declared_n = None
        if isinstance(decl.var_type.size, ast.IntLiteral):
            declared_n = decl.var_type.size.value
        # Also clamp the actual emitted count to the declared size if too many
        # initializers were given (excess-initializer warning territory).
        emit_n = len(init_list.values)
        if declared_n is not None and emit_n > declared_n:
            emit_n = declared_n

        for i, val in enumerate(init_list.values[:emit_n]):
            # Handle DesignatedInit if present
            if isinstance(val, ast.DesignatedInit):
                val = val.value

            offset = i * elem_size

            # Handle aggregate element types (struct/array) with InitializerList
            if isinstance(elem_type, (ast.StructType, ast.ArrayType)) and isinstance(val, ast.InitializerList):
                self._gen_store_member_value(sym, elem_type, offset, val)
                continue

            # Convert int literal to float if target is float
            if is_float and isinstance(val, ast.IntLiteral):
                val = ast.FloatLiteral(value=float(val.value))

            # Generate the value in HL (or DEHL for 32-bit)
            self.gen_expr(val, force_long=is_32bit)

            # Store at array[i]
            if sym.uses_shared_storage:
                # Store to shared storage: ??AUTO+base+offset
                base = sym.shared_offset + offset
                if is_32bit:
                    self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
                    self.ctx.emit_instr("ld", f"(??AUTO+{base + 2}),DE")
                elif elem_size == 1:
                    self.ctx.emit_instr("ld", "A,L")
                    self.ctx.emit_instr("ld", f"(??AUTO+{base}),A")
                else:
                    self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
            else:
                # Stack-based: store at IX+base_offset+offset
                frame_off = sym.offset + offset
                if is_32bit:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 2)}),E")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 3)}),D")
                elif elem_size == 1:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                else:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")

        # Zero-fill the unspecified tail of the array.
        if declared_n is not None and declared_n > emit_n:
            tail_start_off = emit_n * elem_size
            tail_bytes = (declared_n - emit_n) * elem_size
            self._gen_zero_init_region(sym, tail_start_off, tail_bytes)

    def _gen_local_struct_init(self, decl: ast.VarDecl) -> None:
        """Generate code to initialize a local struct from an initializer list."""
        sym = self.ctx.locals[decl.name]
        struct_type = decl.var_type
        init_list = decl.init

        # Get struct members (handles both named and anonymous structs)
        if not isinstance(struct_type, ast.StructType):
            return
        members = self._get_struct_members(struct_type)
        if not members:
            return

        # Initialize struct with values from initializer list
        self._gen_struct_init_values(sym, struct_type, init_list.values, 0)

    def _gen_struct_copy_from_expr(self, decl: ast.VarDecl) -> None:
        """Copy a struct from an expression (e.g., *ptr, struct variable)."""
        sym = self.ctx.locals[decl.name]
        size = self._type_size(decl.var_type)
        init = decl.init

        # Get source address into HL
        if isinstance(init, ast.UnaryOp) and init.op == "*":
            # Dereference: *ptr - load pointer value into HL
            self.gen_expr(init.operand)
        elif isinstance(init, ast.Identifier):
            # Simple identifier - load its address
            src_sym = self.ctx.lookup(init.name)
            if src_sym:
                if src_sym.is_global:
                    self.ctx.emit_instr("ld", f"HL,{src_sym.label()}")
                elif src_sym.uses_shared_storage:
                    self.ctx.emit_instr("ld", f"HL,??AUTO+{src_sym.shared_offset}")
                else:
                    # Stack-based: IX + offset
                    self.ctx.emit_instr("push", "IX")
                    self.ctx.emit_instr("pop", "HL")
                    if src_sym.offset != 0:
                        self.ctx.emit_instr("ld", f"DE,{src_sym.offset}")
                        self.ctx.emit_instr("add", "HL,DE")
        elif isinstance(init, ast.Call):
            # Function call returning struct - for structs > 2 bytes,
            # gen_return copies to __sret_buf and returns address in HL
            self.gen_expr(init)
            if size <= 2:
                # Small struct fits in HL
                sym_local = self.ctx.locals[decl.name]
                if sym_local.uses_shared_storage:
                    self.ctx.emit_instr("ld", f"(??AUTO+{sym_local.shared_offset}),HL")
                else:
                    self._store_local(sym_local)
                return
            # HL = address of struct return buffer, fall through to copy
        else:
            # Other complex expression - evaluate and use as address
            self.gen_expr(init)
            if size <= 2:
                sym_local = self.ctx.locals[decl.name]
                if sym_local.uses_shared_storage:
                    self.ctx.emit_instr("ld", f"(??AUTO+{sym_local.shared_offset}),HL")
                else:
                    self._store_local(sym_local)
                return
            # HL = address, fall through to copy

        # Now HL has source address, copy bytes to destination
        # Use DE as destination pointer, BC for temp storage
        self.ctx.emit_instr("push", "HL")  # Save source

        # Get destination address
        if sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"DE,??AUTO+{sym.shared_offset}")
        elif sym.is_global:
            self.ctx.emit_instr("ld", f"DE,{sym.label()}")
        else:
            self.ctx.emit_instr("push", "IX")
            self.ctx.emit_instr("pop", "DE")
            if sym.offset != 0:
                self.ctx.emit_instr("ld", f"HL,{sym.offset}")
                self.ctx.emit_instr("add", "HL,DE")
                self.ctx.emit_instr("ex", "DE,HL")

        self.ctx.emit_instr("pop", "HL")  # Restore source
        self.ctx.emit_instr("ld", f"BC,{size}")
        self.ctx.emit_instr("ldir")  # Copy BC bytes from HL to DE

    def _gen_struct_assignment(self, expr: ast.BinaryOp, struct_size: int) -> None:
        """Generate struct/union assignment via LDIR copy."""
        # Evaluate source expression to get source address in HL
        right = expr.right
        if isinstance(right, ast.Compound):
            # Compound literal: materialize in DSEG and use its address
            label = self._materialize_compound_literal(right)
            self.ctx.emit_instr("ld", f"HL,{label}")
        elif isinstance(right, ast.Call):
            # Function call returning struct - returns address in HL
            self.gen_expr(right)
        elif isinstance(right, ast.Identifier):
            self._gen_address(right)
        elif isinstance(right, ast.UnaryOp) and right.op == "*":
            self.gen_expr(right.operand)
        elif isinstance(right, ast.Member):
            self._gen_address(right)
        else:
            self.gen_expr(right)

        self.ctx.emit_instr("push", "HL")  # Save source address

        # Get destination address
        self._gen_address(expr.left)
        self.ctx.emit_instr("ex", "DE,HL")  # DE = destination

        self.ctx.emit_instr("pop", "HL")    # HL = source
        self.ctx.emit_instr("ld", f"BC,{struct_size}")
        self.ctx.emit_instr("ldir")

    def _gen_struct_init_values(self, sym: 'Symbol', struct_type: ast.StructType,
                                values: list, base_offset: int) -> int:
        """Generate code to store struct initializer values. Returns number of values consumed."""
        members = self._get_struct_members(struct_type)
        if not members:
            return 0

        # Check for member designators - use non-sequential handling
        has_member_desig = any(
            isinstance(v, ast.DesignatedInit) and v.designators and isinstance(v.designators[0], str)
            for v in values
        )
        if has_member_desig:
            return self._gen_struct_init_designated(sym, struct_type, members, values, base_offset)

        # For unions: zero-init the whole region first, then only initialize the first member.
        # All union members share offset 0, so we must not zero-init remaining members
        # after storing the first member's values (that would overwrite them).
        if struct_type.is_union:
            union_size = self._type_size(struct_type)
            self._gen_zero_init_region(sym, base_offset, union_size)
            if values and members:
                member_name, member_type, member_offset = members[0]
                val = values[0]
                if isinstance(val, ast.DesignatedInit):
                    val = val.value
                if isinstance(member_type, ast.ArrayType) and not isinstance(val, ast.InitializerList):
                    if isinstance(val, ast.StringLiteral) and self._is_char_array(member_type):
                        self._gen_string_init(sym, member_type, val, base_offset + member_offset)
                        return 1
                    else:
                        return self._gen_flat_array_init(sym, member_type, values, 0, base_offset + member_offset)
                elif isinstance(member_type, ast.StructType) and not isinstance(val, (ast.InitializerList, ast.Compound)):
                    return self._gen_struct_init_values(sym, member_type, values, base_offset + member_offset)
                else:
                    self._gen_store_member_value(sym, member_type, base_offset + member_offset, val)
                    return 1
            return 0

        value_index = 0

        # Check if struct has bitfields (need struct_name for bitfield_info lookup)
        struct_name = struct_type.name

        for member_name, member_type, member_offset in members:
            if value_index >= len(values):
                # No more values - zero-initialize remaining members
                bf = self.ctx.bitfield_info.get((struct_name, member_name)) if struct_name else None
                if bf is not None:
                    # Bitfield: zero-init via bitfield write
                    self._gen_bitfield_init_store(sym, base_offset + member_offset, bf,
                                                  ast.IntLiteral(value=0))
                else:
                    self._gen_zero_init_member(sym, member_type, base_offset + member_offset)
                continue

            val = values[value_index]

            # Check if this member is a bitfield
            bf = self.ctx.bitfield_info.get((struct_name, member_name)) if struct_name else None
            if bf is not None and not (bf.bit_offset == 0 and bf.bit_width == bf.storage_size * 8):
                # Bitfield member: use read-modify-write
                value_index += 1
                if isinstance(val, ast.DesignatedInit):
                    val = val.value
                self._gen_bitfield_init_store(sym, base_offset + member_offset, bf, val)
                continue

            # Handle DesignatedInit (non-member, e.g. array index designator)
            if isinstance(val, ast.DesignatedInit):
                val = val.value
                value_index += 1
                self._gen_store_member_value(sym, member_type, base_offset + member_offset, val)
                continue
            elif isinstance(member_type, ast.ArrayType) and not isinstance(val, ast.InitializerList):
                # Check for string literal initializing char array
                if isinstance(val, ast.StringLiteral) and self._is_char_array(member_type):
                    value_index += 1
                    self._gen_string_init(sym, member_type, val, base_offset + member_offset)
                else:
                    # Flat initialization for array member - consume multiple values
                    consumed = self._gen_flat_array_init(sym, member_type, values, value_index, base_offset + member_offset)
                    value_index += consumed
            elif isinstance(member_type, ast.StructType) and not isinstance(val, (ast.InitializerList, ast.Compound)):
                # Check if value is an identifier referring to a struct variable
                if isinstance(val, ast.Identifier):
                    src_sym = self.ctx.lookup(val.name)
                    if src_sym and isinstance(src_sym.sym_type, ast.StructType):
                        # Copy the struct
                        value_index += 1
                        member_size = self._type_size(member_type)
                        self._gen_struct_copy(sym, base_offset + member_offset, src_sym, 0, member_size)
                        continue
                # Check for pointer dereference
                if isinstance(val, ast.UnaryOp) and val.op == "*":
                    value_index += 1
                    member_size = self._type_size(member_type)
                    self._gen_struct_copy_from_expr_to_member(sym, base_offset + member_offset, val, member_size)
                    continue
                # Check for member access (e.g., phdr->daddr)
                if isinstance(val, ast.Member):
                    value_index += 1
                    member_size = self._type_size(member_type)
                    self._gen_struct_copy_from_addr_expr(sym, base_offset + member_offset, val, member_size)
                    continue
                # Check for cast of addressable expression (e.g., (struct S)w->t.s)
                if isinstance(val, ast.Cast) and isinstance(val.expr, (ast.Member, ast.UnaryOp, ast.Identifier)):
                    value_index += 1
                    member_size = self._type_size(member_type)
                    self._gen_struct_copy_from_addr_expr(sym, base_offset + member_offset, val.expr, member_size)
                    continue
                # Flat initialization for nested struct - consume multiple values
                consumed = self._gen_struct_init_values(sym, member_type, values[value_index:], base_offset + member_offset)
                value_index += consumed
            else:
                # Normal case - single value for member
                value_index += 1
                self._gen_store_member_value(sym, member_type, base_offset + member_offset, val)

        return value_index

    def _gen_struct_init_designated(self, sym: 'Symbol', struct_type: ast.StructType,
                                     members: list, values: list, base_offset: int) -> int:
        """Handle local struct init with member designators (e.g., .a.j = 5).

        Zero-initializes the entire struct first, then applies designated values.
        """
        # Zero-initialize the entire struct first
        struct_size = self._type_size(struct_type)
        self._gen_zero_init_region(sym, base_offset, struct_size)

        # Build member -> value mapping, handling nested designators
        member_vals = {}  # member_name -> value or list of (nested_designators, value)
        next_member_idx = 0
        active_nested_member = None  # Track current member for continuation
        active_nested_pos = 0  # next sequential index within the nested member
        active_nested_size = 0  # capacity of the nested member (array size)

        for val in values:
            if isinstance(val, ast.DesignatedInit) and val.designators and isinstance(val.designators[0], str):
                desig_name = val.designators[0]
                if len(val.designators) > 1:
                    # Nested designator like .a[1] = 5 or .a.j = 5
                    if desig_name not in member_vals or not isinstance(member_vals[desig_name], list):
                        member_vals[desig_name] = []
                    member_vals[desig_name].append((val.designators[1:], val.value))
                    active_nested_member = desig_name
                    # Track position for continuation
                    active_nested_size = 0
                    active_nested_pos = 0
                    for mname, mtype, moff in members:
                        if mname == desig_name and isinstance(mtype, ast.ArrayType) and mtype.size is not None:
                            sz = mtype.size
                            active_nested_size = sz.value if isinstance(sz, ast.IntLiteral) else (sz if isinstance(sz, int) else 0)
                            last_desig = val.designators[-1]
                            if isinstance(last_desig, int):
                                active_nested_pos = last_desig + 1
                            elif isinstance(last_desig, ast.IntLiteral):
                                active_nested_pos = last_desig.value + 1
                            elif hasattr(last_desig, 'value') and isinstance(last_desig.value, int):
                                active_nested_pos = last_desig.value + 1
                            break
                else:
                    member_vals[desig_name] = val.value
                    active_nested_member = None
                # Update next member index
                for idx, (mname, mtype, moff) in enumerate(members):
                    if mname == desig_name:
                        next_member_idx = idx + 1
                        break
            else:
                actual_val = val.value if isinstance(val, ast.DesignatedInit) else val
                if active_nested_member is not None:
                    # Check if we've exceeded the nested member's capacity
                    if active_nested_size > 0 and active_nested_pos >= active_nested_size:
                        active_nested_member = None
                    else:
                        # Continuation after nested designator - add to same member
                        # Use None designators to indicate "next sequential element"
                        member_vals[active_nested_member].append((None, actual_val))
                        active_nested_pos += 1
                        continue
                if active_nested_member is None:
                    # Non-designated value
                    if next_member_idx < len(members):
                        mname = members[next_member_idx][0]
                        if mname:
                            member_vals[mname] = actual_val
                        next_member_idx += 1

        # Apply designated values
        for member_name, member_type, member_offset in members:
            if member_name not in member_vals:
                continue  # Already zeroed
            val = member_vals[member_name]
            if isinstance(val, list):
                # Nested designators - list of (designators, value)
                if isinstance(member_type, ast.ArrayType):
                    # Build index -> value map for array
                    elem_type = member_type.base_type
                    elem_size = self._type_size(elem_type)
                    next_idx = 0
                    for sub_desigs, sub_val in val:
                        if sub_desigs is None:
                            # Continuation value at next_idx
                            offset = base_offset + member_offset + next_idx * elem_size
                            self._gen_store_member_value(sym, elem_type, offset, sub_val)
                            next_idx += 1
                        elif len(sub_desigs) == 1 and not isinstance(sub_desigs[0], str):
                            # Array index designator like [1]
                            idx_val = self._eval_const_expr(sub_desigs[0])
                            if idx_val is not None:
                                offset = base_offset + member_offset + idx_val * elem_size
                                self._gen_store_member_value(sym, elem_type, offset, sub_val)
                                next_idx = idx_val + 1
                elif isinstance(member_type, ast.StructType) and member_type.name and member_type.name in self.ctx.structs:
                    sub_members = self.ctx.structs[member_type.name]
                    for sub_desigs, sub_val in val:
                        if sub_desigs is not None and len(sub_desigs) == 1 and isinstance(sub_desigs[0], str):
                            # Simple sub-member designator like .j
                            for smname, smtype, smoffset in sub_members:
                                if smname == sub_desigs[0]:
                                    self._gen_store_member_value(sym, smtype,
                                        base_offset + member_offset + smoffset, sub_val)
                                    break
                        # Could handle deeper nesting here if needed
            else:
                self._gen_store_member_value(sym, member_type, base_offset + member_offset, val)

        return len(values)

    def _gen_zero_init_region(self, sym: 'Symbol', offset: int, size: int) -> None:
        """Zero-initialize a region of memory for a local variable."""
        if size <= 0:
            return
        if sym.uses_shared_storage:
            base = sym.shared_offset + offset
            for i in range(size):
                self.ctx.emit_instr("xor", "A")
                self.ctx.emit_instr("ld", f"(??AUTO+{base + i}),A")
        else:
            # Use LDIR for efficiency if size > 4
            if size > 4:
                frame_off = sym.offset + offset
                # Zero first byte
                self.ctx.emit_instr("xor", "A")
                self._gen_lea_ix_offset(frame_off)  # HL = dest address
                self.ctx.emit_instr("ld", "(HL),A")
                if size > 1:
                    # Copy first byte to rest using LDIR
                    self.ctx.emit_instr("ld", "D,H")
                    self.ctx.emit_instr("ld", "E,L")
                    self.ctx.emit_instr("inc", "DE")
                    self.ctx.emit_instr("ld", f"BC,{size - 1}")
                    self.ctx.emit_instr("ldir")
            else:
                frame_off = sym.offset + offset
                self.ctx.emit_instr("xor", "A")
                for i in range(size):
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + i)}),A")

    def _gen_lea_ix_offset(self, offset: int) -> None:
        """Load effective address IX+offset into HL."""
        self.ctx.emit_instr("push", "IX")
        self.ctx.emit_instr("pop", "HL")
        if offset != 0:
            self.ctx.emit_instr("ld", f"DE,{offset}")
            self.ctx.emit_instr("add", "HL,DE")

    def _gen_struct_copy(self, dest_sym: 'Symbol', dest_offset: int,
                         src_sym: 'Symbol', src_offset: int, size: int) -> None:
        """Copy bytes from one struct location to another."""
        # Copy byte by byte
        for i in range(size):
            # Load source byte
            if src_sym.uses_shared_storage:
                src_addr = src_sym.shared_offset + src_offset + i
                self.ctx.emit_instr("ld", f"A,(??AUTO+{src_addr})")
            elif src_sym.is_global:
                self.ctx.emit_instr("ld", f"A,(_{src_sym.name}+{src_offset + i})")
            else:
                frame_off = src_sym.offset + src_offset + i
                self.ctx.emit_instr("ld", f"A,({ix_off(frame_off)})")

            # Store to destination byte
            if dest_sym.uses_shared_storage:
                dest_addr = dest_sym.shared_offset + dest_offset + i
                self.ctx.emit_instr("ld", f"(??AUTO+{dest_addr}),A")
            elif dest_sym.is_global:
                self.ctx.emit_instr("ld", f"(_{dest_sym.name}+{dest_offset + i}),A")
            else:
                frame_off = dest_sym.offset + dest_offset + i
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),A")

    def _gen_struct_copy_from_expr_to_member(self, dest_sym: 'Symbol', dest_offset: int,
                                              expr: ast.Expression, size: int) -> None:
        """Copy struct from expression to a member offset within dest_sym."""
        # Get source address into HL
        if isinstance(expr, ast.UnaryOp) and expr.op == "*":
            # Dereference: *ptr - load pointer value into HL
            self.gen_expr(expr.operand)
        elif isinstance(expr, ast.Identifier):
            # Simple identifier - get its address
            src_sym = self.ctx.lookup(expr.name)
            if src_sym:
                if src_sym.is_global:
                    self.ctx.emit_instr("ld", f"HL,{src_sym.label()}")
                elif src_sym.uses_shared_storage:
                    self.ctx.emit_instr("ld", f"HL,??AUTO+{src_sym.shared_offset}")
                else:
                    self.ctx.emit_instr("push", "IX")
                    self.ctx.emit_instr("pop", "HL")
                    if src_sym.offset != 0:
                        self.ctx.emit_instr("ld", f"DE,{src_sym.offset}")
                        self.ctx.emit_instr("add", "HL,DE")
        else:
            # Unsupported expression type - fall through with gen_expr
            self.gen_expr(expr)

        # HL has source address, copy bytes to destination using LDIR
        self.ctx.emit_instr("push", "HL")  # Save source

        # Get destination address
        if dest_sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"DE,??AUTO+{dest_sym.shared_offset + dest_offset}")
        elif dest_sym.is_global:
            self.ctx.emit_instr("ld", f"DE,{dest_sym.label()}+{dest_offset}")
        else:
            self.ctx.emit_instr("push", "IX")
            self.ctx.emit_instr("pop", "DE")
            frame_off = dest_sym.offset + dest_offset
            if frame_off != 0:
                self.ctx.emit_instr("ld", f"HL,{frame_off}")
                self.ctx.emit_instr("add", "HL,DE")
                self.ctx.emit_instr("ex", "DE,HL")

        self.ctx.emit_instr("pop", "HL")  # Restore source
        self.ctx.emit_instr("ld", f"BC,{size}")
        self.ctx.emit_instr("ldir")  # Copy BC bytes from HL to DE

    def _gen_struct_copy_from_addr_expr(self, dest_sym: 'Symbol', dest_offset: int,
                                          expr: ast.Expression, size: int) -> None:
        """Copy struct from an addressable expression (member access, etc.) to dest."""
        # Get source address into HL using _gen_address
        self._gen_address(expr)

        # HL has source address, copy bytes to destination using LDIR
        self.ctx.emit_instr("push", "HL")  # Save source

        # Get destination address
        if dest_sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"DE,??AUTO+{dest_sym.shared_offset + dest_offset}")
        elif dest_sym.is_global:
            self.ctx.emit_instr("ld", f"DE,{dest_sym.label()}+{dest_offset}")
        else:
            self.ctx.emit_instr("push", "IX")
            self.ctx.emit_instr("pop", "DE")
            frame_off = dest_sym.offset + dest_offset
            if frame_off != 0:
                self.ctx.emit_instr("ld", f"HL,{frame_off}")
                self.ctx.emit_instr("add", "HL,DE")
                self.ctx.emit_instr("ex", "DE,HL")

        self.ctx.emit_instr("pop", "HL")  # Restore source
        self.ctx.emit_instr("ld", f"BC,{size}")
        self.ctx.emit_instr("ldir")  # Copy BC bytes from HL to DE

    def _gen_flat_array_init(self, sym: 'Symbol', array_type: ast.ArrayType,
                             values: list, start_index: int, base_offset: int) -> int:
        """Initialize an array from flat values. Returns number of values consumed."""
        elem_type = array_type.base_type
        elem_size = self._type_size(elem_type)
        is_long = self._is_long_type(elem_type)
        is_float = self._is_float_type(elem_type)
        is_32bit = is_long or is_float

        # Get array size
        array_size = 1
        if array_type.size:
            if isinstance(array_type.size, ast.IntLiteral):
                array_size = array_type.size.value
            else:
                sz = self._eval_const_expr(array_type.size)
                if sz is not None:
                    array_size = sz

        consumed = 0
        for i in range(array_size):
            idx = start_index + consumed
            if idx >= len(values):
                # No more values - zero-initialize
                self._gen_zero_init_member(sym, elem_type, base_offset + i * elem_size)
                continue

            val = values[idx]
            if isinstance(val, ast.DesignatedInit):
                val = val.value

            offset = base_offset + i * elem_size

            # Handle nested types
            if isinstance(elem_type, ast.StructType) and not isinstance(val, ast.InitializerList):
                # Check if value is an identifier referring to a struct variable
                if isinstance(val, ast.Identifier):
                    src_sym = self.ctx.lookup(val.name)
                    if src_sym and isinstance(src_sym.sym_type, ast.StructType):
                        # Copy the struct
                        self._gen_struct_copy(sym, offset, src_sym, 0, elem_size)
                        consumed += 1
                        continue
                nested_consumed = self._gen_struct_init_values(sym, elem_type, values[idx:], offset)
                consumed += nested_consumed
            elif isinstance(elem_type, ast.ArrayType) and not isinstance(val, ast.InitializerList):
                nested_consumed = self._gen_flat_array_init(sym, elem_type, values, idx, offset)
                consumed += nested_consumed
            else:
                # Scalar element or nested InitializerList
                consumed += 1
                # Convert int literal to float if target is float
                if is_float and isinstance(val, ast.IntLiteral):
                    val = ast.FloatLiteral(value=float(val.value))
                self.gen_expr(val, force_long=is_32bit)
                if is_long and not is_float and not self._is_long_expr(val) and not self._is_float_expr(val):
                    is_signed = not self._is_unsigned_expr(val)
                    self._extend_hl_to_dehl(is_signed)
                if is_float and not self._is_float_expr(val):
                    if not self._is_long_expr(val):
                        is_signed = not self._is_unsigned_expr(val)
                        self._extend_hl_to_dehl(is_signed)
                    self._call_runtime("__itof")

                # Store
                if sym.uses_shared_storage:
                    base = sym.shared_offset + offset
                    if is_32bit:
                        self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
                        self.ctx.emit_instr("ld", f"(??AUTO+{base + 2}),DE")
                    elif elem_size == 1:
                        self.ctx.emit_instr("ld", "A,L")
                        self.ctx.emit_instr("ld", f"(??AUTO+{base}),A")
                    else:
                        self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
                else:
                    frame_off = sym.offset + offset
                    if is_32bit:
                        self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                        self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")
                        self.ctx.emit_instr("ld", f"({ix_off(frame_off + 2)}),E")
                        self.ctx.emit_instr("ld", f"({ix_off(frame_off + 3)}),D")
                    elif elem_size == 1:
                        self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                    else:
                        self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                        self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")

        return consumed

    def _is_char_array(self, array_type: ast.ArrayType) -> bool:
        """Check if array is a char array (can be initialized with string literal)."""
        base = array_type.base_type
        if isinstance(base, ast.BasicType) and base.name == "char":
            return True
        return False

    def _gen_string_init(self, sym: 'Symbol', array_type: ast.ArrayType, string_lit: ast.StringLiteral, base_offset: int) -> None:
        """Initialize a char array from a string literal."""
        string_val = string_lit.value + '\0'  # Include null terminator
        array_size = 1
        if array_type.size:
            if isinstance(array_type.size, ast.IntLiteral):
                array_size = array_type.size.value
            else:
                sz = self._eval_const_expr(array_type.size)
                if sz is not None:
                    array_size = sz

        for i, ch in enumerate(string_val):
            if i >= array_size:
                break
            char_val = ord(ch)
            if sym.uses_shared_storage:
                base = sym.shared_offset + base_offset + i
                self.ctx.emit_instr("ld", f"a,{char_val}")
                self.ctx.emit_instr("ld", f"(??AUTO+{base}),A")
            else:
                frame_off = sym.offset + base_offset + i
                self.ctx.emit_instr("ld", f"a,{char_val}")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),A")

        # Zero-fill remaining bytes
        for i in range(len(string_val), array_size):
            if sym.uses_shared_storage:
                base = sym.shared_offset + base_offset + i
                self.ctx.emit_instr("xor", "a")
                self.ctx.emit_instr("ld", f"(??AUTO+{base}),A")
            else:
                frame_off = sym.offset + base_offset + i
                self.ctx.emit_instr("xor", "a")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),A")

    def _gen_zero_init_member(self, sym: 'Symbol', member_type: ast.TypeNode, offset: int) -> None:
        """Zero-initialize a struct member."""
        size = self._type_size(member_type)
        # For now, just store zeros
        self.ctx.emit_instr("ld", "HL,0")
        if sym.uses_shared_storage:
            base = sym.shared_offset + offset
            for i in range(0, size, 2):
                if i + 1 < size:
                    self.ctx.emit_instr("ld", f"(??AUTO+{base + i}),HL")
                else:
                    self.ctx.emit_instr("ld", "A,L")
                    self.ctx.emit_instr("ld", f"(??AUTO+{base + i}),A")
        elif sym.is_global:
            for i in range(0, size, 2):
                if i + 1 < size:
                    self.ctx.emit_instr("ld", f"({sym.label()}+{offset + i}),HL")
                else:
                    self.ctx.emit_instr("ld", "A,L")
                    self.ctx.emit_instr("ld", f"({sym.label()}+{offset + i}),A")
        else:
            frame_off = sym.offset + offset
            for i in range(0, size, 2):
                if i + 1 < size:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + i)}),L")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + i + 1)}),H")
                else:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + i)}),L")

    def _gen_store_member_value(self, sym: 'Symbol', member_type: ast.TypeNode,
                                 offset: int, val: ast.Expression) -> None:
        """Store a value at a struct member's location."""
        is_long = self._is_long_type(member_type)
        is_float = self._is_float_type(member_type)
        is_32bit = is_long or is_float
        member_size = self._type_size(member_type)

        # Handle nested struct initialization
        if isinstance(member_type, ast.StructType) and isinstance(val, ast.InitializerList):
            self._gen_struct_init_values(sym, member_type, val.values, offset)
            return

        # Handle compound literal: (struct S){...}
        if isinstance(member_type, ast.StructType) and isinstance(val, ast.Compound):
            if isinstance(val.init, ast.InitializerList):
                self._gen_struct_init_values(sym, member_type, val.init.values, offset)
            return

        # Handle nested array initialization
        if isinstance(member_type, ast.ArrayType) and isinstance(val, ast.InitializerList):
            self._gen_array_init_values(sym, member_type, val.values, offset)
            return

        # Handle struct copy from another struct variable
        if isinstance(member_type, ast.StructType) and isinstance(val, ast.Identifier):
            src_sym = self.ctx.lookup(val.name)
            if src_sym and isinstance(src_sym.sym_type, ast.StructType):
                self._gen_struct_copy(sym, offset, src_sym, 0, member_size)
                return

        # Handle struct copy from pointer dereference
        if isinstance(member_type, ast.StructType) and isinstance(val, ast.UnaryOp) and val.op == "*":
            self._gen_struct_copy_from_expr_to_member(sym, offset, val, member_size)
            return

        # Handle struct copy from member access (e.g., phdr->daddr)
        if isinstance(member_type, ast.StructType) and isinstance(val, ast.Member):
            self._gen_struct_copy_from_addr_expr(sym, offset, val, member_size)
            return

        # Handle struct copy from cast of addressable expression (e.g., (struct S)w->t.s)
        if isinstance(member_type, ast.StructType) and isinstance(val, ast.Cast):
            inner = val.expr
            if isinstance(inner, (ast.Member, ast.UnaryOp, ast.Identifier)):
                self._gen_struct_copy_from_addr_expr(sym, offset, inner, member_size)
                return

        # Generate the value expression
        self.gen_expr(val, force_long=is_32bit)

        # Extend to 32-bit if needed (for long but not float - float is already 32-bit)
        if is_long and not is_float and not self._is_long_expr(val) and not self._is_float_expr(val):
            is_signed = not self._is_unsigned_expr(val)
            self._extend_hl_to_dehl(is_signed)

        # Store at the appropriate offset
        if sym.uses_shared_storage:
            base = sym.shared_offset + offset
            if is_32bit:
                self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
                self.ctx.emit_instr("ld", f"(??AUTO+{base + 2}),DE")
            elif member_size == 1:
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("ld", f"(??AUTO+{base}),A")
            else:
                self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
        else:
            frame_off = sym.offset + offset
            if is_32bit:
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off + 2)}),E")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off + 3)}),D")
            elif member_size == 1:
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
            else:
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")

    def _gen_bitfield_init_store(self, sym: 'Symbol', offset: int,
                                 bf: BitfieldInfo, val: ast.Expression) -> None:
        """Store a bitfield value during struct initialization.
        Uses read-modify-write at the storage unit at sym+offset."""
        mask = (1 << bf.bit_width) - 1

        if bf.storage_size == 1:
            # Generate value
            self.gen_expr(val)
            # Mask and shift
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("and", str(mask))
            for _ in range(bf.bit_offset):
                self.ctx.emit_instr("add", "A,A")
            self.ctx.emit_instr("ld", "C,A")
            # Read current byte, clear bits, OR, store
            clear_mask = (~(mask << bf.bit_offset)) & 0xFF
            if sym.uses_shared_storage:
                base = sym.shared_offset + offset
                self.ctx.emit_instr("ld", f"A,(??AUTO+{base})")
                self.ctx.emit_instr("and", str(clear_mask))
                self.ctx.emit_instr("or", "C")
                self.ctx.emit_instr("ld", f"(??AUTO+{base}),A")
            else:
                frame_off = sym.offset + offset
                self.ctx.emit_instr("ld", f"A,({ix_off(frame_off)})")
                self.ctx.emit_instr("and", str(clear_mask))
                self.ctx.emit_instr("or", "C")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),A")

        elif bf.storage_size == 2:
            # Generate value
            self.gen_expr(val)
            # Mask value
            low_mask = mask & 0xFF
            high_mask = (mask >> 8) & 0xFF
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("and", str(low_mask))
            self.ctx.emit_instr("ld", "L,A")
            if high_mask == 0:
                self.ctx.emit_instr("ld", "H,0")
            else:
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("and", str(high_mask))
                self.ctx.emit_instr("ld", "H,A")
            # Shift left
            for _ in range(bf.bit_offset):
                self.ctx.emit_instr("add", "HL,HL")
            # Save shifted value in BC
            self.ctx.emit_instr("ld", "B,H")
            self.ctx.emit_instr("ld", "C,L")
            # Read-modify-write
            clear_mask = (~(mask << bf.bit_offset)) & 0xFFFF
            if sym.uses_shared_storage:
                base = sym.shared_offset + offset
                self.ctx.emit_instr("ld", f"HL,(??AUTO+{base})")
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("and", str(clear_mask & 0xFF))
                self.ctx.emit_instr("or", "C")
                self.ctx.emit_instr("ld", "L,A")
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("and", str((clear_mask >> 8) & 0xFF))
                self.ctx.emit_instr("or", "B")
                self.ctx.emit_instr("ld", "H,A")
                self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
            else:
                frame_off = sym.offset + offset
                self.ctx.emit_instr("ld", f"L,({ix_off(frame_off)})")
                self.ctx.emit_instr("ld", f"H,({ix_off(frame_off + 1)})")
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("and", str(clear_mask & 0xFF))
                self.ctx.emit_instr("or", "C")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),A")
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("and", str((clear_mask >> 8) & 0xFF))
                self.ctx.emit_instr("or", "B")
                self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),A")
        else:
            # 4-byte: fall back to full-width store (sub-word 32-bit bitfield init is rare)
            self._gen_store_member_value(sym, ast.BasicType(name="long"), offset, val)

    def _gen_array_init_values(self, sym: 'Symbol', array_type: ast.ArrayType,
                                values: list, base_offset: int) -> None:
        """Generate code to store array initializer values at an offset."""
        elem_type = array_type.base_type
        elem_size = self._type_size(elem_type)
        is_long = self._is_long_type(elem_type)
        is_float = self._is_float_type(elem_type)
        is_32bit = is_long or is_float

        # Determine the array's declared element count for tail zero-fill.
        declared_n = None
        if isinstance(array_type.size, ast.IntLiteral):
            declared_n = array_type.size.value
        elif array_type.size is not None:
            sz = self._eval_const_expr(array_type.size)
            if sz is not None:
                declared_n = sz

        # Check for designated initializers with index/range designators
        has_index_designators = any(
            isinstance(v, ast.DesignatedInit) and v.designators and
            not isinstance(v.designators[0], str)
            for v in values
        )
        if has_index_designators:
            # Zero-initialize the entire array first (undesignated elements must be zero per C99)
            array_size = 1
            if array_type.size:
                if isinstance(array_type.size, ast.IntLiteral):
                    array_size = array_type.size.value
                else:
                    sz = self._eval_const_expr(array_type.size)
                    if sz is not None:
                        array_size = sz
            total_size = array_size * elem_size
            self._gen_zero_init_region(sym, base_offset, total_size)
            self._gen_designated_array_init_local(sym, array_type, values, base_offset)
            return

        # Detect flat init: scalar values for aggregate element types
        if isinstance(elem_type, (ast.StructType, ast.ArrayType)) and values:
            has_flat = any(not isinstance(v, (ast.InitializerList, ast.DesignatedInit))
                          for v in values)
            if has_flat:
                self._gen_flat_array_init(sym, array_type, values, 0, base_offset)
                return

        for i, val in enumerate(values):
            if isinstance(val, ast.DesignatedInit):
                val = val.value

            offset = base_offset + i * elem_size

            # Handle nested initialization
            if isinstance(elem_type, ast.StructType) and isinstance(val, ast.InitializerList):
                self._gen_struct_init_values(sym, elem_type, val.values, offset)
                continue
            if isinstance(elem_type, ast.ArrayType) and isinstance(val, ast.InitializerList):
                self._gen_array_init_values(sym, elem_type, val.values, offset)
                continue

            # Handle struct copy from identifier
            if isinstance(elem_type, ast.StructType) and isinstance(val, ast.Identifier):
                src_sym = self.ctx.lookup(val.name)
                if src_sym and isinstance(src_sym.sym_type, ast.StructType):
                    self._gen_struct_copy(sym, offset, src_sym, 0, elem_size)
                    continue

            # Convert int literal to float if target is float
            if is_float and isinstance(val, ast.IntLiteral):
                val = ast.FloatLiteral(value=float(val.value))

            # Generate value
            self.gen_expr(val, force_long=is_32bit)
            if is_long and not is_float and not self._is_long_expr(val) and not self._is_float_expr(val):
                is_signed = not self._is_unsigned_expr(val)
                self._extend_hl_to_dehl(is_signed)
            # Convert int to float if needed
            if is_float and not self._is_float_expr(val):
                if not self._is_long_expr(val):
                    is_signed = not self._is_unsigned_expr(val)
                    self._extend_hl_to_dehl(is_signed)
                self._call_runtime("__itof")

            # Store
            if sym.uses_shared_storage:
                base = sym.shared_offset + offset
                if is_32bit:
                    self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
                    self.ctx.emit_instr("ld", f"(??AUTO+{base + 2}),DE")
                elif elem_size == 1:
                    self.ctx.emit_instr("ld", "A,L")
                    self.ctx.emit_instr("ld", f"(??AUTO+{base}),A")
                else:
                    self.ctx.emit_instr("ld", f"(??AUTO+{base}),HL")
            else:
                frame_off = sym.offset + offset
                if is_32bit:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 2)}),E")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 3)}),D")
                elif elem_size == 1:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                else:
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("ld", f"({ix_off(frame_off + 1)}),H")

        # C99 6.7.8/21: zero-fill any unspecified trailing elements.
        if declared_n is not None and declared_n > len(values):
            tail_off = base_offset + len(values) * elem_size
            tail_bytes = (declared_n - len(values)) * elem_size
            self._gen_zero_init_region(sym, tail_off, tail_bytes)

    def _gen_designated_array_init_local(self, sym: 'Symbol', array_type: ast.ArrayType,
                                          values: list, base_offset: int) -> None:
        """Generate code for local array init with designated [index] or [start...end] designators."""
        elem_type = array_type.base_type
        elem_size = self._type_size(elem_type)
        next_index = 0

        for val in values:
            if isinstance(val, ast.DesignatedInit):
                actual_val = val.value
                for desig in val.designators:
                    if isinstance(desig, ast.RangeDesignator):
                        start = self._eval_const_expr(desig.start)
                        end = self._eval_const_expr(desig.end)
                        if start is not None and end is not None:
                            for idx in range(start, end + 1):
                                offset = base_offset + idx * elem_size
                                self._gen_store_member_value(sym, elem_type, offset, actual_val)
                            next_index = end + 1
                    else:
                        idx = self._eval_const_expr(desig)
                        if idx is not None:
                            offset = base_offset + idx * elem_size
                            self._gen_store_member_value(sym, elem_type, offset, actual_val)
                            next_index = idx + 1
            else:
                offset = base_offset + next_index * elem_size
                self._gen_store_member_value(sym, elem_type, offset, val)
                next_index += 1

    def gen_statement(self, stmt: ast.Statement) -> None:
        """Generate code for a statement."""
        if isinstance(stmt, ast.ReturnStmt):
            self.gen_return(stmt)
        elif isinstance(stmt, ast.ExpressionStmt):
            if stmt.expr:
                self.gen_expr(stmt.expr)
        elif isinstance(stmt, ast.CompoundStmt):
            self.gen_compound_stmt(stmt)
        elif isinstance(stmt, ast.IfStmt):
            self.gen_if(stmt)
        elif isinstance(stmt, ast.WhileStmt):
            self.gen_while(stmt)
        elif isinstance(stmt, ast.DoWhileStmt):
            self.gen_do_while(stmt)
        elif isinstance(stmt, ast.ForStmt):
            self.gen_for(stmt)
        elif isinstance(stmt, ast.BreakStmt):
            self.gen_break()
        elif isinstance(stmt, ast.ContinueStmt):
            self.gen_continue()
        elif isinstance(stmt, ast.SwitchStmt):
            self.gen_switch(stmt)
        elif isinstance(stmt, ast.CaseStmt):
            self.gen_case(stmt)
        elif isinstance(stmt, ast.LabelStmt):
            self.gen_label(stmt)
        elif isinstance(stmt, ast.GotoStmt):
            self.gen_goto(stmt)

    def gen_return(self, stmt: ast.ReturnStmt) -> None:
        """Generate code for return statement."""
        if stmt.value:
            ret_type = self.ctx.current_return_type
            # Check for struct return > 2 bytes
            if isinstance(ret_type, ast.StructType) and self._type_size(ret_type) > 2:
                struct_size = self._type_size(ret_type)
                self.ctx.runtime_used.add("__sret_buf")
                # Get source address of the struct value
                self._gen_address(stmt.value)
                # Copy struct to __sret_buf: HL = source, DE = __sret_buf, BC = size
                self.ctx.emit_instr("ld", "DE,__sret_buf")
                self.ctx.emit_instr("ld", f"BC,{struct_size}")
                self.ctx.emit_instr("ldir")
                # Return address of __sret_buf
                self.ctx.emit_instr("ld", "HL,__sret_buf")
            elif self._is_long_long_type(ret_type):
                # 64-bit return: generate value into __acc64
                self._gen_64bit_operand(stmt.value, to_tmp=False)
                # Caller retrieves from __acc64
            else:
                return_is_long = self._is_long_type(ret_type)
                return_is_float = self._is_float_type(ret_type)
                expr_is_float = self._is_float_expr(stmt.value)
                # Generate expression, forcing long if return type is long
                self.gen_expr(stmt.value, force_long=return_is_long or return_is_float)
                # Convert float expression to int/long return type
                if expr_is_float and not return_is_float:
                    self._call_runtime("__ftoi")  # returns 32-bit in DEHL
                # Convert int expression to float return type
                elif return_is_float and not expr_is_float:
                    if not self._is_long_expr(stmt.value):
                        self._extend_hl_to_dehl(self._is_signed_type(self._get_expr_type(stmt.value)))
                    self._call_runtime("__itof")
                # Extend to 32-bit if return type is long but expression is not
                elif return_is_long and not self._is_long_expr(stmt.value) and not expr_is_float:
                    is_signed = self._is_signed_type(self._get_expr_type(stmt.value))
                    self._extend_hl_to_dehl(is_signed)
                else:
                    # Truncate to the declared return type's width.  Without
                    # this, `uint8_t f(uint8_t s) { return s<<2; }` returns
                    # the full 16-bit arithmetic result (0x290 for s=0xa4)
                    # and the caller's comparison against an explicit
                    # (uint8_t) cast sees the un-truncated upper bits.
                    ret_size = self._type_size(ret_type) if ret_type else 2
                    if ret_size == 1:
                        # Skip when the value is a compile-time IntLiteral
                        # that already fits in the target type — gen_expr
                        # emitted the right HL.  Skipping also dodges a
                        # peephole rule that would otherwise eat
                        # `LD HL,N; LD A,L` and leave H undefined.
                        is_signed = self._is_signed_type(ret_type)
                        skip = False
                        if isinstance(stmt.value, ast.IntLiteral):
                            v = stmt.value.value
                            if is_signed:
                                if -128 <= v <= 127:
                                    skip = True
                            else:
                                if 0 <= v <= 255:
                                    skip = True
                        if not skip:
                            if is_signed:
                                # Sign-extend low byte into high byte
                                self.ctx.emit_instr("ld", "A,L")
                                self.ctx.emit_instr("rla")
                                self.ctx.emit_instr("sbc", "A,A")
                                self.ctx.emit_instr("ld", "H,A")
                            else:
                                self.ctx.emit_instr("ld", "H,0")
        # Jump to function epilogue
        self.ctx.emit_instr("jp", f"@{self.ctx.current_function}_ret")

    def gen_if(self, stmt: ast.IfStmt) -> None:
        """Generate code for if statement."""
        else_label = self.ctx.new_label("ELSE")
        end_label = self.ctx.new_label("ENDIF")

        # Evaluate condition - use force_long for float/long conditions
        cond_is_32 = self._is_float_expr(stmt.condition) or self._is_long_expr(stmt.condition)
        self.gen_expr(stmt.condition, force_long=cond_is_32)

        # Test if result is zero
        self._emit_condition_test(stmt.condition)

        if stmt.else_branch:
            self.ctx.emit_instr("jp", f"Z,{else_label}")
            self.gen_statement(stmt.then_branch)
            self.ctx.emit_instr("jp", end_label)
            self.ctx.emit_label(else_label)
            self.gen_statement(stmt.else_branch)
            self.ctx.emit_label(end_label)
        else:
            self.ctx.emit_instr("jp", f"Z,{end_label}")
            self.gen_statement(stmt.then_branch)
            self.ctx.emit_label(end_label)

    def gen_while(self, stmt: ast.WhileStmt) -> None:
        """Generate code for while loop."""
        start_label = self.ctx.new_label("WHILE")
        end_label = self.ctx.new_label("ENDWHILE")

        self.ctx.break_labels.append(end_label)
        self.ctx.continue_labels.append(start_label)

        self.ctx.emit_label(start_label)
        cond_is_32 = self._is_float_expr(stmt.condition) or self._is_long_expr(stmt.condition)
        self.gen_expr(stmt.condition, force_long=cond_is_32)
        self._emit_condition_test(stmt.condition)
        self.ctx.emit_instr("jp", f"Z,{end_label}")

        self.gen_statement(stmt.body)
        self.ctx.emit_instr("jp", start_label)
        self.ctx.emit_label(end_label)

        self.ctx.break_labels.pop()
        self.ctx.continue_labels.pop()

    def gen_do_while(self, stmt: ast.DoWhileStmt) -> None:
        """Generate code for do-while loop."""
        start_label = self.ctx.new_label("DO")
        cond_label = self.ctx.new_label("DOCOND")
        end_label = self.ctx.new_label("ENDDO")

        self.ctx.break_labels.append(end_label)
        self.ctx.continue_labels.append(cond_label)

        self.ctx.emit_label(start_label)
        self.gen_statement(stmt.body)

        self.ctx.emit_label(cond_label)
        cond_is_32 = self._is_float_expr(stmt.condition) or self._is_long_expr(stmt.condition)
        self.gen_expr(stmt.condition, force_long=cond_is_32)
        self._emit_condition_test(stmt.condition)
        self.ctx.emit_instr("jp", f"NZ,{start_label}")
        self.ctx.emit_label(end_label)

        self.ctx.break_labels.pop()
        self.ctx.continue_labels.pop()

    def gen_for(self, stmt: ast.ForStmt) -> None:
        """Generate code for for loop."""
        start_label = self.ctx.new_label("FOR")
        update_label = self.ctx.new_label("FORUPD")
        end_label = self.ctx.new_label("ENDFOR")

        self.ctx.break_labels.append(end_label)
        self.ctx.continue_labels.append(update_label)

        # Init
        if stmt.init:
            if isinstance(stmt.init, ast.Declaration):
                self.gen_local_decl(stmt.init)
            else:
                self.gen_expr(stmt.init)

        self.ctx.emit_label(start_label)

        # Condition
        if stmt.condition:
            cond_is_32 = self._is_float_expr(stmt.condition) or self._is_long_expr(stmt.condition)
            self.gen_expr(stmt.condition, force_long=cond_is_32)
            self._emit_condition_test(stmt.condition)
            self.ctx.emit_instr("jp", f"Z,{end_label}")

        # Body
        self.gen_statement(stmt.body)

        # Update
        self.ctx.emit_label(update_label)
        if stmt.update:
            self.gen_expr(stmt.update)

        self.ctx.emit_instr("jp", start_label)
        self.ctx.emit_label(end_label)

        self.ctx.break_labels.pop()
        self.ctx.continue_labels.pop()

    def gen_break(self) -> None:
        """Generate code for break statement."""
        if self.ctx.break_labels:
            self.ctx.emit_instr("jp", self.ctx.break_labels[-1])

    def gen_continue(self) -> None:
        """Generate code for continue statement."""
        if self.ctx.continue_labels:
            self.ctx.emit_instr("jp", self.ctx.continue_labels[-1])

    def gen_switch(self, stmt: ast.SwitchStmt) -> None:
        """Generate code for switch statement."""
        end_label = self.ctx.new_label("ENDSWITCH")

        # Collect case values and create labels
        cases: list[tuple[int, str]] = []  # (value, label)
        default_label: str | None = None

        def collect_cases(s: ast.Statement) -> None:
            nonlocal default_label
            if isinstance(s, ast.SwitchStmt):
                # Don't recurse into nested switch statements
                return
            if isinstance(s, ast.CaseStmt):
                if s.value is None:
                    # default case
                    default_label = self.ctx.new_label("DEFAULT")
                else:
                    # Regular case - evaluate constant expression
                    const_val = self._eval_const_expr(s.value)
                    if const_val is not None:
                        label = self.ctx.new_label("CASE")
                        cases.append((const_val, label))
                # Recurse into the statement following the case label
                # This handles consecutive case labels like "case 0: case 1:"
                if s.stmt:
                    collect_cases(s.stmt)
            elif isinstance(s, ast.CompoundStmt):
                for item in s.items:
                    if isinstance(item, ast.Statement):
                        collect_cases(item)
            # Recurse through other control structures to find case statements
            elif isinstance(s, ast.ForStmt):
                if s.body:
                    collect_cases(s.body)
            elif isinstance(s, ast.WhileStmt):
                if s.body:
                    collect_cases(s.body)
            elif isinstance(s, ast.DoWhileStmt):
                if s.body:
                    collect_cases(s.body)
            elif isinstance(s, ast.IfStmt):
                if s.then_branch:
                    collect_cases(s.then_branch)
                if s.else_branch:
                    collect_cases(s.else_branch)

        collect_cases(stmt.body)

        # Evaluate switch expression
        self.gen_expr(stmt.expr)

        # Decide: jump table vs comparison chain
        fall_label = default_label if default_label else end_label
        if self._should_use_jump_table(cases):
            self._gen_switch_jump_table(cases, fall_label)
        else:
            self._gen_switch_compare_chain(cases, fall_label)

        # Save previous switch context (for nested switches)
        saved_cases = list(getattr(self, '_switch_cases', []))
        saved_default = getattr(self, '_switch_default', None)

        # Set up case label map for gen_case
        self._switch_cases = cases
        self._switch_default = default_label

        # Push break label
        self.ctx.break_labels.append(end_label)

        # Generate body with case labels
        self.gen_statement(stmt.body)

        # Pop break label
        self.ctx.break_labels.pop()

        # End label
        self.ctx.emit_label(end_label)

        # Restore previous switch context
        self._switch_cases = saved_cases
        self._switch_default = saved_default

    def _should_use_jump_table(self, cases: list[tuple[int, str]]) -> bool:
        """Decide whether to use a jump table for this switch."""
        if len(cases) < 4:
            return False
        values = [v for v, _ in cases]
        min_val = min(values)
        max_val = max(values)
        span = max_val - min_val + 1
        # Use jump table if density >= 50% and span fits in reasonable size
        if span > 256:
            return False
        density = len(cases) / span
        return density >= 0.5

    def _gen_switch_compare_chain(self, cases: list[tuple[int, str]], fall_label: str) -> None:
        """Generate sequential compare-and-branch for sparse switch."""
        for value, label in cases:
            self.ctx.emit_instr("ld", f"DE,{value}")
            self.ctx.emit_instr("or", "A")
            self.ctx.emit_instr("sbc", "HL,DE")
            self.ctx.emit_instr("add", "HL,DE")
            self.ctx.emit_instr("jp", f"Z,{label}")
        self.ctx.emit_instr("jp", fall_label)

    def _gen_switch_jump_table(self, cases: list[tuple[int, str]], fall_label: str) -> None:
        """Generate a jump table dispatch for dense switch."""
        values = [v for v, _ in cases]
        min_val = min(values)
        max_val = max(values)
        span = max_val - min_val + 1
        table_label = self.ctx.new_label("SWTAB")

        # HL = switch value; subtract min to get table index
        if min_val != 0:
            self.ctx.emit_instr("ld", f"DE,{min_val}")
            self.ctx.emit_instr("or", "A")
            self.ctx.emit_instr("sbc", "HL,DE")
        # Range check: unsigned compare with span
        # If HL >= span, out of range -> default/end
        self.ctx.emit_instr("ld", f"DE,{span}")
        self.ctx.emit_instr("or", "A")
        self.ctx.emit_instr("sbc", "HL,DE")
        self.ctx.emit_instr("jp", f"NC,{fall_label}")
        self.ctx.emit_instr("add", "HL,DE")  # Restore index

        # Index into table: HL = HL * 2, then add table base
        self.ctx.emit_instr("add", "HL,HL")
        self.ctx.emit_instr("ld", f"DE,{table_label}")
        self.ctx.emit_instr("add", "HL,DE")
        # Load target address from table and jump
        self.ctx.emit_instr("ld", "E,(HL)")
        self.ctx.emit_instr("inc", "HL")
        self.ctx.emit_instr("ld", "D,(HL)")
        self.ctx.emit_instr("ex", "DE,HL")
        self.ctx.emit_instr("jp", "(HL)")

        # Emit table in code segment
        case_map = {v: lbl for v, lbl in cases}
        self.ctx.emit_label(table_label)
        for i in range(span):
            target = case_map.get(min_val + i, fall_label)
            self.ctx.emit_instr("dw", target)

    def gen_case(self, stmt: ast.CaseStmt) -> None:
        """Generate code for case label."""
        if stmt.value is None:
            # default case
            if self._switch_default:
                self.ctx.emit_label(self._switch_default)
        else:
            # Find matching case label using constant expression evaluation
            const_val = self._eval_const_expr(stmt.value)
            if const_val is not None:
                for value, label in self._switch_cases:
                    if value == const_val:
                        self.ctx.emit_label(label)
                        break

        # Generate code for the statement following the case label
        if stmt.stmt:
            self.gen_statement(stmt.stmt)

    def gen_label(self, stmt: ast.LabelStmt) -> None:
        """Generate code for a labeled statement."""
        # User labels are prefixed with @L_{funcname}_ to be unique per function
        func = self.ctx.current_function or "global"
        self.ctx.emit_label(f"@L_{func}_{stmt.label}")
        self.gen_statement(stmt.stmt)

    def gen_goto(self, stmt: ast.GotoStmt) -> None:
        """Generate code for goto statement."""
        func = self.ctx.current_function or "global"
        self.ctx.emit_instr("jp", f"@L_{func}_{stmt.label}")

    def gen_expr(self, expr: ast.Expression, force_long: bool = False) -> None:
        """Generate code for an expression. Result in HL (16-bit) or DEHL (32-bit)."""
        if isinstance(expr, ast.IntLiteral):
            if self._is_long_expr(expr) or force_long:
                # 32-bit literal: load into DEHL (DE=high, HL=low)
                val = expr.value & 0xFFFFFFFF
                low = val & 0xFFFF
                high = (val >> 16) & 0xFFFF
                self.ctx.emit_instr("ld", f"HL,{low}")
                self.ctx.emit_instr("ld", f"DE,{high}")
            else:
                self.ctx.emit_instr("ld", f"HL,{expr.value}")

        elif isinstance(expr, ast.FloatLiteral):
            # Convert float to IEEE 754 single precision and load as 32-bit
            ieee_val = float_to_ieee754(expr.value)
            low = ieee_val & 0xFFFF
            high = (ieee_val >> 16) & 0xFFFF
            self.ctx.emit_instr("ld", f"HL,{low}")
            self.ctx.emit_instr("ld", f"DE,{high}")

        elif isinstance(expr, ast.CharLiteral):
            # Character constants have type int (C 6.4.4.4)
            val = expr.value
            if val >= 0x80:
                val = (val - 0x100) & 0xFFFF  # Sign extend signed char to 16-bit int
            self.ctx.emit_instr("ld", f"HL,{val}")

        elif isinstance(expr, ast.StringLiteral):
            label = self.ctx.add_string(expr.value, is_wide=expr.is_wide)
            self.ctx.emit_instr("ld", f"HL,{label}")

        elif isinstance(expr, ast.BoolLiteral):
            val = 1 if expr.value else 0
            self.ctx.emit_instr("ld", f"HL,{val}")

        elif isinstance(expr, ast.NullptrLiteral):
            self.ctx.emit_instr("ld", "HL,0")

        elif isinstance(expr, ast.Identifier):
            self.gen_identifier(expr, force_long)

        elif isinstance(expr, ast.BinaryOp):
            self.gen_binary_op(expr)

        elif isinstance(expr, ast.UnaryOp):
            self.gen_unary_op(expr)

        elif isinstance(expr, ast.Call):
            self.gen_call(expr)

        elif isinstance(expr, ast.TernaryOp):
            self.gen_ternary(expr)

        elif isinstance(expr, ast.Cast):
            # Generate cast expression with proper type conversion
            self.gen_cast(expr, force_long)

        elif isinstance(expr, ast.Index):
            self.gen_index(expr)

        elif isinstance(expr, ast.Member):
            self.gen_member(expr)

        elif isinstance(expr, ast.SizeofType):
            size = self._type_size(expr.target_type)
            self.ctx.emit_instr("ld", f"HL,{size}")

        elif isinstance(expr, ast.SizeofExpr):
            # sizeof(string_literal) returns the array size including null terminator
            if isinstance(expr.expr, ast.StringLiteral):
                size = len(expr.expr.value) + 1  # +1 for null terminator
                self.ctx.emit_instr("ld", f"HL,{size}")
            else:
                # Infer type of expression and compute its size
                expr_type = self._get_expr_type(expr.expr)
                if expr_type:
                    size = self._type_size(expr_type)
                else:
                    size = 2  # Default to int if type cannot be inferred
                self.ctx.emit_instr("ld", f"HL,{size}")

        elif isinstance(expr, ast.Compound):
            # Compound literal: (type){initializer}
            target_type = expr.target_type
            if isinstance(target_type, (ast.StructType, ast.ArrayType)):
                # Struct/array compound literal: materialize in memory, return address
                label = self._materialize_compound_literal(expr)
                self.ctx.emit_instr("ld", f"HL,{label}")
            elif isinstance(expr.init, ast.InitializerList) and len(expr.init.values) >= 1:
                # Scalar compound literal: evaluate the value
                val = expr.init.values[0]
                if isinstance(val, ast.DesignatedInit):
                    val = val.value
                self.gen_expr(val, force_long)
            else:
                self.ctx.emit_instr("ld", "HL,0")

        elif isinstance(expr, ast.StmtExpr):
            # Statement expression: ({ ... }) - value is last expression
            self.gen_stmt_expr(expr)

        elif isinstance(expr, ast.GenericSelection):
            # _Generic selection: evaluate matching expression
            self.gen_generic_selection(expr, force_long)

    def gen_stmt_expr(self, expr: ast.StmtExpr) -> None:
        """Generate code for statement expression ({ ... })."""
        # Statement expressions are like compound statements - use gen_compound_stmt
        # for the body, and extract the value of the last expression
        items = expr.body.items

        # Generate all but last item normally
        for item in items[:-1]:
            if isinstance(item, ast.Declaration):
                self.gen_local_decl(item)
            else:
                self.gen_statement(item)

        # Last item should be an expression - generate it for its value
        if items:
            last = items[-1]
            if isinstance(last, ast.ExpressionStmt) and last.expr:
                self.gen_expr(last.expr)
            elif isinstance(last, ast.Declaration):
                # If last is a declaration, gen it and return 0
                self.gen_local_decl(last)
                self.ctx.emit_instr("ld", "HL,0")
            else:
                # Other statement type - execute and return 0
                self.gen_statement(last)
                self.ctx.emit_instr("ld", "HL,0")
        else:
            self.ctx.emit_instr("ld", "HL,0")

    def gen_generic_selection(self, expr: ast.GenericSelection, force_long: bool = False) -> None:
        """Generate code for _Generic selection expression."""
        # Get the type of the controlling expression
        ctrl_type = self._get_expr_type(expr.controlling_expr)

        # Find the matching association
        default_expr = None
        matched_expr = None

        for type_node, value_expr in expr.associations:
            if type_node is None:
                default_expr = value_expr
            elif self._types_compatible(ctrl_type, type_node):
                matched_expr = value_expr
                break

        # Generate code for the matched expression (or default)
        if matched_expr is not None:
            self.gen_expr(matched_expr, force_long)
        elif default_expr is not None:
            self.gen_expr(default_expr, force_long)
        else:
            # No match found - generate 0 (shouldn't happen in valid code)
            self.ctx.emit_instr("ld", "HL,0")

    def gen_identifier(self, expr: ast.Identifier, force_long: bool = False) -> None:
        """Generate code to load an identifier's value into HL (or DEHL for 32-bit)."""
        # Handle __func__ (C99 predefined identifier)
        if expr.name in ('__func__', '__FUNCTION__'):
            func_name = getattr(self.ctx, 'current_function', 'unknown')
            str_expr = ast.StringLiteral(value=func_name, location=expr.location)
            self.gen_expr(str_expr, force_long)
            return

        # Check for enum constant first
        if expr.name in self.ctx.enum_constants:
            val = self.ctx.enum_constants[expr.name]
            self.ctx.emit_instr("ld", f"HL,{val}")
            if force_long:
                # Sign-extend negative enum values to 32-bit
                if val < 0:
                    self.ctx.emit_instr("ld", "DE,65535")
                else:
                    self.ctx.emit_instr("ld", "DE,0")
            return

        sym = self.ctx.lookup(expr.name)
        if sym is None:
            # Assume external function - load its address.  Track it so we
            # emit an EXTRN; otherwise um80 fails the assemble step with
            # "Undefined symbol" (the linker would resolve it, but um80
            # doesn't defer unknowns past the .rel boundary on its own).
            self.ctx.implicit_externs.add(f"_{expr.name}")
            self.ctx.emit_instr("ld", f"HL,_{expr.name}")
            return

        # Check if this is a function (name matches a function we've seen)
        # Functions used as values decay to pointers - load address, not value
        # Only apply to global symbols - local variables can shadow function names
        if isinstance(sym.sym_type, ast.FunctionType) or (sym.is_global and expr.name in self.ctx.function_names):
            self.ctx.emit_instr("ld", f"HL,{sym.label()}")
            return

        # Arrays decay to pointers - return address, not value
        if isinstance(sym.sym_type, ast.ArrayType):
            if sym.is_global:
                self.ctx.emit_instr("ld", f"HL,{sym.label()}")
            elif sym.uses_shared_storage:
                # Load address of array in shared storage
                self.ctx.emit_instr("ld", f"HL,??AUTO+{sym.shared_offset}")
            else:
                # Compute address IX+offset
                self.ctx.emit_instr("ld", f"HL,{sym.offset}")
                self.ctx.emit_instr("push", "IX")
                self.ctx.emit_instr("pop", "DE")
                self.ctx.emit_instr("add", "HL,DE")
            return

        type_is_long_long = self._is_long_long_type(sym.sym_type)
        type_is_long = self._is_long_type(sym.sym_type)
        type_is_float = self._is_float_type(sym.sym_type)
        type_size = self._type_size(sym.sym_type)

        if type_is_long_long:
            # 64-bit variable: load via __load64, return low 32 bits in DEHL
            if sym.is_global:
                self.ctx.emit_instr("ld", f"HL,{sym.label()}")
            elif sym.uses_shared_storage:
                self.ctx.emit_instr("ld", f"HL,??AUTO+{sym.shared_offset}")
            else:
                self.ctx.emit_instr("push", "IX")
                self.ctx.emit_instr("pop", "HL")
                self.ctx.emit_instr("ld", f"DE,{sym.offset}")
                self.ctx.emit_instr("add", "HL,DE")
            self._call_runtime("__load64")
            self.ctx.runtime_used.add("__acc64")
            self.ctx.emit_instr("ld", "HL,(__acc64)")
            self.ctx.emit_instr("ld", "DE,(__acc64+2)")
            return

        if sym.is_global:
            if type_is_long or type_is_float:
                # Load 32-bit value
                self.ctx.emit_instr("ld", f"HL,({sym.label()})")
                self.ctx.emit_instr("ld", f"DE,({sym.label()}+2)")
            elif type_size == 1:
                # Load 8-bit value, sign/zero-extend to HL
                self.ctx.emit_instr("ld", f"A,({sym.label()})")
                self.ctx.emit_instr("ld", "L,A")
                self._emit_char_to_hl(self._is_signed_type(sym.sym_type))
            else:
                # Load 16-bit value
                self.ctx.emit_instr("ld", f"HL,({sym.label()})")
        else:
            # Local variable: IX+offset or shared storage
            if type_is_long or type_is_float:
                self._load_local_32(sym)
            elif type_size == 1:
                # Load 8-bit value, sign/zero-extend to HL
                char_signed = self._is_signed_type(sym.sym_type)
                if sym.uses_shared_storage:
                    self.ctx.emit_instr("ld", f"A,(??AUTO+{sym.shared_offset})")
                    self.ctx.emit_instr("ld", "L,A")
                else:
                    self.ctx.emit_instr("ld", f"L,({ix_off(sym.offset)})")
                self._emit_char_to_hl(char_signed)
            else:
                self._load_local(sym)

        # Extend to 32-bit if requested but type is not already 32-bit
        # (floats are already 32-bit, don't sign-extend them)
        if force_long and not type_is_long and not type_is_float:
            is_signed = self._is_signed_type(sym.sym_type)
            self._extend_hl_to_dehl(is_signed)

    def gen_binary_op(self, expr: ast.BinaryOp) -> None:
        """Generate code for binary operation."""
        op = expr.op

        # Handle assignment specially
        if op == "=":
            self.gen_assignment(expr)
            return

        # Handle compound assignment
        compound_ops = {
            "+=": "+", "-=": "-", "*=": "*", "/=": "/", "%=": "%",
            "&=": "&", "|=": "|", "^=": "^", "<<=": "<<", ">>=": ">>"
        }
        if op in compound_ops:
            self.gen_compound_assignment(expr, compound_ops[op])
            return

        # Handle logical operators with short-circuit
        if op == "&&":
            self.gen_logical_and(expr)
            return
        if op == "||":
            self.gen_logical_or(expr)
            return

        # Comma: evaluate left for side effects, discard its value, return
        # right.  This needs left-first ordering, but the 32/64-bit and float
        # binary-op paths evaluate right first (since their runtime helpers
        # take left in DEHL/__acc64 and right in __tmp32/__tmp64), which would
        # silently drop the side effects of the right operand on memory the
        # left operand also reads — exactly the va_arg(ap, T) pattern
        # ((ap += N), (ap - N)) in stdarg.h.
        if op == ",":
            self.gen_expr(expr.left)
            self.gen_expr(expr.right)
            return

        # Check if this is a complex operation
        is_complex = self._is_complex_expr(expr.left) or self._is_complex_expr(expr.right)

        if is_complex:
            self._gen_binary_op_complex(expr, op)
            return

        # Check if this is a floating-point operation
        is_float = self._is_float_expr(expr.left) or self._is_float_expr(expr.right)

        if is_float:
            self._gen_binary_op_float(expr, op)
            return

        # Check if this is a 64-bit operation
        # For shift operations, only the LEFT operand determines result type (C99 6.5.7)
        if op in ("<<", ">>"):
            is_long_long = self._is_long_long_expr(expr.left)
            is_long = self._is_long_expr(expr.left)
        else:
            is_long_long = self._is_long_long_expr(expr.left) or self._is_long_long_expr(expr.right)
            is_long = self._is_long_expr(expr.left) or self._is_long_expr(expr.right)

        if is_long_long:
            self._gen_binary_op_64(expr, op)
            return

        # Check if this is a 32-bit operation
        if is_long:
            self._gen_binary_op_32(expr, op)
        else:
            self._gen_binary_op_16(expr, op)

    def _gen_binary_op_16(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate 16-bit binary operation."""
        # Pointer arithmetic: scale integer operand by sizeof(*ptr)
        ptr_elem_size = 0
        ptr_sub = False  # True if this is ptr - ptr (result needs division)
        if op in ("+", "-"):
            left_type = self._get_expr_type(expr.left)
            right_type = self._get_expr_type(expr.right)
            if isinstance(left_type, (ast.PointerType, ast.ArrayType)):
                base = left_type.base_type
                ptr_elem_size = self._type_size(base) if base else 1
                if isinstance(right_type, (ast.PointerType, ast.ArrayType)):
                    # ptr - ptr: result is element count
                    ptr_sub = True
            elif isinstance(right_type, (ast.PointerType, ast.ArrayType)):
                base = right_type.base_type
                ptr_elem_size = self._type_size(base) if base else 1

        # Generate left operand (result in HL)
        self.gen_expr(expr.left)

        # Save left operand to stack (DE is used internally by various address computations)
        self.ctx.emit_instr("push", "HL")

        # Generate right operand (result in HL)
        self.gen_expr(expr.right)

        # Scale integer operand for pointer arithmetic (ptr + n -> ptr + n*size)
        if ptr_elem_size > 1 and not ptr_sub:
            right_type = self._get_expr_type(expr.right)
            if not isinstance(right_type, (ast.PointerType, ast.ArrayType)):
                # Right is the integer - scale it
                self._gen_mul_const(ptr_elem_size)
            # else: left is the integer, already on stack - need different approach
            # Actually left was pushed, right is in HL. If left is int, we need to
            # scale left instead. But we already pushed it. Let's handle this:
            else:
                # Left is the integer (on stack), right is the pointer (in HL)
                # Pop left, scale it, push back
                self.ctx.emit_instr("ex", "DE,HL")  # Save pointer in DE
                self.ctx.emit_instr("pop", "HL")     # Get integer
                self._gen_mul_const(ptr_elem_size)
                self.ctx.emit_instr("push", "HL")    # Push scaled integer
                self.ctx.emit_instr("ex", "DE,HL")   # Restore pointer to HL

        # Restore left operand to DE
        self.ctx.emit_instr("pop", "DE")

        # Now: left in DE, right in HL
        # Perform operation, result in HL
        if op == "+":
            self.ctx.emit_instr("add", "HL,DE")
        elif op == "-":
            self.ctx.emit_instr("ex", "DE,HL")
            self.ctx.emit_instr("or", "A")  # Clear carry
            self.ctx.emit_instr("sbc", "HL,DE")
            # For ptr - ptr, divide result by element size
            if ptr_sub and ptr_elem_size > 1:
                self._gen_div_const(ptr_elem_size)
        elif op == "*":
            # Strength reduction: multiply by power-of-2 → repeated ADD HL,HL
            # At this point left is in DE, right is in HL.
            # Check if right operand is a power-of-2 constant
            shift = self._mul_shift_count(expr.right)
            if shift is None:
                # Check if left operand is power-of-2 (commutative)
                shift = self._mul_shift_count(expr.left)
                if shift is not None:
                    # Swap: we want the non-constant in HL
                    # Left (constant) is in DE, right (variable) is in HL - already correct
                    pass
                else:
                    self._call_runtime("__mul16")
            if shift is not None:
                # Emit repeated ADD HL,HL if left was constant,
                # or EX DE,HL + repeated ADD if right was constant
                if self._mul_shift_count(expr.right) is not None:
                    # Right is constant in HL, variable in DE → swap
                    self.ctx.emit_instr("ex", "DE,HL")
                # Now variable is in HL
                for _ in range(shift):
                    self.ctx.emit_instr("add", "HL,HL")
        elif op == "/":
            # Use signed or unsigned division based on promoted operand types
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._call_runtime("__div16" if is_unsigned else "__sdiv16")
        elif op == "%":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._call_runtime("__mod16" if is_unsigned else "__smod16")
        elif op == "&":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("and", "D")
            self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("and", "E")
            self.ctx.emit_instr("ld", "L,A")
        elif op == "|":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "D")
            self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("or", "E")
            self.ctx.emit_instr("ld", "L,A")
        elif op == "^":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("xor", "D")
            self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("xor", "E")
            self.ctx.emit_instr("ld", "L,A")
        elif op == "<<":
            # Strength reduction: shift left by small constant → repeated ADD HL,HL
            # At this point: left in DE, right (shift count) in HL
            if isinstance(expr.right, ast.IntLiteral) and 1 <= expr.right.value <= 8:
                shift = expr.right.value
                self.ctx.emit_instr("ex", "DE,HL")  # value to HL
                for _ in range(shift):
                    self.ctx.emit_instr("add", "HL,HL")
            else:
                self._call_runtime("__shl16")
        elif op == ">>":
            # Strength reduction: right shift by small constant → inline shifts
            # At this point: left in DE, right (shift count) in HL
            if isinstance(expr.right, ast.IntLiteral) and 1 <= expr.right.value <= 4:
                shift = expr.right.value
                is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
                self.ctx.emit_instr("ex", "DE,HL")  # value to HL
                for _ in range(shift):
                    if is_unsigned:
                        self.ctx.emit_instr("srl", "H")
                    else:
                        self.ctx.emit_instr("sra", "H")
                    self.ctx.emit_instr("rr", "L")
            else:
                is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
                if is_unsigned:
                    self._call_runtime("__shr16")
                else:
                    self._call_runtime("__sar16")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            # Check if either operand is unsigned for proper comparison
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._gen_comparison(op, is_unsigned)
        elif op == ",":
            # Comma operator: result is right operand (already in HL)
            pass

        # Release DE now that operation is complete
        self.ctx.regs.release_reg('de', self._emit_reg)
        self.ctx.regs.mark_free('hl')

    def _gen_binary_op_32(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate 32-bit binary operation. Result in DEHL."""
        # For 32-bit: right operand goes to __tmp32, left in DEHL, call runtime

        # Generate right operand first
        left_is_long = self._is_long_expr(expr.left)
        right_is_long = self._is_long_expr(expr.right)
        right_is_long_long = self._is_long_long_expr(expr.right)

        # Generate right operand, extend to 32-bit if needed
        self.gen_expr(expr.right, force_long=True)
        if not right_is_long and not right_is_long_long:
            # Need to extend 16-bit to 32-bit
            # (long long already produces 32-bit in DEHL via __load64)
            is_signed = not self._is_unsigned_expr(expr.right)
            self._extend_hl_to_dehl(is_signed)

        # Store right operand to __tmp32
        self._store_tmp32()

        # Check if left operand might clobber __tmp32
        left_is_complex = self._uses_tmp32(expr.left)
        if left_is_complex:
            # Save __tmp32 on stack before generating left operand
            self.ctx.emit_instr("ld", "HL,(__tmp32)")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", "HL,(__tmp32+2)")
            self.ctx.emit_instr("push", "HL")

        # Generate left operand, extend to 32-bit if needed
        left_is_long_long = self._is_long_long_expr(expr.left)
        self.gen_expr(expr.left, force_long=True)
        if not left_is_long and not left_is_long_long:
            is_signed = not self._is_unsigned_expr(expr.left)
            self._extend_hl_to_dehl(is_signed)

        if left_is_complex:
            # Restore __tmp32 from stack (need to save DEHL first)
            self.ctx.emit_instr("push", "DE")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", "HL,4")
            self.ctx.emit_instr("add", "HL,SP")
            # Stack now: [saved HL][saved DE][high tmp][low tmp]...
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "(__tmp32+2),DE")
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("ld", "(__tmp32),DE")
            # Restore DEHL
            self.ctx.emit_instr("pop", "HL")
            self.ctx.emit_instr("pop", "DE")
            # Clean up saved __tmp32 from stack
            self.ctx.emit_instr("inc", "SP")
            self.ctx.emit_instr("inc", "SP")
            self.ctx.emit_instr("inc", "SP")
            self.ctx.emit_instr("inc", "SP")

        # Now: left in DEHL, right in __tmp32
        if op == "+":
            self._call_runtime("__add32")
        elif op == "-":
            self._call_runtime("__sub32")
        elif op == "*":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            if is_unsigned:
                self._call_runtime("__mul32")
            else:
                self._call_runtime("__smul32")
        elif op == "/":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._call_runtime("__div32" if is_unsigned else "__sdiv32")
        elif op == "%":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._call_runtime("__mod32" if is_unsigned else "__smod32")
        elif op == "&":
            self._call_runtime("__and32")
        elif op == "|":
            self._call_runtime("__or32")
        elif op == "^":
            self._call_runtime("__xor32")
        elif op == "<<":
            # Shift amount should be in A
            # For now, get low byte of __tmp32 into A
            self.ctx.emit_instr("ld", "A,(__tmp32)")
            self._call_runtime("__shl32")
        elif op == ">>":
            self.ctx.emit_instr("ld", "A,(__tmp32)")
            is_unsigned = self._is_promoted_unsigned(expr.left)
            if is_unsigned:
                self._call_runtime("__shr32")
            else:
                self._call_runtime("__sar32")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._gen_comparison_32(op, is_unsigned)
        elif op == ",":
            pass  # Result is already in DEHL

    def _gen_comparison_32(self, op: str, is_unsigned: bool = False) -> None:
        """Generate 32-bit comparison. Left in DEHL, right in __tmp32. Result in HL."""
        true_label = self.ctx.new_label("CMP_T")
        false_label = self.ctx.new_label("CMP_F")
        end_label = self.ctx.new_label("CMP_E")

        if op == "==" or op == "!=":
            # Equality doesn't care about sign
            self._call_runtime("__cmp32")
            if op == "==":
                self.ctx.emit_instr("jp", f"Z,{true_label}")
            else:
                self.ctx.emit_instr("jp", f"NZ,{true_label}")
        elif is_unsigned:
            # Unsigned comparison - use __cmp32 directly
            self._call_runtime("__cmp32")
            if op == "<":
                self.ctx.emit_instr("jp", f"C,{true_label}")
            elif op == ">=":
                self.ctx.emit_instr("jp", f"NC,{true_label}")
            elif op == ">":
                self.ctx.emit_instr("jp", f"Z,{false_label}")
                self.ctx.emit_instr("jp", f"NC,{true_label}")
            elif op == "<=":
                self.ctx.emit_instr("jp", f"Z,{true_label}")
                self.ctx.emit_instr("jp", f"C,{true_label}")
        else:
            # Signed comparison - check sign bits first
            # Left sign is in D (high byte of DEHL)
            # Right sign is in __tmp32+3
            sign_same = self.ctx.new_label("CMP_SS")

            # Check if signs are different: (left_sign XOR right_sign) & 0x80
            self.ctx.emit_instr("ld", "A,(__tmp32+3)")  # A = high byte of right
            self.ctx.emit_instr("xor", "D")  # XOR with high byte of left
            self.ctx.emit_instr("jp", f"P,{sign_same}")  # If bit 7 clear, signs are same

            # Signs differ - negative is always less than positive
            # If left is negative (D & 0x80), left < right
            self.ctx.emit_instr("ld", "A,D")
            self.ctx.emit_instr("and", "80H")
            if op == "<":
                self.ctx.emit_instr("jp", f"NZ,{true_label}")  # Left negative -> true
                self.ctx.emit_instr("jp", false_label)
            elif op == ">=":
                self.ctx.emit_instr("jp", f"Z,{true_label}")  # Left positive -> true
                self.ctx.emit_instr("jp", false_label)
            elif op == ">":
                self.ctx.emit_instr("jp", f"Z,{true_label}")  # Left positive -> true
                self.ctx.emit_instr("jp", false_label)
            elif op == "<=":
                self.ctx.emit_instr("jp", f"NZ,{true_label}")  # Left negative -> true
                self.ctx.emit_instr("jp", false_label)

            # Signs are the same - do unsigned comparison
            self.ctx.emit_label(sign_same)
            self._call_runtime("__cmp32")
            if op == "<":
                self.ctx.emit_instr("jp", f"C,{true_label}")
            elif op == ">=":
                self.ctx.emit_instr("jp", f"NC,{true_label}")
            elif op == ">":
                self.ctx.emit_instr("jp", f"Z,{false_label}")
                self.ctx.emit_instr("jp", f"NC,{true_label}")
            elif op == "<=":
                self.ctx.emit_instr("jp", f"Z,{true_label}")
                self.ctx.emit_instr("jp", f"C,{true_label}")

        # Fall through to false
        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("ld", "HL,0")
        self.ctx.emit_instr("jp", end_label)
        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("ld", "HL,1")
        self.ctx.emit_label(end_label)
        # Clear DE for 16-bit result
        self.ctx.emit_instr("ld", "DE,0")

    def _gen_binary_op_64(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate 64-bit binary operation. Result in __acc64."""
        # For 64-bit: right operand goes to __tmp64, left to __acc64, call runtime
        # Mark both 64-bit storage variables as used for EXTRN declarations
        self.ctx.runtime_used.add("__acc64")
        self.ctx.runtime_used.add("__tmp64")

        # Generate right operand first and store to __tmp64
        right_is_ll = self._is_long_long_expr(expr.right)

        self._gen_64bit_operand(expr.right, to_tmp=True)

        # Check if left operand might clobber __tmp64
        left_is_complex = self._uses_tmp64(expr.left)
        if left_is_complex:
            # Save __tmp64 on stack before generating left operand
            self._call_runtime("__save_tmp64")

        # Generate left operand to __acc64
        self._gen_64bit_operand(expr.left, to_tmp=False)

        if left_is_complex:
            # Restore __tmp64 from stack
            self._call_runtime("__restore_tmp64")

        # Now: left in __acc64, right in __tmp64
        if op == "+":
            self._call_runtime("__add64")
        elif op == "-":
            self._call_runtime("__sub64")
        elif op == "*":
            self._call_runtime("__mul64")
        elif op == "/":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            if is_unsigned:
                self._call_runtime("__div64")
            else:
                self._call_runtime("__sdiv64")
        elif op == "%":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            if is_unsigned:
                self._call_runtime("__mod64")
            else:
                self._call_runtime("__smod64")
        elif op == "&":
            self._call_runtime("__and64")
        elif op == "|":
            self._call_runtime("__or64")
        elif op == "^":
            self._call_runtime("__xor64")
        elif op == "<<":
            # Shift amount in A (low byte of __tmp64)
            self.ctx.emit_instr("ld", "A,(__tmp64)")
            self._call_runtime("__shl64")
        elif op == ">>":
            self.ctx.emit_instr("ld", "A,(__tmp64)")
            is_unsigned = self._is_promoted_unsigned(expr.left)
            if is_unsigned:
                self._call_runtime("__shr64")
            else:
                self._call_runtime("__sar64")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._gen_comparison_64(op, is_unsigned)
        elif op == ",":
            pass  # Result is already in __acc64

        # For comparison result is in HL, otherwise load result from __acc64 to DEHL
        if op not in ("==", "!=", "<", ">", "<=", ">="):
            # Load lower 32 bits of result to DEHL for compatibility
            self.ctx.emit_instr("ld", "HL,(__acc64)")
            self.ctx.emit_instr("ld", "DE,(__acc64+2)")

    def _gen_64bit_operand(self, expr: ast.Expression, to_tmp: bool) -> None:
        """Generate a 64-bit operand, storing to __acc64 or __tmp64."""
        target = "__tmp64" if to_tmp else "__acc64"

        if self._is_long_long_expr(expr):
            # Already 64-bit - generate and store
            if isinstance(expr, ast.IntLiteral):
                # Large literal - emit directly
                val = expr.value & 0xFFFFFFFFFFFFFFFF
                self.ctx.emit_instr("ld", f"HL,{val & 0xFFFF}")
                self.ctx.emit_instr("ld", f"({target}),HL")
                self.ctx.emit_instr("ld", f"HL,{(val >> 16) & 0xFFFF}")
                self.ctx.emit_instr("ld", f"({target}+2),HL")
                self.ctx.emit_instr("ld", f"HL,{(val >> 32) & 0xFFFF}")
                self.ctx.emit_instr("ld", f"({target}+4),HL")
                self.ctx.emit_instr("ld", f"HL,{(val >> 48) & 0xFFFF}")
                self.ctx.emit_instr("ld", f"({target}+6),HL")
            elif isinstance(expr, ast.Identifier):
                # Load 64-bit variable
                sym = self.ctx.lookup(expr.name)
                if sym and sym.is_global:
                    self.ctx.emit_instr("ld", f"HL,{sym.label()}")
                    if to_tmp:
                        self._call_runtime("__load64t")
                    else:
                        self._call_runtime("__load64")
                elif sym:
                    # Local variable - check for shared storage
                    if sym.uses_shared_storage:
                        self.ctx.emit_instr("ld", f"HL,??AUTO+{sym.shared_offset}")
                    else:
                        self.ctx.emit_instr("push", "IX")
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("ld", f"DE,{sym.offset}")
                        self.ctx.emit_instr("add", "HL,DE")
                    if to_tmp:
                        self._call_runtime("__load64t")
                    else:
                        self._call_runtime("__load64")
            else:
                # Complex 64-bit expression - recursively generate
                self.gen_expr(expr)
                # Result should be in __acc64 for 64-bit or DEHL for 32-bit
                if not self._is_long_long_expr(expr):
                    # Need to extend from 32-bit
                    is_signed = not self._is_unsigned_expr(expr)
                    if is_signed:
                        self._call_runtime("__sext64")
                    else:
                        self._call_runtime("__zext64")
                if to_tmp:
                    self._call_runtime("__mov64")
        else:
            # Need to extend from smaller type
            self.gen_expr(expr)
            # If source is float, convert to int first (DEHL float -> DEHL int)
            if self._is_float_expr(expr):
                self._call_runtime("__ftoi")
                # Now DEHL has 32-bit signed integer
                is_signed = True
                self._call_runtime("__sext64")
            elif self._is_long_expr(expr):
                # 32-bit in DEHL - extend to 64-bit
                is_signed = not self._is_unsigned_expr(expr)
                if is_signed:
                    self._call_runtime("__sext64")
                else:
                    self._call_runtime("__zext64")
            else:
                # 16-bit in HL - extend to 64-bit
                is_signed = not self._is_unsigned_expr(expr)
                if is_signed:
                    self._call_runtime("__sext64_hl")
                else:
                    self._call_runtime("__zext64_hl")
            if to_tmp:
                self._call_runtime("__mov64")

    def _gen_comparison_64(self, op: str, is_unsigned: bool = False) -> None:
        """Generate 64-bit comparison. Left in __acc64, right in __tmp64. Result in HL."""
        # Use runtime comparison - returns -1, 0, or 1 in HL
        if is_unsigned:
            self._call_runtime("__ucmp64")
        else:
            self._call_runtime("__cmp64")

        true_label = self.ctx.new_label("CMP64_T")
        false_label = self.ctx.new_label("CMP64_F")
        end_label = self.ctx.new_label("CMP64_E")

        if op == "==":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "L")
            self.ctx.emit_instr("jr", f"Z,{true_label}")
        elif op == "!=":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "L")
            self.ctx.emit_instr("jr", f"NZ,{true_label}")
        elif op == "<":
            # HL == -1 means less than
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("and", "L")
            self.ctx.emit_instr("inc", "A")  # -1 becomes 0
            self.ctx.emit_instr("jr", f"Z,{true_label}")
        elif op == ">=":
            # HL >= 0 means greater or equal
            self.ctx.emit_instr("bit", "7,H")
            self.ctx.emit_instr("jr", f"Z,{true_label}")
        elif op == ">":
            # HL == 1 means greater than
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "A")
            self.ctx.emit_instr("jr", f"NZ,{false_label}")  # H != 0 -> not 1 -> false
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("dec", "A")
            self.ctx.emit_instr("jr", f"Z,{true_label}")
        elif op == "<=":
            # HL <= 0 means less or equal
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "A")
            self.ctx.emit_instr("jr", f"NZ,{true_label}")  # Negative -> true
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("or", "A")
            self.ctx.emit_instr("jr", f"Z,{true_label}")  # Zero -> true

        # False
        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("ld", "HL,0")
        self.ctx.emit_instr("jr", end_label)
        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("ld", "HL,1")
        self.ctx.emit_label(end_label)

    def _gen_binary_op_float(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate floating-point binary operation. Result in DEHL (IEEE 754)."""
        # Float operations: right operand to __tmp32, left in DEHL, call float runtime

        # Generate right operand first
        self._gen_float_operand(expr.right)
        # Store right operand to __tmp32
        self._store_tmp32()

        # Check if left operand might clobber __tmp32
        left_is_complex = self._uses_tmp32(expr.left)
        if left_is_complex:
            # Save __tmp32 on stack before generating left operand
            self.ctx.emit_instr("ld", "HL,(__tmp32)")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", "HL,(__tmp32+2)")
            self.ctx.emit_instr("push", "HL")

        # Generate left operand
        self._gen_float_operand(expr.left)

        if left_is_complex:
            # Restore __tmp32 from stack
            self.ctx.emit_instr("push", "DE")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", "HL,4")
            self.ctx.emit_instr("add", "HL,SP")
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "(__tmp32+2),DE")
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("ld", "(__tmp32),DE")
            self.ctx.emit_instr("pop", "HL")
            self.ctx.emit_instr("pop", "DE")
            self.ctx.emit_instr("inc", "SP")
            self.ctx.emit_instr("inc", "SP")
            self.ctx.emit_instr("inc", "SP")
            self.ctx.emit_instr("inc", "SP")

        # Now: left in DEHL, right in __tmp32
        if op == "+":
            self._call_runtime("__fadd")
        elif op == "-":
            self._call_runtime("__fsub")
        elif op == "*":
            self._call_runtime("__fmul")
        elif op == "/":
            self._call_runtime("__fdiv")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            self._gen_comparison_float(op)
        elif op == ",":
            pass  # Result is already in DEHL

    def _gen_float_operand(self, expr: ast.Expression) -> None:
        """Generate code for a float operand, converting from int if necessary."""
        if self._is_float_expr(expr):
            # Already float, just generate it
            self.gen_expr(expr)
        else:
            # Integer expression, need to convert to float
            self.gen_expr(expr, force_long=True)
            if not self._is_long_expr(expr):
                is_signed = not self._is_unsigned_expr(expr)
                self._extend_hl_to_dehl(is_signed)
            # Convert integer in DEHL to float
            if self._is_unsigned_expr(expr):
                self._call_runtime("__uitof")
            else:
                self._call_runtime("__itof")

    def _gen_comparison_float(self, op: str) -> None:
        """Generate float comparison. Left in DEHL, right in __tmp32. Result in HL."""
        true_label = self.ctx.new_label("FCMP_T")
        false_label = self.ctx.new_label("FCMP_F")
        end_label = self.ctx.new_label("FCMP_E")

        # Call float comparison - returns flags: Z if equal, C if left < right
        self._call_runtime("__fcmp")

        if op == "==":
            self.ctx.emit_instr("jp", f"Z,{true_label}")
        elif op == "!=":
            self.ctx.emit_instr("jp", f"NZ,{true_label}")
        elif op == "<":
            self.ctx.emit_instr("jp", f"C,{true_label}")
        elif op == ">=":
            self.ctx.emit_instr("jp", f"NC,{true_label}")
        elif op == ">":
            self.ctx.emit_instr("jp", f"Z,{false_label}")
            self.ctx.emit_instr("jp", f"NC,{true_label}")
        elif op == "<=":
            self.ctx.emit_instr("jp", f"Z,{true_label}")
            self.ctx.emit_instr("jp", f"C,{true_label}")

        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("ld", "HL,0")
        self.ctx.emit_instr("jp", end_label)
        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("ld", "HL,1")
        self.ctx.emit_label(end_label)
        self.ctx.emit_instr("ld", "DE,0")

    def _gen_binary_op_complex(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate complex number binary operation.
        Complex stored as: real part (4 bytes) + imaginary part (4 bytes).
        Result stored in __cplx_result, address returned in HL.
        """
        # Mark complex work areas as used
        self.ctx.runtime_used.add("__cplx_l")
        self.ctx.runtime_used.add("__cplx_r")
        self.ctx.runtime_used.add("__cplx_result")

        # Generate right operand (complex) - address on stack
        self._gen_complex_operand(expr.right)
        # Store right operand to __cplx_r (8 bytes)
        self.ctx.emit_instr("ex", "DE,HL")  # HL = source addr
        self.ctx.emit_instr("ld", "DE,__cplx_r")  # DE = dest addr
        self.ctx.emit_instr("ld", "BC,8")
        self.ctx.emit_instr("ldir")

        # Generate left operand (complex) - address in HL
        self._gen_complex_operand(expr.left)
        # Store left operand to __cplx_l (8 bytes)
        self.ctx.emit_instr("ex", "DE,HL")  # HL = source addr
        self.ctx.emit_instr("ld", "DE,__cplx_l")  # DE = dest addr
        self.ctx.emit_instr("ld", "BC,8")
        self.ctx.emit_instr("ldir")

        # Now: left in __cplx_l, right in __cplx_r
        if op == "+":
            self._call_runtime("__cadd")
        elif op == "-":
            self._call_runtime("__csub")
        elif op == "*":
            self._call_runtime("__cmul")
        elif op == "/":
            self._call_runtime("__cdiv")
        elif op in ("==", "!="):
            self._gen_comparison_complex(op)
            return
        else:
            # Other operators not supported for complex
            self.ctx.emit(f"; Complex op '{op}' not supported")

        # Result is in __cplx_result, return its address in HL
        self.ctx.emit_instr("ld", "HL,__cplx_result")

    def _gen_complex_operand(self, expr: ast.Expression) -> None:
        """Generate code for a complex operand. Returns address in HL."""
        if self._is_complex_expr(expr):
            # Generate complex expression (should return address in HL)
            self.gen_expr(expr)
        else:
            # Scalar (float or int) - convert to complex with 0 imaginary
            self._gen_float_operand(expr)
            # Store real part to __cplx_tmp
            self.ctx.runtime_used.add("__cplx_tmp")
            self.ctx.emit_instr("ld", "(__cplx_tmp),HL")
            self.ctx.emit_instr("ld", "(__cplx_tmp+2),DE")
            # Zero imaginary part
            self.ctx.emit_instr("ld", "HL,0")
            self.ctx.emit_instr("ld", "(__cplx_tmp+4),HL")
            self.ctx.emit_instr("ld", "(__cplx_tmp+6),HL")
            # Return address
            self.ctx.emit_instr("ld", "HL,__cplx_tmp")

    def _gen_comparison_complex(self, op: str) -> None:
        """Generate complex comparison. Only == and != are valid."""
        # Compare real parts
        self.ctx.emit_instr("ld", "HL,(__cplx_l)")
        self.ctx.emit_instr("ld", "DE,(__cplx_l+2)")
        self.ctx.emit_instr("ld", "BC,(__cplx_r)")
        self.ctx.emit_instr("ld", "A,L")
        self.ctx.emit_instr("cp", "C")
        ne_label = self.ctx.new_label("CNEQ")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")
        self.ctx.emit_instr("ld", "A,H")
        self.ctx.emit_instr("ld", "BC,(__cplx_r)")
        self.ctx.emit_instr("cp", "B")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")
        self.ctx.emit_instr("ld", "BC,(__cplx_r+2)")
        self.ctx.emit_instr("ld", "A,E")
        self.ctx.emit_instr("cp", "C")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")
        self.ctx.emit_instr("ld", "A,D")
        self.ctx.emit_instr("cp", "B")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")

        # Compare imaginary parts
        self.ctx.emit_instr("ld", "HL,(__cplx_l+4)")
        self.ctx.emit_instr("ld", "DE,(__cplx_l+6)")
        self.ctx.emit_instr("ld", "BC,(__cplx_r+4)")
        self.ctx.emit_instr("ld", "A,L")
        self.ctx.emit_instr("cp", "C")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")
        self.ctx.emit_instr("ld", "A,H")
        self.ctx.emit_instr("cp", "B")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")
        self.ctx.emit_instr("ld", "BC,(__cplx_r+6)")
        self.ctx.emit_instr("ld", "A,E")
        self.ctx.emit_instr("cp", "C")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")
        self.ctx.emit_instr("ld", "A,D")
        self.ctx.emit_instr("cp", "B")
        self.ctx.emit_instr("jp", f"NZ,{ne_label}")

        # Equal
        eq_label = self.ctx.new_label("CEQ")
        if op == "==":
            self.ctx.emit_instr("ld", "HL,1")
        else:  # !=
            self.ctx.emit_instr("ld", "HL,0")
        self.ctx.emit_instr("jp", eq_label)

        # Not equal
        self.ctx.emit_label(ne_label)
        if op == "==":
            self.ctx.emit_instr("ld", "HL,0")
        else:  # !=
            self.ctx.emit_instr("ld", "HL,1")

        self.ctx.emit_label(eq_label)
        self.ctx.emit_instr("ld", "DE,0")

    def _emit_reg(self, instr: str, operand: str) -> None:
        """Emit instruction for register allocator callbacks."""
        self.ctx.emit_instr(instr, operand)

    def gen_assignment(self, expr: ast.BinaryOp) -> None:
        """Generate code for assignment."""
        # Check if target is 64-bit, 32-bit, or float using type inference
        target_is_64bit = False
        target_is_32bit = False
        target_is_float = False
        target_type = self._get_expr_type(expr.left)
        if target_type:
            if self._is_long_long_type(target_type):
                target_is_64bit = True
            elif self._is_long_type(target_type):
                target_is_32bit = True
            elif self._is_float_type(target_type):
                target_is_32bit = True
                target_is_float = True

        # 64-bit assignment path
        if target_is_64bit:
            self._gen_assignment_64(expr)
            return

        # Struct/union assignment: copy entire struct via LDIR
        if isinstance(target_type, ast.StructType):
            struct_size = self._type_size(target_type)
            if struct_size > 2:
                self._gen_struct_assignment(expr, struct_size)
                return

        # Check if source is float/double
        source_is_float = self._is_float_expr(expr.right)

        # Generate the value (force_long if target is 32-bit, or source is float)
        self.gen_expr(expr.right, force_long=(target_is_32bit or source_is_float))

        # If the right side was a 64-bit assignment (chain assignment like
        # a = b = long_long_var = 2), __store64 leaves DEHL as garbage (the
        # address it stored to). Extract low 32 bits from __acc64 into DEHL.
        source_is_64bit_assign = (isinstance(expr.right, ast.BinaryOp) and
                                  expr.right.op == "=" and
                                  self._is_long_long_expr(expr.right))
        if source_is_64bit_assign:
            self.ctx.runtime_used.add("__acc64")
            self.ctx.emit_instr("ld", "HL,(__acc64)")
            self.ctx.emit_instr("ld", "DE,(__acc64+2)")

        # If source is float but target is integer, convert float to int
        if source_is_float and not target_is_float:
            if target_type and self._is_bool_type(target_type):
                # Float-to-bool: any non-zero float becomes 1 (C99 6.3.1.2)
                # Don't use __ftoi which truncates (e.g. 0.5 → 0)
                self.ctx.emit_instr("ld", "A,D")
                self.ctx.emit_instr("or", "E")
                self.ctx.emit_instr("or", "H")
                self.ctx.emit_instr("or", "L")
                self.ctx.emit_instr("ld", "HL,0")
                self.ctx.emit_instr("jr", "Z,$+3")
                self.ctx.emit_instr("inc", "L")
            else:
                # DEHL has IEEE float, convert to signed 32-bit int in DEHL
                self._call_runtime("__ftoi")
                # If target is 16-bit, HL already has the low word (truncated)
                # If target is 32-bit, DEHL has the full value
        # If target is 32-bit integer but source is not, extend
        # (Don't extend for float targets - floats are already 32-bit in DEHL)
        # (Don't extend for 64-bit sources - already extracted from __acc64)
        # (Don't extend for long long sources - DEHL already has low 32 bits)
        elif target_is_32bit and not target_is_float and not self._is_long_expr(expr.right) and not source_is_64bit_assign and not source_is_float and not self._is_long_long_expr(expr.right):
            is_signed = not self._is_unsigned_expr(expr.right)
            self._extend_hl_to_dehl(is_signed)
        # If target is float but source is integer, convert to float
        elif target_is_float and not source_is_float:
            # First extend integer to 32-bit if needed
            # (Don't extend long long sources - DEHL already has low 32 bits)
            if not self._is_long_expr(expr.right) and not source_is_64bit_assign and not self._is_long_long_expr(expr.right):
                is_signed = not self._is_unsigned_expr(expr.right)
                self._extend_hl_to_dehl(is_signed)
            # Then convert to float
            if self._is_unsigned_expr(expr.right):
                self._call_runtime("__uitof")
            else:
                self._call_runtime("__itof")

        # _Bool normalization before storing (C99 6.3.1.2)
        if target_type and self._is_bool_type(target_type):
            self._emit_bool_normalize()

        # Store to the target
        if isinstance(expr.left, ast.Identifier):
            sym = self.ctx.lookup(expr.left.name)
            if sym:
                if target_is_32bit:
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"({sym.label()}),HL")
                        self.ctx.emit_instr("ld", f"({sym.label()}+2),DE")
                    else:
                        self._store_local_32(sym)
                else:
                    target_size = self._type_size(target_type) if target_type else 2
                    if sym.is_global:
                        if target_size == 1:
                            self.ctx.emit_instr("ld", "A,L")
                            self.ctx.emit_instr("ld", f"({sym.label()}),A")
                            # Restore HL from A - the peephole optimizer may merge
                            # LD HL,N / LD A,L into LD A,N, destroying HL.
                            # Chained assignments need the result in HL.
                            self.ctx.emit_instr("ld", "L,A")
                            self.ctx.emit_instr("ld", "H,0")
                        else:
                            self.ctx.emit_instr("ld", f"({sym.label()}),HL")
                    else:
                        self._store_local(sym, size=target_size)
        elif isinstance(expr.left, ast.UnaryOp) and expr.left.op == "*":
            # Pointer dereference assignment: *p = value
            target_size = self._type_size(target_type) if target_type else 2
            if target_is_32bit:
                # Save value twice: one for storing, one to restore result
                self.ctx.emit_instr("push", "DE")  # Result high word
                self.ctx.emit_instr("push", "HL")  # Result low word
                self.ctx.emit_instr("push", "DE")  # Store high word
                self.ctx.emit_instr("push", "HL")  # Store low word
                self.gen_expr(expr.left.operand)   # Get address in HL
                self.ctx.emit_instr("ex", "DE,HL") # Address in DE
                self.ctx.emit_instr("pop", "HL")   # Low word in HL
                self.ctx.emit_instr("ex", "DE,HL") # Address in HL, low word in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("pop", "DE")   # High word in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                # Restore DEHL for chained assignments
                self.ctx.emit_instr("pop", "HL")   # Result low word
                self.ctx.emit_instr("pop", "DE")   # Result high word
            elif target_size == 1:
                self.ctx.emit_instr("push", "HL")  # Save value
                self.gen_expr(expr.left.operand)   # Get address in HL
                self.ctx.emit_instr("pop", "DE")   # Value in DE
                self.ctx.emit_instr("ld", "(HL),E")  # Store only 1 byte
                self.ctx.emit_instr("ex", "DE,HL")  # Return value in HL
            else:
                self.ctx.emit_instr("push", "HL")  # Save value
                self.gen_expr(expr.left.operand)   # Get address in HL
                self.ctx.emit_instr("pop", "DE")   # Value in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("ex", "DE,HL")  # Return value in HL
        elif isinstance(expr.left, ast.Index):
            # Array element assignment
            if target_is_32bit:
                # Save value twice: one for storing, one to restore result
                self.ctx.emit_instr("push", "DE")  # Result high word
                self.ctx.emit_instr("push", "HL")  # Result low word
                self.ctx.emit_instr("push", "DE")  # Store high word
                self.ctx.emit_instr("push", "HL")  # Store low word
                self._gen_address(expr.left)       # Get address in HL
                self.ctx.emit_instr("ex", "DE,HL") # Address in DE
                self.ctx.emit_instr("pop", "HL")   # Low word in HL
                self.ctx.emit_instr("ex", "DE,HL") # Address in HL, low word in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("pop", "DE")   # High word in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                # Restore DEHL for chained assignments
                self.ctx.emit_instr("pop", "HL")   # Result low word
                self.ctx.emit_instr("pop", "DE")   # Result high word
            elif target_type and self._type_size(target_type) == 1:
                self.ctx.emit_instr("push", "HL")  # Save value
                self._gen_address(expr.left)       # Get address in HL
                self.ctx.emit_instr("pop", "DE")   # Value in DE
                self.ctx.emit_instr("ld", "(HL),E")  # Store only 1 byte
                self.ctx.emit_instr("ex", "DE,HL")  # Return value in HL
            else:
                self.ctx.emit_instr("push", "HL")  # Save value
                self._gen_address(expr.left)       # Get address in HL
                self.ctx.emit_instr("pop", "DE")   # Value in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("ex", "DE,HL")  # Return value in HL
        elif isinstance(expr.left, ast.Member):
            # Check for bitfield assignment
            bf = self._get_bitfield_info(expr.left)
            if bf is not None:
                self._gen_bitfield_write(expr, bf)
                return

            # Struct member assignment
            member_size = self._get_member_size(expr.left)
            member_type = self._get_member_type(expr.left)
            member_is_32bit = self._is_long_type(member_type) or self._is_float_type(member_type)

            if member_is_32bit:
                # 32-bit member: save DEHL twice, store, restore result
                self.ctx.emit_instr("push", "DE")  # Result high word
                self.ctx.emit_instr("push", "HL")  # Result low word
                self.ctx.emit_instr("push", "DE")  # Store high word
                self.ctx.emit_instr("push", "HL")  # Store low word
                self._gen_address(expr.left)       # Get member address in HL
                self.ctx.emit_instr("ex", "DE,HL") # Address in DE
                self.ctx.emit_instr("pop", "HL")   # Low word in HL
                self.ctx.emit_instr("ex", "DE,HL") # Address in HL, low word in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("pop", "DE")   # High word in DE
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                # Restore DEHL for chained assignments
                self.ctx.emit_instr("pop", "HL")   # Result low word
                self.ctx.emit_instr("pop", "DE")   # Result high word
            else:
                self.ctx.emit_instr("push", "HL")  # Save value
                self._gen_address(expr.left)       # Get member address in HL
                self.ctx.emit_instr("pop", "DE")   # Value in DE
                if member_size == 1:
                    self.ctx.emit_instr("ld", "(HL),E")
                else:
                    self.ctx.emit_instr("ld", "(HL),E")
                    self.ctx.emit_instr("inc", "HL")
                    self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("ex", "DE,HL")  # Return value in HL

    def _gen_assignment_64(self, expr: ast.BinaryOp) -> None:
        """Generate code for 64-bit assignment. Value goes into __acc64, then stored."""
        if isinstance(expr.left, ast.Identifier):
            sym = self.ctx.lookup(expr.left.name)
            if sym:
                # Generate value into __acc64
                self._gen_64bit_operand(expr.right, to_tmp=False)
                # Store __acc64 to target
                if sym.is_global:
                    self.ctx.emit_instr("ld", f"HL,{sym.label()}")
                    self._call_runtime("__store64")
                else:
                    self._store_local_64(sym)
        elif isinstance(expr.left, ast.Index):
            # Array element: compute address first, push it, then generate value
            self._gen_address(expr.left)        # Get address in HL
            self.ctx.emit_instr("push", "HL")   # Save address
            self._gen_64bit_operand(expr.right, to_tmp=False)  # Value into __acc64
            self.ctx.emit_instr("pop", "HL")    # Restore address
            self._call_runtime("__store64")
        elif isinstance(expr.left, ast.UnaryOp) and expr.left.op == "*":
            # Pointer dereference: compute address first, push it, then generate value
            self.gen_expr(expr.left.operand)     # Get address in HL
            self.ctx.emit_instr("push", "HL")   # Save address
            self._gen_64bit_operand(expr.right, to_tmp=False)  # Value into __acc64
            self.ctx.emit_instr("pop", "HL")    # Restore address
            self._call_runtime("__store64")
        elif isinstance(expr.left, ast.Member):
            # Struct member: compute address first, push it, then generate value
            self._gen_address(expr.left)         # Get member address in HL
            self.ctx.emit_instr("push", "HL")   # Save address
            self._gen_64bit_operand(expr.right, to_tmp=False)  # Value into __acc64
            self.ctx.emit_instr("pop", "HL")    # Restore address
            self._call_runtime("__store64")

    def gen_compound_assignment(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate code for compound assignment (+=, -=, etc.)."""
        # If LHS has side effects (function calls, inc/dec), evaluate address once
        if self._expr_has_side_effects(expr.left):
            self._gen_compound_assignment_safe(expr, op)
            return
        # Build a regular binary op and assignment
        inner_op = ast.BinaryOp(op=op, left=expr.left, right=expr.right)
        assign = ast.BinaryOp(op="=", left=expr.left, right=inner_op)
        self.gen_assignment(assign)

    def _expr_has_side_effects(self, expr: ast.Expression) -> bool:
        """Check if expression contains function calls or increment/decrement."""
        if isinstance(expr, ast.Call):
            return True
        if isinstance(expr, ast.UnaryOp):
            if expr.op in ('++', '--', 'post++', 'post--'):
                return True
            return self._expr_has_side_effects(expr.operand)
        if isinstance(expr, ast.BinaryOp):
            return (self._expr_has_side_effects(expr.left) or
                    self._expr_has_side_effects(expr.right))
        if isinstance(expr, ast.Member):
            return self._expr_has_side_effects(expr.obj)
        if isinstance(expr, ast.Index):
            return (self._expr_has_side_effects(expr.array) or
                    self._expr_has_side_effects(expr.index))
        if isinstance(expr, ast.Cast):
            return self._expr_has_side_effects(expr.expr)
        return False

    def _gen_compound_assignment_safe(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate compound assignment where LHS has side effects.
        Evaluates the LHS address only once to avoid double side effects."""
        target_type = self._get_expr_type(expr.left)
        target_size = self._type_size(target_type) if target_type else 2

        # For 32-bit/64-bit, fall back to the simple rewrite (rare case)
        if target_size > 2:
            inner_op = ast.BinaryOp(op=op, left=expr.left, right=expr.right)
            assign = ast.BinaryOp(op="=", left=expr.left, right=inner_op)
            self.gen_assignment(assign)
            return

        # Generate address of LHS once
        self._gen_address(expr.left)
        self.ctx.emit_instr("push", "HL")  # Save address

        # Read current value from address
        if target_size == 1:
            self.ctx.emit_instr("ld", "L,(HL)")
            self._emit_char_to_hl(self._is_signed_type(target_type))
        else:  # 16-bit
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("ex", "DE,HL")
        # old_value is now in HL

        # Compute old_value OP rhs: push old value, evaluate RHS, pop into DE
        self.ctx.emit_instr("push", "HL")
        self.gen_expr(expr.right)
        self.ctx.emit_instr("pop", "DE")
        # DE = old_value (left), HL = rhs (right)

        # Apply operation (DE=left, HL=right convention matches runtime funcs)
        if op == "+":
            self.ctx.emit_instr("add", "HL,DE")
        elif op == "-":
            self.ctx.emit_instr("ex", "DE,HL")
            self.ctx.emit_instr("or", "A")
            self.ctx.emit_instr("sbc", "HL,DE")
        elif op == "*":
            self._call_runtime("__mul16")
        elif op == "/":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._call_runtime("__div16" if is_unsigned else "__sdiv16")
        elif op == "%":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._call_runtime("__mod16" if is_unsigned else "__smod16")
        elif op == "&":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("and", "D")
            self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("and", "E")
            self.ctx.emit_instr("ld", "L,A")
        elif op == "|":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "D")
            self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("or", "E")
            self.ctx.emit_instr("ld", "L,A")
        elif op == "^":
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("xor", "D")
            self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("xor", "E")
            self.ctx.emit_instr("ld", "L,A")
        elif op == "<<":
            self._call_runtime("__shl16")
        elif op == ">>":
            is_unsigned = self._is_promoted_unsigned(expr.left) or self._is_promoted_unsigned(expr.right)
            self._call_runtime("__shr16" if is_unsigned else "__sar16")

        # Result in HL. Pop saved address, store back.
        self.ctx.emit_instr("pop", "DE")     # DE = address
        self.ctx.emit_instr("ex", "DE,HL")   # HL = address, DE = result
        if target_size == 1:
            self.ctx.emit_instr("ld", "(HL),E")
        else:
            self.ctx.emit_instr("ld", "(HL),E")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "(HL),D")
        self.ctx.emit_instr("ex", "DE,HL")   # result back in HL

    def gen_logical_and(self, expr: ast.BinaryOp) -> None:
        """Generate short-circuit logical AND."""
        false_label = self.ctx.new_label("AND_F")
        end_label = self.ctx.new_label("AND_E")

        left_is_32 = self._is_float_expr(expr.left) or self._is_long_expr(expr.left)
        self.gen_expr(expr.left, force_long=left_is_32)
        self._emit_condition_test(expr.left)
        self.ctx.emit_instr("jp", f"Z,{false_label}")

        right_is_32 = self._is_float_expr(expr.right) or self._is_long_expr(expr.right)
        self.gen_expr(expr.right, force_long=right_is_32)
        self._emit_condition_test(expr.right)
        self.ctx.emit_instr("jp", f"Z,{false_label}")

        self.ctx.emit_instr("ld", "HL,1")
        self.ctx.emit_instr("jp", end_label)

        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("ld", "HL,0")

        self.ctx.emit_label(end_label)

    def gen_logical_or(self, expr: ast.BinaryOp) -> None:
        """Generate short-circuit logical OR."""
        true_label = self.ctx.new_label("OR_T")
        end_label = self.ctx.new_label("OR_E")

        left_is_32 = self._is_float_expr(expr.left) or self._is_long_expr(expr.left)
        self.gen_expr(expr.left, force_long=left_is_32)
        self._emit_condition_test(expr.left)
        self.ctx.emit_instr("jp", f"NZ,{true_label}")

        right_is_32 = self._is_float_expr(expr.right) or self._is_long_expr(expr.right)
        self.gen_expr(expr.right, force_long=right_is_32)
        self._emit_condition_test(expr.right)
        self.ctx.emit_instr("jp", f"NZ,{true_label}")

        self.ctx.emit_instr("ld", "HL,0")
        self.ctx.emit_instr("jp", end_label)

        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("ld", "HL,1")

        self.ctx.emit_label(end_label)

    def gen_unary_op(self, expr: ast.UnaryOp) -> None:
        """Generate code for unary operation."""
        op = expr.op

        if op == "-":
            if self._is_long_long_expr(expr.operand):
                # 64-bit negate: generate to __acc64, call __neg64
                self._gen_64bit_operand(expr.operand, to_tmp=False)
                self._call_runtime("__neg64")
            elif self._is_float_expr(expr.operand):
                # Float negate: flip sign bit (bit 31 = bit 7 of high byte of DE)
                # Float stored as: HL=low word, DE=high word
                self.gen_expr(expr.operand)
                # Sign bit is bit 7 of D (high byte of high word)
                self.ctx.emit_instr("ld", "A,D")
                self.ctx.emit_instr("xor", "80H")
                self.ctx.emit_instr("ld", "D,A")
            elif self._is_long_expr(expr.operand):
                # 32-bit negate using runtime
                self.gen_expr(expr.operand)
                self._call_runtime("__neg32")
            else:
                # 16-bit negate: 0 - HL
                self.gen_expr(expr.operand)
                self.ctx.emit_instr("ex", "DE,HL")
                self.ctx.emit_instr("ld", "HL,0")
                self.ctx.emit_instr("or", "A")
                self.ctx.emit_instr("sbc", "HL,DE")

        elif op == "+":
            self.gen_expr(expr.operand)  # No-op

        elif op == "!":
            op_is_32 = self._is_float_expr(expr.operand) or self._is_long_expr(expr.operand)
            self.gen_expr(expr.operand, force_long=op_is_32)
            # Logical NOT: if zero then 1 else 0
            if op_is_32:
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("or", "L")
                self.ctx.emit_instr("or", "E")
                self.ctx.emit_instr("or", "D")
            else:
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("or", "L")
            self.ctx.emit_instr("ld", "HL,0")
            self.ctx.emit_instr("jr", "NZ,$+3")
            self.ctx.emit_instr("inc", "L")

        elif op == "~":
            self.gen_expr(expr.operand)
            if self._is_long_long_expr(expr.operand):
                # 64-bit bitwise NOT using runtime
                self._call_runtime("__not64")
            elif self._is_long_expr(expr.operand):
                # 32-bit bitwise NOT using runtime
                self._call_runtime("__not32")
            else:
                # 16-bit bitwise NOT
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("cpl")
                self.ctx.emit_instr("ld", "H,A")
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("cpl")
                self.ctx.emit_instr("ld", "L,A")

        elif op == "*":
            # Pointer dereference
            self.gen_expr(expr.operand)  # Get address in HL

            # Check if we're dereferencing a function pointer
            # Dereferencing a function pointer is a no-op (functions decay to pointers)
            operand_type = self._get_expr_type(expr.operand)
            if isinstance(operand_type, ast.PointerType):
                if isinstance(operand_type.base_type, ast.FunctionType):
                    # *func_ptr is just func_ptr - no actual load needed
                    return

            # Determine size of dereferenced type
            deref_size = self._get_deref_size(expr.operand)

            if deref_size == 1:
                # 8-bit load, sign/zero-extend to HL
                deref_signed = True
                if isinstance(operand_type, ast.PointerType):
                    deref_signed = self._is_signed_type(operand_type.base_type)
                self.ctx.emit_instr("ld", "L,(HL)")
                self._emit_char_to_hl(deref_signed)
            elif deref_size == 8:
                # 64-bit load: HL has address, load into __acc64
                self._call_runtime("__load64")
                self.ctx.runtime_used.add("__acc64")
                # Return low 32 bits in DEHL for use as rvalue
                self.ctx.emit_instr("ld", "HL,(__acc64)")
                self.ctx.emit_instr("ld", "DE,(__acc64+2)")
            elif deref_size == 4:
                # 32-bit load
                self.ctx.emit_instr("ld", "E,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "D,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "A,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "H,(HL)")
                self.ctx.emit_instr("ld", "L,A")
                self.ctx.emit_instr("ex", "DE,HL")
            else:
                # 16-bit load
                self.ctx.emit_instr("ld", "E,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "D,(HL)")
                self.ctx.emit_instr("ex", "DE,HL")

        elif op == "&":
            # Address-of - cannot take address of bitfield
            if isinstance(expr.operand, ast.Member):
                bf = self._get_bitfield_info(expr.operand)
                if bf is not None and not (bf.bit_offset == 0 and bf.bit_width == bf.storage_size * 8):
                    self._error("cannot take address of bitfield", expr)
                    self.ctx.emit_instr("ld", "HL,0")
                    return
            self._gen_address(expr.operand)

        elif op == "++" or op == "--":
            self._gen_inc_dec(expr)

    def _gen_inc_dec(self, expr: ast.UnaryOp) -> None:
        """Generate code for increment/decrement."""
        is_inc = expr.op == "++"

        if isinstance(expr.operand, ast.Identifier):
            sym = self.ctx.lookup(expr.operand.name)
            if sym:
                is_float = self._is_float_type(sym.sym_type)
                is_long = self._is_long_type(sym.sym_type)
                is_long_long = self._is_long_long_type(sym.sym_type)

                if is_long_long:
                    # 64-bit increment/decrement
                    self.ctx.runtime_used.add("__acc64")
                    self.ctx.runtime_used.add("__tmp64")
                    # Load value to __acc64
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"HL,{sym.label()}")
                    elif sym.uses_shared_storage:
                        self.ctx.emit_instr("ld", f"HL,??AUTO+{sym.shared_offset}")
                    else:
                        self.ctx.emit_instr("push", "IX")
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("ld", f"DE,{sym.offset}")
                        self.ctx.emit_instr("add", "HL,DE")
                    self._call_runtime("__load64")
                    # Postfix: save the original 64-bit value on stack
                    if not expr.is_prefix:
                        self.ctx.emit_instr("ld", "HL,(__acc64+6)")
                        self.ctx.emit_instr("push", "HL")
                        self.ctx.emit_instr("ld", "HL,(__acc64+4)")
                        self.ctx.emit_instr("push", "HL")
                        self.ctx.emit_instr("ld", "HL,(__acc64+2)")
                        self.ctx.emit_instr("push", "HL")
                        self.ctx.emit_instr("ld", "HL,(__acc64)")
                        self.ctx.emit_instr("push", "HL")
                    # Step (1 for integer; pointer scale for pointer)
                    step = 1
                    if isinstance(sym.sym_type, ast.PointerType):
                        step = self._type_size(sym.sym_type.base_type)
                        if step == 0:
                            step = 1
                    # __tmp64 = step (zero-extended)
                    self.ctx.emit_instr("ld", f"HL,{step}")
                    self.ctx.emit_instr("ld", "(__tmp64),HL")
                    self.ctx.emit_instr("ld", "HL,0")
                    self.ctx.emit_instr("ld", "(__tmp64+2),HL")
                    self.ctx.emit_instr("ld", "(__tmp64+4),HL")
                    self.ctx.emit_instr("ld", "(__tmp64+6),HL")
                    if is_inc:
                        self._call_runtime("__add64")
                    else:
                        self._call_runtime("__sub64")
                    # Store result
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"HL,{sym.label()}")
                        self._call_runtime("__store64")
                    else:
                        self._store_local_64(sym)
                    # Postfix: restore the original value as the expression result
                    if not expr.is_prefix:
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("ld", "(__acc64),HL")
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("ld", "(__acc64+2),HL")
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("ld", "(__acc64+4),HL")
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("ld", "(__acc64+6),HL")
                    # Surface low 32 bits in DEHL for rvalue use
                    self.ctx.emit_instr("ld", "HL,(__acc64)")
                    self.ctx.emit_instr("ld", "DE,(__acc64+2)")

                elif is_float:
                    # Float increment/decrement: use __fadd/__fsub with 1.0
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"HL,({sym.label()})")
                        self.ctx.emit_instr("ld", f"DE,({sym.label()}+2)")
                    else:
                        self._load_local_32(sym)
                    if not expr.is_prefix:
                        self.ctx.emit_instr("push", "DE")
                        self.ctx.emit_instr("push", "HL")
                    # Load 1.0 (0x3F800000) into __tmp32
                    self.ctx.runtime_used.add("__tmp32")
                    self.ctx.emit_instr("ld", "HL,0")
                    self.ctx.emit_instr("ld", "(__tmp32),HL")
                    self.ctx.emit_instr("ld", "HL,16256")  # 0x3F80
                    self.ctx.emit_instr("ld", "(__tmp32+2),HL")
                    # Reload value into DEHL
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"HL,({sym.label()})")
                        self.ctx.emit_instr("ld", f"DE,({sym.label()}+2)")
                    else:
                        self._load_local_32(sym)
                    self._call_runtime("__fadd" if is_inc else "__fsub")
                    # Store result
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"({sym.label()}),HL")
                        self.ctx.emit_instr("ld", f"({sym.label()}+2),DE")
                    else:
                        self._store_local_32(sym)
                    if not expr.is_prefix:
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("pop", "DE")

                elif is_long:
                    # Long (32-bit) increment/decrement
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"HL,({sym.label()})")
                        self.ctx.emit_instr("ld", f"DE,({sym.label()}+2)")
                    else:
                        self._load_local_32(sym)
                    if not expr.is_prefix:
                        self.ctx.emit_instr("push", "DE")
                        self.ctx.emit_instr("push", "HL")
                    # Determine step
                    step = 1
                    if isinstance(sym.sym_type, ast.PointerType):
                        step = self._type_size(sym.sym_type.base_type)
                        if step == 0:
                            step = 1
                    # Add/subtract step using __tmp32
                    self.ctx.runtime_used.add("__tmp32")
                    self.ctx.emit_instr("ld", f"(__tmp32),HL")
                    self.ctx.emit_instr("ld", f"(__tmp32+2),DE")
                    if is_inc:
                        self.ctx.emit_instr("ld", f"HL,{step}")
                    else:
                        self.ctx.emit_instr("ld", f"HL,{(-step) & 0xFFFF}")
                    self.ctx.emit_instr("ld", "DE,0" if is_inc else "DE,65535")
                    self._call_runtime("__add32")
                    # Store result
                    if sym.is_global:
                        self.ctx.emit_instr("ld", f"({sym.label()}),HL")
                        self.ctx.emit_instr("ld", f"({sym.label()}+2),DE")
                    else:
                        self._store_local_32(sym)
                    if not expr.is_prefix:
                        self.ctx.emit_instr("pop", "HL")
                        self.ctx.emit_instr("pop", "DE")

                else:
                    # 16-bit increment/decrement (original code path)
                    is_char_sized = self._type_size(sym.sym_type) == 1
                    # Load current value
                    if sym.is_global:
                        if is_char_sized:
                            self.ctx.emit_instr("ld", f"A,({sym.label()})")
                            self.ctx.emit_instr("ld", "L,A")
                            self._emit_char_to_hl(self._is_signed_type(sym.sym_type))
                        else:
                            self.ctx.emit_instr("ld", f"HL,({sym.label()})")
                    else:
                        self._load_local(sym)

                    if not expr.is_prefix:
                        # Postfix: save original value
                        self.ctx.emit_instr("push", "HL")

                    # Determine step size (for pointer types, scale by pointee size)
                    step = 1
                    if isinstance(sym.sym_type, ast.PointerType):
                        step = self._type_size(sym.sym_type.base_type)
                        if step == 0:
                            step = 1

                    # Increment or decrement
                    if step <= 4:
                        for _ in range(step):
                            if is_inc:
                                self.ctx.emit_instr("inc", "HL")
                            else:
                                self.ctx.emit_instr("dec", "HL")
                    else:
                        if is_inc:
                            self.ctx.emit_instr("ld", f"DE,{step}")
                        else:
                            self.ctx.emit_instr("ld", f"DE,{(-step) & 0xFFFF}")
                        self.ctx.emit_instr("add", "HL,DE")

                    # _Bool normalization: ++/-- on _Bool normalizes to 0/1 (C99 6.3.1.2)
                    if self._is_bool_type(sym.sym_type):
                        self._emit_bool_normalize()

                    # Store back
                    if sym.is_global:
                        if is_char_sized:
                            self.ctx.emit_instr("ld", "A,L")
                            self.ctx.emit_instr("ld", f"({sym.label()}),A")
                        else:
                            self.ctx.emit_instr("ld", f"({sym.label()}),HL")
                    else:
                        self._store_local(sym)

                    if not expr.is_prefix:
                        # Postfix: restore original value as result
                        self.ctx.emit_instr("pop", "HL")

        elif isinstance(expr.operand, ast.Member) or \
             isinstance(expr.operand, ast.Index) or \
             (isinstance(expr.operand, ast.UnaryOp) and expr.operand.op == "*"):
            # Check for bitfield inc/dec - rewrite as compound assignment
            if isinstance(expr.operand, ast.Member):
                bf = self._get_bitfield_info(expr.operand)
                if bf is not None and not (bf.bit_offset == 0 and bf.bit_width == bf.storage_size * 8):
                    # Rewrite bf++ as bf = bf + 1, or ++bf similarly
                    one = ast.IntLiteral(value=1)
                    inner = ast.BinaryOp(op="+" if is_inc else "-",
                                         left=expr.operand, right=one)
                    assign = ast.BinaryOp(op="=", left=expr.operand, right=inner)
                    if not expr.is_prefix:
                        # Postfix: save old value first
                        self.gen_member(expr.operand)
                        self.ctx.emit_instr("push", "HL")
                    self.gen_assignment(assign)
                    if not expr.is_prefix:
                        self.ctx.emit_instr("pop", "HL")
                    return

            # Struct member, array index, or pointer dereference: s.x++, t[x]++, (*p)++
            # Get element size and type for pointer increment
            elem_size = 1
            elem_type = None
            if isinstance(expr.operand, ast.Index):
                elem_size = self._get_index_elem_size(expr.operand.array)
                arr_type = self._get_expr_type(expr.operand.array)
                if isinstance(arr_type, (ast.ArrayType, ast.PointerType)):
                    elem_type = arr_type.base_type
            else:
                deref_type = self._get_expr_type(expr.operand)
                if deref_type:
                    elem_size = self._type_size(deref_type)
                    elem_type = deref_type

            # Calculate address and save it
            self._gen_address(expr.operand)
            self.ctx.emit_instr("push", "HL")  # Save address

            # Load current value
            if elem_size == 1:
                self.ctx.emit_instr("ld", "L,(HL)")
                self._emit_char_to_hl(self._is_signed_type(elem_type))
            else:
                self.ctx.emit_instr("ld", "E,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "D,(HL)")
                self.ctx.emit_instr("ex", "DE,HL")

            if not expr.is_prefix:
                # Postfix: save original value
                self.ctx.emit_instr("push", "HL")

            # Increment or decrement
            if is_inc:
                self.ctx.emit_instr("inc", "HL")
            else:
                self.ctx.emit_instr("dec", "HL")

            # Store back: address is on stack (under original value if postfix)
            if not expr.is_prefix:
                self.ctx.emit_instr("ex", "DE,HL")  # DE = new value
                self.ctx.emit_instr("pop", "HL")    # HL = original value
                self.ctx.emit_instr("ex", "(SP),HL")  # HL = address, original on stack
                self.ctx.emit_instr("ex", "DE,HL")  # HL = new value, DE = address
                self.ctx.emit_instr("ex", "DE,HL")  # DE = new value, HL = address
            else:
                self.ctx.emit_instr("ex", "DE,HL")  # DE = new value
                self.ctx.emit_instr("pop", "HL")    # HL = address

            # Store new value at address
            if elem_size == 1:
                self.ctx.emit_instr("ld", "(HL),E")
            else:
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")

            if not expr.is_prefix:
                # Postfix: restore original value as result
                self.ctx.emit_instr("pop", "HL")
            else:
                # Prefix: result is the new value
                self.ctx.emit_instr("ex", "DE,HL")

    def gen_call(self, expr: ast.Call) -> None:
        """Generate code for function call."""
        # Handle GCC builtins
        if isinstance(expr.func, ast.Identifier):
            if expr.func.name == '__builtin_expect':
                # __builtin_expect(x, c) just returns x - it's a hint for branch prediction
                if expr.args:
                    self.gen_expr(expr.args[0])
                else:
                    self.ctx.emit_instr("ld", "HL,0")
                return
            # GCC builtin pass-throughs to libc.  GCC sometimes emits
            # __builtin_memcpy directly (e.g. when the prototype isn't
            # visible) — rewrite to the libc symbol so the linker can
            # resolve it.
            _BUILTIN_TO_LIBC = {
                '__builtin_memcpy':   'memcpy',
                '__builtin_memmove':  'memmove',
                '__builtin_memset':   'memset',
                '__builtin_memcmp':   'memcmp',
                '__builtin_strlen':   'strlen',
                '__builtin_strcpy':   'strcpy',
                '__builtin_strncpy':  'strncpy',
                '__builtin_strcmp':   'strcmp',
                '__builtin_strncmp':  'strncmp',
                '__builtin_strcat':   'strcat',
                '__builtin_strncat':  'strncat',
                '__builtin_strchr':   'strchr',
                '__builtin_abort':    'abort',
                '__builtin_exit':     'exit',
                '__builtin_puts':     'puts',
                '__builtin_printf':   'printf',
                '__builtin_putchar':  'putchar',
            }
            if expr.func.name in _BUILTIN_TO_LIBC:
                expr = ast.Call(
                    func=ast.Identifier(name=_BUILTIN_TO_LIBC[expr.func.name],
                                        location=expr.func.location),
                    args=expr.args,
                    location=expr.location,
                )

        # Get function parameter types if available
        param_types: list[ast.TypeNode] = []
        if isinstance(expr.func, ast.Identifier):
            func_sym = self.ctx.lookup(expr.func.name)
            if func_sym and isinstance(func_sym.sym_type, ast.FunctionType):
                param_types = func_sym.sym_type.param_types
            elif (func_sym and isinstance(func_sym.sym_type, ast.PointerType)
                  and isinstance(func_sym.sym_type.base_type, ast.FunctionType)):
                param_types = func_sym.sym_type.base_type.param_types

        # Push arguments right-to-left, tracking total stack size
        stack_size = 0
        num_args = len(expr.args)
        for i, arg in enumerate(reversed(expr.args)):
            arg_idx = num_args - 1 - i  # Index in forward order
            param_type = param_types[arg_idx] if arg_idx < len(param_types) else None

            # Check if we need to convert float to int
            arg_is_float = self._is_float_expr(arg)
            param_is_int = param_type and not self._is_float_type(param_type) and not self._is_long_type(param_type)

            if arg_is_float and param_is_int:
                # Float argument to int parameter - convert float literal at compile time
                if isinstance(arg, ast.FloatLiteral):
                    int_val = int(arg.value)
                    self.ctx.emit_instr("ld", f"HL,{int_val}")
                else:
                    # Runtime conversion needed
                    self.gen_expr(arg, force_long=True)
                    self._call_runtime("__ftoi")  # Convert DEHL float to HL int
                self.ctx.emit_instr("push", "HL")
                stack_size += 2
            else:
                # Normal argument handling
                # Check if parameter type or argument expression is 64-bit
                arg_is_ll = self._is_long_long_expr(arg)
                param_is_ll = param_type and self._is_long_long_type(param_type)
                if param_is_ll:
                    # Parameter is 64-bit: push 4 words (8 bytes)
                    param_unsigned = (param_type and isinstance(param_type, ast.BasicType)
                                     and param_type.is_signed == False)
                    self._push_long_long_arg(arg, force_unsigned=param_unsigned)
                    stack_size += 8
                    continue
                elif arg_is_ll and not param_type:
                    # No param type info (variadic/unprototyped): push as 64-bit
                    self._push_long_long_arg(arg, force_unsigned=False)
                    stack_size += 8
                    continue

                # Check if parameter type or argument expression is 32-bit
                # When arg is long long but param is smaller, treat as long for gen_expr
                # (gen_identifier loads low 32 bits into DEHL for long long)
                arg_is_long = self._is_long_expr(arg)
                param_is_long = param_type and self._is_long_type(param_type)
                param_is_float = param_type and self._is_float_type(param_type)
                param_is_small = param_type and not param_is_long and not param_is_float and not self._is_long_long_type(param_type)
                # When arg is long long but param is smaller, truncate
                if arg_is_ll and param_is_small:
                    # Truncate 64-bit to 16-bit
                    ll_val = self._try_get_ll_literal_value(arg)
                    if ll_val is not None:
                        # Compile-time truncation
                        self.ctx.emit_instr("ld", f"HL,{ll_val & 0xFFFF}")
                    else:
                        # Runtime: generate 64-bit, load low 16 from __acc64
                        self._gen_64bit_operand(arg, to_tmp=False)
                        self.ctx.emit_instr("ld", "HL,(__acc64)")
                        self.ctx.runtime_used.add("__acc64")
                    self.ctx.emit_instr("push", "HL")
                    stack_size += 2
                    continue
                if arg_is_ll and (param_is_long or param_is_float):
                    # Truncate 64-bit to 32-bit
                    ll_val = self._try_get_ll_literal_value(arg)
                    if ll_val is not None:
                        # Compile-time truncation to 32-bit
                        val32 = ll_val & 0xFFFFFFFF
                        low = val32 & 0xFFFF
                        high = (val32 >> 16) & 0xFFFF
                        self.ctx.emit_instr("ld", f"HL,{low}")
                        self.ctx.emit_instr("ld", f"DE,{high}")
                    else:
                        # Runtime: generate 64-bit, load low 32 from __acc64
                        self._gen_64bit_operand(arg, to_tmp=False)
                        self.ctx.emit_instr("ld", "HL,(__acc64)")
                        self.ctx.emit_instr("ld", "DE,(__acc64+2)")
                        self.ctx.runtime_used.add("__acc64")
                    if param_is_float:
                        is_unsigned = self._is_unsigned_expr(arg)
                        if is_unsigned:
                            self._call_runtime("__uitof")
                        else:
                            self._call_runtime("__itof")
                    self.ctx.emit_instr("push", "DE")
                    self.ctx.emit_instr("push", "HL")
                    stack_size += 4
                    continue
                if arg_is_ll and not arg_is_long:
                    arg_is_long = True  # treat as 32-bit for push below (fallback)
                # Truncate long argument to 16-bit when parameter is small
                if arg_is_long and param_is_small:
                    self.gen_expr(arg, force_long=True)
                    # HL already has the low 16 bits (truncated)
                    if param_type and self._is_bool_type(param_type):
                        self._emit_bool_normalize()
                    self.ctx.emit_instr("push", "HL")
                    stack_size += 2
                    continue
                # Floats are also 32-bit, need to push 4 bytes
                if arg_is_long or param_is_long or arg_is_float or param_is_float:
                    # Compile-time float→int when both arg and param are known.
                    # Under --int=32, int is classified as "long", so the
                    # float→int short-circuit above is skipped; handle it here.
                    if (arg_is_float and param_is_long and not param_is_float
                            and isinstance(arg, ast.FloatLiteral)):
                        int_val = int(arg.value)
                        val32 = int_val & 0xFFFFFFFF
                        self.ctx.emit_instr("ld", f"HL,{val32 & 0xFFFF}")
                        self.ctx.emit_instr("ld", f"DE,{(val32 >> 16) & 0xFFFF}")
                    else:
                        self.gen_expr(arg, force_long=True)
                    # Extend to 32-bit if argument is smaller than parameter
                    if param_is_long and not arg_is_long and not arg_is_float:
                        is_signed = not self._is_unsigned_expr(arg)
                        self._extend_hl_to_dehl(is_signed)
                    # Convert float to int if needed (e.g. --int=32 where int is long)
                    if (arg_is_float and param_is_long and not param_is_float
                            and not isinstance(arg, ast.FloatLiteral)):
                        self._call_runtime("__ftoi")
                    # Convert int to float if needed
                    if param_is_float and not arg_is_float:
                        # Integer argument to float parameter - convert to IEEE 754
                        if not arg_is_long:
                            # 16-bit int needs sign extension first
                            is_signed = not self._is_unsigned_expr(arg)
                            self._extend_hl_to_dehl(is_signed)
                        # Now DEHL contains signed/unsigned 32-bit int
                        is_unsigned = self._is_unsigned_expr(arg)
                        if is_unsigned:
                            self._call_runtime("__uitof")
                        else:
                            self._call_runtime("__itof")
                    # Push 32-bit value: high word (DE) first, then low word (HL)
                    self.ctx.emit_instr("push", "DE")
                    self.ctx.emit_instr("push", "HL")
                    stack_size += 4
                else:
                    # Check if argument is a struct type (pass by value)
                    arg_type = self._get_expr_type(arg)
                    if isinstance(arg_type, ast.StructType):
                        struct_size = self._type_size(arg_type)
                        push_size = (struct_size + 1) & ~1  # Round up to word boundary
                        # Get the address of the struct data
                        self._gen_address(arg)
                        # HL = address of struct data
                        # Push from high end to low end (so low bytes end up at lower stack address)
                        if push_size > 0:
                            self.ctx.emit_instr("ld", f"DE,{push_size - 2}")
                            self.ctx.emit_instr("add", "HL,DE")
                            # HL points to last word
                            remaining = push_size
                            while remaining > 0:
                                self.ctx.emit_instr("ld", "E,(HL)")
                                self.ctx.emit_instr("inc", "HL")
                                self.ctx.emit_instr("ld", "D,(HL)")
                                self.ctx.emit_instr("dec", "HL")
                                self.ctx.emit_instr("push", "DE")
                                remaining -= 2
                                if remaining > 0:
                                    self.ctx.emit_instr("dec", "HL")
                                    self.ctx.emit_instr("dec", "HL")
                        stack_size += push_size
                    else:
                        # Variadic under --int=32: C's default-argument-
                        # promotion widens char/short/int to int, which is
                        # 4 bytes here.  Pointers still occupy pointer_size
                        # bytes (2 on Z80), so only widen basic integer
                        # types.
                        is_ptr_like = isinstance(arg_type,
                                                 (ast.PointerType, ast.ArrayType))
                        widen_to_int32 = (param_type is None
                                and self.type_config.int_size == 4
                                and not isinstance(arg_type, ast.StructType)
                                and not is_ptr_like)
                        if widen_to_int32:
                            self.gen_expr(arg, force_long=True)
                            # If the expression produced only a 16-bit
                            # value (HL), sign- or zero-extend into DE.
                            if not arg_is_long and not arg_is_float and not arg_is_ll:
                                is_signed = not self._is_unsigned_expr(arg)
                                self._extend_hl_to_dehl(is_signed)
                            if param_type and self._is_bool_type(param_type):
                                self._emit_bool_normalize()
                            self.ctx.emit_instr("push", "DE")
                            self.ctx.emit_instr("push", "HL")
                            stack_size += 4
                        else:
                            self.gen_expr(arg)
                            # _Bool parameter normalization (C99 6.3.1.2)
                            if param_type and self._is_bool_type(param_type):
                                self._emit_bool_normalize()
                            self.ctx.emit_instr("push", "HL")
                            stack_size += 2

        # Call the function
        if isinstance(expr.func, ast.Identifier):
            # Check if this is a direct function call or a call through a function pointer variable
            func_sym = self.ctx.lookup(expr.func.name)
            is_direct_function = (
                expr.func.name in self.ctx.function_names or
                (func_sym and isinstance(func_sym.sym_type, ast.FunctionType))
            )
            if is_direct_function:
                # Record EXTRN for any function we don't define locally.  The
                # `function_names` set covers definitions and prototypes seen
                # in this TU; func_sym alone (with FunctionType) means we
                # have a declaration but no body — still external.
                if (expr.func.name not in self.ctx.function_names
                        and not (func_sym and getattr(func_sym, 'has_body', False))):
                    self.ctx.implicit_externs.add(f"_{expr.func.name}")
                self.ctx.emit_instr("call", f"_{expr.func.name}")
            else:
                # Function pointer variable - load pointer and call indirectly
                self.gen_expr(expr.func)
                self._call_runtime("__callhl")
        else:
            # Indirect call through complex expression
            self.gen_expr(expr.func)
            self._call_runtime("__callhl")

        # Clean up stack (caller cleanup)
        # Check if return value is 64-bit or 32-bit to preserve appropriately
        return_is_64bit = False
        return_is_32bit = False
        return_type = self._get_expr_type(expr)
        if self._is_long_long_type(return_type):
            return_is_64bit = True
        elif self._is_long_type(return_type) or self._is_float_type(return_type):
            return_is_32bit = True

        # ABI bridge: under --int=32 the caller expects a 32-bit int back in
        # DEHL, but libc was assembled assuming int=16 and only fills HL.
        # For calls to functions not defined in this translation unit and
        # whose declared return type is int (now 4 bytes), extend HL into DE
        # before the stack-preserving cleanup runs.  This is a no-op for
        # callees that already follow the 32-bit convention, since their DE
        # is the sign- or zero-extension of HL by definition.  Floats and
        # explicit longs still come back as a real 32-bit DEHL from the
        # callee (libc float/long handlers are 32-bit aware).
        needs_int_widen = False
        if (return_is_32bit and self.type_config.int_size == 4
                and isinstance(return_type, ast.BasicType)
                and return_type.name == "int"
                and isinstance(expr.func, ast.Identifier)
                and expr.func.name not in self.ctx.function_names):
            needs_int_widen = True
        if needs_int_widen:
            return_signed = (return_type.is_signed is None or return_type.is_signed)
            if return_signed:
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("rla")
                self.ctx.emit_instr("sbc", "A,A")
                self.ctx.emit_instr("ld", "D,A")
                self.ctx.emit_instr("ld", "E,A")
            else:
                self.ctx.emit_instr("ld", "DE,0")

        if stack_size > 0:
            if return_is_64bit:
                # Return value in __acc64 - registers are free, just clean stack
                self.ctx.emit_instr("ld", f"HL,{stack_size}")
                self.ctx.emit_instr("add", "HL,SP")
                self.ctx.emit_instr("ld", "SP,HL")
            elif return_is_32bit:
                # Return value in DEHL - need to preserve both while cleaning stack
                # Save DE to BC, clean up stack, restore DE
                self.ctx.emit_instr("ld", "B,D")
                self.ctx.emit_instr("ld", "C,E")
                # Now HL has low word, BC has high word
                # Adjust SP to clean up arguments
                self.ctx.emit_instr("ex", "DE,HL")  # Save low word in DE
                self.ctx.emit_instr("ld", f"HL,{stack_size}")
                self.ctx.emit_instr("add", "HL,SP")
                self.ctx.emit_instr("ld", "SP,HL")
                self.ctx.emit_instr("ex", "DE,HL")  # Restore low word to HL
                # Restore high word from BC to DE
                self.ctx.emit_instr("ld", "D,B")
                self.ctx.emit_instr("ld", "E,C")
            elif stack_size <= 6:
                for _ in range(stack_size // 2):
                    self.ctx.emit_instr("pop", "DE")  # Discard
            else:
                self.ctx.emit_instr("ex", "DE,HL")  # Save return value
                self.ctx.emit_instr("ld", f"HL,{stack_size}")
                self.ctx.emit_instr("add", "HL,SP")
                self.ctx.emit_instr("ld", "SP,HL")
                self.ctx.emit_instr("ex", "DE,HL")  # Restore return value

        # For 64-bit return, load low 32 bits from __acc64 into DEHL
        if return_is_64bit:
            self.ctx.runtime_used.add("__acc64")
            self.ctx.emit_instr("ld", "HL,(__acc64)")
            self.ctx.emit_instr("ld", "DE,(__acc64+2)")

    def _push_long_long_arg(self, arg: ast.Expression, force_unsigned: bool = False) -> None:
        """Push an 8-byte (long long) argument onto the stack (4 words, high to low)."""
        if isinstance(arg, ast.IntLiteral):
            # Constant: split into 4 words
            val = arg.value & 0xFFFFFFFFFFFFFFFF
            w0 = val & 0xFFFF          # lowest word
            w1 = (val >> 16) & 0xFFFF
            w2 = (val >> 32) & 0xFFFF
            w3 = (val >> 48) & 0xFFFF  # highest word
            # Push high to low (highest word first)
            self.ctx.emit_instr("ld", f"HL,{w3}")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", f"HL,{w2}")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", f"HL,{w1}")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", f"HL,{w0}")
            self.ctx.emit_instr("push", "HL")
        elif isinstance(arg, ast.Identifier):
            sym = self.ctx.lookup(arg.name)
            if sym and self._is_long_long_type(sym.sym_type):
                # Variable IS 64-bit - load to __acc64 and push
                if sym.is_global:
                    label = sym.label()
                    self.ctx.emit_instr("ld", f"HL,{label}")
                elif sym.uses_shared_storage:
                    self.ctx.emit_instr("ld", f"HL,??AUTO+{sym.shared_offset}")
                else:
                    # Stack frame parameter/local: IX+offset
                    off = sym.offset
                    self.ctx.emit_instr("push", "IX")
                    self.ctx.emit_instr("pop", "HL")
                    self.ctx.emit_instr("ld", f"DE,{off}")
                    self.ctx.emit_instr("add", "HL,DE")
                self._call_runtime("__load64")
                self._call_runtime("__push64_acc")
            else:
                # Non-64-bit variable: evaluate and extend to 64 bits
                expr_is_long = self._is_long_expr(arg) or self._is_float_expr(arg)
                expr_unsigned = self._is_unsigned_expr(arg)
                if expr_is_long:
                    self.gen_expr(arg, force_long=True)
                else:
                    self.gen_expr(arg)
                    # Extend HL to DEHL based on expression type
                    self._extend_hl_to_dehl(not expr_unsigned)
                # Extend DEHL to 64-bit based on force_unsigned or expr type
                is_unsigned = force_unsigned or expr_unsigned
                if is_unsigned:
                    self._call_runtime("__zext64")
                else:
                    self._call_runtime("__sext64")
                self._call_runtime("__push64_acc")
        elif isinstance(arg, ast.UnaryOp) and arg.op == "-" and isinstance(arg.operand, ast.IntLiteral):
            # Negative constant
            val = (-arg.operand.value) & 0xFFFFFFFFFFFFFFFF
            w0 = val & 0xFFFF
            w1 = (val >> 16) & 0xFFFF
            w2 = (val >> 32) & 0xFFFF
            w3 = (val >> 48) & 0xFFFF
            self.ctx.emit_instr("ld", f"HL,{w3}")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", f"HL,{w2}")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", f"HL,{w1}")
            self.ctx.emit_instr("push", "HL")
            self.ctx.emit_instr("ld", f"HL,{w0}")
            self.ctx.emit_instr("push", "HL")
        else:
            # Check if expression itself produces a 64-bit result
            # (e.g. a call to a function returning long long)
            expr_is_ll = self._is_long_long_expr(arg)
            if expr_is_ll:
                # Expression computes 64-bit result in __acc64
                self.gen_expr(arg, force_long=True)
                self._call_runtime("__push64_acc")
                return

            # Expression that returns a value - evaluate and extend to 64 bits
            expr_is_long = self._is_long_expr(arg) or self._is_float_expr(arg)
            expr_unsigned = self._is_unsigned_expr(arg)
            if expr_is_long:
                # Already produces 32-bit result in DEHL
                self.gen_expr(arg, force_long=True)
            else:
                # 16-bit result in HL - extend to DEHL based on expr type
                self.gen_expr(arg)
                self._extend_hl_to_dehl(not expr_unsigned)
            # Extend DEHL to 64-bit based on force_unsigned or expr type
            is_unsigned = force_unsigned or expr_unsigned
            if is_unsigned:
                self._call_runtime("__zext64")
            else:
                self._call_runtime("__sext64")
            self._call_runtime("__push64_acc")

    def gen_ternary(self, expr: ast.TernaryOp) -> None:
        """Generate code for ternary conditional."""
        else_label = self.ctx.new_label("TERN_E")
        end_label = self.ctx.new_label("TERN_END")

        # Check if either branch is 32-bit - both branches must match width
        true_is_long = self._is_long_expr(expr.true_expr) or self._is_float_expr(expr.true_expr)
        false_is_long = self._is_long_expr(expr.false_expr) or self._is_float_expr(expr.false_expr)
        need_long = true_is_long or false_is_long

        cond_is_32 = self._is_float_expr(expr.condition) or self._is_long_expr(expr.condition)
        self.gen_expr(expr.condition, force_long=cond_is_32)
        self._emit_condition_test(expr.condition)
        self.ctx.emit_instr("jp", f"Z,{else_label}")

        self.gen_expr(expr.true_expr, force_long=need_long)
        if need_long and not true_is_long:
            is_signed = not self._is_unsigned_expr(expr.true_expr)
            self._extend_hl_to_dehl(is_signed)
        self.ctx.emit_instr("jp", end_label)

        self.ctx.emit_label(else_label)
        self.gen_expr(expr.false_expr, force_long=need_long)
        if need_long and not false_is_long:
            is_signed = not self._is_unsigned_expr(expr.false_expr)
            self._extend_hl_to_dehl(is_signed)

        self.ctx.emit_label(end_label)

    def gen_cast(self, expr: ast.Cast, force_long: bool = False) -> None:
        """Generate code for cast expression with proper type conversion."""
        source_type = self._get_expr_type(expr.expr)
        target_type = expr.target_type
        target_is_long = self._is_long_type(target_type) or force_long
        source_is_long = self._is_long_expr(expr.expr)
        source_is_float = self._is_float_expr(expr.expr)
        target_is_float = self._is_float_type(target_type)

        # Constant fold: cast of integer literal can be computed at compile time
        # This avoids peephole issues with LD HL,N; LD A,L being collapsed
        target_is_64 = self._is_long_long_type(target_type)
        if (isinstance(expr.expr, (ast.IntLiteral, ast.CharLiteral)) and not target_is_float
                and not source_is_float and not target_is_64):
            # _Bool constant fold: normalize to 0 or 1 (C99 6.3.1.2)
            if self._is_bool_type(target_type):
                val = 0 if expr.expr.value == 0 else 1
                self.ctx.emit_instr("ld", f"HL,{val}")
                return
            target_size = self._type_size(target_type)
            target_signed = self._is_signed_type(target_type)
            val = expr.expr.value
            if target_size == 1:
                val = val & 0xFF
                if target_signed and val >= 0x80:
                    val -= 0x100
                # Result is a 16-bit value with proper sign extension
                val = val & 0xFFFF
            elif target_size == 2:
                val = val & 0xFFFF
                if target_signed and val >= 0x8000:
                    val -= 0x10000
                val = val & 0xFFFF
            if target_is_long or force_long:
                val32 = val & 0xFFFFFFFF
                if target_signed and val < 0:
                    val32 = val & 0xFFFFFFFF
                self.ctx.emit_instr("ld", f"HL,{val32 & 0xFFFF}")
                self.ctx.emit_instr("ld", f"DE,{(val32 >> 16) & 0xFFFF}")
            else:
                self.ctx.emit_instr("ld", f"HL,{val & 0xFFFF}")
            return

        # Generate the source expression without forcing long -
        # the cast itself handles the extension
        self.gen_expr(expr.expr, force_long=False)

        # _Bool normalization: any non-zero value becomes 1 (C99 6.3.1.2)
        if self._is_bool_type(target_type):
            if source_is_long or source_is_float:
                # 32-bit: check all 4 bytes (DEHL)
                self.ctx.emit_instr("ld", "A,D")
                self.ctx.emit_instr("or", "E")
                self.ctx.emit_instr("or", "H")
                self.ctx.emit_instr("or", "L")
                self.ctx.emit_instr("ld", "HL,0")
                self.ctx.emit_instr("jr", "Z,$+3")
                self.ctx.emit_instr("inc", "L")
            else:
                self._emit_bool_normalize()
            return

        source_size = self._type_size(source_type) if source_type else 2
        target_size = self._type_size(target_type)
        target_signed = self._is_signed_type(target_type)

        source_is_64 = self._is_long_long_expr(expr.expr)

        # Handle float conversions first
        if target_is_float and not source_is_float:
            # Int to float conversion
            # Don't extend long long sources - DEHL already has low 32 bits
            if not source_is_long and not source_is_64:
                # 16-bit int needs sign extension first
                is_signed = self._is_signed_type(source_type) if source_type else True
                self._extend_hl_to_dehl(is_signed)
            # Now DEHL contains 32-bit integer, convert to float
            is_unsigned = source_type and not self._is_signed_type(source_type)
            if is_unsigned:
                self._call_runtime("__uitof")
            else:
                self._call_runtime("__itof")
            return
        elif not target_is_float and source_is_float:
            # Float to int conversion
            self._call_runtime("__ftoi")  # Convert DEHL float to DEHL 32-bit int
            if self._is_long_long_type(target_type):
                # Extend 32-bit result in DEHL to 64-bit in __acc64
                self.ctx.runtime_used.add("__acc64")
                if target_signed:
                    self._call_runtime("__sext64")
                else:
                    self._call_runtime("__zext64")
            elif target_is_long:
                pass  # __ftoi already returns 32-bit result in DEHL
            return

        # Handle narrowing conversions first (before widening if force_long)
        if target_size == 1 and source_size >= 1:
            # Narrowing/same-size to char: ensure H matches target signedness
            if target_signed:
                # Signed char: sign-extend L to HL
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("rlca")      # Move bit 7 to carry
                self.ctx.emit_instr("sbc", "A,A") # A = 0xFF if carry, 0x00 if not
                self.ctx.emit_instr("ld", "H,A")
            else:
                # Unsigned char: zero-extend
                self.ctx.emit_instr("ld", "H,0")
        elif target_size == 2 and source_size >= 4:
            # Narrowing from long/long long to short: just keep HL (DE is discarded)
            pass

        # Handle 64-bit target type
        target_is_64 = self._is_long_long_type(target_type)
        source_is_64 = self._is_long_long_expr(expr.expr)

        if target_is_64 and not source_is_64:
            # Need to extend to 64-bit in __acc64
            self.ctx.runtime_used.add("__acc64")
            if source_size <= 2:
                # From 16-bit or smaller: HL -> DEHL -> __acc64
                self._extend_hl_to_dehl(target_signed)
            # Now we have DEHL, extend to __acc64
            if target_signed:
                self._call_runtime("__sext64")
            else:
                self._call_runtime("__zext64")
            # Reload DEHL from __acc64 so low 32 bits are available to callers
            self.ctx.emit_instr("ld", "HL,(__acc64)")
            self.ctx.emit_instr("ld", "DE,(__acc64+2)")
        elif target_is_long and not source_is_long and not source_is_float and not source_is_64:
            # Direct extension to 32-bit (skip if source is float - already 32-bit)
            # (Don't extend long long sources - DEHL already has low 32 bits)
            is_signed = self._is_signed_type(source_type) if source_type else True
            self._extend_hl_to_dehl(is_signed)
        elif target_size == 2 and source_size == 1:
            # 8-bit to 16-bit: already in HL, but may need sign extension
            # HL already has the value; L is the byte, H might need fixing
            if source_type and self._is_signed_type(source_type):
                # Sign extend L to HL
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("rlca")  # Get sign bit into carry
                self.ctx.emit_instr("sbc", "A,A")  # A = 0xFF if sign, 0x00 if not
                self.ctx.emit_instr("ld", "H,A")

    def _is_signed_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is signed."""
        if t is None:
            return True  # Default to signed
        if isinstance(t, ast.BasicType):
            # Check if type has explicit is_signed attribute
            if hasattr(t, 'is_signed') and t.is_signed is not None:
                return t.is_signed
            # Unsigned types are not signed
            if t.name.startswith("unsigned") or t.name == "_Bool" or t.name == "bool":
                return False
            # char, short, int, long without unsigned prefix are signed
            return True
        return True  # Default to signed for other types

    def _emit_char_to_hl(self, is_signed: bool) -> None:
        """Extend 8-bit value in L to full HL with sign or zero extension."""
        if is_signed:
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("rlca")
            self.ctx.emit_instr("sbc", "A,A")
            self.ctx.emit_instr("ld", "H,A")
        else:
            self.ctx.emit_instr("ld", "H,0")

    def gen_index(self, expr: ast.Index) -> None:
        """Generate code for array indexing."""
        # Generate address, then dereference
        self._gen_address(expr)

        # Check if element is itself an array (multi-dimensional arrays):
        # array-to-pointer decay means we return the address, not a value
        arr_type = self._get_expr_type(expr.array)
        if isinstance(arr_type, (ast.ArrayType, ast.PointerType)):
            if isinstance(arr_type.base_type, ast.ArrayType):
                return  # Address already in HL, no dereference needed
            # Struct/union elements are too big to fit in a register: leave the
            # address in HL so the caller (_gen_struct_copy_from_expr,
            # _gen_struct_assignment, gen_call's struct push) can LDIR from it.
            if isinstance(arr_type.base_type, ast.StructType):
                return

        # Determine element size for proper load
        elem_size = self._get_index_elem_size(expr.array)

        if elem_size == 1:
            # 8-bit element, sign/zero-extend to HL
            elem_signed = True
            arr_type = self._get_expr_type(expr.array)
            if isinstance(arr_type, (ast.ArrayType, ast.PointerType)):
                elem_signed = self._is_signed_type(arr_type.base_type)
            self.ctx.emit_instr("ld", "L,(HL)")
            self._emit_char_to_hl(elem_signed)
        elif elem_size == 8:
            # 64-bit element: HL has address, load into __acc64
            self._call_runtime("__load64")
            self.ctx.runtime_used.add("__acc64")
            # Return low 32 bits in DEHL for use as rvalue
            self.ctx.emit_instr("ld", "HL,(__acc64)")
            self.ctx.emit_instr("ld", "DE,(__acc64+2)")
        elif elem_size == 4:
            # 32-bit element: load into DEHL (DE=high, HL=low)
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "A,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "H,(HL)")
            self.ctx.emit_instr("ld", "L,A")
            # Now HL = high word, DE = low word; need to swap
            self.ctx.emit_instr("ex", "DE,HL")
            # Now HL = low word, DE = high word (correct DEHL format)
        else:
            # 16-bit element
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("ex", "DE,HL")

    def gen_member(self, expr: ast.Member) -> None:
        """Generate code for struct member access."""
        # Handle compound literal member access: ((struct){...}).member
        if isinstance(expr.obj, ast.Compound) and not expr.is_arrow:
            val = self._compound_literal_member_value(expr.obj, expr.member)
            if val is not None:
                member_type = self._get_member_type(expr)
                force_long = member_type and self._type_size(member_type) == 4
                self.gen_expr(val, force_long)
                return

        # Generate address of the member
        self._gen_address(expr)

        # Check if member is an array - arrays decay to pointers (return address, not value)
        member_type = self._get_member_type(expr)
        if isinstance(member_type, ast.ArrayType):
            # Array member: address is already in HL, just return it
            return

        # Check for bitfield - use bitfield read if applicable
        bf = self._get_bitfield_info(expr)
        if bf is not None:
            self._gen_bitfield_read(bf)
            return

        # Determine member size and load appropriately
        member_size = self._type_size(member_type) if member_type else 2
        if member_size == 1:
            self.ctx.emit_instr("ld", "L,(HL)")
            self._emit_char_to_hl(self._is_signed_type(member_type))
        elif member_size == 8:
            # 64-bit member: HL has address, call __load64 to load into __acc64
            self._call_runtime("__load64")
            self.ctx.runtime_used.add("__acc64")
            # Return low 32 bits in DEHL for use as rvalue
            self.ctx.emit_instr("ld", "HL,(__acc64)")
            self.ctx.emit_instr("ld", "DE,(__acc64+2)")
        elif member_size == 4:
            # 32-bit member: load into DEHL (DE=high, HL=low)
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "A,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "H,(HL)")
            self.ctx.emit_instr("ld", "L,A")
            self.ctx.emit_instr("ex", "DE,HL")
        else:
            # 16-bit member
            self.ctx.emit_instr("ld", "E,(HL)")
            self.ctx.emit_instr("inc", "HL")
            self.ctx.emit_instr("ld", "D,(HL)")
            self.ctx.emit_instr("ex", "DE,HL")

    def _get_bitfield_info(self, expr: ast.Member) -> BitfieldInfo | None:
        """Return BitfieldInfo if this member is a bitfield, else None."""
        struct_type = self._get_expr_type(expr.obj)
        if isinstance(struct_type, ast.PointerType):
            struct_type = struct_type.base_type
        elif isinstance(struct_type, ast.ArrayType):
            struct_type = struct_type.base_type
        if not isinstance(struct_type, ast.StructType):
            return None
        # Check registered bitfield info by struct name
        if struct_type.name:
            key = (struct_type.name, expr.member)
            bf = self.ctx.bitfield_info.get(key)
            if bf is not None:
                return bf
        # Check inline members for bitfield info
        if struct_type.members:
            for m in struct_type.members:
                if m.name == expr.member and m.bit_width is not None:
                    bf_width = self._eval_const_expr(m.bit_width)
                    if bf_width is not None and bf_width > 0:
                        type_size = self._type_size(m.member_type)
                        if bf_width > type_size * 8:
                            bf_width = type_size * 8
                        # Look up bit_offset from registered info or compute
                        anon_name = struct_type.name or f"__anon_{id(struct_type)}"
                        key = (anon_name, expr.member)
                        bf = self.ctx.bitfield_info.get(key)
                        if bf is not None:
                            return bf
                        # Compute layout to get bit_offset
                        self._compute_struct_layout(
                            anon_name, struct_type.members, struct_type.is_union)
                        return self.ctx.bitfield_info.get(key)
        return None

    def _gen_bitfield_read(self, bf: BitfieldInfo) -> None:
        """Generate code to read a bitfield value. HL = address of storage unit."""
        # Full-width bitfield: just do normal load
        if bf.bit_offset == 0 and bf.bit_width == bf.storage_size * 8:
            if bf.storage_size == 1:
                self.ctx.emit_instr("ld", "L,(HL)")
                self._emit_char_to_hl(bf.is_signed)
            elif bf.storage_size == 4:
                self.ctx.emit_instr("ld", "E,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "D,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "A,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "H,(HL)")
                self.ctx.emit_instr("ld", "L,A")
                self.ctx.emit_instr("ex", "DE,HL")
            else:
                self.ctx.emit_instr("ld", "E,(HL)")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "D,(HL)")
                self.ctx.emit_instr("ex", "DE,HL")
            return

        if bf.storage_size == 1:
            self._gen_bitfield_read_8(bf)
        elif bf.storage_size == 2:
            self._gen_bitfield_read_16(bf)
        elif bf.storage_size == 4:
            self._gen_bitfield_read_32(bf)

    def _gen_bitfield_read_8(self, bf: BitfieldInfo) -> None:
        """Read bitfield from 1-byte storage unit. HL = address."""
        self.ctx.emit_instr("ld", "A,(HL)")
        # Shift right by bit_offset
        for _ in range(bf.bit_offset):
            self.ctx.emit_instr("srl", "A")
        # Mask to bit_width
        mask = (1 << bf.bit_width) - 1
        self.ctx.emit_instr("and", str(mask))
        self.ctx.emit_instr("ld", "L,A")
        # Sign extend from bit_width to 16 bits
        if bf.is_signed and bf.bit_width < 16:
            self._emit_bitfield_sign_extend_hl(bf.bit_width)
        else:
            self.ctx.emit_instr("ld", "H,0")

    def _gen_bitfield_read_16(self, bf: BitfieldInfo) -> None:
        """Read bitfield from 2-byte storage unit. HL = address."""
        self.ctx.emit_instr("ld", "E,(HL)")
        self.ctx.emit_instr("inc", "HL")
        self.ctx.emit_instr("ld", "D,(HL)")
        self.ctx.emit_instr("ex", "DE,HL")
        # Shift right by bit_offset
        if bf.bit_offset > 0:
            if bf.bit_offset <= 3:
                for _ in range(bf.bit_offset):
                    self.ctx.emit_instr("srl", "H")
                    self.ctx.emit_instr("rr", "L")
            else:
                self.ctx.emit_instr("ld", f"B,{bf.bit_offset}")
                lbl = self.ctx.new_label("BSH")
                self.ctx.emit_label(lbl)
                self.ctx.emit_instr("srl", "H")
                self.ctx.emit_instr("rr", "L")
                self.ctx.emit_instr("djnz", lbl)
        # Mask to bit_width
        mask = (1 << bf.bit_width) - 1
        low_mask = mask & 0xFF
        high_mask = (mask >> 8) & 0xFF
        if high_mask == 0:
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("and", str(low_mask))
            self.ctx.emit_instr("ld", "L,A")
        else:
            self.ctx.emit_instr("ld", "A,L")
            self.ctx.emit_instr("and", str(low_mask))
            self.ctx.emit_instr("ld", "L,A")
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("and", str(high_mask))
            self.ctx.emit_instr("ld", "H,A")
        # Sign extend
        if bf.is_signed and bf.bit_width < 16:
            self._emit_bitfield_sign_extend_hl(bf.bit_width)
        elif high_mask == 0:
            self.ctx.emit_instr("ld", "H,0")

    def _gen_bitfield_read_32(self, bf: BitfieldInfo) -> None:
        """Read bitfield from 4-byte storage unit. HL = address.
        Result in DEHL. Only handles simple cases (small bit_offset)."""
        # Load 32-bit value into DEHL
        self.ctx.emit_instr("ld", "E,(HL)")
        self.ctx.emit_instr("inc", "HL")
        self.ctx.emit_instr("ld", "D,(HL)")
        self.ctx.emit_instr("inc", "HL")
        self.ctx.emit_instr("ld", "A,(HL)")
        self.ctx.emit_instr("inc", "HL")
        self.ctx.emit_instr("ld", "H,(HL)")
        self.ctx.emit_instr("ld", "L,A")
        self.ctx.emit_instr("ex", "DE,HL")
        # Now DEHL = value (DE=high, HL=low)
        # Shift right by bit_offset
        if bf.bit_offset > 0:
            self.ctx.emit_instr("ld", f"B,{bf.bit_offset}")
            lbl = self.ctx.new_label("BSH")
            self.ctx.emit_label(lbl)
            self.ctx.emit_instr("srl", "D")
            self.ctx.emit_instr("rr", "E")
            self.ctx.emit_instr("rr", "H")
            self.ctx.emit_instr("rr", "L")
            self.ctx.emit_instr("djnz", lbl)
        # Mask to bit_width
        mask = (1 << bf.bit_width) - 1
        # Apply mask to DEHL
        self.ctx.emit_instr("ld", "A,L")
        self.ctx.emit_instr("and", str(mask & 0xFF))
        self.ctx.emit_instr("ld", "L,A")
        self.ctx.emit_instr("ld", "A,H")
        self.ctx.emit_instr("and", str((mask >> 8) & 0xFF))
        self.ctx.emit_instr("ld", "H,A")
        self.ctx.emit_instr("ld", "A,E")
        self.ctx.emit_instr("and", str((mask >> 16) & 0xFF))
        self.ctx.emit_instr("ld", "E,A")
        self.ctx.emit_instr("ld", "A,D")
        self.ctx.emit_instr("and", str((mask >> 24) & 0xFF))
        self.ctx.emit_instr("ld", "D,A")
        # Sign extend for 32-bit result
        if bf.is_signed and bf.bit_width < 32:
            self._emit_bitfield_sign_extend_dehl(bf.bit_width)

    def _emit_bitfield_sign_extend_hl(self, bit_width: int) -> None:
        """Sign-extend value in HL from bit_width bits to 16 bits."""
        if bit_width >= 16:
            return
        lbl_pos = self.ctx.new_label("BSE")
        lbl_done = self.ctx.new_label("BSE")
        if bit_width <= 8:
            # Sign bit is in L at position bit_width-1
            self.ctx.emit_instr("bit", f"{bit_width - 1},L")
            self.ctx.emit_instr("jr", f"Z,{lbl_pos}")
            # Negative: fill upper bits
            upper_l = (~((1 << bit_width) - 1)) & 0xFF
            if upper_l:
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("or", str(upper_l))
                self.ctx.emit_instr("ld", "L,A")
            self.ctx.emit_instr("ld", "H,255")
            self.ctx.emit_instr("jr", lbl_done)
            self.ctx.emit_label(lbl_pos)
            self.ctx.emit_instr("ld", "H,0")
            self.ctx.emit_label(lbl_done)
        else:
            # bit_width 9-15: sign bit is in H
            h_bit = bit_width - 9
            self.ctx.emit_instr("bit", f"{h_bit},H")
            self.ctx.emit_instr("jr", f"Z,{lbl_pos}")
            upper_h = (~((1 << (bit_width - 8)) - 1)) & 0xFF
            if upper_h:
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("or", str(upper_h))
                self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_label(lbl_pos)
            # Upper bits already 0 from mask

    def _emit_bitfield_sign_extend_dehl(self, bit_width: int) -> None:
        """Sign-extend value in DEHL from bit_width bits to 32 bits."""
        if bit_width >= 32:
            return
        lbl_done = self.ctx.new_label("BSE")
        # Determine which byte contains the sign bit
        if bit_width <= 8:
            self.ctx.emit_instr("bit", f"{bit_width - 1},L")
            self.ctx.emit_instr("jr", f"Z,{lbl_done}")
            upper_l = (~((1 << bit_width) - 1)) & 0xFF
            if upper_l:
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("or", str(upper_l))
                self.ctx.emit_instr("ld", "L,A")
            self.ctx.emit_instr("ld", "H,255")
            self.ctx.emit_instr("ld", "E,255")
            self.ctx.emit_instr("ld", "D,255")
        elif bit_width <= 16:
            sign_bit = bit_width - 9
            self.ctx.emit_instr("bit", f"{sign_bit},H")
            self.ctx.emit_instr("jr", f"Z,{lbl_done}")
            upper_h = (~((1 << (bit_width - 8)) - 1)) & 0xFF
            if upper_h:
                self.ctx.emit_instr("ld", "A,H")
                self.ctx.emit_instr("or", str(upper_h))
                self.ctx.emit_instr("ld", "H,A")
            self.ctx.emit_instr("ld", "E,255")
            self.ctx.emit_instr("ld", "D,255")
        elif bit_width <= 24:
            sign_bit = bit_width - 17
            self.ctx.emit_instr("bit", f"{sign_bit},E")
            self.ctx.emit_instr("jr", f"Z,{lbl_done}")
            upper_e = (~((1 << (bit_width - 16)) - 1)) & 0xFF
            if upper_e:
                self.ctx.emit_instr("ld", "A,E")
                self.ctx.emit_instr("or", str(upper_e))
                self.ctx.emit_instr("ld", "E,A")
            self.ctx.emit_instr("ld", "D,255")
        else:
            sign_bit = bit_width - 25
            self.ctx.emit_instr("bit", f"{sign_bit},D")
            self.ctx.emit_instr("jr", f"Z,{lbl_done}")
            upper_d = (~((1 << (bit_width - 24)) - 1)) & 0xFF
            if upper_d:
                self.ctx.emit_instr("ld", "A,D")
                self.ctx.emit_instr("or", str(upper_d))
                self.ctx.emit_instr("ld", "D,A")
        self.ctx.emit_label(lbl_done)

    def _gen_bitfield_write(self, expr: ast.BinaryOp, bf: BitfieldInfo) -> None:
        """Generate code for bitfield member assignment. RHS value is in HL (or DEHL for 32-bit).
        expr.left is the Member, expr.right is the value expression."""
        # Full-width bitfield: just do normal store
        if bf.bit_offset == 0 and bf.bit_width == bf.storage_size * 8:
            if bf.storage_size == 4:
                # 32-bit: save DEHL, store, restore
                self.ctx.emit_instr("push", "DE")
                self.ctx.emit_instr("push", "HL")
                self.ctx.emit_instr("push", "DE")
                self.ctx.emit_instr("push", "HL")
                self._gen_address(expr.left)
                self.ctx.emit_instr("ex", "DE,HL")
                self.ctx.emit_instr("pop", "HL")
                self.ctx.emit_instr("ex", "DE,HL")
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("pop", "DE")
                self.ctx.emit_instr("ld", "(HL),E")
                self.ctx.emit_instr("inc", "HL")
                self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("pop", "HL")
                self.ctx.emit_instr("pop", "DE")
            else:
                self.ctx.emit_instr("push", "HL")
                self._gen_address(expr.left)
                self.ctx.emit_instr("pop", "DE")
                if bf.storage_size == 1:
                    self.ctx.emit_instr("ld", "(HL),E")
                else:
                    self.ctx.emit_instr("ld", "(HL),E")
                    self.ctx.emit_instr("inc", "HL")
                    self.ctx.emit_instr("ld", "(HL),D")
                self.ctx.emit_instr("ex", "DE,HL")
            return

        if bf.storage_size == 1:
            self._gen_bitfield_write_8(expr, bf)
        elif bf.storage_size == 2:
            self._gen_bitfield_write_16(expr, bf)
        elif bf.storage_size == 4:
            self._gen_bitfield_write_32(expr, bf)

    def _gen_bitfield_write_8(self, expr: ast.BinaryOp, bf: BitfieldInfo) -> None:
        """Write bitfield in 1-byte storage unit. Value in HL."""
        mask = (1 << bf.bit_width) - 1
        clear_mask = (~(mask << bf.bit_offset)) & 0xFF

        # Save original value for return
        self.ctx.emit_instr("push", "HL")
        # Mask and shift the value
        self.ctx.emit_instr("ld", "A,L")
        self.ctx.emit_instr("and", str(mask))
        for _ in range(bf.bit_offset):
            self.ctx.emit_instr("add", "A,A")
        self.ctx.emit_instr("ld", "C,A")  # C = shifted masked value
        self.ctx.emit_instr("push", "BC")
        # Get address
        self._gen_address(expr.left)
        # Read current byte, clear bitfield bits, OR in new value
        self.ctx.emit_instr("ld", "A,(HL)")
        self.ctx.emit_instr("and", str(clear_mask))
        self.ctx.emit_instr("pop", "BC")
        self.ctx.emit_instr("or", "C")
        self.ctx.emit_instr("ld", "(HL),A")
        # Return original value
        self.ctx.emit_instr("pop", "HL")

    def _gen_bitfield_write_16(self, expr: ast.BinaryOp, bf: BitfieldInfo) -> None:
        """Write bitfield in 2-byte storage unit. Value in HL."""
        mask = (1 << bf.bit_width) - 1
        clear_mask = (~(mask << bf.bit_offset)) & 0xFFFF

        # Save original value for return
        self.ctx.emit_instr("push", "HL")
        # Mask value to bit_width
        low_mask = mask & 0xFF
        high_mask = (mask >> 8) & 0xFF
        self.ctx.emit_instr("ld", "A,L")
        self.ctx.emit_instr("and", str(low_mask))
        self.ctx.emit_instr("ld", "L,A")
        if high_mask == 0:
            self.ctx.emit_instr("ld", "H,0")
        else:
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("and", str(high_mask))
            self.ctx.emit_instr("ld", "H,A")
        # Shift left by bit_offset
        if bf.bit_offset > 0:
            if bf.bit_offset <= 3:
                for _ in range(bf.bit_offset):
                    self.ctx.emit_instr("add", "HL,HL")
            else:
                self.ctx.emit_instr("ld", f"B,{bf.bit_offset}")
                lbl = self.ctx.new_label("BSH")
                self.ctx.emit_label(lbl)
                self.ctx.emit_instr("add", "HL,HL")
                self.ctx.emit_instr("djnz", lbl)
        # Save shifted value
        self.ctx.emit_instr("push", "HL")
        # Get address of storage unit
        self._gen_address(expr.left)
        # Load current 16-bit value into DE
        self.ctx.emit_instr("ld", "E,(HL)")
        self.ctx.emit_instr("inc", "HL")
        self.ctx.emit_instr("ld", "D,(HL)")
        # Clear bitfield bits in DE
        self.ctx.emit_instr("ld", "A,E")
        self.ctx.emit_instr("and", str(clear_mask & 0xFF))
        self.ctx.emit_instr("ld", "E,A")
        self.ctx.emit_instr("ld", "A,D")
        self.ctx.emit_instr("and", str((clear_mask >> 8) & 0xFF))
        self.ctx.emit_instr("ld", "D,A")
        # OR with shifted new value
        self.ctx.emit_instr("pop", "BC")  # BC = shifted value
        self.ctx.emit_instr("ld", "A,E")
        self.ctx.emit_instr("or", "C")
        self.ctx.emit_instr("ld", "E,A")
        self.ctx.emit_instr("ld", "A,D")
        self.ctx.emit_instr("or", "B")
        self.ctx.emit_instr("ld", "D,A")
        # Store back: HL points to high byte (addr+1)
        self.ctx.emit_instr("ld", "(HL),D")
        self.ctx.emit_instr("dec", "HL")
        self.ctx.emit_instr("ld", "(HL),E")
        # Return original value
        self.ctx.emit_instr("pop", "HL")

    def _gen_bitfield_write_32(self, expr: ast.BinaryOp, bf: BitfieldInfo) -> None:
        """Write bitfield in 4-byte storage unit. Value in DEHL.
        Uses byte-by-byte read-modify-write via __tmp32."""
        mask = (1 << bf.bit_width) - 1
        clear_mask = (~(mask << bf.bit_offset)) & 0xFFFFFFFF

        # Save original DEHL for return
        self.ctx.emit_instr("push", "DE")
        self.ctx.emit_instr("push", "HL")
        # Mask value to bit_width (DEHL)
        self.ctx.emit_instr("ld", "A,L")
        self.ctx.emit_instr("and", str(mask & 0xFF))
        self.ctx.emit_instr("ld", "L,A")
        self.ctx.emit_instr("ld", "A,H")
        self.ctx.emit_instr("and", str((mask >> 8) & 0xFF))
        self.ctx.emit_instr("ld", "H,A")
        self.ctx.emit_instr("ld", "A,E")
        self.ctx.emit_instr("and", str((mask >> 16) & 0xFF))
        self.ctx.emit_instr("ld", "E,A")
        self.ctx.emit_instr("ld", "A,D")
        self.ctx.emit_instr("and", str((mask >> 24) & 0xFF))
        self.ctx.emit_instr("ld", "D,A")
        # Shift left by bit_offset
        if bf.bit_offset > 0:
            self.ctx.emit_instr("ld", f"B,{bf.bit_offset}")
            lbl = self.ctx.new_label("BSH")
            self.ctx.emit_label(lbl)
            self.ctx.emit_instr("add", "HL,HL")
            self.ctx.emit_instr("rl", "E")
            self.ctx.emit_instr("rl", "D")
            self.ctx.emit_instr("djnz", lbl)
        # Save shifted value to __tmp32
        self.ctx.runtime_used.add("__tmp32")
        self.ctx.emit_instr("ld", "(__tmp32),HL")
        self.ctx.emit_instr("ld", "(__tmp32+2),DE")
        # Get address of storage unit
        self._gen_address(expr.left)
        # HL = base address. Process each byte in-place.
        for i in range(4):
            cm_byte = (clear_mask >> (i * 8)) & 0xFF
            self.ctx.emit_instr("ld", "A,(HL)")
            self.ctx.emit_instr("and", str(cm_byte))
            self.ctx.emit_instr("ld", "C,A")
            self.ctx.emit_instr("ld", f"A,(__tmp32+{i})")
            self.ctx.emit_instr("or", "C")
            self.ctx.emit_instr("ld", "(HL),A")
            if i < 3:
                self.ctx.emit_instr("inc", "HL")
        # Restore original DEHL
        self.ctx.emit_instr("pop", "HL")
        self.ctx.emit_instr("pop", "DE")

    def _get_member_type(self, expr: ast.Member) -> ast.TypeNode | None:
        """Get the type of a struct member."""
        struct_type = self._get_expr_type(expr.obj)
        # For arrow operator or array decay, get the element/base type
        if isinstance(struct_type, ast.PointerType):
            struct_type = struct_type.base_type
        elif isinstance(struct_type, ast.ArrayType):
            struct_type = struct_type.base_type
        if isinstance(struct_type, ast.StructType):
            # Try inline members first
            if struct_type.members:
                for m in struct_type.members:
                    if m.name == expr.member:
                        return m.member_type
                # Search anonymous struct/union members inline
                for m in struct_type.members:
                    if m.name is None and isinstance(m.member_type, ast.StructType):
                        if m.member_type.members:
                            for sm in m.member_type.members:
                                if sm.name == expr.member:
                                    return sm.member_type
            # Then try registered structs
            if struct_type.name and struct_type.name in self.ctx.structs:
                for name, member_type, _ in self.ctx.structs[struct_type.name]:
                    if name == expr.member:
                        return member_type
                # Search anonymous members
                result = self._find_anon_member_type(struct_type.name, expr.member)
                if result is not None:
                    return result
        return None

    def _compound_literal_member_value(self, compound: ast.Compound, member_name: str) -> ast.Expression | None:
        """Extract the value for a specific member from a compound literal.

        Returns the expression to generate for ((struct){...}).member,
        or None if we can't resolve it statically.
        """
        if not isinstance(compound.init, ast.InitializerList):
            return None
        target_type = compound.target_type
        # Resolve struct members
        struct_type = target_type
        if isinstance(struct_type, ast.StructType):
            members = None
            if struct_type.members:
                members = [(m.name, m.member_type) for m in struct_type.members]
            elif struct_type.name and struct_type.name in self.ctx.structs:
                members = [(n, t) for n, t, _ in self.ctx.structs[struct_type.name]]
            if members is None:
                return None

            values = compound.init.values
            # Check for designated initializers
            has_designators = any(isinstance(v, ast.DesignatedInit) for v in values)

            if has_designators:
                # Designated init: find the value by designator name
                for v in values:
                    if isinstance(v, ast.DesignatedInit) and v.designators:
                        desig = v.designators[0]
                        if isinstance(desig, str) and desig == member_name:
                            return v.value
                # Not designated - member is zero-initialized
                return ast.IntLiteral(value=0)
            else:
                # Positional: find member index
                for i, (mname, mtype) in enumerate(members):
                    if mname == member_name:
                        if i < len(values):
                            val = values[i]
                            if isinstance(val, ast.DesignatedInit):
                                val = val.value
                            return val
                        return ast.IntLiteral(value=0)
        return None

    def _materialize_compound_literal(self, compound: ast.Compound) -> str:
        """Materialize a compound literal into DSEG and return its label."""
        label = self.ctx.new_label("CL")
        target_type = compound.target_type
        # For arrays without explicit size, infer from initializer count
        if isinstance(target_type, ast.ArrayType) and target_type.size is None:
            if isinstance(compound.init, ast.InitializerList):
                n = len(compound.init.values)
                target_type = ast.ArrayType(base_type=target_type.base_type,
                                             size=ast.IntLiteral(value=n))
        size = self._type_size(target_type)
        # Queue the data for emission in the data section
        if not hasattr(self.ctx, 'compound_literals'):
            self.ctx.compound_literals = []
        self.ctx.compound_literals.append((label, compound.init, target_type, size))
        return label

    def _get_member_size(self, expr: ast.Member) -> int:
        """Get the size of a struct member."""
        member_type = self._get_member_type(expr)
        if member_type:
            return self._type_size(member_type)
        return 2  # Default to 16-bit

    def _get_member_offset(self, struct_name: str, member_name: str) -> int:
        """Get the offset of a member within a struct.

        Also searches through anonymous struct/union members recursively.
        """
        if struct_name in self.ctx.structs:
            for name, member_type, offset in self.ctx.structs[struct_name]:
                if name == member_name:
                    return offset
            # Search anonymous struct/union members
            result = self._find_anon_member_offset(struct_name, member_name)
            if result is not None:
                return result
        return 0

    def _resolve_member_offset(self, struct_type: ast.StructType, member_name: str) -> int:
        """Get offset of a member, searching inline members first then registry.

        Works for anonymous structs (no tag name) by computing offsets from
        the StructType's inline member list.  Handles bitfield packing.
        """
        # Try inline members first
        if struct_type.members:
            has_bitfields = any(m.bit_width is not None for m in struct_type.members)
            if has_bitfields:
                # Use layout computation for correct bitfield offsets
                anon_name = struct_type.name or f"__anon_{id(struct_type)}"
                members, _, _ = self._compute_struct_layout(
                    anon_name, struct_type.members, struct_type.is_union)
                for name, mtype, moffset in members:
                    if name == member_name:
                        return moffset
                # Search anonymous sub-members
                offset = 0
                bit_pos = 0
                storage_unit_start = 0
                storage_unit_size = 0
                for m in struct_type.members:
                    if m.bit_width is not None:
                        type_size = self._type_size(m.member_type)
                        bf_width = self._eval_const_expr(m.bit_width) or 0
                        if bf_width == 0:
                            if bit_pos > 0 and not struct_type.is_union:
                                offset = storage_unit_start + storage_unit_size
                                bit_pos = 0
                            continue
                        if bit_pos > 0 and storage_unit_size == type_size and bit_pos + bf_width <= type_size * 8:
                            bit_pos += bf_width
                        else:
                            if bit_pos > 0 and not struct_type.is_union:
                                offset = storage_unit_start + storage_unit_size
                            storage_unit_start = offset
                            storage_unit_size = type_size
                            bit_pos = bf_width
                    else:
                        if bit_pos > 0 and not struct_type.is_union:
                            offset = storage_unit_start + storage_unit_size
                            bit_pos = 0
                        if m.name is None and isinstance(m.member_type, ast.StructType):
                            sub_result = self._resolve_member_offset(m.member_type, member_name)
                            if sub_result >= 0:
                                return offset + sub_result
                        if not struct_type.is_union:
                            offset += self._type_size(m.member_type)
                return -1
            else:
                offset = 0
                for m in struct_type.members:
                    if m.name == member_name:
                        return offset
                    if m.name is None and isinstance(m.member_type, ast.StructType):
                        # Anonymous sub-member - search recursively
                        sub_result = self._resolve_member_offset(m.member_type, member_name)
                        if sub_result >= 0:
                            return offset + sub_result
                    if not struct_type.is_union:
                        offset += self._type_size(m.member_type)
        # Fall back to registered structs
        if struct_type.name:
            return self._get_member_offset(struct_type.name, member_name)
        return -1

    def _find_anon_member_offset(self, struct_name: str, member_name: str) -> int | None:
        """Search for a member in anonymous struct/union sub-members.

        Looks at the original AST members to find anonymous struct/union members,
        then searches their sub-members recursively.
        """
        # We need to look at the original struct declaration to find anonymous members
        # Check registered structs for any member whose type is a struct/union
        # that contains the target member name
        if struct_name not in self.ctx.structs:
            return None

        # Look through all registered members for struct/union types that might
        # contain anonymous sub-members. But we need the original AST to find
        # anonymous members. Store them during registration.
        if struct_name in self.ctx.anon_members:
            for anon_type, anon_offset in self.ctx.anon_members[struct_name]:
                # Search this anonymous struct/union for the member
                anon_members = self._get_struct_members(anon_type)
                for name, mtype, moffset in anon_members:
                    if name == member_name:
                        return anon_offset + moffset
                # Recursively search nested anonymous members
                if hasattr(anon_type, 'members') and anon_type.members:
                    for m in anon_type.members:
                        if m.name is None and isinstance(m.member_type, ast.StructType):
                            sub_members = self._get_struct_members(m.member_type)
                            sub_offset = 0 if anon_type.is_union else sum(
                                self._type_size(sm.member_type) for sm in anon_type.members
                                if sm.name is not None and sm is not m
                            )
                            for sname, stype, soffset in sub_members:
                                if sname == member_name:
                                    return anon_offset + soffset
        return None

    def _find_anon_member_type(self, struct_name: str, member_name: str) -> ast.TypeNode | None:
        """Search for a member type in anonymous struct/union sub-members."""
        if struct_name in self.ctx.anon_members:
            for anon_type, anon_offset in self.ctx.anon_members[struct_name]:
                anon_members = self._get_struct_members(anon_type)
                for name, mtype, moffset in anon_members:
                    if name == member_name:
                        return mtype
        return None

    def _get_expr_type(self, expr: ast.Expression) -> ast.TypeNode | None:
        """Try to infer the type of an expression."""
        if isinstance(expr, ast.IntLiteral):
            # Integer literal type depends on suffix and value
            # C standard type promotion for hex/octal:
            #   int -> unsigned int -> long -> unsigned long -> long long -> unsigned long long
            if expr.is_long:
                return ast.BasicType(name="long", is_signed=not expr.is_unsigned)
            val = expr.value
            if expr.is_hex and not expr.is_unsigned:
                if 32768 <= val <= 65535:
                    return ast.BasicType(name="int", is_signed=False)  # unsigned int
                if val > 65535 and val <= 2147483647:
                    return ast.BasicType(name="long", is_signed=True)  # long
                if val > 2147483647 and val <= 4294967295:
                    return ast.BasicType(name="long", is_signed=False)  # unsigned long
                if val > 4294967295:
                    return ast.BasicType(name="long long", is_signed=False)  # unsigned long long
            elif not expr.is_hex and not expr.is_unsigned:
                # Decimal: int -> long -> long long
                if val > 32767 and val <= 2147483647:
                    return ast.BasicType(name="long", is_signed=True)
                if val > 2147483647:
                    return ast.BasicType(name="long long", is_signed=True)
            return ast.BasicType(name="int", is_signed=not expr.is_unsigned)
        elif isinstance(expr, ast.CharLiteral):
            return ast.BasicType(name="char")
        elif isinstance(expr, ast.StringLiteral):
            # String literals are char* (or const char* in C99+)
            return ast.PointerType(base_type=ast.BasicType(name="char"))
        elif isinstance(expr, ast.FloatLiteral):
            return ast.BasicType(name="float" if expr.is_float else "double")
        elif isinstance(expr, ast.BoolLiteral):
            return ast.BasicType(name="bool")
        elif isinstance(expr, ast.Identifier):
            if expr.name in ('__func__', '__FUNCTION__'):
                return ast.PointerType(base_type=ast.BasicType(name="char"))
            # Enum constants: have type int.  Without this, _is_long_expr
            # falls through to _is_long_type(None) → False, and under
            # --int=32 the call site pushes only HL (16 bits) for an enum
            # arg, so variadic printf reads garbage from the next slot.
            if expr.name in self.ctx.enum_constants:
                return ast.BasicType(name="int", is_signed=True)
            sym = self.ctx.lookup(expr.name)
            if sym:
                return sym.sym_type
        elif isinstance(expr, ast.UnaryOp) and expr.op == "*":
            # Dereference - get base type of pointer (or array decayed to pointer)
            ptr_type = self._get_expr_type(expr.operand)
            if isinstance(ptr_type, ast.PointerType):
                return ptr_type.base_type
            elif isinstance(ptr_type, ast.ArrayType):
                return ptr_type.base_type
        elif isinstance(expr, ast.UnaryOp) and expr.op == "&":
            # Address-of: return pointer to operand type
            operand_type = self._get_expr_type(expr.operand)
            if operand_type:
                return ast.PointerType(base_type=operand_type)
        elif isinstance(expr, ast.UnaryOp) and expr.op == "!":
            # Logical NOT always returns int (C99 6.5.3.3)
            return ast.BasicType(name="int")
        elif isinstance(expr, ast.UnaryOp) and expr.op in ("-", "+", "~", "++", "--"):
            # These preserve the operand type.  ++/-- (post or pre) need to
            # be in this list too, otherwise _is_long_expr falls back to
            # _is_long_type(None) → False and the 32-bit binary-op codegen
            # spuriously emits __sext32 over an already-32-bit value.
            return self._get_expr_type(expr.operand)
        elif isinstance(expr, ast.Index):
            # Array indexing: return element type
            array_type = self._get_expr_type(expr.array)
            if isinstance(array_type, ast.ArrayType):
                return array_type.base_type
            elif isinstance(array_type, ast.PointerType):
                return array_type.base_type
        elif isinstance(expr, ast.Member):
            # Member access: return member type
            return self._get_member_type(expr)
        elif isinstance(expr, ast.BinaryOp):
            # For arithmetic/bitwise ops, result type is based on operand types
            left_type = self._get_expr_type(expr.left)
            right_type = self._get_expr_type(expr.right)

            # Pointer arithmetic: ptr ± int → ptr.  Without this, an
            # expression like `*(0 + (unsigned char *)&x)` infers `int` as
            # the result type and gen_assignment emits a 2-byte store at
            # the dereference instead of 1 byte.
            if expr.op in ("+", "-"):
                if isinstance(left_type, (ast.PointerType, ast.ArrayType)):
                    return left_type
                if isinstance(right_type, (ast.PointerType, ast.ArrayType)) and expr.op == "+":
                    return right_type

            # For shift operations, result type is the promoted LEFT operand (C99 6.5.7)
            # Integer promotion: char promotes to int (6.3.1.1)
            if expr.op in ("<<", ">>"):
                if left_type and isinstance(left_type, ast.BasicType) and left_type.name == 'char':
                    return ast.BasicType(name="int")  # char promotes to int
                if left_type:
                    return left_type
                return ast.BasicType(name="int")  # Default to int for literal 1

            # Apply usual arithmetic conversions (C99 6.3.1.8):
            # 1. float/double wins over integer types
            # 2. long long wins over long/int
            # 3. long wins over int
            left_is_float = self._is_float_type(left_type)
            right_is_float = self._is_float_type(right_type)
            if left_is_float or right_is_float:
                return ast.BasicType(name="double")

            # "long long wins" — only when an operand is actually named
            # "long long".  Don't promote just because long happens to be
            # 8 bytes under --long=64; that would name the result
            # "long long" and pull it into the 64-bit codegen path even
            # when both operands were `long`.
            def _is_named_ll(t):
                return isinstance(t, ast.BasicType) and t.name == "long long"
            if _is_named_ll(left_type) or _is_named_ll(right_type):
                left_unsigned = isinstance(left_type, ast.BasicType) and left_type.is_signed == False
                right_unsigned = isinstance(right_type, ast.BasicType) and right_type.is_signed == False
                return ast.BasicType(name="long long", is_signed=not (left_unsigned or right_unsigned))

            # "long wins over int" — but only when an operand is actually
            # named "long" (or short/long long via earlier branches).  Don't
            # promote to "long" just because int happens to be 4 bytes under
            # --int=32; that would inflate to 8 bytes under --long=64 and
            # mis-route variadic args to the 64-bit push path.
            def _is_named_long(t):
                return isinstance(t, ast.BasicType) and t.name == "long"
            if _is_named_long(left_type) or _is_named_long(right_type):
                return ast.BasicType(name="long")

            # Integer promotion: char operands promote to int (C99 6.3.1.1)
            # All binary arithmetic/bitwise results are at least int-width
            result_type = left_type or right_type
            if result_type and isinstance(result_type, ast.BasicType) and result_type.name == 'char':
                return ast.BasicType(name="int")
            if result_type:
                return result_type
        elif isinstance(expr, ast.Cast):
            return expr.target_type
        elif isinstance(expr, ast.Call):
            # Get return type of function call
            if isinstance(expr.func, ast.Identifier):
                sym = self.ctx.lookup(expr.func.name)
                if sym and isinstance(sym.sym_type, ast.FunctionType):
                    return sym.sym_type.return_type
                elif sym:
                    # Function pointer variable: extract return type from pointer-to-function
                    if isinstance(sym.sym_type, ast.PointerType) and isinstance(sym.sym_type.base_type, ast.FunctionType):
                        return sym.sym_type.base_type.return_type
                    return sym.sym_type
            else:
                # Function pointer call: (*p)(...) or similar
                func_type = self._get_expr_type(expr.func)
                if isinstance(func_type, ast.FunctionType):
                    return func_type.return_type
                elif isinstance(func_type, ast.PointerType) and isinstance(func_type.base_type, ast.FunctionType):
                    return func_type.base_type.return_type
        elif isinstance(expr, ast.TernaryOp):
            # Result type is the common type of true/false branches
            true_type = self._get_expr_type(expr.true_expr)
            false_type = self._get_expr_type(expr.false_expr)
            # Apply usual arithmetic conversions
            if self._is_float_type(true_type) or self._is_float_type(false_type):
                return ast.BasicType(name="double")
            # "long long wins" — only when an operand is actually named
            # "long long".  Don't promote `long` (8 bytes under --long=64)
            # into "long long" name and pull it through 64-bit codegen.
            def _is_named_ll(t):
                return isinstance(t, ast.BasicType) and t.name == "long long"
            if _is_named_ll(true_type) or _is_named_ll(false_type):
                return ast.BasicType(name="long long")
            # "long wins over int" — only when an operand is actually named
            # "long".  Don't promote int → long just because both are 4 bytes
            # under --int=32 (would inflate to 8 under --long=64).
            def _is_named_long(t):
                return isinstance(t, ast.BasicType) and t.name == "long"
            if _is_named_long(true_type) or _is_named_long(false_type):
                return ast.BasicType(name="long")
            # Pointer types
            if isinstance(true_type, ast.PointerType):
                return true_type
            if isinstance(false_type, ast.PointerType):
                return false_type
            return true_type or false_type
        elif isinstance(expr, ast.GenericSelection):
            # Resolve the _Generic to get matched expression's type
            ctrl_type = self._get_expr_type(expr.controlling_expr)
            for type_node, value_expr in expr.associations:
                if type_node is None:
                    continue
                if self._types_compatible(ctrl_type, type_node):
                    return self._get_expr_type(value_expr)
            # Try default
            for type_node, value_expr in expr.associations:
                if type_node is None:
                    return self._get_expr_type(value_expr)
        elif isinstance(expr, ast.Compound):
            # Compound literal type is its target type
            # For arrays without explicit size, infer from initializer
            target = expr.target_type
            if isinstance(target, ast.ArrayType) and target.size is None:
                if isinstance(expr.init, ast.InitializerList):
                    n = len(expr.init.values)
                    return ast.ArrayType(base_type=target.base_type,
                                         size=ast.IntLiteral(value=n))
            return target
        return None

    def _types_compatible(self, expr_type: ast.TypeNode | None, sel_type: ast.TypeNode) -> bool:
        """Check if expression type matches selector type for _Generic.

        C11/C23 semantics (6.5.1.1): the controlling expression undergoes
        lvalue conversion, which strips top-level qualifiers (const/
        volatile).  Generic associations may not have qualified types
        (C23) — gcc/clang accept the qualified form anyway and treat both
        sides as unqualified.  We match the gcc/clang behavior here, so
        `const int x; _Generic(x, int: A, const int: B)` picks A.
        Pointed-to qualifiers on a pointer type are NOT stripped — those
        are part of the type proper.
        """
        if expr_type is None:
            return False

        # Helper to check basic type compatibility
        def basic_types_match(t1: ast.BasicType, t2: ast.BasicType) -> bool:
            # Name must match
            if t1.name != t2.name:
                return False
            # Top-level const/volatile are stripped before matching
            # (lvalue conversion on the controlling expression).
            # Check signedness for types where it matters
            if t1.name == 'char':
                # In C, char, signed char, and unsigned char are THREE distinct types
                # None = plain char, True = signed char, False = unsigned char
                if t1.is_signed != t2.is_signed:
                    return False
            elif t1.name in ('int', 'short', 'long', 'long long'):
                # For int/short/long, default signedness is signed
                s1 = t1.is_signed if t1.is_signed is not None else True
                s2 = t2.is_signed if t2.is_signed is not None else True
                if s1 != s2:
                    return False
            return True

        # Helper to check pointer type compatibility
        def pointer_types_match(t1: ast.PointerType, t2: ast.PointerType) -> bool:
            # For pointers, top-level const is stripped by lvalue conversion
            # But pointed-to type must match including qualifiers
            b1, b2 = t1.base_type, t2.base_type

            # Check if both base types are the same kind
            if type(b1) != type(b2):
                return False

            if isinstance(b1, ast.BasicType):
                # For pointer-to-basic, check name, signedness, and constness of pointed-to type
                if b1.name != b2.name:
                    return False
                # Check const qualification on the pointed-to type
                if b1.is_const != b2.is_const:
                    return False
                # Check signedness - use same rules as basic_types_match
                if b1.name == 'char':
                    # char, signed char, and unsigned char are distinct types
                    if b1.is_signed != b2.is_signed:
                        return False
                elif b1.name in ('int', 'short', 'long', 'long long'):
                    s1 = b1.is_signed if b1.is_signed is not None else True
                    s2 = b2.is_signed if b2.is_signed is not None else True
                    if s1 != s2:
                        return False
                return True

            return types_match(b1, b2)

        def types_match(t1: ast.TypeNode, t2: ast.TypeNode) -> bool:
            if type(t1) != type(t2):
                # Special case: function type matches function pointer
                if isinstance(t1, ast.FunctionType) and isinstance(t2, ast.PointerType):
                    if isinstance(t2.base_type, ast.FunctionType):
                        return True
                return False
            if isinstance(t1, ast.BasicType):
                return basic_types_match(t1, t2)
            if isinstance(t1, ast.PointerType):
                return pointer_types_match(t1, t2)
            if isinstance(t1, ast.StructType):
                return t1.name == t2.name and t1.is_union == t2.is_union
            if isinstance(t1, ast.EnumType):
                return t1.name == t2.name
            if isinstance(t1, ast.FunctionType):
                return True  # Simplified - just check it's a function type
            return False

        return types_match(expr_type, sel_type)

    def _gen_address(self, expr: ast.Expression) -> None:
        """Generate code to compute address of an expression into HL."""
        if isinstance(expr, ast.Identifier):
            sym = self.ctx.lookup(expr.name)
            if sym:
                if sym.is_global:
                    self.ctx.emit_instr("ld", f"HL,{sym.label()}")
                elif sym.uses_shared_storage:
                    # Shared storage: direct address
                    self.ctx.emit_instr("ld", f"HL,??AUTO+{sym.shared_offset}")
                else:
                    # Local: compute IX+offset
                    self.ctx.emit_instr("ld", f"HL,{sym.offset}")
                    self.ctx.emit_instr("push", "IX")
                    self.ctx.emit_instr("pop", "DE")
                    self.ctx.emit_instr("add", "HL,DE")

        elif isinstance(expr, ast.Index):
            # array[index]: base + index * element_size
            elem_size = self._get_index_elem_size(expr.array)

            self.gen_expr(expr.index)

            # Scale index by element size
            if elem_size == 1:
                pass  # No scaling needed
            elif elem_size == 2:
                self.ctx.emit_instr("add", "HL,HL")  # index * 2
            elif elem_size == 4:
                self.ctx.emit_instr("add", "HL,HL")  # index * 2
                self.ctx.emit_instr("add", "HL,HL")  # index * 4
            elif elem_size == 8:
                self.ctx.emit_instr("add", "HL,HL")  # * 2
                self.ctx.emit_instr("add", "HL,HL")  # * 4
                self.ctx.emit_instr("add", "HL,HL")  # * 8
            else:
                # Arbitrary size - use multiplication
                self.ctx.emit_instr("ld", f"DE,{elem_size}")
                self._call_runtime("__mul16")  # HL = HL * DE

            self.ctx.emit_instr("push", "HL")
            self.gen_expr(expr.array)  # Get base address
            self.ctx.emit_instr("pop", "DE")
            self.ctx.emit_instr("add", "HL,DE")

        elif isinstance(expr, ast.UnaryOp) and expr.op == "*":
            # Address of *p is p
            self.gen_expr(expr.operand)

        elif isinstance(expr, ast.Call):
            # Function call returning struct: HL = address of __sret_buf
            self.gen_expr(expr)
            # For small structs (≤2 bytes), value is returned in HL, not address
            ret_type = self._get_expr_type(expr)
            if isinstance(ret_type, ast.StructType) and self._type_size(ret_type) <= 2:
                self.ctx.runtime_used.add("__sret_buf")
                self.ctx.emit_instr("ld", "(__sret_buf),HL")
                self.ctx.emit_instr("ld", "HL,__sret_buf")

        elif isinstance(expr, ast.Member):
            if expr.is_arrow:
                self.gen_expr(expr.obj)  # p->member: p is the address
            elif isinstance(expr.obj, ast.Call):
                # Call returning struct: generate the call
                self.gen_expr(expr.obj)
                # For small structs (≤2 bytes), value is in HL not address
                ret_type = self._get_expr_type(expr.obj)
                if isinstance(ret_type, ast.StructType) and self._type_size(ret_type) <= 2:
                    self.ctx.runtime_used.add("__sret_buf")
                    self.ctx.emit_instr("ld", "(__sret_buf),HL")
                    self.ctx.emit_instr("ld", "HL,__sret_buf")
            else:
                self._gen_address(expr.obj)  # s.member: address of s

            # Get struct type and member offset
            struct_type = self._get_expr_type(expr.obj)
            if expr.is_arrow and isinstance(struct_type, ast.PointerType):
                struct_type = struct_type.base_type
            if isinstance(struct_type, ast.StructType):
                offset = self._resolve_member_offset(struct_type, expr.member)
                if offset > 0:
                    self.ctx.emit_instr("ld", f"DE,{offset}")
                    self.ctx.emit_instr("add", "HL,DE")

        elif isinstance(expr, ast.Compound):
            # Compound literal: materialize in DSEG, return address
            label = self._materialize_compound_literal(expr)
            self.ctx.emit_instr("ld", f"HL,{label}")

    @staticmethod
    def _mul_shift_count(expr: ast.Expression) -> int | None:
        """If expr is an IntLiteral power-of-2 > 1, return log2. Else None."""
        if isinstance(expr, ast.IntLiteral):
            v = expr.value
            if v > 1 and (v & (v - 1)) == 0:
                return v.bit_length() - 1
        return None

    def _gen_mul_const(self, n: int) -> None:
        """Multiply HL by constant n. Result in HL."""
        if n == 1:
            return
        if n == 2:
            self.ctx.emit_instr("add", "HL,HL")
        elif n == 4:
            self.ctx.emit_instr("add", "HL,HL")
            self.ctx.emit_instr("add", "HL,HL")
        elif n == 8:
            self.ctx.emit_instr("add", "HL,HL")
            self.ctx.emit_instr("add", "HL,HL")
            self.ctx.emit_instr("add", "HL,HL")
        elif n > 1 and (n & (n - 1)) == 0:
            # Power of 2
            shift = n.bit_length() - 1
            for _ in range(shift):
                self.ctx.emit_instr("add", "HL,HL")
        else:
            # General case: use DE * HL multiply
            self.ctx.emit_instr("ld", f"DE,{n}")
            self._call_runtime("__mul16")

    def _gen_div_const(self, n: int) -> None:
        """Divide HL by constant n (signed). Result in HL."""
        if n == 1:
            return
        if n > 1 and (n & (n - 1)) == 0:
            # Power of 2 - use arithmetic right shift
            shift = n.bit_length() - 1
            for _ in range(shift):
                self.ctx.emit_instr("sra", "H")
                self.ctx.emit_instr("rr", "L")
        else:
            self.ctx.emit_instr("ld", f"DE,{n}")
            self.ctx.emit_instr("ex", "DE,HL")
            self._call_runtime("__sdiv16")

    def _is_unsigned_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has unsigned type."""
        expr_type = self._get_expr_type(expr)
        if isinstance(expr_type, ast.BasicType):
            # is_signed=False means unsigned, is_signed=None means default (signed)
            return expr_type.is_signed == False
        return False

    def _is_promoted_unsigned(self, expr: ast.Expression) -> bool:
        """Check if an expression is unsigned AFTER integer promotion.

        Per C standard 6.3.1.1, integer promotion converts small types to int:
        - unsigned char promotes to (signed) int (all values fit in 16-bit int)
        - unsigned short = unsigned int on 16-bit systems, stays unsigned
        - unsigned int, unsigned long, unsigned long long stay unsigned
        """
        expr_type = self._get_expr_type(expr)
        if isinstance(expr_type, ast.BasicType):
            if expr_type.is_signed == False:
                # unsigned char promotes to signed int
                if expr_type.name == "char":
                    return False
                return True
        # IntLiteral with is_unsigned flag
        if isinstance(expr, ast.IntLiteral):
            return expr.is_unsigned
        return False

    def _is_long_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is 32-bit integer (needs DEHL register pair).

        Byte-width dispatch: returns True for any BasicType whose TypeConfig
        size is exactly 4 bytes. With default Z80_CPM, that's only 'long';
        with --int=32, 'int' also hits this path.
        """
        if isinstance(t, ast.BasicType):
            size = self.type_config.sizeof_basic(t.name)
            return size == 4 and t.name not in ("float", "double", "long double")
        return False

    def _is_long_long_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is 64-bit integer (needs __tmp64 slot).

        Byte-width dispatch: returns True for any BasicType whose TypeConfig
        size is exactly 8 bytes. With default Z80_CPM, that's only 'long long'.
        """
        if isinstance(t, ast.BasicType):
            size = self.type_config.sizeof_basic(t.name)
            return size == 8 and t.name not in ("float", "double", "long double")
        return False

    def _is_float_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is floating-point (float or double)."""
        if isinstance(t, ast.BasicType):
            return t.name in ("float", "double", "long double")
        return False

    def _is_float_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has floating-point type."""
        if isinstance(expr, ast.FloatLiteral):
            return True
        if isinstance(expr, ast.UnaryOp):
            if expr.op in ("-", "+"):
                return self._is_float_expr(expr.operand)
        if isinstance(expr, ast.BinaryOp):
            # Comparison operators always return int
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                return False
            # Binary operation is float if either operand is float
            if expr.op not in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="):
                return self._is_float_expr(expr.left) or self._is_float_expr(expr.right)
        if isinstance(expr, ast.Cast):
            return self._is_float_type(expr.target_type)
        expr_type = self._get_expr_type(expr)
        return self._is_float_type(expr_type)

    def _is_complex_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is a complex number type (_Complex)."""
        return isinstance(t, ast.ComplexType)

    def _is_complex_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has complex type."""
        if isinstance(expr, ast.UnaryOp):
            if expr.op in ("-", "+"):
                return self._is_complex_expr(expr.operand)
        if isinstance(expr, ast.BinaryOp):
            # Comparison operators always return int
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                return False
            # Binary operation is complex if either operand is complex
            if expr.op not in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="):
                return self._is_complex_expr(expr.left) or self._is_complex_expr(expr.right)
        if isinstance(expr, ast.Cast):
            return self._is_complex_type(expr.target_type)
        expr_type = self._get_expr_type(expr)
        return self._is_complex_type(expr_type)

    def _is_long_long_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has 64-bit type (long long)."""
        tc = self.type_config
        if isinstance(expr, ast.IntLiteral):
            # Check for explicit LL suffix or value too large for 32-bit
            if hasattr(expr, 'is_long_long') and expr.is_long_long:
                return True
            # For hex or unsigned literals, values up to ULONG_MAX fit in unsigned long
            if (expr.is_hex or expr.is_unsigned) and 0 <= expr.value <= tc.ulong_max:
                return False
            # Values too large for signed long (and not hex unsigned long)
            if expr.value > tc.long_max or expr.value < -(tc.long_max + 1):
                return True
            return False
        if isinstance(expr, ast.UnaryOp):
            if expr.op in ("-", "+", "~"):
                return self._is_long_long_expr(expr.operand)
            if expr.op == "!":
                return False
        if isinstance(expr, ast.BinaryOp):
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                return False
            # For shift operations, result type is determined only by LEFT operand (C99 6.5.7)
            if expr.op in ("<<", ">>"):
                return self._is_long_long_expr(expr.left)
            if expr.op not in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="):
                # C99 6.3.1.8: float/double wins over long long in usual arithmetic conversions
                if self._is_float_expr(expr.left) or self._is_float_expr(expr.right):
                    return False
                return self._is_long_long_expr(expr.left) or self._is_long_long_expr(expr.right)
        if isinstance(expr, ast.Cast):
            return self._is_long_long_type(expr.target_type)
        expr_type = self._get_expr_type(expr)
        return self._is_long_long_type(expr_type)

    def _try_get_ll_literal_value(self, expr: ast.Expression) -> int | None:
        """Try to extract a compile-time integer value from a long-long expression.
        Returns the 64-bit value or None if not a compile-time constant."""
        if isinstance(expr, ast.IntLiteral):
            return expr.value
        if isinstance(expr, ast.UnaryOp) and expr.op == "-":
            inner = self._try_get_ll_literal_value(expr.operand)
            if inner is not None:
                return -inner
        if isinstance(expr, ast.UnaryOp) and expr.op == "+":
            return self._try_get_ll_literal_value(expr.operand)
        if isinstance(expr, ast.UnaryOp) and expr.op == "~":
            inner = self._try_get_ll_literal_value(expr.operand)
            if inner is not None:
                return ~inner
        if isinstance(expr, ast.Cast):
            return self._try_get_ll_literal_value(expr.expr)
        return None

    def _is_long_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has 32-bit type (long but not long long)."""
        # First check if it's long long - if so, it's not just "long"
        if self._is_long_long_expr(expr):
            return False
        tc = self.type_config
        if isinstance(expr, ast.IntLiteral):
            # Explicit L suffix: always a long
            if expr.is_long:
                return True
            # Under --int=32, "int" itself is 32-bit and shares codegen with
            # "long" (DEHL register pair).  Any literal that fits in int (or
            # unsigned int) needs the 32-bit codegen path.  Without this,
            # values 0x10000..0xFFFFFFFF would only get LD HL,low loaded and
            # the high 16 bits would silently vanish.
            if tc.int_size == 4:
                if -(tc.int_max + 1) <= expr.value <= tc.uint_max:
                    return True
            # C standard type promotion for literals:
            # Decimal: int -> long -> long long
            # Hex/octal/U: int -> unsigned int -> long -> unsigned long -> ...
            val = expr.value
            if val > tc.int_max or val < -(tc.int_max + 1):
                # For hex/octal/unsigned literals, check if it fits in unsigned int
                if (expr.is_hex or expr.is_unsigned) and 0 <= val <= tc.uint_max:
                    return False  # Fits in unsigned int, stays int-sized
                # Check if it fits in signed long
                if -(tc.long_max + 1) <= val <= tc.long_max:
                    return True
                # For hex/octal/unsigned, check unsigned long
                if (expr.is_hex or expr.is_unsigned) and 0 <= val <= tc.ulong_max:
                    return True
            return False
        if isinstance(expr, ast.UnaryOp):
            # Unary operators preserve operand size for -, +, ~
            if expr.op in ("-", "+", "~"):
                return self._is_long_expr(expr.operand)
            # Logical NOT always returns int (0 or 1)
            if expr.op == "!":
                return False
            # Address-of, dereference - fall through to type checking
        if isinstance(expr, ast.BinaryOp):
            # Comparison operators always return int (0 or 1), not long
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                return False
            # For shift operations, result type is determined only by LEFT operand (C99 6.5.7)
            if expr.op in ("<<", ">>"):
                return self._is_long_expr(expr.left)
            # Binary operation is long if either operand is long
            # (excluding assignment operators which return target type)
            if expr.op not in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="):
                return self._is_long_expr(expr.left) or self._is_long_expr(expr.right)
        expr_type = self._get_expr_type(expr)
        return self._is_long_type(expr_type)

    def _uses_tmp32(self, expr: ast.Expression) -> bool:
        """Check if an expression might use __tmp32 (and thus clobber it)."""
        # Complex expressions that use __tmp32 internally
        if isinstance(expr, ast.BinaryOp):
            # Any 32-bit binary op (long or float) will use __tmp32
            if (self._is_long_expr(expr.left) or self._is_long_expr(expr.right) or
                self._is_float_expr(expr.left) or self._is_float_expr(expr.right)):
                return True
            # Check nested expressions
            return self._uses_tmp32(expr.left) or self._uses_tmp32(expr.right)
        if isinstance(expr, ast.UnaryOp):
            # ++/-- on a 32-bit operand expands into __add32 etc., which
            # writes the operand value to __tmp32 as scratch.  If the caller
            # had set __tmp32 for an enclosing comparison, that gets stomped.
            if expr.op in ("++", "--") and (self._is_long_expr(expr.operand)
                                            or self._is_float_expr(expr.operand)):
                return True
            return self._uses_tmp32(expr.operand)
        if isinstance(expr, ast.TernaryOp):
            return (self._uses_tmp32(expr.condition) or
                    self._uses_tmp32(expr.true_expr) or
                    self._uses_tmp32(expr.false_expr))
        if isinstance(expr, ast.Call):
            # Function calls might clobber __tmp32 (conservative)
            return True
        if isinstance(expr, ast.Cast):
            return self._uses_tmp32(expr.expr)
        # Simple expressions (identifiers, literals) don't use __tmp32
        return False

    def _uses_tmp64(self, expr: ast.Expression) -> bool:
        """Check if an expression might use __tmp64 (and thus clobber it)."""
        # Complex expressions that use __tmp64 internally
        if isinstance(expr, ast.BinaryOp):
            # Any 64-bit binary op will use __tmp64
            if self._is_long_long_expr(expr.left) or self._is_long_long_expr(expr.right):
                return True
            # Check nested expressions
            return self._uses_tmp64(expr.left) or self._uses_tmp64(expr.right)
        if isinstance(expr, ast.UnaryOp):
            return self._uses_tmp64(expr.operand)
        if isinstance(expr, ast.TernaryOp):
            return (self._uses_tmp64(expr.condition) or
                    self._uses_tmp64(expr.true_expr) or
                    self._uses_tmp64(expr.false_expr))
        if isinstance(expr, ast.Call):
            # Function calls might clobber __tmp64 (conservative)
            return True
        if isinstance(expr, ast.Cast):
            return self._uses_tmp64(expr.expr)
        # Simple expressions (identifiers, literals) don't use __tmp64
        return False

    def _get_expr_size(self, expr: ast.Expression) -> int:
        """Get the size of an expression result in bytes."""
        if isinstance(expr, ast.IntLiteral):
            if expr.value > 32767 or expr.value < -32768:
                return 4
            return 2
        if isinstance(expr, ast.CharLiteral):
            return 1
        expr_type = self._get_expr_type(expr)
        if expr_type:
            return self._type_size(expr_type)
        return 2  # Default to int

    def _gen_comparison(self, op: str, is_unsigned: bool = False) -> None:
        """Generate code for comparison. Left in DE, right in HL."""
        # Result should be 1 if true, 0 if false
        true_label = self.ctx.new_label("CMP_T")
        false_label = self.ctx.new_label("CMP_F")
        end_label = self.ctx.new_label("CMP_E")

        # Compare DE with HL (compute DE - HL)
        self.ctx.emit_instr("ex", "DE,HL")
        self.ctx.emit_instr("or", "A")  # Clear carry
        self.ctx.emit_instr("sbc", "HL,DE")

        # Now flags reflect HL - DE (original left - right)
        # For signed comparison, we need to check Sign XOR Overflow
        # Z80's P/V flag after SBC indicates overflow
        if op == "==":
            self.ctx.emit_instr("jp", f"Z,{true_label}")
        elif op == "!=":
            self.ctx.emit_instr("jp", f"NZ,{true_label}")
        elif op == "<":
            if is_unsigned:
                # Unsigned less than: carry set means left < right
                self.ctx.emit_instr("jp", f"C,{true_label}")
            else:
                # Signed less than: true if Sign XOR Overflow
                # No overflow: true if Sign set (M)
                # Overflow: true if Sign clear (P)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("jp", f"PE,{ov_label}")
                self.ctx.emit_instr("jp", f"M,{true_label}")
                self.ctx.emit_instr("jp", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("jp", f"P,{true_label}")
                self.ctx.emit_instr("jp", false_label)
        elif op == ">=":
            if is_unsigned:
                # Unsigned greater or equal: no carry
                self.ctx.emit_instr("jp", f"NC,{true_label}")
            else:
                # Signed >=: true if NOT (Sign XOR Overflow)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("jp", f"PE,{ov_label}")
                self.ctx.emit_instr("jp", f"P,{true_label}")
                self.ctx.emit_instr("jp", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("jp", f"M,{true_label}")
                self.ctx.emit_instr("jp", false_label)
        elif op == ">":
            if is_unsigned:
                # Unsigned greater: no carry and not zero
                self.ctx.emit_instr("jp", f"Z,{false_label}")
                self.ctx.emit_instr("jp", f"NC,{true_label}")
            else:
                # Signed >: not equal AND NOT (Sign XOR Overflow)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("jp", f"Z,{false_label}")
                self.ctx.emit_instr("jp", f"PE,{ov_label}")
                self.ctx.emit_instr("jp", f"P,{true_label}")
                self.ctx.emit_instr("jp", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("jp", f"M,{true_label}")
                self.ctx.emit_instr("jp", false_label)
        elif op == "<=":
            if is_unsigned:
                # Unsigned less or equal: carry or zero
                self.ctx.emit_instr("jp", f"Z,{true_label}")
                self.ctx.emit_instr("jp", f"C,{true_label}")
            else:
                # Signed <=: equal OR (Sign XOR Overflow)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("jp", f"Z,{true_label}")
                self.ctx.emit_instr("jp", f"PE,{ov_label}")
                self.ctx.emit_instr("jp", f"M,{true_label}")
                self.ctx.emit_instr("jp", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("jp", f"P,{true_label}")
                self.ctx.emit_instr("jp", false_label)

        # Fall through to false for simple cases (==, !=, unsigned)
        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("ld", "HL,0")
        self.ctx.emit_instr("jp", end_label)

        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("ld", "HL,1")

        self.ctx.emit_label(end_label)

    def _load_local(self, sym: Symbol) -> None:
        """Load a local variable into HL."""
        if sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"HL,(??AUTO+{sym.shared_offset})")
        else:
            self.ctx.emit_instr("ld", f"L,({ix_off(sym.offset)})")
            self.ctx.emit_instr("ld", f"H,({ix_off(sym.offset + 1)})")

    def _store_local(self, sym: Symbol, size: int = 0) -> None:
        """Store HL into a local variable."""
        if size == 0:
            # Auto-detect size from symbol type
            size = self._type_size(sym.sym_type) if sym.sym_type else 2
            if size > 2:
                size = 2  # _store_local only handles 1 or 2 byte stores
        if size == 1:
            if sym.uses_shared_storage:
                self.ctx.emit_instr("ld", "A,L")
                self.ctx.emit_instr("ld", f"(??AUTO+{sym.shared_offset}),A")
                # Restore HL from A for peephole safety (chained assignments)
                self.ctx.emit_instr("ld", "L,A")
                self.ctx.emit_instr("ld", "H,0")
            else:
                self.ctx.emit_instr("ld", f"({ix_off(sym.offset)}),L")
        elif sym.uses_shared_storage:
            # Store to shared automatic storage
            self.ctx.emit_instr("ld", f"(??AUTO+{sym.shared_offset}),HL")
        else:
            self.ctx.emit_instr("ld", f"({ix_off(sym.offset)}),L")
            self.ctx.emit_instr("ld", f"({ix_off(sym.offset + 1)}),H")

    def _load_local_32(self, sym: Symbol) -> None:
        """Load a 32-bit local variable into DEHL (DE=high, HL=low)."""
        if sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"HL,(??AUTO+{sym.shared_offset})")
            self.ctx.emit_instr("ld", f"DE,(??AUTO+{sym.shared_offset + 2})")
        else:
            self.ctx.emit_instr("ld", f"L,({ix_off(sym.offset)})")
            self.ctx.emit_instr("ld", f"H,({ix_off(sym.offset + 1)})")
            self.ctx.emit_instr("ld", f"E,({ix_off(sym.offset + 2)})")
            self.ctx.emit_instr("ld", f"D,({ix_off(sym.offset + 3)})")

    def _store_local_32(self, sym: Symbol) -> None:
        """Store DEHL (32-bit) into a local variable."""
        if sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"(??AUTO+{sym.shared_offset}),HL")
            self.ctx.emit_instr("ld", f"(??AUTO+{sym.shared_offset + 2}),DE")
        else:
            self.ctx.emit_instr("ld", f"({ix_off(sym.offset)}),L")
            self.ctx.emit_instr("ld", f"({ix_off(sym.offset + 1)}),H")
            self.ctx.emit_instr("ld", f"({ix_off(sym.offset + 2)}),E")
            self.ctx.emit_instr("ld", f"({ix_off(sym.offset + 3)}),D")

    def _store_local_64(self, sym: Symbol) -> None:
        """Store __acc64 (64-bit) into a local variable."""
        self.ctx.runtime_used.add("__acc64")
        if sym.uses_shared_storage:
            self.ctx.emit_instr("ld", f"HL,??AUTO+{sym.shared_offset}")
            self._call_runtime("__store64")
        else:
            self.ctx.emit_instr("push", "IX")
            self.ctx.emit_instr("pop", "HL")
            self.ctx.emit_instr("ld", f"DE,{sym.offset}")
            self.ctx.emit_instr("add", "HL,DE")
            self._call_runtime("__store64")

    def _call_runtime(self, name: str) -> None:
        """Call a runtime library function."""
        self.ctx.runtime_used.add(name)
        self.ctx.emit_instr("call", name)

    def _store_tmp32(self) -> None:
        """Store DEHL to __tmp32 for 32-bit binary operations."""
        self.ctx.runtime_used.add("__tmp32")
        self.ctx.emit_instr("ld", "(__tmp32),HL")
        self.ctx.emit_instr("ld", "(__tmp32+2),DE")

    def _emit_condition_test(self, condition: ast.Expression) -> None:
        """Emit zero-test for a condition expression. Sets Z flag if zero.
        For 16-bit values: LD A,H; OR L
        For 32-bit (long/float): LD A,H; OR L; OR E; OR D
        For 64-bit (long long): OR all 8 bytes via __acc64
        """
        if self._is_long_long_expr(condition):
            # The full 64-bit value lives in __acc64; DEHL only mirrors the
            # low 32 bits.  OR every byte so a non-zero high half can't slip
            # past as zero.  Z80 has no `or (nn)`, so route each byte through
            # B and accumulate into A.
            self.ctx.runtime_used.add("__acc64")
            self.ctx.emit_instr("ld", "A,(__acc64)")
            for off in range(1, 8):
                self.ctx.emit_instr("ld", "B,A")
                self.ctx.emit_instr("ld", "A,(__acc64+%d)" % off)
                self.ctx.emit_instr("or", "B")
            return
        if self._is_float_expr(condition) or self._is_long_expr(condition):
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "L")
            self.ctx.emit_instr("or", "E")
            self.ctx.emit_instr("or", "D")
        else:
            self.ctx.emit_instr("ld", "A,H")
            self.ctx.emit_instr("or", "L")

    def _emit_bool_normalize(self) -> None:
        """Normalize HL to 0 (false) or 1 (true) for _Bool type (C99 6.3.1.2)."""
        self.ctx.emit_instr("ld", "A,H")
        self.ctx.emit_instr("or", "L")
        self.ctx.emit_instr("ld", "HL,0")
        self.ctx.emit_instr("jr", "Z,$+3")
        self.ctx.emit_instr("inc", "L")

    def _is_bool_type(self, t) -> bool:
        """Check if a type is _Bool/bool."""
        return isinstance(t, ast.BasicType) and t.name in ('bool', '_Bool')

    def _extend_hl_to_dehl(self, is_signed: bool = True) -> None:
        """Sign or zero extend HL to DEHL."""
        if is_signed:
            self._call_runtime("__sext32")
        else:
            self._call_runtime("__zext32")

    def _get_deref_size(self, expr: ast.Expression) -> int:
        """Get the size of the type that would be loaded when dereferencing expr.

        For pointer dereference, this is the size of the pointed-to type.
        Returns 1 for char*, 2 for int*, etc.
        """
        # Try to infer the type from the expression
        if isinstance(expr, ast.Identifier):
            sym = self.ctx.lookup(expr.name)
            if sym and isinstance(sym.sym_type, ast.PointerType):
                return self._type_size(sym.sym_type.base_type)
            elif sym and isinstance(sym.sym_type, ast.ArrayType):
                # Arrays decay to pointers; deref size is element size
                return self._type_size(sym.sym_type.base_type)

        elif isinstance(expr, ast.BinaryOp):
            # For pointer arithmetic (ptr + n), get type from left operand
            if expr.op in ("+", "-"):
                return self._get_deref_size(expr.left)

        elif isinstance(expr, ast.UnaryOp):
            # For &x, the type is pointer to x's type - return x's type size
            if expr.op == "&":
                if isinstance(expr.operand, ast.Identifier):
                    sym = self.ctx.lookup(expr.operand.name)
                    if sym:
                        return self._type_size(sym.sym_type)
            # For p++ or p--, the type is the same as p
            elif expr.op in ("++", "--"):
                return self._get_deref_size(expr.operand)
            # For *p, look through to get p's type
            elif expr.op == "*":
                return self._get_deref_size(expr.operand)

        elif isinstance(expr, ast.Cast):
            # Use the cast target type
            if isinstance(expr.target_type, ast.PointerType):
                return self._type_size(expr.target_type.base_type)

        # General fallback: use _get_expr_type to determine the pointer/array type
        expr_type = self._get_expr_type(expr)
        if isinstance(expr_type, ast.PointerType):
            return self._type_size(expr_type.base_type)
        elif isinstance(expr_type, ast.ArrayType):
            return self._type_size(expr_type.base_type)

        # Default to 16-bit (int)
        return 2

    def _get_index_elem_size(self, array_expr: ast.Expression) -> int:
        """Get the element size for array indexing.

        For array[index], this is the size of the element type.
        """
        if isinstance(array_expr, ast.Identifier):
            sym = self.ctx.lookup(array_expr.name)
            if sym:
                if isinstance(sym.sym_type, ast.PointerType):
                    return self._type_size(sym.sym_type.base_type)
                elif isinstance(sym.sym_type, ast.ArrayType):
                    return self._type_size(sym.sym_type.base_type)
        elif isinstance(array_expr, ast.Member):
            # Member expression like s.array or s->array
            member_type = self._get_member_type(array_expr)
            if member_type:
                if isinstance(member_type, ast.ArrayType):
                    return self._type_size(member_type.base_type)
                elif isinstance(member_type, ast.PointerType):
                    return self._type_size(member_type.base_type)
        elif isinstance(array_expr, ast.Index):
            # Nested index like arr[i][j] - get type of inner expression
            array_type = self._get_expr_type(array_expr)
            if isinstance(array_type, ast.ArrayType):
                return self._type_size(array_type.base_type)
            elif isinstance(array_type, ast.PointerType):
                return self._type_size(array_type.base_type)

        # Default to 16-bit
        return 2

    def _calc_locals_size(self, body: ast.CompoundStmt) -> int:
        """Calculate total size needed for local variables."""
        size = 0
        for item in body.items:
            if isinstance(item, ast.VarDecl):
                if item.storage_class in ("static", "extern"):
                    continue
                size += self._type_size(item.var_type)
            elif isinstance(item, ast.DeclarationList):
                for decl in item.declarations:
                    if isinstance(decl, ast.VarDecl):
                        size += self._type_size(decl.var_type)
            elif isinstance(item, ast.CompoundStmt):
                size += self._calc_locals_size(item)
            elif isinstance(item, ast.ForStmt):
                if isinstance(item.init, ast.VarDecl):
                    size += self._type_size(item.init.var_type)
                elif isinstance(item.init, ast.DeclarationList):
                    for decl in item.init.declarations:
                        if isinstance(decl, ast.VarDecl):
                            size += self._type_size(decl.var_type)
                if isinstance(item.body, ast.CompoundStmt):
                    size += self._calc_locals_size(item.body)
            elif isinstance(item, ast.IfStmt):
                if isinstance(item.then_branch, ast.CompoundStmt):
                    size += self._calc_locals_size(item.then_branch)
                if isinstance(item.else_branch, ast.CompoundStmt):
                    size += self._calc_locals_size(item.else_branch)
                elif isinstance(item.else_branch, ast.IfStmt):
                    fake = ast.CompoundStmt(items=[item.else_branch])
                    size += self._calc_locals_size(fake)
            elif isinstance(item, (ast.WhileStmt, ast.DoWhileStmt)):
                if isinstance(item.body, ast.CompoundStmt):
                    size += self._calc_locals_size(item.body)
            elif isinstance(item, ast.SwitchStmt):
                if isinstance(item.body, ast.CompoundStmt):
                    size += self._calc_locals_size(item.body)
            elif isinstance(item, ast.CaseStmt):
                # Case/default labels in switch body - recurse into their statement
                if isinstance(item.stmt, ast.CompoundStmt):
                    size += self._calc_locals_size(item.stmt)
                elif isinstance(item.stmt, ast.CaseStmt):
                    # Nested case: case X: case Y: stmt
                    fake = ast.CompoundStmt(items=[item.stmt])
                    size += self._calc_locals_size(fake)
        return size

    def _type_size(self, t: ast.TypeNode) -> int:
        """Return the size of a type in bytes."""
        if isinstance(t, ast.BasicType):
            if t.name == "void":
                return 0
            size = self.type_config.sizeof_basic(t.name)
            if size is not None:
                return size
            return self.type_config.int_size  # Default
        elif isinstance(t, ast.PointerType):
            return self.type_config.ptr_size
        elif isinstance(t, ast.ArrayType):
            # Array size * element size
            base_size = self._type_size(t.base_type)
            if t.size:
                if isinstance(t.size, ast.IntLiteral):
                    return base_size * t.size.value
                size_val = self._eval_const_expr(t.size)
                if size_val is not None:
                    return base_size * size_val
            return 0  # Unsized/flexible array member - zero size for sizeof
        elif isinstance(t, ast.StructType):
            # Use cached size if available (handles bitfield packing correctly)
            if t.name and t.name in self.ctx.struct_sizes:
                return self.ctx.struct_sizes[t.name]
            # Handle inline struct definitions with members
            if t.members:
                # Check if any members have bitfields
                has_bitfields = any(m.bit_width is not None for m in t.members)
                if has_bitfields:
                    # Compute layout to get correct packed size
                    anon_name = t.name or f"__anon_{id(t)}"
                    _, _, total_size = self._compute_struct_layout(
                        anon_name, t.members, t.is_union)
                    return total_size
                if t.is_union:
                    return max((self._type_size(m.member_type) for m in t.members), default=0)
                else:
                    return sum(self._type_size(m.member_type) for m in t.members)
            # Look up struct definition by name
            if t.name and t.name in self.ctx.structs:
                members = self.ctx.structs[t.name]
                if t.is_union:
                    # Union: size is max of all members
                    return max((self._type_size(mt) for _, mt, _ in members), default=0)
                else:
                    # Struct: size is sum of all members
                    return sum(self._type_size(mt) for _, mt, _ in members)
            return 0  # Unknown struct
        elif isinstance(t, ast.ComplexType):
            # Complex types are two floats (real + imaginary)
            return 2 * self.type_config.float_size
        return self.type_config.int_size  # Default

    def _get_struct_members(self, struct_type: ast.StructType) -> list:
        """Get struct members as list of (name, type, offset) tuples.

        Handles both inline struct definitions (with members) and named structs
        (looked up in ctx.structs).  Handles bitfield packing.  Recurses into
        anonymous struct/union members so their fields appear in the returned
        list at their actual offsets — this is what makes
        `union { struct { u8 a, b; }; u8 x[2]; }` look up `.a` correctly.
        """
        if struct_type.members:
            has_bitfields = any(m.bit_width is not None for m in struct_type.members)
            if has_bitfields:
                anon_name = struct_type.name or f"__anon_{id(struct_type)}"
                members, _, _ = self._compute_struct_layout(
                    anon_name, struct_type.members, struct_type.is_union)
                return members
            # Inline struct definition - compute offsets from members
            members = []
            offset = 0
            for m in struct_type.members:
                if m.name:
                    members.append((m.name, m.member_type, offset))
                    if not struct_type.is_union:
                        offset += self._type_size(m.member_type)
                elif isinstance(m.member_type, ast.StructType):
                    # Anonymous struct/union member: flatten its fields so
                    # dotted designators (`.a`) and regular lookups work.
                    for sub_name, sub_type, sub_off in self._get_struct_members(m.member_type):
                        members.append((sub_name, sub_type, offset + sub_off))
                    if not struct_type.is_union:
                        offset += self._type_size(m.member_type)
                else:
                    # Unnamed non-aggregate (e.g. zero-width bitfield): advance
                    # offset but don't expose.
                    if not struct_type.is_union:
                        offset += self._type_size(m.member_type)
            return members
        elif struct_type.name and struct_type.name in self.ctx.structs:
            return self.ctx.structs[struct_type.name]
        return []

    def _count_flat_struct_values(self, struct_type: ast.StructType) -> int:
        """Count total scalar values needed to initialize a struct with flat init."""
        members = self._get_struct_members(struct_type)
        count = 0
        for name, member_type, offset in members:
            if isinstance(member_type, ast.ArrayType):
                # Array member: count array elements
                array_size = 1
                if member_type.size:
                    if isinstance(member_type.size, ast.IntLiteral):
                        array_size = member_type.size.value
                    else:
                        sz = self._eval_const_expr(member_type.size)
                        if sz is not None:
                            array_size = sz
                base_type = member_type.base_type
                if isinstance(base_type, ast.StructType):
                    count += array_size * self._count_flat_struct_values(base_type)
                else:
                    count += array_size
            elif isinstance(member_type, ast.StructType):
                count += self._count_flat_struct_values(member_type)
            else:
                count += 1
        return count

    def _emit_array_init_flat_inline(self, values: list, start_index: int,
                                      array_type: ast.ArrayType) -> int:
        """Emit array initialization from flat values, handling inline struct types.

        Returns number of values consumed.
        """
        elem_type = array_type.base_type
        elem_size = self._type_size(elem_type)

        # Get array size if known
        array_size = 1
        if array_type.size and isinstance(array_type.size, ast.IntLiteral):
            array_size = array_type.size.value
        else:
            # Unsized array - infer from number of values remaining
            # For struct arrays, count total flat values needed per struct
            if isinstance(elem_type, ast.StructType):
                flat_count = self._count_flat_struct_values(elem_type)
                if flat_count > 0:
                    remaining_values = len(values) - start_index
                    array_size = (remaining_values + flat_count - 1) // flat_count
                    array_size = max(1, array_size)

        consumed = 0
        for i in range(array_size):
            idx = start_index + consumed
            if idx >= len(values):
                # No more values - zero-initialize remaining
                self.ctx.emit_instr("ds", str(elem_size))
                continue

            val = values[idx]
            if isinstance(val, ast.DesignatedInit):
                val = val.value

            # Handle nested types
            if isinstance(elem_type, ast.StructType) and not isinstance(val, ast.InitializerList):
                members = self._get_struct_members(elem_type)
                if members:
                    if elem_type.is_union:
                        # Union: only initialize the first member, pad the rest
                        first_member = members[0]
                        first_size = self._type_size(first_member[1])
                        if isinstance(first_member[1], ast.StructType):
                            sub_members = self._get_struct_members(first_member[1])
                            if sub_members:
                                nested_consumed = self._emit_struct_init_flat(values[idx:], sub_members)
                                consumed += nested_consumed
                            else:
                                consumed += 1
                                self._emit_initializer(val, first_member[1])
                        elif isinstance(first_member[1], ast.ArrayType):
                            nested_consumed = self._emit_array_init_flat_inline(values, idx, first_member[1])
                            consumed += nested_consumed
                        else:
                            consumed += 1
                            self._emit_initializer(val, first_member[1])
                        union_size = self._type_size(elem_type)
                        if union_size > first_size:
                            self.ctx.emit_instr("ds", str(union_size - first_size))
                    else:
                        nested_consumed = self._emit_struct_init_flat(values[idx:], members)
                        consumed += nested_consumed
                else:
                    consumed += 1
                    self._emit_initializer(val, elem_type)
            elif isinstance(elem_type, ast.ArrayType) and not isinstance(val, ast.InitializerList):
                nested_consumed = self._emit_array_init_flat_inline(values, idx, elem_type)
                consumed += nested_consumed
            else:
                consumed += 1
                self._emit_initializer(val, elem_type)

        return consumed

    def _emit_designated_array_init(self, init_list: ast.InitializerList,
                                       array_type: ast.ArrayType) -> None:
        """Emit array initialization with designated initializers (including ranges).

        Handles [index] and [start ... end] designators, with proper overriding
        for overlapping ranges.
        """
        base_type = array_type.base_type
        base_size = self._type_size(base_type)
        array_size = 1
        if array_type.size:
            if isinstance(array_type.size, ast.IntLiteral):
                array_size = array_type.size.value
            else:
                sz = self._eval_const_expr(array_type.size)
                if sz is not None:
                    array_size = sz

        # Build final element array: index -> value (None means zero)
        elements = [None] * array_size
        next_index = 0

        for val in init_list.values:
            if isinstance(val, ast.DesignatedInit):
                for desig in val.designators:
                    if isinstance(desig, ast.RangeDesignator):
                        start = self._eval_const_expr(desig.start)
                        end = self._eval_const_expr(desig.end)
                        if start is not None and end is not None:
                            for idx in range(start, end + 1):
                                if idx < array_size:
                                    elements[idx] = val.value
                            next_index = end + 1
                    else:
                        idx = self._eval_const_expr(desig)
                        if idx is not None:
                            if idx < array_size:
                                elements[idx] = val.value
                            next_index = idx + 1
            else:
                if next_index < array_size:
                    elements[next_index] = val
                next_index += 1

        # Emit elements
        for elem in elements:
            if elem is None:
                self.ctx.emit_instr("ds", str(base_size))
            else:
                self._emit_initializer(elem, base_type)

    def _emit_initializer(self, init: ast.Expression, elem_type: ast.TypeNode) -> None:
        """Emit initialized data for a global variable or array element."""
        elem_size = self._type_size(elem_type)

        if isinstance(init, ast.InitializerList):
            # Handle array or struct initializer
            if isinstance(elem_type, ast.ArrayType):
                base_type = elem_type.base_type
                base_size = self._type_size(base_type)

                # Check for designated initializers with index/range designators
                has_index_designators = any(
                    isinstance(v, ast.DesignatedInit) and v.designators and
                    not isinstance(v.designators[0], str)
                    for v in init.values
                )

                has_known_size = False
                if elem_type.size:
                    has_known_size = isinstance(elem_type.size, ast.IntLiteral) or self._eval_const_expr(elem_type.size) is not None
                if has_index_designators and has_known_size:
                    # Designated array init with [index] or [start...end] designators
                    self._emit_designated_array_init(init, elem_type)
                else:
                    # Check for braced string literal: char x[] = {"XXX"}
                    is_braced_string = (
                        len(init.values) == 1 and
                        isinstance(init.values[0], ast.StringLiteral) and
                        isinstance(base_type, ast.BasicType) and
                        base_type.name in ("char", "signed char", "unsigned char")
                    )
                    # Check if this is a flat/mixed init for array of aggregates
                    # (structs or sub-arrays with some non-braced values)
                    is_flat_aggregate_init = (
                        isinstance(base_type, (ast.StructType, ast.ArrayType)) and
                        init.values and
                        any(not isinstance(v, (ast.InitializerList, ast.DesignatedInit))
                            for v in init.values)
                    )
                    if is_braced_string:
                        self._emit_string_for_array(init.values[0], elem_type)
                    elif is_flat_aggregate_init:
                        # Flat/mixed init for array of structs or sub-arrays
                        consumed = self._emit_array_init_flat_inline(init.values, 0, elem_type)
                    else:
                        # Normal array init - each value is a complete element
                        for val in init.values:
                            if isinstance(val, ast.DesignatedInit):
                                self._emit_initializer(val.value, base_type)
                            else:
                                self._emit_initializer(val, base_type)
                        # Pad with zeros if initializer is shorter than array size
                        declared_size = None
                        if elem_type.size:
                            if isinstance(elem_type.size, ast.IntLiteral):
                                declared_size = elem_type.size.value
                            else:
                                declared_size = self._eval_const_expr(elem_type.size)
                        if declared_size is not None:
                            remaining = declared_size - len(init.values)
                            if remaining > 0:
                                pad_size = remaining * base_size
                                self.ctx.emit_instr("ds", str(pad_size))
            elif isinstance(elem_type, ast.StructType):
                # Struct/union initializer
                members = self._get_struct_members(elem_type)
                if members:
                    if elem_type.is_union:
                        # Union: determine which member to initialize
                        # Per C standard, last designator in union init wins
                        target_member = members[0]  # Default: first named member
                        target_sub_members = None
                        target_value = None

                        # Find all designated initializers
                        all_desigs = [v for v in init.values
                                      if isinstance(v, ast.DesignatedInit)
                                      and v.designators
                                      and isinstance(v.designators[0], str)]

                        # If multiple designators all target fields of the
                        # same anonymous struct/union member, apply them all
                        # together (they're at different offsets within the
                        # anon sub-struct, so "last wins" is wrong).
                        anon_match = None
                        if all_desigs and elem_type.name and elem_type.name in self.ctx.anon_members:
                            for anon_type, anon_offset in self.ctx.anon_members[elem_type.name]:
                                anon_mems = self._get_struct_members(anon_type)
                                anon_names = {a[0] for a in anon_mems}
                                if all(d.designators[0] in anon_names for d in all_desigs):
                                    anon_match = (anon_type, anon_offset, anon_mems)
                                    break
                        if anon_match is not None:
                            anon_type, anon_offset, anon_mems = anon_match
                            # Emit the anon sub-struct with all the
                            # designators; it's at offset 0 in the union,
                            # and the union has no other initialized members
                            # when we took this branch.
                            self._emit_struct_init_designated(init.values, anon_mems)
                            emitted_size = self._type_size(anon_type)
                            union_size = self._type_size(elem_type)
                            if union_size > emitted_size:
                                self.ctx.emit_instr("ds", str(union_size - emitted_size))
                            return

                        last_desig = all_desigs[-1] if all_desigs else None

                        if last_desig is not None:
                            desig_name = last_desig.designators[0]
                            # Check if designator matches a direct member
                            found_direct = False
                            for m in members:
                                if m[0] == desig_name:
                                    target_member = m
                                    found_direct = True
                                    break
                            if not found_direct and elem_type.name:
                                # Check anonymous struct/union sub-members
                                if elem_type.name in self.ctx.anon_members:
                                    for anon_type, anon_offset in self.ctx.anon_members[elem_type.name]:
                                        anon_mems = self._get_struct_members(anon_type)
                                        for aname, atype, aoff in anon_mems:
                                            if aname == desig_name:
                                                target_member = (None, anon_type, anon_offset)
                                                target_sub_members = anon_mems
                                                break
                                        if target_sub_members:
                                            break
                            target_value = [last_desig]
                        else:
                            target_value = init.values

                        target_size = self._type_size(target_member[1])
                        if target_sub_members is not None:
                            self._emit_struct_init_flat(target_value, target_sub_members)
                        else:
                            self._emit_struct_init_flat(target_value, [target_member])
                        union_size = self._type_size(elem_type)
                        if union_size > target_size:
                            self.ctx.emit_instr("ds", str(union_size - target_size))
                    else:
                        # Compute the struct's bitfield_info key so packing
                        # picks the right bit_offset / bit_width per field —
                        # without this, an anon struct's "a" can match
                        # bitfield_info entries for some other "a" added by
                        # a different struct earlier in the file.
                        sname = elem_type.name
                        if not sname:
                            sname = f"__anon_{id(elem_type)}"
                        self._emit_struct_init_flat(init.values, members,
                                                    struct_name=sname)
                else:
                    # Unknown struct, just reserve space
                    self.ctx.emit_instr("ds", str(elem_size))
            else:
                # Scalar with initializer list (e.g., int x = {1})
                if init.values:
                    self._emit_initializer(init.values[0], elem_type)
                else:
                    self.ctx.emit_instr("ds", str(elem_size))
        elif isinstance(init, ast.IntLiteral):
            # Check if target type is float - if so, convert to float representation
            if self._is_float_type(elem_type):
                self._emit_float_value(float(init.value))
            else:
                self._emit_int_value(init.value, elem_size)
        elif isinstance(init, ast.FloatLiteral):
            self._emit_float_value(init.value)
        elif isinstance(init, ast.CharLiteral):
            if elem_size == 1:
                self.ctx.emit_instr("db", str(init.value))
            else:
                # Char constant has type int - sign extend per C 6.4.4.4
                val = init.value
                if val >= 0x80:
                    val = val - 0x100
                self._emit_int_value(val, elem_size)
        elif isinstance(init, ast.StringLiteral):
            if isinstance(elem_type, ast.PointerType):
                # Pointer member initialized with string literal - emit pointer to string
                label = self.ctx.add_string(init.value, is_wide=getattr(init, 'is_wide', False))
                self.ctx.emit_instr("dw", label)
            else:
                # Array or char member - emit as bytes
                escaped = self._escape_string(init.value)
                self.ctx.emit_instr("db", f"'{escaped}',0")
        elif isinstance(init, ast.UnaryOp) and init.op == "-":
            # Handle negative literals
            if isinstance(init.operand, ast.IntLiteral):
                if self._is_float_type(elem_type):
                    self._emit_float_value(float(-init.operand.value))
                else:
                    self._emit_int_value(-init.operand.value, elem_size)
            elif isinstance(init.operand, ast.FloatLiteral):
                self._emit_float_value(-init.operand.value)
            else:
                # Complex expression - reserve space (runtime init would be needed)
                self.ctx.emit_instr("ds", str(elem_size))
        elif isinstance(init, ast.Identifier):
            # Check for enum constant first
            if init.name in self.ctx.enum_constants:
                val = self.ctx.enum_constants[init.name]
                if self._is_float_type(elem_type):
                    self._emit_float_value(float(val))
                else:
                    self._emit_int_value(val, elem_size)
            else:
                # Address of a symbol - emit as label reference
                sym = self.ctx.lookup(init.name)
                if sym:
                    label = sym.label()
                elif init.name in self.ctx.static_local_labels:
                    label = self.ctx.static_local_labels[init.name]
                else:
                    label = f"_{init.name}"
                self.ctx.emit_instr("dw", label)
        elif isinstance(init, ast.UnaryOp) and init.op == "&":
            # Address-of expression
            label, offset = self._try_resolve_address_const(init)
            if label is not None:
                self._emit_address_const(label, offset)
            else:
                self.ctx.emit_instr("ds", str(elem_size))
        elif isinstance(init, ast.Cast):
            # Cast expression - try to evaluate constant
            const_val = self._eval_const_expr(init)
            if const_val is not None:
                if self._is_float_type(elem_type):
                    self._emit_float_value(float(const_val))
                else:
                    self._emit_int_value(const_val, elem_size)
            else:
                self.ctx.emit_instr("ds", str(elem_size))
        elif isinstance(init, ast.Compound):
            # Compound literal: (type){initializer} - extract the initializer
            self._emit_initializer(init.init, init.target_type)
        elif isinstance(init, ast.BinaryOp):
            # Try to evaluate as pure constant first
            const_val = self._eval_const_expr(init)
            if const_val is not None:
                if self._is_float_type(elem_type) or isinstance(const_val, float):
                    self._emit_float_value(float(const_val))
                else:
                    self._emit_int_value(const_val, elem_size)
            else:
                # Try to resolve as address constant (e.g., &a+1, &st.x-&st)
                label, offset = self._try_resolve_address_const(init)
                if label is not None:
                    self._emit_address_const(label, offset)
                else:
                    self.ctx.emit_instr("ds", str(elem_size))
        else:
            # Try to evaluate as a constant expression before giving up
            const_val = self._eval_const_expr(init)
            if const_val is not None:
                if self._is_float_type(elem_type) or isinstance(const_val, float):
                    self._emit_float_value(float(const_val))
                else:
                    self._emit_int_value(const_val, elem_size)
            else:
                # Complex initializer - reserve space
                self.ctx.emit_instr("ds", str(elem_size))

    def _try_resolve_address_const(self, expr) -> tuple:
        """Try to resolve an expression as (label, offset) for static initialization.
        Returns (label_str, offset_int) or (None, 0) if not resolvable.
        Handles: &var, &var.member, &var+N, var (for arrays/functions).
        """
        if isinstance(expr, ast.UnaryOp) and expr.op == "&":
            operand = expr.operand
            if isinstance(operand, ast.Identifier):
                sym = self.ctx.lookup(operand.name)
                if sym:
                    return (sym.label(), 0)
                elif operand.name in self.ctx.static_local_labels:
                    return (self.ctx.static_local_labels[operand.name], 0)
                else:
                    return (f"_{operand.name}", 0)
            elif isinstance(operand, ast.Member):
                # &struct.member or &((struct*)base)->member chain
                if not operand.is_arrow and isinstance(operand.obj, ast.Identifier):
                    sym = self.ctx.lookup(operand.obj.name)
                    if sym and isinstance(sym.sym_type, ast.StructType):
                        offset = self._resolve_member_offset(sym.sym_type, operand.member)
                        if offset >= 0:
                            return (sym.label(), offset)
                # Fallback: handle nested member chains, arrow access on cast, etc.
                result = self._resolve_const_member_chain(operand)
                if result is not None:
                    return result
            elif isinstance(operand, ast.Index):
                # &array[index] - try to resolve as base + index*elem_size
                if isinstance(operand.array, ast.Identifier):
                    sym = self.ctx.lookup(operand.array.name)
                    if sym:
                        idx_val = self._eval_const_expr(operand.index)
                        if idx_val is not None:
                            arr_type = sym.sym_type
                            if isinstance(arr_type, ast.ArrayType):
                                elem_size = self._type_size(arr_type.base_type)
                                return (sym.label(), idx_val * elem_size)
                # &((struct*)base)->member[index] or &struct.member[index]
                if isinstance(operand.array, ast.Member):
                    member_result = self._resolve_const_member_chain(operand.array)
                    if member_result is not None:
                        base_label, base_offset = member_result
                        idx_val = self._eval_const_expr(operand.index)
                        if idx_val is not None:
                            member_type = self._get_expr_type(operand.array)
                            if isinstance(member_type, ast.ArrayType):
                                elem_size = self._type_size(member_type.base_type)
                            else:
                                elem_size = 1
                            return (base_label, base_offset + idx_val * elem_size)
        elif isinstance(expr, ast.Identifier):
            # For function pointers or array names
            sym = self.ctx.lookup(expr.name)
            if sym and isinstance(sym.sym_type, (ast.FunctionType, ast.ArrayType)):
                return (sym.label(), 0)
            elif expr.name in self.ctx.static_local_labels:
                return (self.ctx.static_local_labels[expr.name], 0)
        elif isinstance(expr, ast.BinaryOp) and expr.op in ('+', '-'):
            # Address +/- constant offset
            label, base_offset = self._try_resolve_address_const(expr.left)
            if label is not None:
                const_val = self._eval_const_expr(expr.right)
                if const_val is not None:
                    # Scale by pointed-to type size for pointer arithmetic
                    left_type = self._get_expr_type(expr.left)
                    if isinstance(left_type, ast.PointerType):
                        scale = self._type_size(left_type.base_type)
                    else:
                        scale = 1
                    if expr.op == '+':
                        return (label, base_offset + const_val * scale)
                    else:
                        return (label, base_offset - const_val * scale)
            # Try const + address (commutative for +)
            if expr.op == '+':
                const_val = self._eval_const_expr(expr.left)
                if const_val is not None:
                    label, base_offset = self._try_resolve_address_const(expr.right)
                    if label is not None:
                        right_type = self._get_expr_type(expr.right)
                        if isinstance(right_type, ast.PointerType):
                            scale = self._type_size(right_type.base_type)
                        else:
                            scale = 1
                        return (label, base_offset + const_val * scale)
        elif isinstance(expr, ast.Cast):
            # Cast of address expression (e.g., (int *)&x)
            return self._try_resolve_address_const(expr.expr)
        return (None, 0)

    def _resolve_const_member_chain(self, expr: ast.Member) -> tuple | None:
        """Resolve a member access chain to (label_or_value, offset) for const init.
        Handles: ((struct*)0x1234)->member, struct_var.member, and nested chains.
        Returns (label_str, offset) or (int_str, offset) or None if not resolvable.
        """
        obj = expr.obj
        member = expr.member

        if expr.is_arrow:
            # Arrow access: base->member. Base must be a pointer to struct.
            # Check for cast of integer constant: ((struct*)0x1234)->member
            base_val = None
            struct_type = None
            if isinstance(obj, ast.Cast):
                base_val = self._eval_const_expr(obj.expr)
                if isinstance(obj.target_type, ast.PointerType):
                    pt = obj.target_type.base_type
                    if isinstance(pt, ast.StructType):
                        struct_type = pt
            if base_val is not None and struct_type is not None:
                offset = self._resolve_member_offset(struct_type, member)
                if offset >= 0:
                    return (str(base_val + offset), 0)
            # Arrow on a variable: ptr->member
            return None
        else:
            # Dot access: obj.member
            if isinstance(obj, ast.Identifier):
                sym = self.ctx.lookup(obj.name)
                if sym and isinstance(sym.sym_type, ast.StructType):
                    offset = self._resolve_member_offset(sym.sym_type, member)
                    if offset >= 0:
                        return (sym.label(), offset)
            # Nested member: obj.outer.inner
            elif isinstance(obj, ast.Member):
                parent_result = self._resolve_const_member_chain(obj)
                if parent_result is not None:
                    parent_label, parent_offset = parent_result
                    # Get the struct type of the parent member
                    parent_type = self._get_expr_type(obj)
                    if isinstance(parent_type, ast.StructType):
                        inner_offset = self._resolve_member_offset(parent_type, member)
                        if inner_offset >= 0:
                            return (parent_label, parent_offset + inner_offset)
            return None

    def _emit_address_const(self, label: str, offset: int) -> None:
        """Emit a DW directive for a label+offset address constant."""
        if offset > 0:
            self.ctx.emit_instr("dw", f"{label}+{offset}")
        elif offset < 0:
            self.ctx.emit_instr("dw", f"{label}-{-offset}")
        else:
            self.ctx.emit_instr("dw", label)

    def _emit_struct_init_flat(self, values: list, members: list,
                                struct_name: str | None = None) -> int:
        """Emit struct initialization with flat value list. Returns values consumed.

        struct_name disambiguates bitfield_info lookups when several distinct
        structs share a member name (very common: "a", "b" everywhere).
        """
        # Check for member designators requiring non-sequential emit
        has_member_desig = any(
            isinstance(v, ast.DesignatedInit) and v.designators and isinstance(v.designators[0], str)
            for v in values
        )

        if has_member_desig:
            return self._emit_struct_init_designated(values, members)

        # Detect bitfield groups: consecutive members sharing the same byte offset
        # Group them so we can pack their values at compile time
        # Helper: does a member name correspond to a real bitfield?
        def _bf_for(member_name):
            if struct_name is not None:
                return self.ctx.bitfield_info.get((struct_name, member_name))
            for key, info in self.ctx.bitfield_info.items():
                if key[1] == member_name:
                    return info
            return None

        # Helper: a member shares its byte offset with another member only if
        # both are bitfields packed into the same storage unit, OR one is a
        # zero-size aggregate (e.g. struct {}) which doesn't really occupy
        # the slot.  The latter must NOT be lumped into a "bitfield group"
        # or we end up packing a normal scalar member with the empty struct
        # and losing the scalar's emit altogether.
        def _is_packable_bitfield(member_name, member_type):
            if _bf_for(member_name) is not None:
                return True
            return False

        member_groups = []  # list of (offset, [(name, type, bf_info_or_None)])
        i = 0
        while i < len(members):
            name, mtype, offset = members[i]
            # Collect consecutive members at the same offset that are all
            # genuine bitfields — that's the only valid reason to share an
            # offset.  A zero-size aggregate sharing the offset breaks out.
            j = i + 1
            if _is_packable_bitfield(name, mtype):
                while (j < len(members) and members[j][2] == offset
                       and _is_packable_bitfield(members[j][0], members[j][1])):
                    j += 1
            if j > i + 1:
                # Multiple bitfield members at same offset = bitfield group
                group = []
                for k in range(i, j):
                    n, t, o = members[k]
                    group.append((n, t, _bf_for(n)))
                member_groups.append((offset, group))
                i = j
            else:
                # Single member at unique offset - treat as normal
                # (single bitfields at bit_offset=0 use full storage unit, no masking needed)
                member_groups.append((offset, [(name, mtype, None)]))
                i += 1

        value_index = 0

        for group_offset, group in member_groups:
            if len(group) > 1 or (len(group) == 1 and group[0][2] is not None):
                # Bitfield group: pack values at compile time
                bf_info_first = group[0][2]
                storage_size = bf_info_first.storage_size if bf_info_first else self._type_size(group[0][1])
                packed_value = 0
                for gname, gtype, gbf in group:
                    if value_index >= len(values):
                        break
                    val = values[value_index]
                    value_index += 1
                    if isinstance(val, ast.DesignatedInit):
                        val = val.value
                    # Try to evaluate as constant
                    const_val = self._eval_const_expr(val)
                    if const_val is None:
                        const_val = 0
                    if isinstance(const_val, float):
                        const_val = int(const_val)
                    if gbf:
                        mask = (1 << gbf.bit_width) - 1
                        packed_value |= (const_val & mask) << gbf.bit_offset
                    else:
                        packed_value = const_val
                # Emit packed value
                if storage_size == 1:
                    self.ctx.emit_instr("db", str(packed_value & 0xFF))
                elif storage_size == 2:
                    self.ctx.emit_instr("dw", str(packed_value & 0xFFFF))
                elif storage_size == 4:
                    self.ctx.emit_instr("dw", str(packed_value & 0xFFFF))
                    self.ctx.emit_instr("dw", str((packed_value >> 16) & 0xFFFF))
                continue

            # Single non-bitfield member
            member_name, member_type, _ = group[0]
            if value_index >= len(values):
                # No more values - zero-initialize
                size = self._type_size(member_type)
                if size > 0:
                    self.ctx.emit_instr("ds", str(size))
                continue

            val = values[value_index]

            # Handle DesignatedInit (without member name - e.g. array index designator)
            if isinstance(val, ast.DesignatedInit):
                val = val.value
                value_index += 1
                self._emit_initializer(val, member_type)
            elif isinstance(member_type, ast.ArrayType) and not isinstance(val, ast.InitializerList):
                # Check for string literal initializing char array
                if isinstance(val, ast.StringLiteral) and self._is_char_array(member_type):
                    value_index += 1
                    self._emit_string_for_array(val, member_type)
                else:
                    # Flat array init
                    consumed = self._emit_array_init_flat(values, value_index, member_type)
                    value_index += consumed
            elif isinstance(member_type, ast.StructType) and not isinstance(val, (ast.InitializerList, ast.Compound)):
                # Flat nested struct init (but not compound literals - those are complete values)
                if member_type.is_union:
                    # Union: only init the first member, pad the rest
                    nested_members = self._get_struct_members(member_type)
                    if nested_members:
                        first_member = nested_members[0]
                        first_size = self._type_size(first_member[1])
                        if isinstance(first_member[1], ast.StructType):
                            sub_members = self._get_struct_members(first_member[1])
                            if sub_members:
                                consumed = self._emit_struct_init_flat(values[value_index:], sub_members)
                                value_index += consumed
                            else:
                                value_index += 1
                                self._emit_initializer(val, first_member[1])
                        else:
                            value_index += 1
                            self._emit_initializer(val, first_member[1])
                        union_size = self._type_size(member_type)
                        if union_size > first_size:
                            self.ctx.emit_instr("ds", str(union_size - first_size))
                    else:
                        value_index += 1
                        self._emit_initializer(val, member_type)
                else:
                    nested_members = self._get_struct_members(member_type)
                    if nested_members:
                        consumed = self._emit_struct_init_flat(values[value_index:], nested_members)
                        value_index += consumed
                    else:
                        value_index += 1
                        self._emit_initializer(val, member_type)
            else:
                # Normal case (includes InitializerList and Compound for struct members)
                value_index += 1
                self._emit_initializer(val, member_type)

        return value_index

    def _emit_struct_init_designated(self, values: list, members: list) -> int:
        """Emit struct init with member designators. Values go to designated member positions."""
        # Build member -> value list mapping
        # Each member maps to a list of values (DesignatedInit or plain) to allow
        # continuation after nested designators (e.g., .a[1]=4, 7 -> a gets both)
        member_vals = {}  # member_name -> list of values
        next_idx = 0
        # Track the current "active" member for continuation after nested designators
        active_nested_member = None  # member name if last desig was nested
        active_nested_pos = 0  # next sequential index within the nested member
        active_nested_size = 0  # capacity of the nested member (array size)

        for val in values:
            if isinstance(val, ast.DesignatedInit) and val.designators and isinstance(val.designators[0], str):
                name = val.designators[0]
                if len(val.designators) > 1:
                    # Nested designator like .a[1] or .a.j
                    sub_desig = ast.DesignatedInit(
                        designators=val.designators[1:], value=val.value, location=val.location)
                    if name in member_vals and len(member_vals[name]) == 1 and isinstance(member_vals[name][0], ast.InitializerList):
                        # Merge into existing InitializerList (e.g., {[1]=4,5} then .a[4]=1)
                        member_vals[name][0].values.append(sub_desig)
                    else:
                        if name not in member_vals:
                            member_vals[name] = []
                        member_vals[name].append(sub_desig)
                    active_nested_member = name
                    # Track position for continuation: find array size and compute next pos
                    active_nested_size = 0
                    active_nested_pos = 0
                    for mname, mtype, moff in members:
                        if mname == name and isinstance(mtype, ast.ArrayType) and mtype.size is not None:
                            sz = mtype.size
                            active_nested_size = sz.value if isinstance(sz, ast.IntLiteral) else (sz if isinstance(sz, int) else 0)
                            # Compute next position from the last designator index
                            last_desig = val.designators[-1]
                            if isinstance(last_desig, int):
                                active_nested_pos = last_desig + 1
                            elif isinstance(last_desig, ast.IntLiteral):
                                active_nested_pos = last_desig.value + 1
                            elif hasattr(last_desig, 'value') and isinstance(last_desig.value, int):
                                active_nested_pos = last_desig.value + 1
                            break
                else:
                    member_vals[name] = [val.value]
                    active_nested_member = None
                # Update next_idx for sequential continuation after this member
                for idx, (mname, mtype, moff) in enumerate(members):
                    if mname == name:
                        next_idx = idx + 1
                        break
            else:
                actual_val = val.value if isinstance(val, ast.DesignatedInit) else val
                if active_nested_member is not None:
                    # Check if we've exceeded the nested member's capacity
                    if active_nested_size > 0 and active_nested_pos >= active_nested_size:
                        # Array is full — fall through to next struct member
                        active_nested_member = None
                    else:
                        # Continuation after nested designator - add to same member
                        member_vals[active_nested_member].append(actual_val)
                        active_nested_pos += 1
                        continue
                if active_nested_member is None:
                    # Non-designated value - goes to next sequential member
                    if next_idx < len(members):
                        mname = members[next_idx][0]
                        if mname:
                            member_vals[mname] = [actual_val]
                        next_idx += 1

        # Emit members in declaration order
        for member_name, member_type, member_offset in members:
            if member_name and member_name in member_vals:
                vals = member_vals[member_name]
                if len(vals) == 1 and not isinstance(vals[0], ast.DesignatedInit):
                    # Single plain value
                    self._emit_initializer(vals[0], member_type)
                elif any(isinstance(v, ast.DesignatedInit) for v in vals):
                    # Has designated inits - wrap in InitializerList and delegate
                    if isinstance(member_type, ast.StructType):
                        sub_members = self._get_struct_members(member_type)
                        if sub_members:
                            self._emit_struct_init_designated(vals, sub_members)
                            continue
                    elif isinstance(member_type, ast.ArrayType):
                        init_list = ast.InitializerList(values=vals, location=vals[0].location if hasattr(vals[0], 'location') else None)
                        self._emit_initializer(init_list, member_type)
                        continue
                    # Fallback for single designated init
                    self._emit_initializer(vals[0].value if isinstance(vals[0], ast.DesignatedInit) else vals[0], member_type)
                else:
                    # Multiple plain values - wrap as InitializerList
                    init_list = ast.InitializerList(values=vals, location=None)
                    self._emit_initializer(init_list, member_type)
            else:
                size = self._type_size(member_type)
                if size > 0:
                    self.ctx.emit_instr("ds", str(size))

        return len(values)

    def _emit_array_init_flat(self, values: list, start_index: int, array_type: ast.ArrayType) -> int:
        """Emit array initialization from flat values. Returns values consumed."""
        elem_type = array_type.base_type
        elem_size = self._type_size(elem_type)

        # Get array size
        array_size = 1
        if array_type.size:
            if isinstance(array_type.size, ast.IntLiteral):
                array_size = array_type.size.value
            else:
                sz = self._eval_const_expr(array_type.size)
                if sz is not None:
                    array_size = sz

        consumed = 0
        for i in range(array_size):
            idx = start_index + consumed
            if idx >= len(values):
                # No more values - zero-initialize
                self.ctx.emit_instr("ds", str(elem_size))
                continue

            val = values[idx]
            if isinstance(val, ast.DesignatedInit):
                val = val.value

            # Handle nested types
            if isinstance(elem_type, ast.StructType) and not isinstance(val, ast.InitializerList):
                nested_members = self._get_struct_members(elem_type)
                if nested_members:
                    nested_consumed = self._emit_struct_init_flat(values[idx:], nested_members)
                    consumed += nested_consumed
                else:
                    consumed += 1
                    self._emit_initializer(val, elem_type)
            elif isinstance(elem_type, ast.ArrayType) and not isinstance(val, ast.InitializerList):
                nested_consumed = self._emit_array_init_flat(values, idx, elem_type)
                consumed += nested_consumed
            else:
                consumed += 1
                self._emit_initializer(val, elem_type)

        return consumed

    def _emit_string_for_array(self, string_lit: ast.StringLiteral, array_type: ast.ArrayType) -> None:
        """Emit a string literal to fill a char array, with proper padding."""
        array_size = 1
        if array_type.size:
            if isinstance(array_type.size, ast.IntLiteral):
                array_size = array_type.size.value
            else:
                sz = self._eval_const_expr(array_type.size)
                if sz is not None:
                    array_size = sz

        string_val = string_lit.value
        escaped = self._escape_string(string_val)

        # Emit the string with null terminator
        self.ctx.emit_instr("db", f"'{escaped}',0")

        # String length including null
        string_len = len(string_val) + 1

        # Pad remaining bytes with zeros
        remaining = array_size - string_len
        if remaining > 0:
            self.ctx.emit_instr("ds", str(remaining))

    def _emit_int_value(self, value: int, size: int) -> None:
        """Emit an integer value with the specified size."""
        if size == 1:
            self.ctx.emit_instr("db", str(value & 0xFF))
        elif size == 2:
            self.ctx.emit_instr("dw", str(value & 0xFFFF))
        elif size == 4:
            # 32-bit: emit low word first, then high word
            val = value & 0xFFFFFFFF
            low = val & 0xFFFF
            high = (val >> 16) & 0xFFFF
            self.ctx.emit_instr("dw", str(low))
            self.ctx.emit_instr("dw", str(high))
        elif size == 8:
            # 64-bit: emit four words, low to high
            val = value & 0xFFFFFFFFFFFFFFFF
            self.ctx.emit_instr("dw", str(val & 0xFFFF))
            self.ctx.emit_instr("dw", str((val >> 16) & 0xFFFF))
            self.ctx.emit_instr("dw", str((val >> 32) & 0xFFFF))
            self.ctx.emit_instr("dw", str((val >> 48) & 0xFFFF))
        else:
            # Unknown size - emit as words, pad to size
            self.ctx.emit_instr("dw", str(value & 0xFFFF))
            if size > 2:
                self.ctx.emit_instr("ds", str(size - 2))

    def _emit_float_value(self, value: float) -> None:
        """Emit a 32-bit IEEE-754 float value."""
        import struct
        # Pack as little-endian 32-bit float
        packed = struct.pack('<f', value)
        # Unpack as little-endian 32-bit unsigned integer
        ieee_val = struct.unpack('<I', packed)[0]
        low = ieee_val & 0xFFFF
        high = (ieee_val >> 16) & 0xFFFF
        self.ctx.emit_instr("dw", str(low))
        self.ctx.emit_instr("dw", str(high))

    def _eval_const_expr(self, expr: ast.Expression) -> int | float | None:
        """Try to evaluate a constant expression at compile time. Returns None if not constant."""
        if isinstance(expr, ast.IntLiteral):
            return expr.value
        elif isinstance(expr, ast.FloatLiteral):
            return expr.value
        elif isinstance(expr, ast.Identifier):
            # Check for enum constant
            if expr.name in self.ctx.enum_constants:
                return self.ctx.enum_constants[expr.name]
            return None  # Not a compile-time constant
        elif isinstance(expr, ast.CharLiteral):
            # Character constants have type int; value is as-if stored in char
            # first then converted to int (C 6.4.4.4). Char is signed by default.
            val = expr.value
            if val >= 0x80:
                val = val - 0x100  # Sign extend signed char to int
            return val
        elif isinstance(expr, ast.UnaryOp):
            operand_val = self._eval_const_expr(expr.operand)
            if operand_val is None:
                return None
            if expr.op == "-":
                return -operand_val
            elif expr.op == "+":
                return operand_val
            elif expr.op == "~" and not isinstance(operand_val, float):
                return ~operand_val
            elif expr.op == "!":
                return 0 if operand_val else 1
        elif isinstance(expr, ast.Cast):
            # Evaluate the inner expression
            inner_val = self._eval_const_expr(expr.expr)
            if inner_val is None:
                return None
            # Apply type conversion based on target type
            target_type = expr.target_type
            if isinstance(target_type, ast.BasicType):
                name = target_type.name
                if self._is_float_type(target_type):
                    return float(inner_val)
                is_signed = self._is_signed_type(target_type)
                if isinstance(inner_val, float):
                    inner_val = int(inner_val)
                target_size = self.type_config.sizeof_basic(name)
                if target_size is not None and target_size > 0:
                    bits = target_size * 8
                    mask = (1 << bits) - 1
                    sign_bit = 1 << (bits - 1)
                    val = inner_val & mask
                    if is_signed and val >= sign_bit:
                        val = val - (1 << bits)  # Sign extend
                    return val
            return inner_val  # Fallback: no conversion
        elif isinstance(expr, ast.BinaryOp):
            left_val = self._eval_const_expr(expr.left)
            right_val = self._eval_const_expr(expr.right)
            if left_val is None or right_val is None:
                return None
            is_float = isinstance(left_val, float) or isinstance(right_val, float)
            if expr.op == "+":
                return left_val + right_val
            elif expr.op == "-":
                return left_val - right_val
            elif expr.op == "*":
                return left_val * right_val
            elif expr.op == "/" and right_val != 0:
                if is_float:
                    return left_val / right_val
                return left_val // right_val
            elif expr.op == "%" and right_val != 0:
                return left_val % right_val
            elif expr.op == "&" and not is_float:
                return left_val & right_val
            elif expr.op == "|" and not is_float:
                return left_val | right_val
            elif expr.op == "^" and not is_float:
                return left_val ^ right_val
            elif expr.op == "<<":
                return left_val << right_val
            elif expr.op == ">>":
                # Need to determine signedness of left operand for arithmetic vs logical shift
                left_type = self._get_expr_type(expr.left)
                is_signed = True  # C default is signed
                if left_type and isinstance(left_type, ast.BasicType):
                    if left_type.is_signed is False:
                        is_signed = False
                elif isinstance(expr.left, ast.IntLiteral) and expr.left.is_unsigned:
                    is_signed = False
                if is_signed:
                    # Determine width and do arithmetic shift
                    width = 16  # default int
                    if left_type:
                        sz = self._type_size(left_type)
                        width = sz * 8
                    elif isinstance(expr.left, ast.IntLiteral) and expr.left.is_long:
                        if left_val > 0x7FFFFFFF or left_val < -0x80000000:
                            width = 64
                        else:
                            width = 32
                    mask = (1 << width) - 1
                    sign_bit = 1 << (width - 1)
                    val = left_val & mask
                    if val & sign_bit:
                        # Negative in signed representation - arithmetic shift
                        val = val - (1 << width)  # Convert to negative Python int
                    return (val >> right_val) & mask
                else:
                    # Unsigned logical shift - mask to appropriate width first
                    width = 16  # default int
                    if left_type:
                        sz = self._type_size(left_type)
                        width = sz * 8
                    elif isinstance(expr.left, ast.IntLiteral) and expr.left.is_long:
                        if left_val > 0x7FFFFFFF or left_val < -0x80000000:
                            width = 64
                        else:
                            width = 32
                    mask = (1 << width) - 1
                    return (left_val & mask) >> right_val
            elif expr.op == "==":
                return 1 if left_val == right_val else 0
            elif expr.op == "!=":
                return 1 if left_val != right_val else 0
            elif expr.op == "<":
                return 1 if left_val < right_val else 0
            elif expr.op == ">":
                return 1 if left_val > right_val else 0
            elif expr.op == "<=":
                return 1 if left_val <= right_val else 0
            elif expr.op == ">=":
                return 1 if left_val >= right_val else 0
            elif expr.op == "&&":
                return 1 if left_val and right_val else 0
            elif expr.op == "||":
                return 1 if left_val or right_val else 0
        elif isinstance(expr, ast.SizeofType):
            return self._type_size(expr.target_type)
        elif isinstance(expr, ast.SizeofExpr):
            expr_type = self._get_expr_type(expr.expr)
            if expr_type:
                return self._type_size(expr_type)
            return None
        return None  # Not a constant expression

    def _escape_string(self, s: str) -> str:
        """Escape a string for assembly."""
        # For now, just handle basic escapes
        result = []
        i = 0
        while i < len(s):
            c = s[i]
            if c == "'":
                result.append("''")  # Escape single quote
            elif c == "\n":
                result.append("',0AH,'")
            elif c == "\r":
                result.append("',0DH,'")
            elif c == "\t":
                result.append("',09H,'")
            elif c == "\\":
                if i + 1 < len(s):
                    next_c = s[i + 1]
                    if next_c == "n":
                        result.append("',0AH,'")
                        i += 1
                    elif next_c == "r":
                        result.append("',0DH,'")
                        i += 1
                    elif next_c == "t":
                        result.append("',09H,'")
                        i += 1
                    elif next_c == "'":
                        result.append("''")
                        i += 1
                    elif next_c == "\\":
                        result.append("\\")
                        i += 1
                    else:
                        result.append(c)
                else:
                    result.append(c)
            else:
                result.append(c)
            i += 1
        return "".join(result)


def generate(unit: ast.TranslationUnit, module_name: str = "main",
             enable_shared_storage: bool = True,
             enable_dead_elimination: bool = True,
             enable_inlining: bool = True,
             enable_const_propagation: bool = True,
             whole_program: bool = True,
             embed_runtime: bool = False) -> str:
    """Generate Z80 assembly for a translation unit."""
    gen = CodeGenerator(module_name, enable_shared_storage, enable_dead_elimination,
                       enable_inlining, enable_const_propagation, whole_program, embed_runtime)
    return gen.generate(unit)
