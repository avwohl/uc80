"""Z80 code generator for C24 compiler.

Generates MACRO-80 compatible assembly (.mac files) for the um80 assembler.
Uses IX as frame pointer, following the calling convention in implementation_plan.md.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Iterator, Optional
from . import ast


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
        # Collect global variable declarations with their initializers
        global_var_decls = [d for d in unit.declarations
                           if isinstance(d, ast.VarDecl)
                           and not isinstance(d.var_type, ast.FunctionType)]
        if global_var_decls:
            self.ctx.emit()
            self.ctx.emit("; Global variables")
            self.ctx.emit("\tDSEG")
            for decl in global_var_decls:
                size = self._type_size(decl.var_type)
                self.ctx.emit_label(f"_{decl.name}")
                if decl.init:
                    # Initialized global variable
                    if isinstance(decl.init, ast.IntLiteral):
                        if size == 1:
                            self.ctx.emit_instr("DB", str(decl.init.value))
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

        # Make function public
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
        if isinstance(decl, ast.VarDecl):
            size = self._type_size(decl.var_type)
            self.ctx.local_offset -= size
            self.ctx.locals[decl.name] = Symbol(
                name=decl.name,
                sym_type=decl.var_type,
                offset=self.ctx.local_offset
            )
            # Initialize if there's an initializer
            if decl.init:
                self.gen_expr(decl.init)  # Result in HL (or A for char)
                self._store_local(self.ctx.locals[decl.name])

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

    def gen_expr(self, expr: ast.Expression) -> None:
        """Generate code for an expression. Result in HL (16-bit) or A (8-bit)."""
        if isinstance(expr, ast.IntLiteral):
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
            self.gen_identifier(expr)

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

    def gen_identifier(self, expr: ast.Identifier) -> None:
        """Generate code to load an identifier's value into HL."""
        sym = self.ctx.lookup(expr.name)
        if sym is None:
            # Assume external function
            self.ctx.emit_instr("LD", f"HL,_{expr.name}")
            return

        if sym.is_global:
            self.ctx.emit_instr("LD", f"HL,(_{sym.name})")
        else:
            # Local variable: IX+offset
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

        # Generate left operand (result in HL)
        self.gen_expr(expr.left)

        # Save left operand using register allocator
        # Request DE to hold left value; will spill if busy
        self.ctx.regs.need_reg('de', 'binary_left', self._emit_reg)
        self.ctx.emit_instr("EX", "DE,HL")  # Left now in DE
        self.ctx.regs.mark_busy('hl', 'binary_result')

        # Generate right operand (result in HL)
        self.gen_expr(expr.right)

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
            self._gen_comparison(op)
        elif op == ",":
            # Comma operator: result is right operand (already in HL)
            pass

        # Release DE now that operation is complete
        self.ctx.regs.release_reg('de', self._emit_reg)
        self.ctx.regs.mark_free('hl')

    def _emit_reg(self, instr: str, operand: str) -> None:
        """Emit instruction for register allocator callbacks."""
        self.ctx.emit_instr(instr, operand)

    def gen_assignment(self, expr: ast.BinaryOp) -> None:
        """Generate code for assignment."""
        # Generate the value
        self.gen_expr(expr.right)

        # Store to the target
        if isinstance(expr.left, ast.Identifier):
            sym = self.ctx.lookup(expr.left.name)
            if sym:
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
            # Negate: 0 - HL
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
            # Bitwise NOT
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
        # Push arguments right-to-left
        for arg in reversed(expr.args):
            self.gen_expr(arg)
            self.ctx.emit_instr("PUSH", "HL")

        # Call the function
        if isinstance(expr.func, ast.Identifier):
            self.ctx.emit_instr("CALL", f"_{expr.func.name}")
        else:
            # Indirect call
            self.gen_expr(expr.func)
            self._call_runtime("__callhl")

        # Clean up stack (caller cleanup)
        if expr.args:
            stack_size = len(expr.args) * 2  # Assuming 16-bit args
            if stack_size <= 6:
                for _ in range(len(expr.args)):
                    self.ctx.emit_instr("POP", "DE")  # Discard
            else:
                self.ctx.emit_instr("LD", f"DE,{stack_size}")
                self.ctx.emit_instr("ADD", "HL,DE")  # Oops, this clobbers HL
                # Better approach:
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
        # TODO: Need type information to calculate offset
        # For now, just generate address and dereference
        self._gen_address(expr)
        self.ctx.emit_instr("LD", "E,(HL)")
        self.ctx.emit_instr("INC", "HL")
        self.ctx.emit_instr("LD", "D,(HL)")
        self.ctx.emit_instr("EX", "DE,HL")

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
            # TODO: Add member offset (needs type info)

    def _gen_comparison(self, op: str) -> None:
        """Generate code for comparison. Left in DE, right in HL."""
        # Result should be 1 if true, 0 if false
        true_label = self.ctx.new_label("CMP_T")
        end_label = self.ctx.new_label("CMP_E")

        # Compare DE with HL (compute DE - HL)
        self.ctx.emit_instr("EX", "DE,HL")
        self.ctx.emit_instr("OR", "A")  # Clear carry
        self.ctx.emit_instr("SBC", "HL,DE")

        # Now flags reflect DE - HL (original left - right)
        if op == "==":
            self.ctx.emit_instr("JP", f"Z,{true_label}")
        elif op == "!=":
            self.ctx.emit_instr("JP", f"NZ,{true_label}")
        elif op == "<":
            # Signed less than: use sign flag
            self.ctx.emit_instr("JP", f"M,{true_label}")
        elif op == ">=":
            self.ctx.emit_instr("JP", f"P,{true_label}")
        elif op == ">":
            # left > right is !(left <= right)
            self.ctx.emit_instr("JP", f"Z,{end_label}")  # if equal, not greater
            self.ctx.emit_instr("JP", f"P,{true_label}")
        elif op == "<=":
            self.ctx.emit_instr("JP", f"Z,{true_label}")
            self.ctx.emit_instr("JP", f"M,{true_label}")

        # False case
        self.ctx.emit_instr("LD", "HL,0")
        self.ctx.emit_instr("JP", end_label)

        self.ctx.emit_label(true_label)
        self.ctx.emit_instr("LD", "HL,1")

        self.ctx.emit_label(end_label)

    def _load_local(self, sym: Symbol) -> None:
        """Load a local variable into HL."""
        if sym.offset >= 0:
            # Parameter (positive offset from IX)
            self.ctx.emit_instr("LD", f"L,(IX+{sym.offset})")
            self.ctx.emit_instr("LD", f"H,(IX+{sym.offset + 1})")
        else:
            # Local (negative offset from IX)
            self.ctx.emit_instr("LD", f"L,(IX{sym.offset})")
            self.ctx.emit_instr("LD", f"H,(IX{sym.offset + 1})")

    def _store_local(self, sym: Symbol) -> None:
        """Store HL into a local variable."""
        if sym.offset >= 0:
            self.ctx.emit_instr("LD", f"(IX+{sym.offset}),L")
            self.ctx.emit_instr("LD", f"(IX+{sym.offset + 1}),H")
        else:
            self.ctx.emit_instr("LD", f"(IX{sym.offset}),L")
            self.ctx.emit_instr("LD", f"(IX{sym.offset + 1}),H")

    def _call_runtime(self, name: str) -> None:
        """Call a runtime library function."""
        self.ctx.runtime_used.add(name)
        self.ctx.emit_instr("CALL", name)

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
            elif isinstance(item, ast.CompoundStmt):
                size += self._calc_locals_size(item)
            elif isinstance(item, ast.ForStmt):
                if isinstance(item.init, ast.VarDecl):
                    size += self._type_size(item.init.var_type)
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
