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

    def build_call_graph(self, unit: ast.TranslationUnit) -> None:
        """Build call graph by analyzing all function bodies."""
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
                    size += self._var_size(item.var_type)
            elif isinstance(item, ast.DeclarationList):
                for decl in item.declarations:
                    if isinstance(decl, ast.VarDecl) and decl.storage_class != "static":
                        size += self._var_size(decl.var_type)
            elif isinstance(item, ast.CompoundStmt):
                size += self._calc_locals_size(item)
            elif isinstance(item, ast.ForStmt):
                if isinstance(item.init, ast.VarDecl):
                    size += self._var_size(item.init.var_type)
                elif isinstance(item.init, ast.DeclarationList):
                    for decl in item.init.declarations:
                        if isinstance(decl, ast.VarDecl):
                            size += self._var_size(decl.var_type)
                if isinstance(item.body, ast.CompoundStmt):
                    size += self._calc_locals_size(item.body)
        return size

    def _var_size(self, t: ast.TypeNode) -> int:
        """Return the size of a type in bytes."""
        if isinstance(t, ast.BasicType):
            name = t.name
            if name in ("char", "_Bool", "bool"):
                return 1
            elif name in ("short", "int"):
                return 2
            elif name in ("long", "float", "double"):
                return 4
            elif name == "void":
                return 0
            return 2
        elif isinstance(t, ast.PointerType):
            return 2
        elif isinstance(t, ast.ArrayType):
            base_size = self._var_size(t.base_type)
            if t.size and isinstance(t.size, ast.IntLiteral):
                return base_size * t.size.value
            return base_size
        elif isinstance(t, ast.StructType):
            # Would need struct table lookup - estimate
            return 4
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

        return items[0].value is not None

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

    # Function names (for distinguishing functions from variables)
    function_names: set[str] = field(default_factory=set)

    # Enum constants: name -> integer value
    enum_constants: dict[str, int] = field(default_factory=dict)

    # Static local variables: label -> (type, init_value)
    static_locals: dict[str, tuple[ast.TypeNode, Optional[ast.Expression]]] = field(default_factory=dict)
    static_counter: int = 0

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

    def add_string(self, value: str) -> str:
        """Add a string literal and return its label."""
        # Check if string already exists
        for label, s in self.strings.items():
            if s == value:
                return label
        label = self.new_string_label()
        self.strings[label] = value
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
                self.ctx.globals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=decl.var_type,
                    is_global=True
                )
            elif isinstance(decl, ast.DeclarationList):
                for d in decl.declarations:
                    if isinstance(d, ast.VarDecl):
                        self.ctx.globals[d.name] = Symbol(
                            name=d.name,
                            sym_type=d.var_type,
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

        # Data segment with string literals
        if self.ctx.strings:
            self.ctx.emit()
            self.ctx.emit("; String literals")
            self.ctx.emit("\tDSEG")
            for label, value in self.ctx.strings.items():
                self.ctx.emit_label(label)
                # Emit string bytes with null terminator
                escaped = self._escape_string(value)
                self.ctx.emit_instr("DB", f"'{escaped}',0")

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
            self.ctx.emit("\tDSEG")
            for name, decl in global_vars.items():
                size = self._type_size(decl.var_type)
                self.ctx.emit_label(f"_{decl.name}")
                if decl.init:
                    # Initialized global variable - use helper to emit data
                    self._emit_initializer(decl.init, decl.var_type)
                else:
                    # Uninitialized global - just reserve space
                    self.ctx.emit_instr("DS", str(size))

        # Static local variables (in DSEG)
        if self.ctx.static_locals:
            if not global_vars and not self.ctx.strings:
                self.ctx.emit()
                self.ctx.emit("\tDSEG")
            self.ctx.emit("; Static local variables")
            for label, (var_type, init) in self.ctx.static_locals.items():
                self.ctx.emit_label(label)
                size = self._type_size(var_type)
                if init:
                    self._emit_initializer(init, var_type)
                else:
                    self.ctx.emit_instr("DS", str(size))

        # Shared automatic storage for non-recursive functions
        if self.call_graph_analyzer and self.call_graph_analyzer.total_shared_storage > 0:
            need_dseg = not global_vars and not self.ctx.strings and not self.ctx.static_locals
            if need_dseg:
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
        offset = 0
        for member in decl.members:
            if member.name:
                members.append((member.name, member.member_type, offset))
                if decl.is_union:
                    # Union: all members at offset 0
                    pass
                else:
                    # Struct: sequential layout
                    offset += self._type_size(member.member_type)
        self.ctx.structs[decl.name] = members

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
                offset = 0
                for member in type_node.members:
                    if member.name:
                        members.append((member.name, member.member_type, offset))
                        if not type_node.is_union:
                            offset += self._type_size(member.member_type)
                self.ctx.structs[type_node.name] = members
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
                offset = 0
                for member in struct_type.members:
                    if member.name:
                        members.append((member.name, member.member_type, offset))
                        if struct_type.is_union:
                            pass  # Union: all at offset 0
                        else:
                            offset += self._type_size(member.member_type)
                self.ctx.structs[struct_name] = members
        # For enum types, nothing special needed - enum values are already constants

    def gen_function(self, func: ast.FunctionDecl) -> None:
        """Generate code for a function."""
        if func.body is None:
            # Just a declaration, emit EXTRN
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
                param_offset += size

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
            # Check for static local variable
            if decl.storage_class == "static":
                self._gen_static_local(decl)
                return

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
                # Handle array initialization specially
                if isinstance(decl.var_type, ast.ArrayType) and isinstance(decl.init, ast.InitializerList):
                    self._gen_local_array_init(decl)
                else:
                    is_long = self._is_long_type(decl.var_type)
                    init_is_float = self._is_float_expr(decl.init)
                    target_is_int = not self._is_float_type(decl.var_type) and not is_long

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
                    else:
                        self.gen_expr(decl.init, force_long=is_long)

                        # Extend to 32-bit if target is long but source is not
                        if is_long and not self._is_long_expr(decl.init):
                            is_signed = not self._is_unsigned_expr(decl.init)
                            self._extend_hl_to_dehl(is_signed)

                    sym = self.ctx.locals[decl.name]
                    if is_long:
                        self._store_local_32(sym)
                    else:
                        self._store_local(sym)

    def _gen_static_local(self, decl: ast.VarDecl) -> None:
        """Handle static local variable."""
        # Generate unique label for this static variable
        # Use function name + counter to make globally unique, don't use @ prefix
        func_name = self.ctx.current_function or "global"
        label = f"__{func_name}_S{self.ctx.static_counter}"
        self.ctx.static_counter += 1

        # Store type and init value for data segment emission
        self.ctx.static_locals[label] = (decl.var_type, decl.init)

        # Register as a "global" for access purposes, but mark is_static=True
        # so we don't add another _ prefix when accessing
        self.ctx.locals[decl.name] = Symbol(
            name=label,  # Use label as name for global-style access
            sym_type=decl.var_type,
            is_global=True,
            is_static=True  # Mark as static to avoid double underscore
        )

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
            return_is_long = self._is_long_type(self.ctx.current_return_type)
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
                    # Regular case - evaluate constant
                    if isinstance(s.value, ast.IntLiteral):
                        label = self.ctx.new_label("CASE")
                        cases.append((s.value.value, label))
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
            # Find matching case label
            if isinstance(stmt.value, ast.IntLiteral):
                for value, label in self._switch_cases:
                    if value == stmt.value.value:
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
            label = self.ctx.add_string(expr.value)
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

    def gen_identifier(self, expr: ast.Identifier, force_long: bool = False) -> None:
        """Generate code to load an identifier's value into HL (or DEHL for 32-bit)."""
        # Check for enum constant first
        if expr.name in self.ctx.enum_constants:
            val = self.ctx.enum_constants[expr.name]
            self.ctx.emit_instr("LD", f"HL,{val}")
            if force_long:
                self.ctx.emit_instr("LD", "DE,0")
            return

        sym = self.ctx.lookup(expr.name)
        if sym is None:
            # Assume external function - load its address
            self.ctx.emit_instr("LD", f"HL,_{expr.name}")
            return

        # Check if this is a function (name matches a function we've seen)
        # Functions used as values decay to pointers - load address, not value
        if isinstance(sym.sym_type, ast.FunctionType) or expr.name in self.ctx.function_names:
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

        type_is_long = self._is_long_type(sym.sym_type)
        type_is_float = self._is_float_type(sym.sym_type)
        type_size = self._type_size(sym.sym_type)

        if sym.is_global:
            if type_is_long or type_is_float:
                # Load 32-bit value
                self.ctx.emit_instr("LD", f"HL,({sym.label()})")
                self.ctx.emit_instr("LD", f"DE,({sym.label()}+2)")
            elif type_size == 1:
                # Load 8-bit value, zero-extend to HL
                self.ctx.emit_instr("LD", f"A,({sym.label()})")
                self.ctx.emit_instr("LD", "L,A")
                self.ctx.emit_instr("LD", "H,0")
            else:
                # Load 16-bit value
                self.ctx.emit_instr("LD", f"HL,({sym.label()})")
        else:
            # Local variable: IX+offset or shared storage
            if type_is_long or type_is_float:
                self._load_local_32(sym)
            elif type_size == 1:
                # Load 8-bit value, zero-extend to HL
                if sym.uses_shared_storage:
                    self.ctx.emit_instr("LD", f"A,(??AUTO+{sym.shared_offset})")
                    self.ctx.emit_instr("LD", "L,A")
                    self.ctx.emit_instr("LD", "H,0")
                else:
                    self.ctx.emit_instr("LD", f"L,({ix_off(sym.offset)})")
                    self.ctx.emit_instr("LD", "H,0")
            else:
                self._load_local(sym)

        # Extend to 32-bit if requested but type is not already long
        if force_long and not type_is_long:
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

        # Check if this is a 32-bit operation
        is_long = self._is_long_expr(expr.left) or self._is_long_expr(expr.right)

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
            self._call_runtime("__mul16")
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
            self._call_runtime("__shl16")
        elif op == ">>":
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
        # Check if target is 32-bit
        target_is_long = False
        if isinstance(expr.left, ast.Identifier):
            sym = self.ctx.lookup(expr.left.name)
            if sym and self._is_long_type(sym.sym_type):
                target_is_long = True

        # Generate the value (force_long if target is 32-bit)
        self.gen_expr(expr.right, force_long=target_is_long)

        # If target is 32-bit but source is not, extend
        if target_is_long and not self._is_long_expr(expr.right):
            is_signed = not self._is_unsigned_expr(expr.right)
            self._extend_hl_to_dehl(is_signed)

        # Store to the target
        if isinstance(expr.left, ast.Identifier):
            sym = self.ctx.lookup(expr.left.name)
            if sym:
                if target_is_long:
                    if sym.is_global:
                        self.ctx.emit_instr("LD", f"({sym.label()}),HL")
                        self.ctx.emit_instr("LD", f"({sym.label()}+2),DE")
                    else:
                        self._store_local_32(sym)
                else:
                    if sym.is_global:
                        self.ctx.emit_instr("LD", f"({sym.label()}),HL")
                    else:
                        self._store_local(sym)
        elif isinstance(expr.left, ast.UnaryOp) and expr.left.op == "*":
            # Pointer dereference assignment: *p = value
            self.ctx.emit_instr("PUSH", "HL")  # Save value
            self.gen_expr(expr.left.operand)   # Get address in HL
            self.ctx.emit_instr("POP", "DE")   # Value in DE
            self.ctx.emit_instr("LD", "(HL),E")
            self.ctx.emit_instr("INC", "HL")
            self.ctx.emit_instr("LD", "(HL),D")
            self.ctx.emit_instr("EX", "DE,HL")  # Return value in HL
        elif isinstance(expr.left, ast.Index):
            # Array element assignment
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
            self.gen_expr(expr.operand)
            if self._is_long_expr(expr.operand):
                # 32-bit negate using runtime
                self._call_runtime("__neg32")
            else:
                # 16-bit negate: 0 - HL
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
            if self._is_long_expr(expr.operand):
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

            # Determine size of dereferenced type
            deref_size = self._get_deref_size(expr.operand)

            if deref_size == 1:
                # 8-bit load, zero-extend to HL
                self.ctx.emit_instr("LD", "L,(HL)")
                self.ctx.emit_instr("LD", "H,0")
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

                # Increment or decrement
                if is_inc:
                    self.ctx.emit_instr("INC", "HL")
                else:
                    self.ctx.emit_instr("DEC", "HL")

                # Store back
                if sym.is_global:
                    self.ctx.emit_instr("LD", f"({sym.label()}),HL")
                else:
                    self._store_local(sym)

                if not expr.is_prefix:
                    # Postfix: restore original value as result
                    self.ctx.emit_instr("POP", "HL")

    def gen_call(self, expr: ast.Call) -> None:
        """Generate code for function call."""
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
                arg_is_long = self._is_long_expr(arg)
                if arg_is_long:
                    self.gen_expr(arg, force_long=True)
                    # Push 32-bit value: high word (DE) first, then low word (HL)
                    self.ctx.emit_instr("PUSH", "DE")
                    self.ctx.emit_instr("PUSH", "HL")
                    stack_size += 4
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
        # Check if return value is 32-bit to preserve DEHL
        return_is_long = False
        if isinstance(expr.func, ast.Identifier):
            return_type = self._get_expr_type(expr)
            return_is_long = self._is_long_type(return_type)

        if stack_size > 0:
            if return_is_long:
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

        # Generate the source expression without forcing long -
        # the cast itself handles the extension
        self.gen_expr(expr.expr, force_long=False)

        source_size = self._type_size(source_type) if source_type else 2
        target_size = self._type_size(target_type)
        target_signed = self._is_signed_type(target_type)

        # Handle narrowing conversions first (before widening if force_long)
        if target_size == 1 and source_size > 1:
            # Narrowing to char: truncate to L, clear H
            self.ctx.emit_instr("LD", "H,0")
        elif target_size == 2 and source_size == 4:
            # Narrowing from long to short: just keep HL (DE is discarded)
            pass

        # Handle widening conversions
        if target_is_long and target_size < 4:
            # Need to extend to 32-bit DEHL after narrowing
            # Use the target type's signedness for extension
            self._extend_hl_to_dehl(target_signed)
        elif target_is_long and not source_is_long:
            # Direct extension to 32-bit
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

    def gen_index(self, expr: ast.Index) -> None:
        """Generate code for array indexing."""
        # Generate address, then dereference
        self._gen_address(expr)

        # Determine element size for proper load
        elem_size = self._get_index_elem_size(expr.array)

        if elem_size == 1:
            # 8-bit element, zero-extend to HL
            self.ctx.emit_instr("LD", "L,(HL)")
            self.ctx.emit_instr("LD", "H,0")
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

        # Determine member size and load appropriately
        member_size = self._get_member_size(expr)
        if member_size == 1:
            self.ctx.emit_instr("LD", "L,(HL)")
            self.ctx.emit_instr("LD", "H,0")
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

    def _get_member_size(self, expr: ast.Member) -> int:
        """Get the size of a struct member."""
        struct_type = self._get_expr_type(expr.obj)
        if isinstance(struct_type, ast.PointerType):
            struct_type = struct_type.base_type
        if isinstance(struct_type, ast.StructType) and struct_type.name:
            if struct_type.name in self.ctx.structs:
                for name, member_type, _ in self.ctx.structs[struct_type.name]:
                    if name == expr.member:
                        return self._type_size(member_type)
        return 2  # Default to 16-bit

    def _get_member_offset(self, struct_name: str, member_name: str) -> int:
        """Get the offset of a member within a struct."""
        if struct_name in self.ctx.structs:
            for name, _, offset in self.ctx.structs[struct_name]:
                if name == member_name:
                    return offset
        return 0

    def _get_expr_type(self, expr: ast.Expression) -> ast.TypeNode | None:
        """Try to infer the type of an expression."""
        if isinstance(expr, ast.Identifier):
            sym = self.ctx.lookup(expr.name)
            if sym:
                return sym.sym_type
        elif isinstance(expr, ast.UnaryOp) and expr.op == "*":
            # Dereference - get base type of pointer
            ptr_type = self._get_expr_type(expr.operand)
            if isinstance(ptr_type, ast.PointerType):
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
        elif isinstance(expr, ast.BinaryOp):
            # For arithmetic/bitwise ops, result type is based on operand types
            # This is simplified - real C would have more complex rules
            left_type = self._get_expr_type(expr.left)
            right_type = self._get_expr_type(expr.right)
            # If either operand is unsigned, result is unsigned
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
        return None

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
            if elem_size == 2:
                self.ctx.emit_instr("ADD", "HL,HL")  # index * 2
            elif elem_size == 4:
                self.ctx.emit_instr("ADD", "HL,HL")  # index * 2
                self.ctx.emit_instr("ADD", "HL,HL")  # index * 4
            # For elem_size == 1, no scaling needed

            self.ctx.emit_instr("PUSH", "HL")
            self.gen_expr(expr.array)  # Get base address
            self.ctx.emit_instr("POP", "DE")
            self.ctx.emit_instr("ADD", "HL,DE")

        elif isinstance(expr, ast.UnaryOp) and expr.op == "*":
            # Address of *p is p
            self.gen_expr(expr.operand)

        elif isinstance(expr, ast.Member):
            if expr.is_arrow:
                self.gen_expr(expr.obj)  # p->member: p is the address
            else:
                self._gen_address(expr.obj)  # s.member: address of s

            # Get struct type and member offset
            struct_type = self._get_expr_type(expr.obj)
            if expr.is_arrow and isinstance(struct_type, ast.PointerType):
                struct_type = struct_type.base_type
            if isinstance(struct_type, ast.StructType) and struct_type.name:
                offset = self._get_member_offset(struct_type.name, expr.member)
                if offset > 0:
                    self.ctx.emit_instr("LD", f"DE,{offset}")
                    self.ctx.emit_instr("ADD", "HL,DE")

    def _is_unsigned_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has unsigned type."""
        expr_type = self._get_expr_type(expr)
        if isinstance(expr_type, ast.BasicType):
            # is_signed=False means unsigned, is_signed=None means default (signed)
            return expr_type.is_signed == False
        return False

    def _is_long_type(self, t: ast.TypeNode | None) -> bool:
        """Check if a type is 32-bit (long)."""
        if isinstance(t, ast.BasicType):
            return t.name in ("long", "long long")
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

    def _is_long_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression has 32-bit type."""
        if isinstance(expr, ast.IntLiteral):
            # Check for explicit L suffix or value too large for signed 16-bit
            if expr.is_long:
                return True
            # In C, decimal literals are int, long, long long (first that fits)
            return expr.value > 32767 or expr.value < -32768
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
            # Any 32-bit binary op will use __tmp32
            if self._is_long_expr(expr.left) or self._is_long_expr(expr.right):
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

    def _store_local(self, sym: Symbol) -> None:
        """Store HL into a local variable."""
        if sym.uses_shared_storage:
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

        elif isinstance(expr, ast.Cast):
            # Use the cast target type
            if isinstance(expr.target_type, ast.PointerType):
                return self._type_size(expr.target_type.base_type)

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
        return size

    def _type_size(self, t: ast.TypeNode) -> int:
        """Return the size of a type in bytes."""
        if isinstance(t, ast.BasicType):
            name = t.name
            if name in ("char", "_Bool", "bool"):
                return 1
            elif name in ("short", "int"):
                return 2
            elif name in ("long", "float", "double"):
                return 4
            elif name == "void":
                return 0
            return 2  # Default
        elif isinstance(t, ast.PointerType):
            return 2  # 16-bit pointers
        elif isinstance(t, ast.ArrayType):
            # Array size * element size
            base_size = self._type_size(t.base_type)
            if t.size and isinstance(t.size, ast.IntLiteral):
                return base_size * t.size.value
            return base_size  # Unsized array, return element size
        elif isinstance(t, ast.StructType):
            # Look up struct definition
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

    def _emit_initializer(self, init: ast.Expression, elem_type: ast.TypeNode) -> None:
        """Emit initialized data for a global variable or array element."""
        elem_size = self._type_size(elem_type)

        if isinstance(init, ast.InitializerList):
            # Handle array or struct initializer
            if isinstance(elem_type, ast.ArrayType):
                # Array initializer - emit each element
                base_type = elem_type.base_type
                for val in init.values:
                    if isinstance(val, ast.DesignatedInit):
                        self._emit_initializer(val.value, base_type)
                    else:
                        self._emit_initializer(val, base_type)
                # Pad with zeros if initializer is shorter than array size
                if elem_type.size and isinstance(elem_type.size, ast.IntLiteral):
                    remaining = elem_type.size.value - len(init.values)
                    if remaining > 0:
                        pad_size = remaining * self._type_size(base_type)
                        self.ctx.emit_instr("DS", str(pad_size))
            elif isinstance(elem_type, ast.StructType):
                # Struct initializer
                if elem_type.name and elem_type.name in self.ctx.structs:
                    members = self.ctx.structs[elem_type.name]
                    for i, val in enumerate(init.values):
                        if i < len(members):
                            _, member_type, _ = members[i]
                            if isinstance(val, ast.DesignatedInit):
                                self._emit_initializer(val.value, member_type)
                            else:
                                self._emit_initializer(val, member_type)
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
            # String literal - emit as bytes
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
            self.ctx.emit_instr("DW", f"_{init.name}")
        elif isinstance(init, ast.UnaryOp) and init.op == "&":
            # Address-of expression
            if isinstance(init.operand, ast.Identifier):
                self.ctx.emit_instr("DW", f"_{init.operand.name}")
            else:
                self.ctx.emit_instr("DS", str(elem_size))
        elif isinstance(init, ast.Cast):
            # Cast expression - try to evaluate constant
            const_val = self._eval_const_expr(init)
            if const_val is not None:
                self._emit_int_value(const_val, elem_size)
            else:
                self.ctx.emit_instr("DS", str(elem_size))
        else:
            # Complex initializer - reserve space
            self.ctx.emit_instr("DS", str(elem_size))

    def _emit_int_value(self, value: int, size: int) -> None:
        """Emit an integer value with the specified size."""
        if size == 1:
            self.ctx.emit_instr("DB", str(value & 0xFF))
        elif size == 4:
            # 32-bit: emit low word first, then high word
            val = value & 0xFFFFFFFF
            low = val & 0xFFFF
            high = (val >> 16) & 0xFFFF
            self.ctx.emit_instr("DW", str(low))
            self.ctx.emit_instr("DW", str(high))
        else:
            self.ctx.emit_instr("DW", str(value & 0xFFFF))

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
                return left_val >> right_val
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
