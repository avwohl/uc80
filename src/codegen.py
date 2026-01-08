"""Z80 code generator for C24 compiler.

Generates MACRO-80 compatible assembly (.mac files) for the um80 assembler.
Uses IX as frame pointer, following the calling convention in implementation_plan.md.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterator, Optional
from . import ast


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
    local_offset: int = 0  # Current stack offset for locals

    # Loop context for break/continue
    break_labels: list[str] = field(default_factory=list)
    continue_labels: list[str] = field(default_factory=list)

    # Runtime functions used (need EXTRN)
    runtime_used: set[str] = field(default_factory=set)

    # Struct definitions: name -> list of (member_name, member_type, offset)
    structs: dict[str, list[tuple[str, ast.TypeNode, int]]] = field(default_factory=dict)

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

    def __init__(self, module_name: str = "main"):
        self.module_name = module_name
        self.ctx = CodeGenContext()
        # Switch statement context
        self._switch_cases: list[tuple[int, str]] = []
        self._switch_default: str | None = None

    def generate(self, unit: ast.TranslationUnit) -> str:
        """Generate assembly for a translation unit."""
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
                self.ctx.globals[decl.name] = Symbol(
                    name=decl.name,
                    sym_type=decl.return_type,
                    is_global=True
                )
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

        # Emit EXTRN for runtime functions used
        if self.ctx.runtime_used:
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
        for d in unit.declarations:
            decls_to_check = []
            if isinstance(d, ast.VarDecl) and not isinstance(d.var_type, ast.FunctionType):
                decls_to_check.append(d)
            elif isinstance(d, ast.DeclarationList):
                for inner in d.declarations:
                    if isinstance(inner, ast.VarDecl) and not isinstance(inner.var_type, ast.FunctionType):
                        decls_to_check.append(inner)
            for decl in decls_to_check:
                if decl.name in global_vars:
                    # Already seen - prefer the one with initializer
                    if decl.init and not global_vars[decl.name].init:
                        global_vars[decl.name] = decl
                else:
                    global_vars[decl.name] = decl

        if global_vars:
            self.ctx.emit()
            self.ctx.emit("; Global variables")
            self.ctx.emit("\tDSEG")
            for name, decl in global_vars.items():
                size = self._type_size(decl.var_type)
                self.ctx.emit_label(f"_{decl.name}")
                if decl.init:
                    # Initialized global variable
                    if isinstance(decl.init, ast.IntLiteral):
                        if size == 1:
                            self.ctx.emit_instr("DB", str(decl.init.value))
                        elif size == 4:
                            # 32-bit: emit low word first, then high word
                            val = decl.init.value & 0xFFFFFFFF
                            low = val & 0xFFFF
                            high = (val >> 16) & 0xFFFF
                            self.ctx.emit_instr("DW", str(low))
                            self.ctx.emit_instr("DW", str(high))
                        else:
                            self.ctx.emit_instr("DW", str(decl.init.value))
                    elif isinstance(decl.init, ast.CharLiteral):
                        self.ctx.emit_instr("DB", str(decl.init.value))
                    else:
                        # Complex initializer - reserve space (would need runtime init)
                        self.ctx.emit_instr("DS", str(size))
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
                if init and isinstance(init, ast.IntLiteral):
                    if size == 1:
                        self.ctx.emit_instr("DB", str(init.value))
                    elif size == 4:
                        val = init.value & 0xFFFFFFFF
                        low = val & 0xFFFF
                        high = (val >> 16) & 0xFFFF
                        self.ctx.emit_instr("DW", str(low))
                        self.ctx.emit_instr("DW", str(high))
                    else:
                        self.ctx.emit_instr("DW", str(init.value))
                else:
                    self.ctx.emit_instr("DS", str(size))

        self.ctx.emit()
        self.ctx.emit("\tEND")

        return "\n".join(self.ctx.lines)

    def gen_declaration(self, decl: ast.Declaration) -> None:
        """Generate code for a declaration."""
        if isinstance(decl, ast.FunctionDecl):
            self.gen_function(decl)
        elif isinstance(decl, ast.VarDecl):
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

    def _register_typedef(self, decl: ast.TypedefDecl) -> None:
        """Register typedef, especially for anonymous structs."""
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
        self.ctx.locals.clear()
        self.ctx.local_offset = 0
        self.ctx.regs.reset()  # Reset register allocator for new function

        # Make function public (unless static)
        if func.storage_class != "static":
            self.ctx.emit_instr("PUBLIC", f"_{func.name}")
        self.ctx.emit()
        self.ctx.emit(f"; Function {func.name}")
        self.ctx.emit_label(f"_{func.name}")

        # Function prologue: save IX, set up frame
        self.ctx.emit_instr("PUSH", "IX")
        self.ctx.emit_instr("LD", "IX,0")
        self.ctx.emit_instr("ADD", "IX,SP")

        # Calculate space needed for locals
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
        self.ctx.emit_instr("LD", "SP,IX")
        self.ctx.emit_instr("POP", "IX")
        self.ctx.emit_instr("RET")
        self.ctx.emit()

        self.ctx.current_function = None

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

        if isinstance(decl, ast.VarDecl):
            # Check for static local variable
            if decl.storage_class == "static":
                self._gen_static_local(decl)
                return

            size = self._type_size(decl.var_type)
            self.ctx.local_offset -= size
            self.ctx.locals[decl.name] = Symbol(
                name=decl.name,
                sym_type=decl.var_type,
                offset=self.ctx.local_offset
            )
            # Initialize if there's an initializer
            if decl.init:
                is_long = self._is_long_type(decl.var_type)
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
        label = f"@S{self.ctx.static_counter}"
        self.ctx.static_counter += 1

        # Store type and init value for data segment emission
        self.ctx.static_locals[label] = (decl.var_type, decl.init)

        # Register as a "global" for access purposes
        self.ctx.locals[decl.name] = Symbol(
            name=label,  # Use label as name for global-style access
            sym_type=decl.var_type,
            is_global=True
        )

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

    def gen_return(self, stmt: ast.ReturnStmt) -> None:
        """Generate code for return statement."""
        if stmt.value:
            self.gen_expr(stmt.value)  # Result in HL (16-bit) or A (8-bit)
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
            if isinstance(s, ast.CaseStmt):
                if s.value is None:
                    # default case
                    default_label = self.ctx.new_label("DEFAULT")
                else:
                    # Regular case - evaluate constant
                    if isinstance(s.value, ast.IntLiteral):
                        label = self.ctx.new_label("CASE")
                        cases.append((s.value.value, label))
            elif isinstance(s, ast.CompoundStmt):
                for item in s.items:
                    if isinstance(item, ast.Statement):
                        collect_cases(item)

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

        # Set up case label map for gen_case
        case_idx = 0
        self._switch_cases = cases
        self._switch_default = default_label
        self._switch_case_idx = 0

        # Push break label
        self.ctx.break_labels.append(end_label)

        # Generate body with case labels
        self.gen_statement(stmt.body)

        # Pop break label
        self.ctx.break_labels.pop()

        # End label
        self.ctx.emit_label(end_label)

        # Clean up
        self._switch_cases = []
        self._switch_default = None

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

        # Generate the statement following the case label
        self.gen_statement(stmt.stmt)

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
            # For now, just generate the inner expression
            self.gen_expr(expr.expr)

        elif isinstance(expr, ast.Index):
            self.gen_index(expr)

        elif isinstance(expr, ast.Member):
            self.gen_member(expr)

        elif isinstance(expr, ast.SizeofType):
            size = self._type_size(expr.target_type)
            self.ctx.emit_instr("LD", f"HL,{size}")

        elif isinstance(expr, ast.SizeofExpr):
            # Would need type inference; for now assume int
            self.ctx.emit_instr("LD", "HL,2")

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
            # Assume external function
            self.ctx.emit_instr("LD", f"HL,_{expr.name}")
            return

        # Arrays decay to pointers - return address, not value
        if isinstance(sym.sym_type, ast.ArrayType):
            if sym.is_global:
                self.ctx.emit_instr("LD", f"HL,_{sym.name}")
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

        is_long = self._is_long_type(sym.sym_type) or force_long
        if sym.is_global:
            if is_long:
                self.ctx.emit_instr("LD", f"HL,(_{sym.name})")
                self.ctx.emit_instr("LD", f"DE,(_{sym.name}+2)")
            else:
                self.ctx.emit_instr("LD", f"HL,(_{sym.name})")
        else:
            # Local variable: IX+offset
            if is_long:
                self._load_local_32(sym)
            else:
                self._load_local(sym)

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
            self._call_runtime("__div16")
        elif op == "%":
            self._call_runtime("__mod16")
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

        # Check if left operand is complex (might clobber __tmp32)
        left_is_complex = self._is_complex_expr(expr.left)
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
            self._call_runtime("__div32")
        elif op == "%":
            self._call_runtime("__mod32")
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
                        self.ctx.emit_instr("LD", f"(_{sym.name}),HL")
                        self.ctx.emit_instr("LD", f"(_{sym.name}+2),DE")
                    else:
                        self._store_local_32(sym)
                else:
                    if sym.is_global:
                        self.ctx.emit_instr("LD", f"(_{sym.name}),HL")
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
                    self.ctx.emit_instr("LD", f"HL,(_{sym.name})")
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
                    self.ctx.emit_instr("LD", f"(_{sym.name}),HL")
                else:
                    self._store_local(sym)

                if not expr.is_prefix:
                    # Postfix: restore original value as result
                    self.ctx.emit_instr("POP", "HL")

    def gen_call(self, expr: ast.Call) -> None:
        """Generate code for function call."""
        # Push arguments right-to-left, tracking total stack size
        stack_size = 0
        for arg in reversed(expr.args):
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
            self.ctx.emit_instr("CALL", f"_{expr.func.name}")
        else:
            # Indirect call
            self.gen_expr(expr.func)
            self._call_runtime("__callhl")

        # Clean up stack (caller cleanup)
        if stack_size > 0:
            if stack_size <= 6:
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
        else:
            # 16-bit or larger element
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
        else:
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
        return None

    def _gen_address(self, expr: ast.Expression) -> None:
        """Generate code to compute address of an expression into HL."""
        if isinstance(expr, ast.Identifier):
            sym = self.ctx.lookup(expr.name)
            if sym:
                if sym.is_global:
                    self.ctx.emit_instr("LD", f"HL,_{sym.name}")
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

    def _is_complex_expr(self, expr: ast.Expression) -> bool:
        """Check if an expression might use __tmp32 (and thus clobber it)."""
        # Complex expressions that use __tmp32 internally
        if isinstance(expr, ast.BinaryOp):
            # Any 32-bit binary op will use __tmp32
            if self._is_long_expr(expr.left) or self._is_long_expr(expr.right):
                return True
            # Check nested expressions
            return self._is_complex_expr(expr.left) or self._is_complex_expr(expr.right)
        if isinstance(expr, ast.UnaryOp):
            return self._is_complex_expr(expr.operand)
        if isinstance(expr, ast.TernaryOp):
            return (self._is_complex_expr(expr.condition) or
                    self._is_complex_expr(expr.true_expr) or
                    self._is_complex_expr(expr.false_expr))
        if isinstance(expr, ast.Call):
            # Function calls might clobber __tmp32 (conservative)
            return True
        if isinstance(expr, ast.Cast):
            return self._is_complex_expr(expr.expr)
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
        self.ctx.emit_instr("LD", f"L,({ix_off(sym.offset)})")
        self.ctx.emit_instr("LD", f"H,({ix_off(sym.offset + 1)})")

    def _store_local(self, sym: Symbol) -> None:
        """Store HL into a local variable."""
        self.ctx.emit_instr("LD", f"({ix_off(sym.offset)}),L")
        self.ctx.emit_instr("LD", f"({ix_off(sym.offset + 1)}),H")

    def _load_local_32(self, sym: Symbol) -> None:
        """Load a 32-bit local variable into DEHL (DE=high, HL=low)."""
        self.ctx.emit_instr("LD", f"L,({ix_off(sym.offset)})")
        self.ctx.emit_instr("LD", f"H,({ix_off(sym.offset + 1)})")
        self.ctx.emit_instr("LD", f"E,({ix_off(sym.offset + 2)})")
        self.ctx.emit_instr("LD", f"D,({ix_off(sym.offset + 3)})")

    def _store_local_32(self, sym: Symbol) -> None:
        """Store DEHL (32-bit) into a local variable."""
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
        return 2  # Default

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


def generate(unit: ast.TranslationUnit, module_name: str = "main") -> str:
    """Generate Z80 assembly for a translation unit."""
    gen = CodeGenerator(module_name)
    return gen.generate(unit)
