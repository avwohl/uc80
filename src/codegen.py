"""Z80 code generator for C24 compiler.

Generates MACRO-80 compatible assembly (.mac files) for the um80 assembler.
Uses IX as frame pointer, following the calling convention in implementation_plan.md.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
import struct
from typing import Callable, Iterator, Optional
from . import ast


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
        emit_fn("PUSH", reg.upper())
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
            emit_fn("POP", reg.upper())
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

    def label(self) -> str:
        """Get the assembly label for this symbol."""
        # Static locals already have __ prefix, don't add another _
        if self.is_static:
            return self.name
        # Global symbols get _ prefix
        return f"_{self.name}"


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
            name = t.name
            if name in ("char", "_Bool", "bool"):
                return 1
            elif name in ("short", "int"):
                return 2
            elif name in ("long", "float", "double", "long double"):
                return 4
            elif name == "long long":
                return 8  # 64-bit
            elif name == "void":
                return 0
            return 2
        elif isinstance(t, ast.PointerType):
            return 2
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

    @staticmethod
    def _is_long_long_type_node(t: ast.TypeNode) -> bool:
        """Check if a type node is a 64-bit type."""
        if isinstance(t, ast.BasicType) and t.name == "long long":
            return True
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
                            param_map[param.name] = new_args[i]

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

    def _inline_functions_once(self, unit: ast.TranslationUnit) -> tuple[ast.TranslationUnit, int]:
        """Single pass of inlining. Used internally."""
        # Build function body map
        func_bodies: dict[str, ast.FunctionDecl] = {}
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl) and decl.body:
                func_bodies[decl.name] = decl

        # Count calls
        call_counts = self.count_calls()

        # Find inlineable functions (trivial functions that should be inlined)
        inlineable: set[str] = set()
        for name, func in func_bodies.items():
            if self.should_inline(name, func_bodies, call_counts):
                if self._is_trivial_function(func):
                    inlineable.add(name)

        if not inlineable:
            return unit, 0

        # Count inlined calls
        inlined_count = sum(call_counts.get(f, 0) for f in inlineable)

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

        return ast.TranslationUnit(declarations=new_decls), inlined_count

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
            return expr.value
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

    # Function names (for distinguishing functions from variables)
    function_names: set[str] = field(default_factory=set)

    # Enum constants: name -> integer value
    enum_constants: dict[str, int] = field(default_factory=dict)

    # Static local variables: label -> (type, init_value)
    static_locals: dict[str, tuple[ast.TypeNode, Optional[ast.Expression]]] = field(default_factory=dict)
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
        """Look up a symbol in local then global scope."""
        if name in self.locals:
            return self.locals[name]
        if name in self.globals:
            return self.globals[name]
        return None


class CodeGenerator:
    """Z80 code generator."""

    def __init__(self, module_name: str = "main", enable_shared_storage: bool = True,
                 enable_dead_elimination: bool = True, enable_inlining: bool = True,
                 enable_const_propagation: bool = True, whole_program: bool = True,
                 embed_runtime: bool = False):
        self.module_name = module_name
        self.ctx = CodeGenContext()
        self.enable_shared_storage = enable_shared_storage
        self.enable_dead_elimination = enable_dead_elimination
        self.enable_inlining = enable_inlining
        self.enable_const_propagation = enable_const_propagation
        self.whole_program = whole_program
        self.embed_runtime = embed_runtime
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

        # Count elements in initializer
        if isinstance(base_type, ast.StructType):
            # Struct array with flat init - count struct elements
            flat_count = self._count_struct_init_values(base_type)
            if flat_count > 0:
                array_size = (len(init.values) + flat_count - 1) // flat_count
            else:
                array_size = len(init.values)
        else:
            array_size = len(init.values)

        # Create new ArrayType with inferred size
        return ast.ArrayType(
            base_type=base_type,
            size=ast.IntLiteral(value=array_size, is_long=False, is_unsigned=False)
        )

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
            self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program)
            self.call_graph_analyzer.build_call_graph(unit)

        # Inline expansion (before dead elimination so inlined functions can be removed)
        if self.enable_inlining and self.call_graph_analyzer:
            unit, self.inlined_calls = self.call_graph_analyzer.inline_functions(unit)
            # Rebuild call graph after inlining
            if self.inlined_calls > 0:
                self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program)
                self.call_graph_analyzer.build_call_graph(unit)

        # Interprocedural constant propagation (after inlining, before dead elimination)
        if self.enable_const_propagation and self.call_graph_analyzer:
            unit, self.constants_propagated = self.call_graph_analyzer.propagate_constants(unit)
            # Rebuild call graph after constant propagation if any changes
            if self.constants_propagated > 0:
                self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program)
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
                self.call_graph_analyzer = CallGraphAnalyzer(whole_program=self.whole_program)
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
        self.ctx.emit("\t.Z80")
        self.ctx.emit()

        # First pass: collect global declarations
        for decl in unit.declarations:
            if isinstance(decl, ast.FunctionDecl):
                # Create FunctionType with return type and parameter types
                func_type = ast.FunctionType(
                    return_type=decl.return_type,
                    param_types=[p.param_type for p in decl.params],
                    is_variadic=decl.is_variadic if hasattr(decl, 'is_variadic') else False
                )
                self.ctx.globals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=func_type,
                    is_global=True
                )
                self.ctx.function_names.add(decl.name)
            elif isinstance(decl, ast.VarDecl):
                var_type = self._infer_array_size(decl.var_type, decl.init)
                self.ctx.globals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=var_type,
                    is_global=True
                )
            elif isinstance(decl, ast.DeclarationList):
                for d in decl.declarations:
                    if isinstance(d, ast.VarDecl):
                        var_type = self._infer_array_size(d.var_type, d.init)
                        self.ctx.globals[d.name] = Symbol(
                            name=d.name,
                            sym_type=var_type,
                            is_global=True
                        )

        # Code segment
        self.ctx.emit("\tCSEG")
        self.ctx.emit()

        # Generate code for each declaration
        for decl in unit.declarations:
            self.gen_declaration(decl)

        # Emit EXTRN for runtime functions used (unless embedding runtime)
        if self.ctx.runtime_used and not self.embed_runtime:
            self.ctx.emit()
            self.ctx.emit("; Runtime library functions")
            for name in sorted(self.ctx.runtime_used):
                self.ctx.emit_instr("EXTRN", name)

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
            self.ctx.emit_instr("EXTRN", f"_{name}")

        if global_vars:
            self.ctx.emit()
            self.ctx.emit("; Global variables")
            # Emit PUBLIC for non-static global variables so linker can resolve
            # cross-module references (same as we do for functions)
            for name, decl in global_vars.items():
                if decl.storage_class != "static":
                    self.ctx.emit_instr("PUBLIC", f"_{decl.name}")
            self.ctx.emit("\tDSEG")
            for name, decl in global_vars.items():
                # Use the inferred type from the Symbol (which has array size from initializer)
                sym = self.ctx.globals.get(name)
                var_type = sym.sym_type if sym else decl.var_type
                size = self._type_size(var_type)
                self.ctx.emit_label(f"_{decl.name}")
                if decl.init:
                    # Initialized global variable - use helper to emit data
                    self._emit_initializer(decl.init, var_type)
                else:
                    # Uninitialized global - just reserve space
                    self.ctx.emit_instr("DS", str(size))

        # Track whether DSEG has been emitted (global_vars emit it above)
        in_dseg = bool(global_vars)

        # Static local variables (in DSEG)
        if self.ctx.static_locals:
            if not in_dseg:
                self.ctx.emit()
                self.ctx.emit("\tDSEG")
                in_dseg = True
            self.ctx.emit("; Static local variables")
            for label, (var_type, init) in self.ctx.static_locals.items():
                self.ctx.emit_label(label)
                size = self._type_size(var_type)
                if init:
                    self._emit_initializer(init, var_type)
                else:
                    self.ctx.emit_instr("DS", str(size))

        # Data segment with string literals (emitted after globals so that
        # strings created during global initializer emission are included)
        if self.ctx.strings:
            if not in_dseg:
                self.ctx.emit()
                self.ctx.emit("\tDSEG")
                in_dseg = True
            self.ctx.emit()
            self.ctx.emit("; String literals")
            for label, value in self.ctx.strings.items():
                self.ctx.emit_label(label)
                if label in self.ctx.wide_strings:
                    # Wide string: emit each character as a 16-bit word (little-endian)
                    for ch in value:
                        self.ctx.emit_instr("DW", str(ord(ch)))
                    self.ctx.emit_instr("DW", "0")  # 16-bit null terminator
                else:
                    # Narrow string: emit as bytes with null terminator
                    escaped = self._escape_string(value)
                    self.ctx.emit_instr("DB", f"'{escaped}',0")

        # Shared automatic storage for non-recursive functions
        if self.call_graph_analyzer and self.call_graph_analyzer.total_shared_storage > 0:
            if not in_dseg:
                self.ctx.emit()
                self.ctx.emit("\tDSEG")
            self.ctx.emit()
            self.ctx.emit("; Shared automatic storage for non-recursive functions")
            self.ctx.emit_label("??AUTO")
            self.ctx.emit_instr("DS", str(self.call_graph_analyzer.total_shared_storage))

        self.ctx.emit()
        self.ctx.emit("\tEND")

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
                # (but not for static functions, which are defined locally)
                if decl.storage_class != "static":
                    self.ctx.emit_instr("EXTRN", f"_{decl.name}")
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

    def _register_struct(self, decl: ast.StructDecl) -> None:
        """Register a struct definition for later use."""
        if not decl.is_definition or not decl.name:
            return

        members = []
        anon_list = []
        offset = 0
        for member in decl.members:
            if member.name:
                members.append((member.name, member.member_type, offset))
            else:
                # Anonymous struct/union member - track for member lookup
                if isinstance(member.member_type, ast.StructType):
                    anon_list.append((member.member_type, offset))
            if decl.is_union:
                # Union: all members at offset 0
                pass
            else:
                # Struct: sequential layout
                offset += self._type_size(member.member_type)
        self.ctx.structs[decl.name] = members
        if anon_list:
            self.ctx.anon_members[decl.name] = anon_list

    def _register_enum_type_values(self, enum_type: ast.EnumType) -> None:
        """Register enum constants from an inline EnumType."""
        if not enum_type.values:
            return

        next_value = 0
        for enum_val in enum_type.values:
            if enum_val.value is not None:
                if isinstance(enum_val.value, ast.IntLiteral):
                    next_value = enum_val.value.value
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
                # Explicit value - must be a constant expression
                if isinstance(enum_val.value, ast.IntLiteral):
                    next_value = enum_val.value.value
                else:
                    # For now, only support integer literals
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
                members = []
                anon_list = []
                offset = 0
                for member in type_node.members:
                    if member.name:
                        members.append((member.name, member.member_type, offset))
                    elif isinstance(member.member_type, ast.StructType):
                        anon_list.append((member.member_type, offset))
                    if not type_node.is_union:
                        offset += self._type_size(member.member_type)
                self.ctx.structs[type_node.name] = members
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
                members = []
                anon_list = []
                offset = 0
                for member in struct_type.members:
                    if member.name:
                        members.append((member.name, member.member_type, offset))
                    elif isinstance(member.member_type, ast.StructType):
                        anon_list.append((member.member_type, offset))
                    if struct_type.is_union:
                        pass  # Union: all at offset 0
                    else:
                        offset += self._type_size(member.member_type)
                self.ctx.structs[struct_name] = members
                if anon_list:
                    self.ctx.anon_members[struct_name] = anon_list
        # For enum types, nothing special needed - enum values are already constants

    def gen_function(self, func: ast.FunctionDecl) -> None:
        """Generate code for a function."""
        if func.body is None:
            # Just a declaration, emit EXTRN (but not for static functions)
            if func.storage_class != "static":
                self.ctx.emit_instr("EXTRN", f"_{func.name}")
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
        if func.storage_class != "static":
            self.ctx.emit_instr("PUBLIC", f"_{func.name}")
        self.ctx.emit()
        if use_shared_storage:
            self.ctx.emit(f"; Function {func.name} (uses shared storage)")
        else:
            self.ctx.emit(f"; Function {func.name}")
        self.ctx.emit_label(f"_{func.name}")

        # Function prologue: save IX, set up frame
        self.ctx.emit_instr("PUSH", "IX")
        self.ctx.emit_instr("LD", "IX,0")
        self.ctx.emit_instr("ADD", "IX,SP")

        # Calculate space needed for locals (only for stack-based functions)
        if not use_shared_storage:
            local_size = self._calc_locals_size(func.body)
            if local_size > 0:
                self.ctx.emit_instr("LD", f"HL,-{local_size}")
                self.ctx.emit_instr("ADD", "HL,SP")
                self.ctx.emit_instr("LD", "SP,HL")

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
        self.ctx.emit_instr("LD", "SP,IX")
        self.ctx.emit_instr("POP", "IX")
        self.ctx.emit_instr("RET")
        self.ctx.emit()

        self.ctx.current_function = None
        self.ctx.current_return_type = None
        self._use_shared_storage = False

    def gen_compound_stmt(self, stmt: ast.CompoundStmt) -> None:
        """Generate code for a compound statement (block)."""
        for item in stmt.items:
            if isinstance(item, ast.Declaration):
                self.gen_local_decl(item)
            else:
                self.gen_statement(item)

    def gen_local_decl(self, decl: ast.Declaration) -> None:
        """Generate code for a local declaration."""
        if isinstance(decl, ast.DeclarationList):
            # Handle multiple declarations (e.g., 'int a, b;')
            for d in decl.declarations:
                self.gen_local_decl(d)
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
                        if isinstance(decl.init, ast.FloatLiteral):
                            # Compile-time conversion
                            int_val = int(decl.init.value)
                            self.ctx.emit_instr("LD", f"HL,{int_val}")
                        else:
                            # Runtime conversion
                            self.gen_expr(decl.init, force_long=True)
                            self._call_runtime("__ftoi")
                    elif is_long_long:
                        # 64-bit initialization
                        self._gen_64bit_operand(decl.init, to_tmp=False)
                    else:
                        # Both long and float need 32-bit handling
                        need_32bit = is_long or is_float
                        self.gen_expr(decl.init, force_long=need_32bit)

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

        # Store type and init value for data segment emission
        self.ctx.static_locals[label] = (decl.var_type, decl.init)
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
        self.ctx.emit_instr("LD", f"HL,{label}")
        # Destination: local variable address
        if sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"DE,??AUTO+{sym.shared_offset}")
        else:
            self.ctx.emit_instr("PUSH", "HL")
            self._gen_lea_ix_offset(sym.offset)
            self.ctx.emit_instr("EX", "DE,HL")
            self.ctx.emit_instr("POP", "HL")
        self.ctx.emit_instr("LD", f"BC,{total_size}")
        self.ctx.emit_instr("LDIR")

    def _gen_local_array_init(self, decl: ast.VarDecl) -> None:
        """Generate code to initialize a local array from an initializer list."""
        sym = self.ctx.locals[decl.name]
        init_list = decl.init
        elem_type = decl.var_type.base_type
        elem_size = self._type_size(elem_type)
        is_long = self._is_long_type(elem_type)

        for i, val in enumerate(init_list.values):
            # Handle DesignatedInit if present
            if isinstance(val, ast.DesignatedInit):
                val = val.value

            # Generate the value in HL (or DEHL for 32-bit)
            self.gen_expr(val, force_long=is_long)

            # Store at array[i]
            offset = i * elem_size
            if sym.uses_shared_storage:
                # Store to shared storage: ??AUTO+base+offset
                base = sym.shared_offset + offset
                if is_long:
                    self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
                    self.ctx.emit_instr("LD", f"(??AUTO+{base + 2}),DE")
                elif elem_size == 1:
                    self.ctx.emit_instr("LD", "A,L")
                    self.ctx.emit_instr("LD", f"(??AUTO+{base}),A")
                else:
                    self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
            else:
                # Stack-based: store at IX+base_offset+offset
                frame_off = sym.offset + offset
                if is_long:
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 2)}),E")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 3)}),D")
                elif elem_size == 1:
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                else:
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")

    def _gen_local_struct_init(self, decl: ast.VarDecl) -> None:
        """Generate code to initialize a local struct from an initializer list."""
        sym = self.ctx.locals[decl.name]
        struct_type = decl.var_type
        init_list = decl.init

        # Get struct members
        if not isinstance(struct_type, ast.StructType) or not struct_type.name:
            return
        if struct_type.name not in self.ctx.structs:
            return

        members = self.ctx.structs[struct_type.name]

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
                    self.ctx.emit_instr("LD", f"HL,{src_sym.label()}")
                elif src_sym.uses_shared_storage:
                    self.ctx.emit_instr("LD", f"HL,??AUTO+{src_sym.shared_offset}")
                else:
                    # Stack-based: IX + offset
                    self.ctx.emit_instr("PUSH", "IX")
                    self.ctx.emit_instr("POP", "HL")
                    if src_sym.offset != 0:
                        self.ctx.emit_instr("LD", f"DE,{src_sym.offset}")
                        self.ctx.emit_instr("ADD", "HL,DE")
        elif isinstance(init, ast.Call):
            # Function call returning struct - for structs > 2 bytes,
            # gen_return copies to __sret_buf and returns address in HL
            self.gen_expr(init)
            if size <= 2:
                # Small struct fits in HL
                sym_local = self.ctx.locals[decl.name]
                if sym_local.uses_shared_storage:
                    self.ctx.emit_instr("LD", f"(??AUTO+{sym_local.shared_offset}),HL")
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
                    self.ctx.emit_instr("LD", f"(??AUTO+{sym_local.shared_offset}),HL")
                else:
                    self._store_local(sym_local)
                return
            # HL = address, fall through to copy

        # Now HL has source address, copy bytes to destination
        # Use DE as destination pointer, BC for temp storage
        self.ctx.emit_instr("PUSH", "HL")  # Save source

        # Get destination address
        if sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"DE,??AUTO+{sym.shared_offset}")
        elif sym.is_global:
            self.ctx.emit_instr("LD", f"DE,{sym.label()}")
        else:
            self.ctx.emit_instr("PUSH", "IX")
            self.ctx.emit_instr("POP", "DE")
            if sym.offset != 0:
                self.ctx.emit_instr("LD", f"HL,{sym.offset}")
                self.ctx.emit_instr("ADD", "HL,DE")
                self.ctx.emit_instr("EX", "DE,HL")

        self.ctx.emit_instr("POP", "HL")  # Restore source
        self.ctx.emit_instr("LD", f"BC,{size}")
        self.ctx.emit_instr("LDIR")  # Copy BC bytes from HL to DE

    def _gen_struct_assignment(self, expr: ast.BinaryOp, struct_size: int) -> None:
        """Generate struct/union assignment via LDIR copy."""
        # Evaluate source expression to get source address in HL
        right = expr.right
        if isinstance(right, ast.Call):
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

        self.ctx.emit_instr("PUSH", "HL")  # Save source address

        # Get destination address
        self._gen_address(expr.left)
        self.ctx.emit_instr("EX", "DE,HL")  # DE = destination

        self.ctx.emit_instr("POP", "HL")    # HL = source
        self.ctx.emit_instr("LD", f"BC,{struct_size}")
        self.ctx.emit_instr("LDIR")

    def _gen_struct_init_values(self, sym: 'Symbol', struct_type: ast.StructType,
                                values: list, base_offset: int) -> int:
        """Generate code to store struct initializer values. Returns number of values consumed."""
        if not struct_type.name or struct_type.name not in self.ctx.structs:
            return 0

        members = self.ctx.structs[struct_type.name]

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

        for member_name, member_type, member_offset in members:
            if value_index >= len(values):
                # No more values - zero-initialize remaining members
                self._gen_zero_init_member(sym, member_type, base_offset + member_offset)
                continue

            val = values[value_index]

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

        for val in values:
            if isinstance(val, ast.DesignatedInit) and val.designators and isinstance(val.designators[0], str):
                desig_name = val.designators[0]
                if len(val.designators) > 1:
                    # Nested designator like .a[1] = 5 or .a.j = 5
                    if desig_name not in member_vals or not isinstance(member_vals[desig_name], list):
                        member_vals[desig_name] = []
                    member_vals[desig_name].append((val.designators[1:], val.value))
                    active_nested_member = desig_name
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
                    # Continuation after nested designator - add to same member
                    # Use None designators to indicate "next sequential element"
                    member_vals[active_nested_member].append((None, actual_val))
                else:
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
                self.ctx.emit_instr("XOR", "A")
                self.ctx.emit_instr("LD", f"(??AUTO+{base + i}),A")
        else:
            # Use LDIR for efficiency if size > 4
            if size > 4:
                frame_off = sym.offset + offset
                # Zero first byte
                self.ctx.emit_instr("XOR", "A")
                self._gen_lea_ix_offset(frame_off)  # HL = dest address
                self.ctx.emit_instr("LD", "(HL),A")
                if size > 1:
                    # Copy first byte to rest using LDIR
                    self.ctx.emit_instr("LD", "D,H")
                    self.ctx.emit_instr("LD", "E,L")
                    self.ctx.emit_instr("INC", "DE")
                    self.ctx.emit_instr("LD", f"BC,{size - 1}")
                    self.ctx.emit_instr("LDIR")
            else:
                frame_off = sym.offset + offset
                self.ctx.emit_instr("XOR", "A")
                for i in range(size):
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + i)}),A")

    def _gen_lea_ix_offset(self, offset: int) -> None:
        """Load effective address IX+offset into HL."""
        self.ctx.emit_instr("PUSH", "IX")
        self.ctx.emit_instr("POP", "HL")
        if offset != 0:
            self.ctx.emit_instr("LD", f"DE,{offset}")
            self.ctx.emit_instr("ADD", "HL,DE")

    def _gen_struct_copy(self, dest_sym: 'Symbol', dest_offset: int,
                         src_sym: 'Symbol', src_offset: int, size: int) -> None:
        """Copy bytes from one struct location to another."""
        # Copy byte by byte
        for i in range(size):
            # Load source byte
            if src_sym.uses_shared_storage:
                src_addr = src_sym.shared_offset + src_offset + i
                self.ctx.emit_instr("LD", f"A,(??AUTO+{src_addr})")
            elif src_sym.is_global:
                self.ctx.emit_instr("LD", f"A,(_{src_sym.name}+{src_offset + i})")
            else:
                frame_off = src_sym.offset + src_offset + i
                self.ctx.emit_instr("LD", f"A,({ix_off(frame_off)})")

            # Store to destination byte
            if dest_sym.uses_shared_storage:
                dest_addr = dest_sym.shared_offset + dest_offset + i
                self.ctx.emit_instr("LD", f"(??AUTO+{dest_addr}),A")
            elif dest_sym.is_global:
                self.ctx.emit_instr("LD", f"(_{dest_sym.name}+{dest_offset + i}),A")
            else:
                frame_off = dest_sym.offset + dest_offset + i
                self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),A")

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
                    self.ctx.emit_instr("LD", f"HL,{src_sym.label()}")
                elif src_sym.uses_shared_storage:
                    self.ctx.emit_instr("LD", f"HL,??AUTO+{src_sym.shared_offset}")
                else:
                    self.ctx.emit_instr("PUSH", "IX")
                    self.ctx.emit_instr("POP", "HL")
                    if src_sym.offset != 0:
                        self.ctx.emit_instr("LD", f"DE,{src_sym.offset}")
                        self.ctx.emit_instr("ADD", "HL,DE")
        else:
            # Unsupported expression type - fall through with gen_expr
            self.gen_expr(expr)

        # HL has source address, copy bytes to destination using LDIR
        self.ctx.emit_instr("PUSH", "HL")  # Save source

        # Get destination address
        if dest_sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"DE,??AUTO+{dest_sym.shared_offset + dest_offset}")
        elif dest_sym.is_global:
            self.ctx.emit_instr("LD", f"DE,{dest_sym.label()}+{dest_offset}")
        else:
            self.ctx.emit_instr("PUSH", "IX")
            self.ctx.emit_instr("POP", "DE")
            frame_off = dest_sym.offset + dest_offset
            if frame_off != 0:
                self.ctx.emit_instr("LD", f"HL,{frame_off}")
                self.ctx.emit_instr("ADD", "HL,DE")
                self.ctx.emit_instr("EX", "DE,HL")

        self.ctx.emit_instr("POP", "HL")  # Restore source
        self.ctx.emit_instr("LD", f"BC,{size}")
        self.ctx.emit_instr("LDIR")  # Copy BC bytes from HL to DE

    def _gen_struct_copy_from_addr_expr(self, dest_sym: 'Symbol', dest_offset: int,
                                          expr: ast.Expression, size: int) -> None:
        """Copy struct from an addressable expression (member access, etc.) to dest."""
        # Get source address into HL using _gen_address
        self._gen_address(expr)

        # HL has source address, copy bytes to destination using LDIR
        self.ctx.emit_instr("PUSH", "HL")  # Save source

        # Get destination address
        if dest_sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"DE,??AUTO+{dest_sym.shared_offset + dest_offset}")
        elif dest_sym.is_global:
            self.ctx.emit_instr("LD", f"DE,{dest_sym.label()}+{dest_offset}")
        else:
            self.ctx.emit_instr("PUSH", "IX")
            self.ctx.emit_instr("POP", "DE")
            frame_off = dest_sym.offset + dest_offset
            if frame_off != 0:
                self.ctx.emit_instr("LD", f"HL,{frame_off}")
                self.ctx.emit_instr("ADD", "HL,DE")
                self.ctx.emit_instr("EX", "DE,HL")

        self.ctx.emit_instr("POP", "HL")  # Restore source
        self.ctx.emit_instr("LD", f"BC,{size}")
        self.ctx.emit_instr("LDIR")  # Copy BC bytes from HL to DE

    def _gen_flat_array_init(self, sym: 'Symbol', array_type: ast.ArrayType,
                             values: list, start_index: int, base_offset: int) -> int:
        """Initialize an array from flat values. Returns number of values consumed."""
        elem_type = array_type.base_type
        elem_size = self._type_size(elem_type)
        is_long = self._is_long_type(elem_type)

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
                self.gen_expr(val, force_long=is_long)
                if is_long and not self._is_long_expr(val):
                    is_signed = not self._is_unsigned_expr(val)
                    self._extend_hl_to_dehl(is_signed)

                # Store
                if sym.uses_shared_storage:
                    base = sym.shared_offset + offset
                    if is_long:
                        self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
                        self.ctx.emit_instr("LD", f"(??AUTO+{base + 2}),DE")
                    elif elem_size == 1:
                        self.ctx.emit_instr("LD", "A,L")
                        self.ctx.emit_instr("LD", f"(??AUTO+{base}),A")
                    else:
                        self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
                else:
                    frame_off = sym.offset + offset
                    if is_long:
                        self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                        self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")
                        self.ctx.emit_instr("LD", f"({ix_off(frame_off + 2)}),E")
                        self.ctx.emit_instr("LD", f"({ix_off(frame_off + 3)}),D")
                    elif elem_size == 1:
                        self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                    else:
                        self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                        self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")

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
                self.ctx.emit_instr("LD", f"(??AUTO+{base}),A")
            else:
                frame_off = sym.offset + base_offset + i
                self.ctx.emit_instr("ld", f"a,{char_val}")
                self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),A")

        # Zero-fill remaining bytes
        for i in range(len(string_val), array_size):
            if sym.uses_shared_storage:
                base = sym.shared_offset + base_offset + i
                self.ctx.emit_instr("xor", "a")
                self.ctx.emit_instr("LD", f"(??AUTO+{base}),A")
            else:
                frame_off = sym.offset + base_offset + i
                self.ctx.emit_instr("xor", "a")
                self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),A")

    def _gen_zero_init_member(self, sym: 'Symbol', member_type: ast.TypeNode, offset: int) -> None:
        """Zero-initialize a struct member."""
        size = self._type_size(member_type)
        # For now, just store zeros
        self.ctx.emit_instr("LD", "HL,0")
        if sym.uses_shared_storage:
            base = sym.shared_offset + offset
            for i in range(0, size, 2):
                if i + 1 < size:
                    self.ctx.emit_instr("LD", f"(??AUTO+{base + i}),HL")
                else:
                    self.ctx.emit_instr("LD", "A,L")
                    self.ctx.emit_instr("LD", f"(??AUTO+{base + i}),A")

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
                self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
                self.ctx.emit_instr("LD", f"(??AUTO+{base + 2}),DE")
            elif member_size == 1:
                self.ctx.emit_instr("LD", "A,L")
                self.ctx.emit_instr("LD", f"(??AUTO+{base}),A")
            else:
                self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
        else:
            frame_off = sym.offset + offset
            if is_32bit:
                self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")
                self.ctx.emit_instr("LD", f"({ix_off(frame_off + 2)}),E")
                self.ctx.emit_instr("LD", f"({ix_off(frame_off + 3)}),D")
            elif member_size == 1:
                self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
            else:
                self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")

    def _gen_array_init_values(self, sym: 'Symbol', array_type: ast.ArrayType,
                                values: list, base_offset: int) -> None:
        """Generate code to store array initializer values at an offset."""
        elem_type = array_type.base_type
        elem_size = self._type_size(elem_type)
        is_long = self._is_long_type(elem_type)

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

            # Generate value
            self.gen_expr(val, force_long=is_long)
            if is_long and not self._is_long_expr(val):
                is_signed = not self._is_unsigned_expr(val)
                self._extend_hl_to_dehl(is_signed)

            # Store
            if sym.uses_shared_storage:
                base = sym.shared_offset + offset
                if is_long:
                    self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
                    self.ctx.emit_instr("LD", f"(??AUTO+{base + 2}),DE")
                elif elem_size == 1:
                    self.ctx.emit_instr("LD", "A,L")
                    self.ctx.emit_instr("LD", f"(??AUTO+{base}),A")
                else:
                    self.ctx.emit_instr("LD", f"(??AUTO+{base}),HL")
            else:
                frame_off = sym.offset + offset
                if is_long:
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 2)}),E")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 3)}),D")
                elif elem_size == 1:
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                else:
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off)}),L")
                    self.ctx.emit_instr("LD", f"({ix_off(frame_off + 1)}),H")

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
                self.ctx.emit_instr("LD", "DE,__sret_buf")
                self.ctx.emit_instr("LD", f"BC,{struct_size}")
                self.ctx.emit_instr("LDIR")
                # Return address of __sret_buf
                self.ctx.emit_instr("LD", "HL,__sret_buf")
            elif self._is_long_long_type(ret_type):
                # 64-bit return: generate value into __acc64
                self._gen_64bit_operand(stmt.value, to_tmp=False)
                # Caller retrieves from __acc64
            else:
                return_is_long = self._is_long_type(ret_type)
                # Generate expression, forcing long if return type is long
                self.gen_expr(stmt.value, force_long=return_is_long)
                # Extend to 32-bit if return type is long but expression is not
                if return_is_long and not self._is_long_expr(stmt.value):
                    is_signed = self._is_signed_type(self._get_expr_type(stmt.value))
                    self._extend_hl_to_dehl(is_signed)
        # Jump to function epilogue
        self.ctx.emit_instr("JP", f"@{self.ctx.current_function}_ret")

    def gen_if(self, stmt: ast.IfStmt) -> None:
        """Generate code for if statement."""
        else_label = self.ctx.new_label("ELSE")
        end_label = self.ctx.new_label("ENDIF")

        # Evaluate condition
        self.gen_expr(stmt.condition)

        # Test if HL is zero
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")

        if stmt.else_branch:
            self.ctx.emit_instr("JP", f"Z,{else_label}")
            self.gen_statement(stmt.then_branch)
            self.ctx.emit_instr("JP", end_label)
            self.ctx.emit_label(else_label)
            self.gen_statement(stmt.else_branch)
            self.ctx.emit_label(end_label)
        else:
            self.ctx.emit_instr("JP", f"Z,{end_label}")
            self.gen_statement(stmt.then_branch)
            self.ctx.emit_label(end_label)

    def gen_while(self, stmt: ast.WhileStmt) -> None:
        """Generate code for while loop."""
        start_label = self.ctx.new_label("WHILE")
        end_label = self.ctx.new_label("ENDWHILE")

        self.ctx.break_labels.append(end_label)
        self.ctx.continue_labels.append(start_label)

        self.ctx.emit_label(start_label)
        self.gen_expr(stmt.condition)
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")
        self.ctx.emit_instr("JP", f"Z,{end_label}")

        self.gen_statement(stmt.body)
        self.ctx.emit_instr("JP", start_label)
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
        self.gen_expr(stmt.condition)
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")
        self.ctx.emit_instr("JP", f"NZ,{start_label}")
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
            self.gen_expr(stmt.condition)
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("OR", "L")
            self.ctx.emit_instr("JP", f"Z,{end_label}")

        # Body
        self.gen_statement(stmt.body)

        # Update
        self.ctx.emit_label(update_label)
        if stmt.update:
            self.gen_expr(stmt.update)

        self.ctx.emit_instr("JP", start_label)
        self.ctx.emit_label(end_label)

        self.ctx.break_labels.pop()
        self.ctx.continue_labels.pop()

    def gen_break(self) -> None:
        """Generate code for break statement."""
        if self.ctx.break_labels:
            self.ctx.emit_instr("JP", self.ctx.break_labels[-1])

    def gen_continue(self) -> None:
        """Generate code for continue statement."""
        if self.ctx.continue_labels:
            self.ctx.emit_instr("JP", self.ctx.continue_labels[-1])

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

        # Generate comparison chain
        for value, label in cases:
            # Compare HL with value
            self.ctx.emit_instr("LD", f"DE,{value}")
            self.ctx.emit_instr("OR", "A")  # Clear carry
            self.ctx.emit_instr("SBC", "HL,DE")
            self.ctx.emit_instr("ADD", "HL,DE")  # Restore HL (SBC modified it)
            self.ctx.emit_instr("JP", f"Z,{label}")

        # Jump to default or end
        if default_label:
            self.ctx.emit_instr("JP", default_label)
        else:
            self.ctx.emit_instr("JP", end_label)

        # Save previous switch context (for nested switches)
        saved_cases = getattr(self, '_switch_cases', [])
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
        self.ctx.emit_instr("JP", f"@L_{func}_{stmt.label}")

    def gen_expr(self, expr: ast.Expression, force_long: bool = False) -> None:
        """Generate code for an expression. Result in HL (16-bit) or DEHL (32-bit)."""
        if isinstance(expr, ast.IntLiteral):
            if self._is_long_expr(expr) or force_long:
                # 32-bit literal: load into DEHL (DE=high, HL=low)
                val = expr.value & 0xFFFFFFFF
                low = val & 0xFFFF
                high = (val >> 16) & 0xFFFF
                self.ctx.emit_instr("LD", f"HL,{low}")
                self.ctx.emit_instr("LD", f"DE,{high}")
            else:
                self.ctx.emit_instr("LD", f"HL,{expr.value}")

        elif isinstance(expr, ast.FloatLiteral):
            # Convert float to IEEE 754 single precision and load as 32-bit
            ieee_val = float_to_ieee754(expr.value)
            low = ieee_val & 0xFFFF
            high = (ieee_val >> 16) & 0xFFFF
            self.ctx.emit_instr("LD", f"HL,{low}")
            self.ctx.emit_instr("LD", f"DE,{high}")

        elif isinstance(expr, ast.CharLiteral):
            self.ctx.emit_instr("LD", f"HL,{expr.value}")

        elif isinstance(expr, ast.StringLiteral):
            label = self.ctx.add_string(expr.value, is_wide=expr.is_wide)
            self.ctx.emit_instr("LD", f"HL,{label}")

        elif isinstance(expr, ast.BoolLiteral):
            val = 1 if expr.value else 0
            self.ctx.emit_instr("LD", f"HL,{val}")

        elif isinstance(expr, ast.NullptrLiteral):
            self.ctx.emit_instr("LD", "HL,0")

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
            self.ctx.emit_instr("LD", f"HL,{size}")

        elif isinstance(expr, ast.SizeofExpr):
            # Infer type of expression and compute its size
            expr_type = self._get_expr_type(expr.expr)
            if expr_type:
                size = self._type_size(expr_type)
            else:
                size = 2  # Default to int if type cannot be inferred
            self.ctx.emit_instr("LD", f"HL,{size}")

        elif isinstance(expr, ast.Compound):
            # Compound literal: (type){initializer}
            # For simple structs with one value, just evaluate that value
            if isinstance(expr.init, ast.InitializerList) and len(expr.init.values) == 1:
                val = expr.init.values[0]
                if isinstance(val, ast.DesignatedInit):
                    val = val.value
                self.gen_expr(val, force_long)
            elif isinstance(expr.init, ast.InitializerList) and len(expr.init.values) > 1:
                # Multi-member struct compound literal - evaluate first value
                # (used as an expression, struct gets truncated to first member)
                val = expr.init.values[0]
                if isinstance(val, ast.DesignatedInit):
                    val = val.value
                self.gen_expr(val, force_long)
            else:
                self.ctx.emit_instr("LD", "HL,0")

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
                self.ctx.emit_instr("LD", "HL,0")
            else:
                # Other statement type - execute and return 0
                self.gen_statement(last)
                self.ctx.emit_instr("LD", "HL,0")
        else:
            self.ctx.emit_instr("LD", "HL,0")

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
            self.ctx.emit_instr("LD", "HL,0")

    def gen_identifier(self, expr: ast.Identifier, force_long: bool = False) -> None:
        """Generate code to load an identifier's value into HL (or DEHL for 32-bit)."""
        # Check for enum constant first
        if expr.name in self.ctx.enum_constants:
            val = self.ctx.enum_constants[expr.name]
            self.ctx.emit_instr("LD", f"HL,{val}")
            if force_long:
                # Sign-extend negative enum values to 32-bit
                if val < 0:
                    self.ctx.emit_instr("LD", "DE,65535")
                else:
                    self.ctx.emit_instr("LD", "DE,0")
            return

        sym = self.ctx.lookup(expr.name)
        if sym is None:
            # Assume external function - load its address
            self.ctx.emit_instr("LD", f"HL,_{expr.name}")
            return

        # Check if this is a function (name matches a function we've seen)
        # Functions used as values decay to pointers - load address, not value
        # Only apply to global symbols - local variables can shadow function names
        if isinstance(sym.sym_type, ast.FunctionType) or (sym.is_global and expr.name in self.ctx.function_names):
            self.ctx.emit_instr("LD", f"HL,{sym.label()}")
            return

        # Arrays decay to pointers - return address, not value
        if isinstance(sym.sym_type, ast.ArrayType):
            if sym.is_global:
                self.ctx.emit_instr("LD", f"HL,{sym.label()}")
            elif sym.uses_shared_storage:
                # Load address of array in shared storage
                self.ctx.emit_instr("LD", f"HL,??AUTO+{sym.shared_offset}")
            else:
                # Compute address IX+offset
                if sym.offset >= 0:
                    self.ctx.emit_instr("LD", f"HL,{sym.offset}")
                else:
                    self.ctx.emit_instr("LD", f"HL,{sym.offset}")
                self.ctx.emit_instr("PUSH", "IX")
                self.ctx.emit_instr("POP", "DE")
                self.ctx.emit_instr("ADD", "HL,DE")
            return

        type_is_long_long = self._is_long_long_type(sym.sym_type)
        type_is_long = self._is_long_type(sym.sym_type)
        type_is_float = self._is_float_type(sym.sym_type)
        type_size = self._type_size(sym.sym_type)

        if type_is_long_long:
            # 64-bit variable: load via __load64, return low 32 bits in DEHL
            if sym.is_global:
                self.ctx.emit_instr("LD", f"HL,{sym.label()}")
            elif sym.uses_shared_storage:
                self.ctx.emit_instr("LD", f"HL,??AUTO+{sym.shared_offset}")
            else:
                self.ctx.emit_instr("PUSH", "IX")
                self.ctx.emit_instr("POP", "HL")
                self.ctx.emit_instr("LD", f"DE,{sym.offset}")
                self.ctx.emit_instr("ADD", "HL,DE")
            self._call_runtime("__load64")
            self.ctx.runtime_used.add("__acc64")
            self.ctx.emit_instr("LD", "HL,(__acc64)")
            self.ctx.emit_instr("LD", "DE,(__acc64+2)")
            return

        if sym.is_global:
            if type_is_long or type_is_float:
                # Load 32-bit value
                self.ctx.emit_instr("LD", f"HL,({sym.label()})")
                self.ctx.emit_instr("LD", f"DE,({sym.label()}+2)")
            elif type_size == 1:
                # Load 8-bit value, sign/zero-extend to HL
                self.ctx.emit_instr("LD", f"A,({sym.label()})")
                self.ctx.emit_instr("LD", "L,A")
                self._emit_char_to_hl(self._is_signed_type(sym.sym_type))
            else:
                # Load 16-bit value
                self.ctx.emit_instr("LD", f"HL,({sym.label()})")
        else:
            # Local variable: IX+offset or shared storage
            if type_is_long or type_is_float:
                self._load_local_32(sym)
            elif type_size == 1:
                # Load 8-bit value, sign/zero-extend to HL
                char_signed = self._is_signed_type(sym.sym_type)
                if sym.uses_shared_storage:
                    self.ctx.emit_instr("LD", f"A,(??AUTO+{sym.shared_offset})")
                    self.ctx.emit_instr("LD", "L,A")
                else:
                    self.ctx.emit_instr("LD", f"L,({ix_off(sym.offset)})")
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
        # Generate left operand (result in HL)
        self.gen_expr(expr.left)

        # Save left operand to stack (DE is used internally by various address computations)
        self.ctx.emit_instr("PUSH", "HL")

        # Generate right operand (result in HL)
        self.gen_expr(expr.right)

        # Restore left operand to DE
        self.ctx.emit_instr("POP", "DE")

        # Now: left in DE, right in HL
        # Perform operation, result in HL
        if op == "+":
            self.ctx.emit_instr("ADD", "HL,DE")
        elif op == "-":
            self.ctx.emit_instr("EX", "DE,HL")
            self.ctx.emit_instr("OR", "A")  # Clear carry
            self.ctx.emit_instr("SBC", "HL,DE")
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
                    self.ctx.emit_instr("EX", "DE,HL")
                # Now variable is in HL
                for _ in range(shift):
                    self.ctx.emit_instr("ADD", "HL,HL")
        elif op == "/":
            # Use signed or unsigned division based on operand types
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
            self._call_runtime("__div16" if is_unsigned else "__sdiv16")
        elif op == "%":
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
            self._call_runtime("__mod16" if is_unsigned else "__smod16")
        elif op == "&":
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("AND", "D")
            self.ctx.emit_instr("LD", "H,A")
            self.ctx.emit_instr("LD", "A,L")
            self.ctx.emit_instr("AND", "E")
            self.ctx.emit_instr("LD", "L,A")
        elif op == "|":
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("OR", "D")
            self.ctx.emit_instr("LD", "H,A")
            self.ctx.emit_instr("LD", "A,L")
            self.ctx.emit_instr("OR", "E")
            self.ctx.emit_instr("LD", "L,A")
        elif op == "^":
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("XOR", "D")
            self.ctx.emit_instr("LD", "H,A")
            self.ctx.emit_instr("LD", "A,L")
            self.ctx.emit_instr("XOR", "E")
            self.ctx.emit_instr("LD", "L,A")
        elif op == "<<":
            # Strength reduction: shift left by small constant → repeated ADD HL,HL
            # At this point: left in DE, right (shift count) in HL
            if isinstance(expr.right, ast.IntLiteral) and 1 <= expr.right.value <= 8:
                shift = expr.right.value
                self.ctx.emit_instr("EX", "DE,HL")  # value to HL
                for _ in range(shift):
                    self.ctx.emit_instr("ADD", "HL,HL")
            else:
                self._call_runtime("__shl16")
        elif op == ">>":
            # Strength reduction: right shift by small constant → inline shifts
            # At this point: left in DE, right (shift count) in HL
            if isinstance(expr.right, ast.IntLiteral) and 1 <= expr.right.value <= 4:
                shift = expr.right.value
                is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
                self.ctx.emit_instr("EX", "DE,HL")  # value to HL
                for _ in range(shift):
                    if is_unsigned:
                        self.ctx.emit_instr("SRL", "H")
                    else:
                        self.ctx.emit_instr("SRA", "H")
                    self.ctx.emit_instr("RR", "L")
            else:
                self._call_runtime("__shr16")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            # Check if either operand is unsigned for proper comparison
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
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

        # Generate right operand, extend to 32-bit if needed
        self.gen_expr(expr.right, force_long=True)
        if not right_is_long:
            # Need to extend 16-bit to 32-bit
            is_signed = not self._is_unsigned_expr(expr.right)
            self._extend_hl_to_dehl(is_signed)

        # Store right operand to __tmp32
        self._store_tmp32()

        # Check if left operand might clobber __tmp32
        left_is_complex = self._uses_tmp32(expr.left)
        if left_is_complex:
            # Save __tmp32 on stack before generating left operand
            self.ctx.emit_instr("LD", "HL,(__tmp32)")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", "HL,(__tmp32+2)")
            self.ctx.emit_instr("PUSH", "HL")

        # Generate left operand, extend to 32-bit if needed
        self.gen_expr(expr.left, force_long=True)
        if not left_is_long:
            is_signed = not self._is_unsigned_expr(expr.left)
            self._extend_hl_to_dehl(is_signed)

        if left_is_complex:
            # Restore __tmp32 from stack (need to save DEHL first)
            self.ctx.emit_instr("PUSH", "DE")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", "HL,4")
            self.ctx.emit_instr("ADD", "HL,SP")
            # Stack now: [saved HL][saved DE][high tmp][low tmp]...
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "(__tmp32+2),DE")
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("LD", "(__tmp32),DE")
            # Restore DEHL
            self.ctx.emit_instr("POP", "HL")
            self.ctx.emit_instr("POP", "DE")
            # Clean up saved __tmp32 from stack
            self.ctx.emit_instr("INC", "SP")
            self.ctx.emit_instr("INC", "SP")
            self.ctx.emit_instr("INC", "SP")
            self.ctx.emit_instr("INC", "SP")

        # Now: left in DEHL, right in __tmp32
        if op == "+":
            self._call_runtime("__add32")
        elif op == "-":
            self._call_runtime("__sub32")
        elif op == "*":
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
            if is_unsigned:
                self._call_runtime("__mul32")
            else:
                self._call_runtime("__smul32")
        elif op == "/":
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
            self._call_runtime("__div32" if is_unsigned else "__sdiv32")
        elif op == "%":
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
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
            self.ctx.emit_instr("LD", "A,(__tmp32)")
            self._call_runtime("__shl32")
        elif op == ">>":
            self.ctx.emit_instr("LD", "A,(__tmp32)")
            is_unsigned = self._is_unsigned_expr(expr.left)
            if is_unsigned:
                self._call_runtime("__shr32")
            else:
                self._call_runtime("__sar32")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
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
                self.ctx.emit_instr("JP", f"Z,{true_label}")
            else:
                self.ctx.emit_instr("JP", f"NZ,{true_label}")
        elif is_unsigned:
            # Unsigned comparison - use __cmp32 directly
            self._call_runtime("__cmp32")
            if op == "<":
                self.ctx.emit_instr("JP", f"C,{true_label}")
            elif op == ">=":
                self.ctx.emit_instr("JP", f"NC,{true_label}")
            elif op == ">":
                self.ctx.emit_instr("JP", f"Z,{false_label}")
                self.ctx.emit_instr("JP", f"NC,{true_label}")
            elif op == "<=":
                self.ctx.emit_instr("JP", f"Z,{true_label}")
                self.ctx.emit_instr("JP", f"C,{true_label}")
        else:
            # Signed comparison - check sign bits first
            # Left sign is in D (high byte of DEHL)
            # Right sign is in __tmp32+3
            sign_same = self.ctx.new_label("CMP_SS")

            # Check if signs are different: (left_sign XOR right_sign) & 0x80
            self.ctx.emit_instr("LD", "A,(__tmp32+3)")  # A = high byte of right
            self.ctx.emit_instr("XOR", "D")  # XOR with high byte of left
            self.ctx.emit_instr("JP", f"P,{sign_same}")  # If bit 7 clear, signs are same

            # Signs differ - negative is always less than positive
            # If left is negative (D & 0x80), left < right
            self.ctx.emit_instr("LD", "A,D")
            self.ctx.emit_instr("AND", "80H")
            if op == "<":
                self.ctx.emit_instr("JP", f"NZ,{true_label}")  # Left negative -> true
                self.ctx.emit_instr("JP", false_label)
            elif op == ">=":
                self.ctx.emit_instr("JP", f"Z,{true_label}")  # Left positive -> true
                self.ctx.emit_instr("JP", false_label)
            elif op == ">":
                self.ctx.emit_instr("JP", f"Z,{true_label}")  # Left positive -> true
                self.ctx.emit_instr("JP", false_label)
            elif op == "<=":
                self.ctx.emit_instr("JP", f"NZ,{true_label}")  # Left negative -> true
                self.ctx.emit_instr("JP", false_label)

            # Signs are the same - do unsigned comparison
            self.ctx.emit_label(sign_same)
            self._call_runtime("__cmp32")
            if op == "<":
                self.ctx.emit_instr("JP", f"C,{true_label}")
            elif op == ">=":
                self.ctx.emit_instr("JP", f"NC,{true_label}")
            elif op == ">":
                self.ctx.emit_instr("JP", f"Z,{false_label}")
                self.ctx.emit_instr("JP", f"NC,{true_label}")
            elif op == "<=":
                self.ctx.emit_instr("JP", f"Z,{true_label}")
                self.ctx.emit_instr("JP", f"C,{true_label}")

        # Fall through to false
        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("LD", "HL,0")
        self.ctx.emit_instr("JP", end_label)
        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("LD", "HL,1")
        self.ctx.emit_label(end_label)
        # Clear DE for 16-bit result
        self.ctx.emit_instr("LD", "DE,0")

    def _gen_binary_op_64(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate 64-bit binary operation. Result in __acc64."""
        # For 64-bit: right operand goes to __tmp64, left to __acc64, call runtime
        # Mark both 64-bit storage variables as used for EXTRN declarations
        self.ctx.runtime_used.add("__acc64")
        self.ctx.runtime_used.add("__tmp64")

        # Generate right operand first and store to __tmp64
        left_is_ll = self._is_long_long_expr(expr.left)
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
            # 64-bit division not implemented - would need complex routine
            self.ctx.emit("; WARNING: 64-bit division not implemented")
        elif op == "%":
            # 64-bit modulo not implemented
            self.ctx.emit("; WARNING: 64-bit modulo not implemented")
        elif op == "&":
            self._call_runtime("__and64")
        elif op == "|":
            self._call_runtime("__or64")
        elif op == "^":
            self._call_runtime("__xor64")
        elif op == "<<":
            # Shift amount in A (low byte of __tmp64)
            self.ctx.emit_instr("LD", "A,(__tmp64)")
            self._call_runtime("__shl64")
        elif op == ">>":
            self.ctx.emit_instr("LD", "A,(__tmp64)")
            is_unsigned = self._is_unsigned_expr(expr.left)
            if is_unsigned:
                self._call_runtime("__shr64")
            else:
                self._call_runtime("__sar64")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            is_unsigned = self._is_unsigned_expr(expr.left) or self._is_unsigned_expr(expr.right)
            self._gen_comparison_64(op, is_unsigned)
        elif op == ",":
            pass  # Result is already in __acc64

        # For comparison result is in HL, otherwise load result from __acc64 to DEHL
        if op not in ("==", "!=", "<", ">", "<=", ">="):
            # Load lower 32 bits of result to DEHL for compatibility
            self.ctx.emit_instr("LD", "HL,(__acc64)")
            self.ctx.emit_instr("LD", "DE,(__acc64+2)")

    def _gen_64bit_operand(self, expr: ast.Expression, to_tmp: bool) -> None:
        """Generate a 64-bit operand, storing to __acc64 or __tmp64."""
        target = "__tmp64" if to_tmp else "__acc64"

        if self._is_long_long_expr(expr):
            # Already 64-bit - generate and store
            if isinstance(expr, ast.IntLiteral):
                # Large literal - emit directly
                val = expr.value & 0xFFFFFFFFFFFFFFFF
                self.ctx.emit_instr("LD", f"HL,{val & 0xFFFF}")
                self.ctx.emit_instr("LD", f"({target}),HL")
                self.ctx.emit_instr("LD", f"HL,{(val >> 16) & 0xFFFF}")
                self.ctx.emit_instr("LD", f"({target}+2),HL")
                self.ctx.emit_instr("LD", f"HL,{(val >> 32) & 0xFFFF}")
                self.ctx.emit_instr("LD", f"({target}+4),HL")
                self.ctx.emit_instr("LD", f"HL,{(val >> 48) & 0xFFFF}")
                self.ctx.emit_instr("LD", f"({target}+6),HL")
            elif isinstance(expr, ast.Identifier):
                # Load 64-bit variable
                sym = self.ctx.lookup(expr.name)
                if sym and sym.is_global:
                    self.ctx.emit_instr("LD", f"HL,{sym.label()}")
                    if to_tmp:
                        self._call_runtime("__load64t")
                    else:
                        self._call_runtime("__load64")
                elif sym:
                    # Local variable - check for shared storage
                    if sym.uses_shared_storage:
                        self.ctx.emit_instr("LD", f"HL,??AUTO+{sym.shared_offset}")
                    else:
                        self.ctx.emit_instr("PUSH", "IX")
                        self.ctx.emit_instr("POP", "HL")
                        self.ctx.emit_instr("LD", f"DE,{sym.offset}")
                        self.ctx.emit_instr("ADD", "HL,DE")
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
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("OR", "L")
            self.ctx.emit_instr("JR", f"Z,{true_label}")
        elif op == "!=":
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("OR", "L")
            self.ctx.emit_instr("JR", f"NZ,{true_label}")
        elif op == "<":
            # HL == -1 means less than
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("AND", "L")
            self.ctx.emit_instr("INC", "A")  # -1 becomes 0
            self.ctx.emit_instr("JR", f"Z,{true_label}")
        elif op == ">=":
            # HL >= 0 means greater or equal
            self.ctx.emit_instr("BIT", "7,H")
            self.ctx.emit_instr("JR", f"Z,{true_label}")
        elif op == ">":
            # HL == 1 means greater than
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("OR", "A")
            self.ctx.emit_instr("JR", f"NZ,{false_label}")  # H != 0 -> not 1 -> false
            self.ctx.emit_instr("LD", "A,L")
            self.ctx.emit_instr("DEC", "A")
            self.ctx.emit_instr("JR", f"Z,{true_label}")
        elif op == "<=":
            # HL <= 0 means less or equal
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("OR", "A")
            self.ctx.emit_instr("JR", f"NZ,{true_label}")  # Negative -> true
            self.ctx.emit_instr("LD", "A,L")
            self.ctx.emit_instr("OR", "A")
            self.ctx.emit_instr("JR", f"Z,{true_label}")  # Zero -> true

        # False
        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("LD", "HL,0")
        self.ctx.emit_instr("JR", end_label)
        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("LD", "HL,1")
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
            self.ctx.emit_instr("LD", "HL,(__tmp32)")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", "HL,(__tmp32+2)")
            self.ctx.emit_instr("PUSH", "HL")

        # Generate left operand
        self._gen_float_operand(expr.left)

        if left_is_complex:
            # Restore __tmp32 from stack
            self.ctx.emit_instr("PUSH", "DE")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", "HL,4")
            self.ctx.emit_instr("ADD", "HL,SP")
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "(__tmp32+2),DE")
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("LD", "(__tmp32),DE")
            self.ctx.emit_instr("POP", "HL")
            self.ctx.emit_instr("POP", "DE")
            self.ctx.emit_instr("INC", "SP")
            self.ctx.emit_instr("INC", "SP")
            self.ctx.emit_instr("INC", "SP")
            self.ctx.emit_instr("INC", "SP")

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
            self.ctx.emit_instr("JP", f"Z,{true_label}")
        elif op == "!=":
            self.ctx.emit_instr("JP", f"NZ,{true_label}")
        elif op == "<":
            self.ctx.emit_instr("JP", f"C,{true_label}")
        elif op == ">=":
            self.ctx.emit_instr("JP", f"NC,{true_label}")
        elif op == ">":
            self.ctx.emit_instr("JP", f"Z,{false_label}")
            self.ctx.emit_instr("JP", f"NC,{true_label}")
        elif op == "<=":
            self.ctx.emit_instr("JP", f"Z,{true_label}")
            self.ctx.emit_instr("JP", f"C,{true_label}")

        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("LD", "HL,0")
        self.ctx.emit_instr("JP", end_label)
        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("LD", "HL,1")
        self.ctx.emit_label(end_label)
        self.ctx.emit_instr("LD", "DE,0")

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
        self.ctx.emit_instr("EX", "DE,HL")  # HL = source addr
        self.ctx.emit_instr("LD", "DE,__cplx_r")  # DE = dest addr
        self.ctx.emit_instr("LD", "BC,8")
        self.ctx.emit_instr("LDIR")

        # Generate left operand (complex) - address in HL
        self._gen_complex_operand(expr.left)
        # Store left operand to __cplx_l (8 bytes)
        self.ctx.emit_instr("EX", "DE,HL")  # HL = source addr
        self.ctx.emit_instr("LD", "DE,__cplx_l")  # DE = dest addr
        self.ctx.emit_instr("LD", "BC,8")
        self.ctx.emit_instr("LDIR")

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
            self.ctx.emit_comment(f"Complex op '{op}' not supported")

        # Result is in __cplx_result, return its address in HL
        self.ctx.emit_instr("LD", "HL,__cplx_result")

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
            self.ctx.emit_instr("LD", "(__cplx_tmp),HL")
            self.ctx.emit_instr("LD", "(__cplx_tmp+2),DE")
            # Zero imaginary part
            self.ctx.emit_instr("LD", "HL,0")
            self.ctx.emit_instr("LD", "(__cplx_tmp+4),HL")
            self.ctx.emit_instr("LD", "(__cplx_tmp+6),HL")
            # Return address
            self.ctx.emit_instr("LD", "HL,__cplx_tmp")

    def _gen_comparison_complex(self, op: str) -> None:
        """Generate complex comparison. Only == and != are valid."""
        # Compare real parts
        self.ctx.emit_instr("LD", "HL,(__cplx_l)")
        self.ctx.emit_instr("LD", "DE,(__cplx_l+2)")
        self.ctx.emit_instr("LD", "BC,(__cplx_r)")
        self.ctx.emit_instr("LD", "A,L")
        self.ctx.emit_instr("CP", "C")
        ne_label = self.ctx.new_label("CNEQ")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("LD", "BC,(__cplx_r)")
        self.ctx.emit_instr("CP", "B")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")
        self.ctx.emit_instr("LD", "BC,(__cplx_r+2)")
        self.ctx.emit_instr("LD", "A,E")
        self.ctx.emit_instr("CP", "C")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")
        self.ctx.emit_instr("LD", "A,D")
        self.ctx.emit_instr("CP", "B")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")

        # Compare imaginary parts
        self.ctx.emit_instr("LD", "HL,(__cplx_l+4)")
        self.ctx.emit_instr("LD", "DE,(__cplx_l+6)")
        self.ctx.emit_instr("LD", "BC,(__cplx_r+4)")
        self.ctx.emit_instr("LD", "A,L")
        self.ctx.emit_instr("CP", "C")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("CP", "B")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")
        self.ctx.emit_instr("LD", "BC,(__cplx_r+6)")
        self.ctx.emit_instr("LD", "A,E")
        self.ctx.emit_instr("CP", "C")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")
        self.ctx.emit_instr("LD", "A,D")
        self.ctx.emit_instr("CP", "B")
        self.ctx.emit_instr("JP", f"NZ,{ne_label}")

        # Equal
        eq_label = self.ctx.new_label("CEQ")
        if op == "==":
            self.ctx.emit_instr("LD", "HL,1")
        else:  # !=
            self.ctx.emit_instr("LD", "HL,0")
        self.ctx.emit_instr("JP", eq_label)

        # Not equal
        self.ctx.emit_label(ne_label)
        if op == "==":
            self.ctx.emit_instr("LD", "HL,0")
        else:  # !=
            self.ctx.emit_instr("LD", "HL,1")

        self.ctx.emit_label(eq_label)
        self.ctx.emit_instr("LD", "DE,0")

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

        # If source is float but target is integer, convert float to int
        if source_is_float and not target_is_float:
            # DEHL has IEEE float, convert to signed 32-bit int in DEHL
            self._call_runtime("__ftoi")
            # If target is 16-bit, HL already has the low word (truncated)
            # If target is 32-bit, DEHL has the full value
        # If target is 32-bit integer but source is not, extend
        # (Don't extend for float targets - floats are already 32-bit in DEHL)
        elif target_is_32bit and not target_is_float and not self._is_long_expr(expr.right) and not source_is_float:
            is_signed = not self._is_unsigned_expr(expr.right)
            self._extend_hl_to_dehl(is_signed)
        # If target is float but source is integer, convert to float
        elif target_is_float and not source_is_float:
            # First extend integer to 32-bit if needed
            if not self._is_long_expr(expr.right):
                is_signed = not self._is_unsigned_expr(expr.right)
                self._extend_hl_to_dehl(is_signed)
            # Then convert to float
            if self._is_unsigned_expr(expr.right):
                self._call_runtime("__uitof")
            else:
                self._call_runtime("__itof")

        # Store to the target
        if isinstance(expr.left, ast.Identifier):
            sym = self.ctx.lookup(expr.left.name)
            if sym:
                if target_is_32bit:
                    if sym.is_global:
                        self.ctx.emit_instr("LD", f"({sym.label()}),HL")
                        self.ctx.emit_instr("LD", f"({sym.label()}+2),DE")
                    else:
                        self._store_local_32(sym)
                else:
                    target_size = self._type_size(target_type) if target_type else 2
                    if sym.is_global:
                        if target_size == 1:
                            self.ctx.emit_instr("LD", "A,L")
                            self.ctx.emit_instr("LD", f"({sym.label()}),A")
                        else:
                            self.ctx.emit_instr("LD", f"({sym.label()}),HL")
                    else:
                        self._store_local(sym, size=target_size)
        elif isinstance(expr.left, ast.UnaryOp) and expr.left.op == "*":
            # Pointer dereference assignment: *p = value
            target_size = self._type_size(target_type) if target_type else 2
            if target_is_32bit:
                self.ctx.emit_instr("PUSH", "DE")  # Save high word
                self.ctx.emit_instr("PUSH", "HL")  # Save low word
                self.gen_expr(expr.left.operand)   # Get address in HL
                self.ctx.emit_instr("EX", "DE,HL") # Address in DE
                self.ctx.emit_instr("POP", "HL")   # Low word in HL
                self.ctx.emit_instr("EX", "DE,HL") # Address in HL, low word in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("POP", "DE")   # High word in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
            elif target_size == 1:
                self.ctx.emit_instr("PUSH", "HL")  # Save value
                self.gen_expr(expr.left.operand)   # Get address in HL
                self.ctx.emit_instr("POP", "DE")   # Value in DE
                self.ctx.emit_instr("LD", "(HL),E")  # Store only 1 byte
                self.ctx.emit_instr("EX", "DE,HL")  # Return value in HL
            else:
                self.ctx.emit_instr("PUSH", "HL")  # Save value
                self.gen_expr(expr.left.operand)   # Get address in HL
                self.ctx.emit_instr("POP", "DE")   # Value in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
                self.ctx.emit_instr("EX", "DE,HL")  # Return value in HL
        elif isinstance(expr.left, ast.Index):
            # Array element assignment
            if target_is_32bit:
                self.ctx.emit_instr("PUSH", "DE")  # Save high word
                self.ctx.emit_instr("PUSH", "HL")  # Save low word
                self._gen_address(expr.left)       # Get address in HL
                self.ctx.emit_instr("EX", "DE,HL") # Address in DE
                self.ctx.emit_instr("POP", "HL")   # Low word in HL
                self.ctx.emit_instr("EX", "DE,HL") # Address in HL, low word in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("POP", "DE")   # High word in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
            elif target_type and self._type_size(target_type) == 1:
                self.ctx.emit_instr("PUSH", "HL")  # Save value
                self._gen_address(expr.left)       # Get address in HL
                self.ctx.emit_instr("POP", "DE")   # Value in DE
                self.ctx.emit_instr("LD", "(HL),E")  # Store only 1 byte
                self.ctx.emit_instr("EX", "DE,HL")  # Return value in HL
            else:
                self.ctx.emit_instr("PUSH", "HL")  # Save value
                self._gen_address(expr.left)       # Get address in HL
                self.ctx.emit_instr("POP", "DE")   # Value in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
                self.ctx.emit_instr("EX", "DE,HL")  # Return value in HL
        elif isinstance(expr.left, ast.Member):
            # Struct member assignment
            member_size = self._get_member_size(expr.left)
            member_type = self._get_member_type(expr.left)
            member_is_32bit = self._is_long_type(member_type) or self._is_float_type(member_type)

            if member_is_32bit:
                # 32-bit member: save DEHL, get address, restore and store 4 bytes
                self.ctx.emit_instr("PUSH", "DE")  # Save high word
                self.ctx.emit_instr("PUSH", "HL")  # Save low word
                self._gen_address(expr.left)       # Get member address in HL
                self.ctx.emit_instr("EX", "DE,HL") # Address in DE
                self.ctx.emit_instr("POP", "HL")   # Low word in HL
                self.ctx.emit_instr("EX", "DE,HL") # Address in HL, low word in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("POP", "DE")   # High word in DE
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")
                # Leave DEHL with the stored value for chained assignment
            else:
                self.ctx.emit_instr("PUSH", "HL")  # Save value
                self._gen_address(expr.left)       # Get member address in HL
                self.ctx.emit_instr("POP", "DE")   # Value in DE
                if member_size == 1:
                    self.ctx.emit_instr("LD", "(HL),E")
                else:
                    self.ctx.emit_instr("LD", "(HL),E")
                    self.ctx.emit_instr("INC", "HL")
                    self.ctx.emit_instr("LD", "(HL),D")
                self.ctx.emit_instr("EX", "DE,HL")  # Return value in HL

    def _gen_assignment_64(self, expr: ast.BinaryOp) -> None:
        """Generate code for 64-bit assignment. Value goes into __acc64, then stored."""
        if isinstance(expr.left, ast.Identifier):
            sym = self.ctx.lookup(expr.left.name)
            if sym:
                # Generate value into __acc64
                self._gen_64bit_operand(expr.right, to_tmp=False)
                # Store __acc64 to target
                if sym.is_global:
                    self.ctx.emit_instr("LD", f"HL,{sym.label()}")
                    self._call_runtime("__store64")
                else:
                    self._store_local_64(sym)
        elif isinstance(expr.left, ast.Index):
            # Array element: compute address first, push it, then generate value
            self._gen_address(expr.left)        # Get address in HL
            self.ctx.emit_instr("PUSH", "HL")   # Save address
            self._gen_64bit_operand(expr.right, to_tmp=False)  # Value into __acc64
            self.ctx.emit_instr("POP", "HL")    # Restore address
            self._call_runtime("__store64")
        elif isinstance(expr.left, ast.UnaryOp) and expr.left.op == "*":
            # Pointer dereference: compute address first, push it, then generate value
            self.gen_expr(expr.left.operand)     # Get address in HL
            self.ctx.emit_instr("PUSH", "HL")   # Save address
            self._gen_64bit_operand(expr.right, to_tmp=False)  # Value into __acc64
            self.ctx.emit_instr("POP", "HL")    # Restore address
            self._call_runtime("__store64")
        elif isinstance(expr.left, ast.Member):
            # Struct member: compute address first, push it, then generate value
            self._gen_address(expr.left)         # Get member address in HL
            self.ctx.emit_instr("PUSH", "HL")   # Save address
            self._gen_64bit_operand(expr.right, to_tmp=False)  # Value into __acc64
            self.ctx.emit_instr("POP", "HL")    # Restore address
            self._call_runtime("__store64")

    def gen_compound_assignment(self, expr: ast.BinaryOp, op: str) -> None:
        """Generate code for compound assignment (+=, -=, etc.)."""
        # Build a regular binary op and assignment
        inner_op = ast.BinaryOp(op=op, left=expr.left, right=expr.right)
        assign = ast.BinaryOp(op="=", left=expr.left, right=inner_op)
        self.gen_assignment(assign)

    def gen_logical_and(self, expr: ast.BinaryOp) -> None:
        """Generate short-circuit logical AND."""
        false_label = self.ctx.new_label("AND_F")
        end_label = self.ctx.new_label("AND_E")

        self.gen_expr(expr.left)
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")
        self.ctx.emit_instr("JP", f"Z,{false_label}")

        self.gen_expr(expr.right)
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")
        self.ctx.emit_instr("JP", f"Z,{false_label}")

        self.ctx.emit_instr("LD", "HL,1")
        self.ctx.emit_instr("JP", end_label)

        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("LD", "HL,0")

        self.ctx.emit_label(end_label)

    def gen_logical_or(self, expr: ast.BinaryOp) -> None:
        """Generate short-circuit logical OR."""
        true_label = self.ctx.new_label("OR_T")
        end_label = self.ctx.new_label("OR_E")

        self.gen_expr(expr.left)
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")
        self.ctx.emit_instr("JP", f"NZ,{true_label}")

        self.gen_expr(expr.right)
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")
        self.ctx.emit_instr("JP", f"NZ,{true_label}")

        self.ctx.emit_instr("LD", "HL,0")
        self.ctx.emit_instr("JP", end_label)

        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("LD", "HL,1")

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
                self.ctx.emit_instr("LD", "A,D")
                self.ctx.emit_instr("XOR", "80H")
                self.ctx.emit_instr("LD", "D,A")
            elif self._is_long_expr(expr.operand):
                # 32-bit negate using runtime
                self.gen_expr(expr.operand)
                self._call_runtime("__neg32")
            else:
                # 16-bit negate: 0 - HL
                self.gen_expr(expr.operand)
                self.ctx.emit_instr("EX", "DE,HL")
                self.ctx.emit_instr("LD", "HL,0")
                self.ctx.emit_instr("OR", "A")
                self.ctx.emit_instr("SBC", "HL,DE")

        elif op == "+":
            self.gen_expr(expr.operand)  # No-op

        elif op == "!":
            self.gen_expr(expr.operand)
            # Logical NOT: if HL==0 then 1 else 0
            self.ctx.emit_instr("LD", "A,H")
            self.ctx.emit_instr("OR", "L")
            self.ctx.emit_instr("LD", "HL,0")
            self.ctx.emit_instr("JR", "NZ,$+3")
            self.ctx.emit_instr("INC", "L")

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
                self.ctx.emit_instr("LD", "A,H")
                self.ctx.emit_instr("CPL")
                self.ctx.emit_instr("LD", "H,A")
                self.ctx.emit_instr("LD", "A,L")
                self.ctx.emit_instr("CPL")
                self.ctx.emit_instr("LD", "L,A")

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
                self.ctx.emit_instr("LD", "L,(HL)")
                self._emit_char_to_hl(deref_signed)
            elif deref_size == 8:
                # 64-bit load: HL has address, load into __acc64
                self._call_runtime("__load64")
                self.ctx.runtime_used.add("__acc64")
                # Return low 32 bits in DEHL for use as rvalue
                self.ctx.emit_instr("LD", "HL,(__acc64)")
                self.ctx.emit_instr("LD", "DE,(__acc64+2)")
            elif deref_size == 4:
                # 32-bit load
                self.ctx.emit_instr("LD", "E,(HL)")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "D,(HL)")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "A,(HL)")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "H,(HL)")
                self.ctx.emit_instr("LD", "L,A")
                self.ctx.emit_instr("EX", "DE,HL")
            else:
                # 16-bit load
                self.ctx.emit_instr("LD", "E,(HL)")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "D,(HL)")
                self.ctx.emit_instr("EX", "DE,HL")

        elif op == "&":
            # Address-of
            self._gen_address(expr.operand)

        elif op == "++" or op == "--":
            self._gen_inc_dec(expr)

    def _gen_inc_dec(self, expr: ast.UnaryOp) -> None:
        """Generate code for increment/decrement."""
        is_inc = expr.op == "++"

        if isinstance(expr.operand, ast.Identifier):
            sym = self.ctx.lookup(expr.operand.name)
            if sym:
                # Load current value
                if sym.is_global:
                    self.ctx.emit_instr("LD", f"HL,({sym.label()})")
                else:
                    self._load_local(sym)

                if not expr.is_prefix:
                    # Postfix: save original value
                    self.ctx.emit_instr("PUSH", "HL")

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
                            self.ctx.emit_instr("INC", "HL")
                        else:
                            self.ctx.emit_instr("DEC", "HL")
                else:
                    if is_inc:
                        self.ctx.emit_instr("LD", f"DE,{step}")
                    else:
                        self.ctx.emit_instr("LD", f"DE,{(-step) & 0xFFFF}")
                    self.ctx.emit_instr("ADD", "HL,DE")

                # Store back
                if sym.is_global:
                    self.ctx.emit_instr("LD", f"({sym.label()}),HL")
                else:
                    self._store_local(sym)

                if not expr.is_prefix:
                    # Postfix: restore original value as result
                    self.ctx.emit_instr("POP", "HL")

        elif isinstance(expr.operand, ast.Index) or \
             (isinstance(expr.operand, ast.UnaryOp) and expr.operand.op == "*"):
            # Array index or pointer dereference: t[x]++ or (*p)++
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
            self.ctx.emit_instr("PUSH", "HL")  # Save address

            # Load current value
            if elem_size == 1:
                self.ctx.emit_instr("LD", "L,(HL)")
                self._emit_char_to_hl(self._is_signed_type(elem_type))
            else:
                self.ctx.emit_instr("LD", "E,(HL)")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "D,(HL)")
                self.ctx.emit_instr("EX", "DE,HL")

            if not expr.is_prefix:
                # Postfix: save original value
                self.ctx.emit_instr("PUSH", "HL")

            # Increment or decrement
            if is_inc:
                self.ctx.emit_instr("INC", "HL")
            else:
                self.ctx.emit_instr("DEC", "HL")

            # Store back: address is on stack (under original value if postfix)
            if not expr.is_prefix:
                self.ctx.emit_instr("EX", "DE,HL")  # DE = new value
                self.ctx.emit_instr("POP", "HL")    # HL = original value
                self.ctx.emit_instr("EX", "(SP),HL")  # HL = address, original on stack
                self.ctx.emit_instr("EX", "DE,HL")  # HL = new value, DE = address
                self.ctx.emit_instr("EX", "DE,HL")  # DE = new value, HL = address
            else:
                self.ctx.emit_instr("EX", "DE,HL")  # DE = new value
                self.ctx.emit_instr("POP", "HL")    # HL = address

            # Store new value at address
            if elem_size == 1:
                self.ctx.emit_instr("LD", "(HL),E")
            else:
                self.ctx.emit_instr("LD", "(HL),E")
                self.ctx.emit_instr("INC", "HL")
                self.ctx.emit_instr("LD", "(HL),D")

            if not expr.is_prefix:
                # Postfix: restore original value as result
                self.ctx.emit_instr("POP", "HL")
            else:
                # Prefix: result is the new value
                self.ctx.emit_instr("EX", "DE,HL")

    def gen_call(self, expr: ast.Call) -> None:
        """Generate code for function call."""
        # Handle GCC builtins
        if isinstance(expr.func, ast.Identifier):
            if expr.func.name == '__builtin_expect':
                # __builtin_expect(x, c) just returns x - it's a hint for branch prediction
                if expr.args:
                    self.gen_expr(expr.args[0])
                else:
                    self.ctx.emit_instr("LD", "HL,0")
                return

        # Get function parameter types if available
        param_types: list[ast.TypeNode] = []
        if isinstance(expr.func, ast.Identifier):
            func_sym = self.ctx.lookup(expr.func.name)
            if func_sym and isinstance(func_sym.sym_type, ast.FunctionType):
                param_types = func_sym.sym_type.param_types

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
                    self.ctx.emit_instr("LD", f"HL,{int_val}")
                else:
                    # Runtime conversion needed
                    self.gen_expr(arg, force_long=True)
                    self._call_runtime("__ftoi")  # Convert DEHL float to HL int
                self.ctx.emit_instr("PUSH", "HL")
                stack_size += 2
            else:
                # Normal argument handling
                # Check if parameter type or argument expression is 64-bit
                arg_is_ll = self._is_long_long_expr(arg)
                param_is_ll = param_type and self._is_long_long_type(param_type)
                if arg_is_ll or param_is_ll:
                    # 64-bit argument: push 4 words (8 bytes)
                    param_unsigned = (param_type and isinstance(param_type, ast.BasicType)
                                     and param_type.is_signed == False)
                    self._push_long_long_arg(arg, force_unsigned=param_unsigned)
                    stack_size += 8
                    continue

                # Check if parameter type or argument expression is 32-bit
                arg_is_long = self._is_long_expr(arg)
                param_is_long = param_type and self._is_long_type(param_type)
                param_is_float = param_type and self._is_float_type(param_type)
                # Floats are also 32-bit, need to push 4 bytes
                if arg_is_long or param_is_long or arg_is_float or param_is_float:
                    self.gen_expr(arg, force_long=True)
                    # Extend to 32-bit if argument is smaller than parameter
                    if param_is_long and not arg_is_long and not arg_is_float:
                        is_signed = not self._is_unsigned_expr(arg)
                        self._extend_hl_to_dehl(is_signed)
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
                    self.ctx.emit_instr("PUSH", "DE")
                    self.ctx.emit_instr("PUSH", "HL")
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
                            self.ctx.emit_instr("LD", f"DE,{push_size - 2}")
                            self.ctx.emit_instr("ADD", "HL,DE")
                            # HL points to last word
                            remaining = push_size
                            while remaining > 0:
                                self.ctx.emit_instr("LD", "E,(HL)")
                                self.ctx.emit_instr("INC", "HL")
                                self.ctx.emit_instr("LD", "D,(HL)")
                                self.ctx.emit_instr("DEC", "HL")
                                self.ctx.emit_instr("PUSH", "DE")
                                remaining -= 2
                                if remaining > 0:
                                    self.ctx.emit_instr("DEC", "HL")
                                    self.ctx.emit_instr("DEC", "HL")
                        stack_size += push_size
                    else:
                        self.gen_expr(arg)
                        self.ctx.emit_instr("PUSH", "HL")
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
                self.ctx.emit_instr("CALL", f"_{expr.func.name}")
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
        if isinstance(expr.func, ast.Identifier):
            return_type = self._get_expr_type(expr)
            if self._is_long_long_type(return_type):
                return_is_64bit = True
            elif self._is_long_type(return_type) or self._is_float_type(return_type):
                return_is_32bit = True

        if stack_size > 0:
            if return_is_64bit:
                # Return value in __acc64 - registers are free, just clean stack
                self.ctx.emit_instr("LD", f"HL,{stack_size}")
                self.ctx.emit_instr("ADD", "HL,SP")
                self.ctx.emit_instr("LD", "SP,HL")
            elif return_is_32bit:
                # Return value in DEHL - need to preserve both while cleaning stack
                # Save DE to BC, clean up stack, restore DE
                self.ctx.emit_instr("LD", "B,D")
                self.ctx.emit_instr("LD", "C,E")
                # Now HL has low word, BC has high word
                # Adjust SP to clean up arguments
                self.ctx.emit_instr("EX", "DE,HL")  # Save low word in DE
                self.ctx.emit_instr("LD", f"HL,{stack_size}")
                self.ctx.emit_instr("ADD", "HL,SP")
                self.ctx.emit_instr("LD", "SP,HL")
                self.ctx.emit_instr("EX", "DE,HL")  # Restore low word to HL
                # Restore high word from BC to DE
                self.ctx.emit_instr("LD", "D,B")
                self.ctx.emit_instr("LD", "E,C")
            elif stack_size <= 6:
                for _ in range(stack_size // 2):
                    self.ctx.emit_instr("POP", "DE")  # Discard
            else:
                self.ctx.emit_instr("EX", "DE,HL")  # Save return value
                self.ctx.emit_instr("LD", f"HL,{stack_size}")
                self.ctx.emit_instr("ADD", "HL,SP")
                self.ctx.emit_instr("LD", "SP,HL")
                self.ctx.emit_instr("EX", "DE,HL")  # Restore return value

        # For 64-bit return, load low 32 bits from __acc64 into DEHL
        if return_is_64bit:
            self.ctx.runtime_used.add("__acc64")
            self.ctx.emit_instr("LD", "HL,(__acc64)")
            self.ctx.emit_instr("LD", "DE,(__acc64+2)")

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
            self.ctx.emit_instr("LD", f"HL,{w3}")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", f"HL,{w2}")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", f"HL,{w1}")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", f"HL,{w0}")
            self.ctx.emit_instr("PUSH", "HL")
        elif isinstance(arg, ast.Identifier):
            sym = self.ctx.lookup(arg.name)
            if sym and self._is_long_long_type(sym.sym_type):
                # Variable IS 64-bit - load to __acc64 and push
                if sym.is_global:
                    label = sym.label()
                    self.ctx.emit_instr("LD", f"HL,{label}")
                elif sym.uses_shared_storage:
                    self.ctx.emit_instr("LD", f"HL,??AUTO+{sym.shared_offset}")
                else:
                    # Stack frame parameter/local: IX+offset
                    off = sym.offset
                    self.ctx.emit_instr("PUSH", "IX")
                    self.ctx.emit_instr("POP", "HL")
                    self.ctx.emit_instr("LD", f"DE,{off}")
                    self.ctx.emit_instr("ADD", "HL,DE")
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
            self.ctx.emit_instr("LD", f"HL,{w3}")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", f"HL,{w2}")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", f"HL,{w1}")
            self.ctx.emit_instr("PUSH", "HL")
            self.ctx.emit_instr("LD", f"HL,{w0}")
            self.ctx.emit_instr("PUSH", "HL")
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

        self.gen_expr(expr.condition)
        self.ctx.emit_instr("LD", "A,H")
        self.ctx.emit_instr("OR", "L")
        self.ctx.emit_instr("JP", f"Z,{else_label}")

        self.gen_expr(expr.true_expr)
        self.ctx.emit_instr("JP", end_label)

        self.ctx.emit_label(else_label)
        self.gen_expr(expr.false_expr)

        self.ctx.emit_label(end_label)

    def gen_cast(self, expr: ast.Cast, force_long: bool = False) -> None:
        """Generate code for cast expression with proper type conversion."""
        source_type = self._get_expr_type(expr.expr)
        target_type = expr.target_type
        target_is_long = self._is_long_type(target_type) or force_long
        source_is_long = self._is_long_expr(expr.expr)
        source_is_float = self._is_float_expr(expr.expr)
        target_is_float = self._is_float_type(target_type)

        # Generate the source expression without forcing long -
        # the cast itself handles the extension
        self.gen_expr(expr.expr, force_long=False)

        source_size = self._type_size(source_type) if source_type else 2
        target_size = self._type_size(target_type)
        target_signed = self._is_signed_type(target_type)

        # Handle float conversions first
        if target_is_float and not source_is_float:
            # Int to float conversion
            if not source_is_long:
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
                self._extend_hl_to_dehl(target_signed)
            return

        # Handle narrowing conversions first (before widening if force_long)
        if target_size == 1 and source_size > 1:
            # Narrowing to char: truncate to L, sign or zero extend H
            if target_signed:
                # Signed char: sign-extend L to HL
                self.ctx.emit_instr("LD", "A,L")
                self.ctx.emit_instr("RLCA")      # Move bit 7 to carry
                self.ctx.emit_instr("SBC", "A,A") # A = 0xFF if carry, 0x00 if not
                self.ctx.emit_instr("LD", "H,A")
            else:
                # Unsigned char: zero-extend
                self.ctx.emit_instr("LD", "H,0")
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
        elif target_is_long and target_size < 4:
            # Need to extend to 32-bit DEHL after narrowing
            # Use the target type's signedness for extension
            self._extend_hl_to_dehl(target_signed)
        elif target_is_long and not source_is_long and not source_is_float:
            # Direct extension to 32-bit (skip if source is float - already 32-bit)
            is_signed = self._is_signed_type(source_type) if source_type else True
            self._extend_hl_to_dehl(is_signed)
        elif target_size == 2 and source_size == 1:
            # 8-bit to 16-bit: already in HL, but may need sign extension
            # HL already has the value; L is the byte, H might need fixing
            if source_type and self._is_signed_type(source_type):
                # Sign extend L to HL
                self.ctx.emit_instr("LD", "A,L")
                self.ctx.emit_instr("RLCA")  # Get sign bit into carry
                self.ctx.emit_instr("SBC", "A,A")  # A = 0xFF if sign, 0x00 if not
                self.ctx.emit_instr("LD", "H,A")

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
            self.ctx.emit_instr("LD", "A,L")
            self.ctx.emit_instr("RLCA")
            self.ctx.emit_instr("SBC", "A,A")
            self.ctx.emit_instr("LD", "H,A")
        else:
            self.ctx.emit_instr("LD", "H,0")

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

        # Determine element size for proper load
        elem_size = self._get_index_elem_size(expr.array)

        if elem_size == 1:
            # 8-bit element, sign/zero-extend to HL
            elem_signed = True
            arr_type = self._get_expr_type(expr.array)
            if isinstance(arr_type, (ast.ArrayType, ast.PointerType)):
                elem_signed = self._is_signed_type(arr_type.base_type)
            self.ctx.emit_instr("LD", "L,(HL)")
            self._emit_char_to_hl(elem_signed)
        elif elem_size == 8:
            # 64-bit element: HL has address, load into __acc64
            self._call_runtime("__load64")
            self.ctx.runtime_used.add("__acc64")
            # Return low 32 bits in DEHL for use as rvalue
            self.ctx.emit_instr("LD", "HL,(__acc64)")
            self.ctx.emit_instr("LD", "DE,(__acc64+2)")
        elif elem_size == 4:
            # 32-bit element: load into DEHL (DE=high, HL=low)
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "A,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "H,(HL)")
            self.ctx.emit_instr("LD", "L,A")
            # Now HL = high word, DE = low word; need to swap
            self.ctx.emit_instr("EX", "DE,HL")
            # Now HL = low word, DE = high word (correct DEHL format)
        else:
            # 16-bit element
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("EX", "DE,HL")

    def gen_member(self, expr: ast.Member) -> None:
        """Generate code for struct member access."""
        # Generate address of the member
        self._gen_address(expr)

        # Check if member is an array - arrays decay to pointers (return address, not value)
        member_type = self._get_member_type(expr)
        if isinstance(member_type, ast.ArrayType):
            # Array member: address is already in HL, just return it
            return

        # Determine member size and load appropriately
        member_size = self._type_size(member_type) if member_type else 2
        if member_size == 1:
            self.ctx.emit_instr("LD", "L,(HL)")
            self._emit_char_to_hl(self._is_signed_type(member_type))
        elif member_size == 8:
            # 64-bit member: HL has address, call __load64 to load into __acc64
            self._call_runtime("__load64")
            self.ctx.runtime_used.add("__acc64")
            # Return low 32 bits in DEHL for use as rvalue
            self.ctx.emit_instr("LD", "HL,(__acc64)")
            self.ctx.emit_instr("LD", "DE,(__acc64+2)")
        elif member_size == 4:
            # 32-bit member: load into DEHL (DE=high, HL=low)
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "A,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "H,(HL)")
            self.ctx.emit_instr("LD", "L,A")
            self.ctx.emit_instr("EX", "DE,HL")
        else:
            # 16-bit member
            self.ctx.emit_instr("LD", "E,(HL)")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "D,(HL)")
            self.ctx.emit_instr("EX", "DE,HL")

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
        the StructType's inline member list.
        """
        # Try inline members first
        if struct_type.members:
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
        elif isinstance(expr, ast.UnaryOp) and expr.op in ("-", "+", "~"):
            # These preserve the operand type
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

            # For shift operations, result type is the promoted LEFT operand (C99 6.5.7)
            if expr.op in ("<<", ">>"):
                if left_type:
                    return left_type
                return ast.BasicType(name="int")  # Default to int for literal 1

            # For other ops, apply usual arithmetic conversions: if either is long, result is long
            left_is_long = isinstance(left_type, ast.BasicType) and left_type.name == 'long'
            right_is_long = isinstance(right_type, ast.BasicType) and right_type.name == 'long'

            if left_is_long or right_is_long:
                return ast.BasicType(name="long")
            if left_type:
                return left_type
            if right_type:
                return right_type
        elif isinstance(expr, ast.Cast):
            return expr.target_type
        elif isinstance(expr, ast.Call):
            # Get return type of function call
            if isinstance(expr.func, ast.Identifier):
                sym = self.ctx.lookup(expr.func.name)
                if sym and isinstance(sym.sym_type, ast.FunctionType):
                    return sym.sym_type.return_type
                elif sym:
                    # Function pointer or direct function symbol
                    return sym.sym_type
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
        return None

    def _types_compatible(self, expr_type: ast.TypeNode | None, sel_type: ast.TypeNode) -> bool:
        """Check if expression type matches selector type for _Generic.

        C23 semantics (6.5.1.1): The controlling expression's type is used
        directly for matching - qualifiers (const/volatile) are preserved,
        unlike C11 which applied lvalue conversion to strip them.
        Arrays still decay to pointers, functions to function pointers.
        """
        if expr_type is None:
            return False

        # Helper to check basic type compatibility
        def basic_types_match(t1: ast.BasicType, t2: ast.BasicType) -> bool:
            # Name must match
            if t1.name != t2.name:
                return False
            # C23: qualifiers must match (const, volatile)
            if t1.is_const != t2.is_const:
                return False
            if t1.is_volatile != t2.is_volatile:
                return False
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
                    self.ctx.emit_instr("LD", f"HL,{sym.label()}")
                elif sym.uses_shared_storage:
                    # Shared storage: direct address
                    self.ctx.emit_instr("LD", f"HL,??AUTO+{sym.shared_offset}")
                else:
                    # Local: compute IX+offset
                    self.ctx.emit_instr("LD", f"HL,{sym.offset}")
                    self.ctx.emit_instr("PUSH", "IX")
                    self.ctx.emit_instr("POP", "DE")
                    self.ctx.emit_instr("ADD", "HL,DE")

        elif isinstance(expr, ast.Index):
            # array[index]: base + index * element_size
            elem_size = self._get_index_elem_size(expr.array)

            self.gen_expr(expr.index)

            # Scale index by element size
            if elem_size == 1:
                pass  # No scaling needed
            elif elem_size == 2:
                self.ctx.emit_instr("ADD", "HL,HL")  # index * 2
            elif elem_size == 4:
                self.ctx.emit_instr("ADD", "HL,HL")  # index * 2
                self.ctx.emit_instr("ADD", "HL,HL")  # index * 4
            elif elem_size == 8:
                self.ctx.emit_instr("ADD", "HL,HL")  # * 2
                self.ctx.emit_instr("ADD", "HL,HL")  # * 4
                self.ctx.emit_instr("ADD", "HL,HL")  # * 8
            else:
                # Arbitrary size - use multiplication
                self.ctx.emit_instr("LD", f"DE,{elem_size}")
                self._call_runtime("__mul16")  # HL = HL * DE

            self.ctx.emit_instr("PUSH", "HL")
            self.gen_expr(expr.array)  # Get base address
            self.ctx.emit_instr("POP", "DE")
            self.ctx.emit_instr("ADD", "HL,DE")

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
                self.ctx.emit_instr("LD", "(__sret_buf),HL")
                self.ctx.emit_instr("LD", "HL,__sret_buf")

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
                    self.ctx.emit_instr("LD", "(__sret_buf),HL")
                    self.ctx.emit_instr("LD", "HL,__sret_buf")
            else:
                self._gen_address(expr.obj)  # s.member: address of s

            # Get struct type and member offset
            struct_type = self._get_expr_type(expr.obj)
            if expr.is_arrow and isinstance(struct_type, ast.PointerType):
                struct_type = struct_type.base_type
            if isinstance(struct_type, ast.StructType):
                offset = self._resolve_member_offset(struct_type, expr.member)
                if offset > 0:
                    self.ctx.emit_instr("LD", f"DE,{offset}")
                    self.ctx.emit_instr("ADD", "HL,DE")

    @staticmethod
    def _mul_shift_count(expr: ast.Expression) -> int | None:
        """If expr is an IntLiteral power-of-2 > 1, return log2. Else None."""
        if isinstance(expr, ast.IntLiteral):
            v = expr.value
            if v > 1 and (v & (v - 1)) == 0:
                return v.bit_length() - 1
        return None

    def _is_unsigned_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has unsigned type."""
        expr_type = self._get_expr_type(expr)
        if isinstance(expr_type, ast.BasicType):
            # is_signed=False means unsigned, is_signed=None means default (signed)
            return expr_type.is_signed == False
        return False

    def _is_long_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is 32-bit (long but not long long)."""
        if isinstance(t, ast.BasicType):
            return t.name == "long"
        return False

    def _is_long_long_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is 64-bit (long long)."""
        if isinstance(t, ast.BasicType):
            return t.name == "long long"
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
        if isinstance(expr, ast.IntLiteral):
            # Check for explicit LL suffix or value too large for 32-bit
            if hasattr(expr, 'is_long_long') and expr.is_long_long:
                return True
            # For hex literals, values up to 4294967295 fit in unsigned long (32-bit)
            if expr.is_hex and 0 <= expr.value <= 4294967295:
                return False
            # Values too large for signed 32-bit (and not hex unsigned long)
            if expr.value > 2147483647 or expr.value < -2147483648:
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
            if expr.op not in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="):
                return self._is_long_long_expr(expr.left) or self._is_long_long_expr(expr.right)
        if isinstance(expr, ast.Cast):
            return self._is_long_long_type(expr.target_type)
        expr_type = self._get_expr_type(expr)
        return self._is_long_long_type(expr_type)

    def _is_long_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has 32-bit type (long but not long long)."""
        # First check if it's long long - if so, it's not just "long"
        if self._is_long_long_expr(expr):
            return False
        if isinstance(expr, ast.IntLiteral):
            # Check for explicit L suffix or value too large for signed 16-bit
            if expr.is_long:
                return True
            # C standard type promotion for literals:
            # Decimal: int -> long -> long long
            # Hex/octal: int -> unsigned int -> long -> unsigned long -> long long -> unsigned long long
            val = expr.value
            if val > 32767 or val < -32768:
                # For hex/octal literals, check if it fits in unsigned int (16-bit) first
                if expr.is_hex and 0 <= val <= 65535:
                    return False  # Fits in unsigned int, stays 16-bit
                # Check if it fits in 32-bit
                if val <= 2147483647 and val >= -2147483648:
                    return True
                # For hex/octal, check unsigned long (32-bit)
                if expr.is_hex and 0 <= val <= 4294967295:
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
        self.ctx.emit_instr("EX", "DE,HL")
        self.ctx.emit_instr("OR", "A")  # Clear carry
        self.ctx.emit_instr("SBC", "HL,DE")

        # Now flags reflect HL - DE (original left - right)
        # For signed comparison, we need to check Sign XOR Overflow
        # Z80's P/V flag after SBC indicates overflow
        if op == "==":
            self.ctx.emit_instr("JP", f"Z,{true_label}")
        elif op == "!=":
            self.ctx.emit_instr("JP", f"NZ,{true_label}")
        elif op == "<":
            if is_unsigned:
                # Unsigned less than: carry set means left < right
                self.ctx.emit_instr("JP", f"C,{true_label}")
            else:
                # Signed less than: true if Sign XOR Overflow
                # No overflow: true if Sign set (M)
                # Overflow: true if Sign clear (P)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("JP", f"PE,{ov_label}")
                self.ctx.emit_instr("JP", f"M,{true_label}")
                self.ctx.emit_instr("JP", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("JP", f"P,{true_label}")
                self.ctx.emit_instr("JP", false_label)
        elif op == ">=":
            if is_unsigned:
                # Unsigned greater or equal: no carry
                self.ctx.emit_instr("JP", f"NC,{true_label}")
            else:
                # Signed >=: true if NOT (Sign XOR Overflow)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("JP", f"PE,{ov_label}")
                self.ctx.emit_instr("JP", f"P,{true_label}")
                self.ctx.emit_instr("JP", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("JP", f"M,{true_label}")
                self.ctx.emit_instr("JP", false_label)
        elif op == ">":
            if is_unsigned:
                # Unsigned greater: no carry and not zero
                self.ctx.emit_instr("JP", f"Z,{false_label}")
                self.ctx.emit_instr("JP", f"NC,{true_label}")
            else:
                # Signed >: not equal AND NOT (Sign XOR Overflow)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("JP", f"Z,{false_label}")
                self.ctx.emit_instr("JP", f"PE,{ov_label}")
                self.ctx.emit_instr("JP", f"P,{true_label}")
                self.ctx.emit_instr("JP", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("JP", f"M,{true_label}")
                self.ctx.emit_instr("JP", false_label)
        elif op == "<=":
            if is_unsigned:
                # Unsigned less or equal: carry or zero
                self.ctx.emit_instr("JP", f"Z,{true_label}")
                self.ctx.emit_instr("JP", f"C,{true_label}")
            else:
                # Signed <=: equal OR (Sign XOR Overflow)
                ov_label = self.ctx.new_label("CMP_OV")
                self.ctx.emit_instr("JP", f"Z,{true_label}")
                self.ctx.emit_instr("JP", f"PE,{ov_label}")
                self.ctx.emit_instr("JP", f"M,{true_label}")
                self.ctx.emit_instr("JP", false_label)
                self.ctx.emit_label(ov_label)
                self.ctx.emit_instr("JP", f"P,{true_label}")
                self.ctx.emit_instr("JP", false_label)

        # Fall through to false for simple cases (==, !=, unsigned)
        self.ctx.emit_label(false_label)
        self.ctx.emit_instr("LD", "HL,0")
        self.ctx.emit_instr("JP", end_label)

        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("LD", "HL,1")

        self.ctx.emit_label(end_label)

    def _load_local(self, sym: Symbol) -> None:
        """Load a local variable into HL."""
        if sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"HL,(??AUTO+{sym.shared_offset})")
        else:
            self.ctx.emit_instr("LD", f"L,({ix_off(sym.offset)})")
            self.ctx.emit_instr("LD", f"H,({ix_off(sym.offset + 1)})")

    def _store_local(self, sym: Symbol, size: int = 2) -> None:
        """Store HL into a local variable."""
        if size == 1:
            if sym.uses_shared_storage:
                self.ctx.emit_instr("LD", "A,L")
                self.ctx.emit_instr("LD", f"(??AUTO+{sym.shared_offset}),A")
            else:
                self.ctx.emit_instr("LD", f"({ix_off(sym.offset)}),L")
        elif sym.uses_shared_storage:
            # Store to shared automatic storage
            self.ctx.emit_instr("LD", f"(??AUTO+{sym.shared_offset}),HL")
        else:
            self.ctx.emit_instr("LD", f"({ix_off(sym.offset)}),L")
            self.ctx.emit_instr("LD", f"({ix_off(sym.offset + 1)}),H")

    def _load_local_32(self, sym: Symbol) -> None:
        """Load a 32-bit local variable into DEHL (DE=high, HL=low)."""
        if sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"HL,(??AUTO+{sym.shared_offset})")
            self.ctx.emit_instr("LD", f"DE,(??AUTO+{sym.shared_offset + 2})")
        else:
            self.ctx.emit_instr("LD", f"L,({ix_off(sym.offset)})")
            self.ctx.emit_instr("LD", f"H,({ix_off(sym.offset + 1)})")
            self.ctx.emit_instr("LD", f"E,({ix_off(sym.offset + 2)})")
            self.ctx.emit_instr("LD", f"D,({ix_off(sym.offset + 3)})")

    def _store_local_32(self, sym: Symbol) -> None:
        """Store DEHL (32-bit) into a local variable."""
        if sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"(??AUTO+{sym.shared_offset}),HL")
            self.ctx.emit_instr("LD", f"(??AUTO+{sym.shared_offset + 2}),DE")
        else:
            self.ctx.emit_instr("LD", f"({ix_off(sym.offset)}),L")
            self.ctx.emit_instr("LD", f"({ix_off(sym.offset + 1)}),H")
            self.ctx.emit_instr("LD", f"({ix_off(sym.offset + 2)}),E")
            self.ctx.emit_instr("LD", f"({ix_off(sym.offset + 3)}),D")

    def _store_local_64(self, sym: Symbol) -> None:
        """Store __acc64 (64-bit) into a local variable."""
        self.ctx.runtime_used.add("__acc64")
        if sym.uses_shared_storage:
            self.ctx.emit_instr("LD", f"HL,??AUTO+{sym.shared_offset}")
            self._call_runtime("__store64")
        else:
            self.ctx.emit_instr("PUSH", "IX")
            self.ctx.emit_instr("POP", "HL")
            self.ctx.emit_instr("LD", f"DE,{sym.offset}")
            self.ctx.emit_instr("ADD", "HL,DE")
            self._call_runtime("__store64")

    def _call_runtime(self, name: str) -> None:
        """Call a runtime library function."""
        self.ctx.runtime_used.add(name)
        self.ctx.emit_instr("CALL", name)

    def _store_tmp32(self) -> None:
        """Store DEHL to __tmp32 for 32-bit binary operations."""
        self.ctx.runtime_used.add("__tmp32")
        self.ctx.emit_instr("LD", "(__tmp32),HL")
        self.ctx.emit_instr("LD", "(__tmp32+2),DE")

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
        return size

    def _type_size(self, t: ast.TypeNode) -> int:
        """Return the size of a type in bytes."""
        if isinstance(t, ast.BasicType):
            name = t.name
            if name in ("char", "_Bool", "bool"):
                return 1
            elif name in ("short", "int"):
                return 2
            elif name in ("long", "float", "double", "long double"):
                return 4
            elif name == "long long":
                return 8  # 64-bit
            elif name == "void":
                return 0
            return 2  # Default
        elif isinstance(t, ast.PointerType):
            return 2  # 16-bit pointers
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
            # Handle inline struct definitions with members
            if t.members:
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
            return 8  # 2 * 4 bytes
        return 2  # Default

    def _get_struct_members(self, struct_type: ast.StructType) -> list:
        """Get struct members as list of (name, type, offset) tuples.

        Handles both inline struct definitions (with members) and named structs
        (looked up in ctx.structs).
        """
        if struct_type.members:
            # Inline struct definition - compute offsets from members
            members = []
            offset = 0
            for m in struct_type.members:
                if m.name:
                    members.append((m.name, m.member_type, offset))
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
                self.ctx.emit_instr("DS", str(elem_size))
                continue

            val = values[idx]
            if isinstance(val, ast.DesignatedInit):
                val = val.value

            # Handle nested types
            if isinstance(elem_type, ast.StructType) and not isinstance(val, ast.InitializerList):
                members = self._get_struct_members(elem_type)
                if members:
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
                self.ctx.emit_instr("DS", str(base_size))
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
                    # Check if this is a flat init for array of structs
                    is_flat_struct_init = (
                        isinstance(base_type, ast.StructType) and
                        init.values and
                        not isinstance(init.values[0], ast.InitializerList)
                    )
                    if is_braced_string:
                        self._emit_string_for_array(init.values[0], elem_type)
                    elif is_flat_struct_init:
                        # Flat init for array of structs - use flat handler
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
                                self.ctx.emit_instr("DS", str(pad_size))
            elif isinstance(elem_type, ast.StructType):
                # Struct/union initializer
                members = self._get_struct_members(elem_type)
                if members:
                    if elem_type.is_union:
                        # Union: determine which member to initialize
                        target_member = members[0]  # Default: first named member
                        target_sub_members = None

                        # Check if init values have designators targeting anonymous members
                        if init.values and isinstance(init.values[0], ast.DesignatedInit):
                            desig = init.values[0]
                            if desig.designators and isinstance(desig.designators[0], str):
                                desig_name = desig.designators[0]
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
                                                    # Designator targets anonymous member's sub-member
                                                    target_member = (None, anon_type, anon_offset)
                                                    target_sub_members = anon_mems
                                                    break
                                            if target_sub_members:
                                                break

                        target_size = self._type_size(target_member[1])
                        if target_sub_members is not None:
                            # Initialize anonymous struct/union sub-members
                            self._emit_struct_init_flat(init.values, target_sub_members)
                        else:
                            self._emit_struct_init_flat(init.values, [target_member])
                        union_size = self._type_size(elem_type)
                        if union_size > target_size:
                            self.ctx.emit_instr("DS", str(union_size - target_size))
                    else:
                        self._emit_struct_init_flat(init.values, members)
                else:
                    # Unknown struct, just reserve space
                    self.ctx.emit_instr("DS", str(elem_size))
            else:
                # Scalar with initializer list (e.g., int x = {1})
                if init.values:
                    self._emit_initializer(init.values[0], elem_type)
                else:
                    self.ctx.emit_instr("DS", str(elem_size))
        elif isinstance(init, ast.IntLiteral):
            # Check if target type is float - if so, convert to float representation
            if self._is_float_type(elem_type):
                self._emit_float_value(float(init.value))
            else:
                self._emit_int_value(init.value, elem_size)
        elif isinstance(init, ast.FloatLiteral):
            self._emit_float_value(init.value)
        elif isinstance(init, ast.CharLiteral):
            self.ctx.emit_instr("DB", str(init.value))
        elif isinstance(init, ast.StringLiteral):
            if isinstance(elem_type, ast.PointerType):
                # Pointer member initialized with string literal - emit pointer to string
                label = self.ctx.add_string(init.value, is_wide=getattr(init, 'is_wide', False))
                self.ctx.emit_instr("DW", label)
            else:
                # Array or char member - emit as bytes
                escaped = self._escape_string(init.value)
                self.ctx.emit_instr("DB", f"'{escaped}',0")
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
                self.ctx.emit_instr("DS", str(elem_size))
        elif isinstance(init, ast.Identifier):
            # Address of a symbol - emit as label reference
            sym = self.ctx.lookup(init.name)
            if sym:
                label = sym.label()
            elif init.name in self.ctx.static_local_labels:
                label = self.ctx.static_local_labels[init.name]
            else:
                label = f"_{init.name}"
            self.ctx.emit_instr("DW", label)
        elif isinstance(init, ast.UnaryOp) and init.op == "&":
            # Address-of expression
            if isinstance(init.operand, ast.Identifier):
                sym = self.ctx.lookup(init.operand.name)
                if sym:
                    label = sym.label()
                elif init.operand.name in self.ctx.static_local_labels:
                    label = self.ctx.static_local_labels[init.operand.name]
                else:
                    label = f"_{init.operand.name}"
                self.ctx.emit_instr("DW", label)
            else:
                self.ctx.emit_instr("DS", str(elem_size))
        elif isinstance(init, ast.Cast):
            # Cast expression - try to evaluate constant
            const_val = self._eval_const_expr(init)
            if const_val is not None:
                if self._is_float_type(elem_type):
                    self._emit_float_value(float(const_val))
                else:
                    self._emit_int_value(const_val, elem_size)
            else:
                self.ctx.emit_instr("DS", str(elem_size))
        elif isinstance(init, ast.Compound):
            # Compound literal: (type){initializer} - extract the initializer
            self._emit_initializer(init.init, init.target_type)
        elif isinstance(init, ast.BinaryOp):
            # Binary expression - try to evaluate as constant
            const_val = self._eval_const_expr(init)
            if const_val is not None:
                if self._is_float_type(elem_type):
                    self._emit_float_value(float(const_val))
                else:
                    self._emit_int_value(const_val, elem_size)
            else:
                self.ctx.emit_instr("DS", str(elem_size))
        else:
            # Try to evaluate as a constant expression before giving up
            const_val = self._eval_const_expr(init)
            if const_val is not None:
                if self._is_float_type(elem_type):
                    self._emit_float_value(float(const_val))
                else:
                    self._emit_int_value(const_val, elem_size)
            else:
                # Complex initializer - reserve space
                self.ctx.emit_instr("DS", str(elem_size))

    def _emit_struct_init_flat(self, values: list, members: list) -> int:
        """Emit struct initialization with flat value list. Returns values consumed."""
        # Check for member designators requiring non-sequential emit
        has_member_desig = any(
            isinstance(v, ast.DesignatedInit) and v.designators and isinstance(v.designators[0], str)
            for v in values
        )

        if has_member_desig:
            return self._emit_struct_init_designated(values, members)

        value_index = 0

        for member_name, member_type, member_offset in members:
            if value_index >= len(values):
                # No more values - zero-initialize
                size = self._type_size(member_type)
                if size > 0:
                    self.ctx.emit_instr("DS", str(size))
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
                            self.ctx.emit_instr("DS", str(union_size - first_size))
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
                    # Continuation after nested designator - add to same member
                    member_vals[active_nested_member].append(actual_val)
                else:
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
                    self.ctx.emit_instr("DS", str(size))

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
                self.ctx.emit_instr("DS", str(elem_size))
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
        self.ctx.emit_instr("DB", f"'{escaped}',0")

        # String length including null
        string_len = len(string_val) + 1

        # Pad remaining bytes with zeros
        remaining = array_size - string_len
        if remaining > 0:
            self.ctx.emit_instr("DS", str(remaining))

    def _emit_int_value(self, value: int, size: int) -> None:
        """Emit an integer value with the specified size."""
        if size == 1:
            self.ctx.emit_instr("DB", str(value & 0xFF))
        elif size == 2:
            self.ctx.emit_instr("DW", str(value & 0xFFFF))
        elif size == 4:
            # 32-bit: emit low word first, then high word
            val = value & 0xFFFFFFFF
            low = val & 0xFFFF
            high = (val >> 16) & 0xFFFF
            self.ctx.emit_instr("DW", str(low))
            self.ctx.emit_instr("DW", str(high))
        elif size == 8:
            # 64-bit: emit four words, low to high
            val = value & 0xFFFFFFFFFFFFFFFF
            self.ctx.emit_instr("DW", str(val & 0xFFFF))
            self.ctx.emit_instr("DW", str((val >> 16) & 0xFFFF))
            self.ctx.emit_instr("DW", str((val >> 32) & 0xFFFF))
            self.ctx.emit_instr("DW", str((val >> 48) & 0xFFFF))
        else:
            # Unknown size - emit as words, pad to size
            self.ctx.emit_instr("DW", str(value & 0xFFFF))
            if size > 2:
                self.ctx.emit_instr("DS", str(size - 2))

    def _emit_float_value(self, value: float) -> None:
        """Emit a 32-bit IEEE-754 float value."""
        import struct
        # Pack as little-endian 32-bit float
        packed = struct.pack('<f', value)
        # Unpack as little-endian 32-bit unsigned integer
        ieee_val = struct.unpack('<I', packed)[0]
        low = ieee_val & 0xFFFF
        high = (ieee_val >> 16) & 0xFFFF
        self.ctx.emit_instr("DW", str(low))
        self.ctx.emit_instr("DW", str(high))

    def _eval_const_expr(self, expr: ast.Expression) -> int | None:
        """Try to evaluate a constant expression at compile time. Returns None if not constant."""
        if isinstance(expr, ast.IntLiteral):
            return expr.value
        elif isinstance(expr, ast.Identifier):
            # Check for enum constant
            if expr.name in self.ctx.enum_constants:
                return self.ctx.enum_constants[expr.name]
            return None  # Not a compile-time constant
        elif isinstance(expr, ast.CharLiteral):
            return expr.value
        elif isinstance(expr, ast.UnaryOp):
            operand_val = self._eval_const_expr(expr.operand)
            if operand_val is None:
                return None
            if expr.op == "-":
                return -operand_val
            elif expr.op == "+":
                return operand_val
            elif expr.op == "~":
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
                is_signed = self._is_signed_type(target_type)
                if name == "char":
                    # 8-bit: mask and sign extend if signed
                    val = inner_val & 0xFF
                    if is_signed and val >= 0x80:
                        val = val - 0x100  # Sign extend
                    return val
                elif name in ("short", "int"):
                    # 16-bit
                    val = inner_val & 0xFFFF
                    if is_signed and val >= 0x8000:
                        val = val - 0x10000  # Sign extend
                    return val
                elif name == "long":
                    # 32-bit
                    val = inner_val & 0xFFFFFFFF
                    if is_signed and val >= 0x80000000:
                        val = val - 0x100000000  # Sign extend
                    return val
            return inner_val  # Fallback: no conversion
        elif isinstance(expr, ast.BinaryOp):
            left_val = self._eval_const_expr(expr.left)
            right_val = self._eval_const_expr(expr.right)
            if left_val is None or right_val is None:
                return None
            if expr.op == "+":
                return left_val + right_val
            elif expr.op == "-":
                return left_val - right_val
            elif expr.op == "*":
                return left_val * right_val
            elif expr.op == "/" and right_val != 0:
                return left_val // right_val
            elif expr.op == "%" and right_val != 0:
                return left_val % right_val
            elif expr.op == "&":
                return left_val & right_val
            elif expr.op == "|":
                return left_val | right_val
            elif expr.op == "^":
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
