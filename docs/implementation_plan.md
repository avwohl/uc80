# uc80: C24 Compiler for Z80 - Implementation Plan

## Overview

A C compiler targeting the Z80 processor, implementing ISO/IEC 9899:2024 (C24).
Output: Z80 assembly for um80 assembler, linked with ul80, tested via cpmemu.

## Architecture

```
C source → Preprocessor → Lexer → Parser → AST → Semantic Analysis → IR → Code Gen → .mac file
                                                                                          ↓
                                                                                    um80 → .rel
                                                                                          ↓
                                                                                    ul80 → .com
                                                                                          ↓
                                                                                    cpmemu (test)
```

## Z80-Specific Design Decisions

### Type Sizes (Z80 is 8-bit, 16-bit address space)
| C Type      | Size    | Notes                          |
|-------------|---------|--------------------------------|
| char        | 8 bits  | signed by default              |
| short       | 16 bits |                                |
| int         | 16 bits | Z80 native word size           |
| long        | 32 bits |                                |
| long long   | 32 bits | (optional, same as long)       |
| pointer     | 16 bits | 64KB address space             |
| float       | 32 bits | IEEE 754 (software emulated)   |
| double      | 32 bits | same as float for Z80          |

### Calling Convention
- Parameters pushed right-to-left on stack
- Return value in A (8-bit), HL (16-bit), or memory (32-bit)
- Caller cleans up stack
- IX used as frame pointer

### Register Usage
- AF: accumulator, flags (scratch)
- BC, DE: scratch, parameters
- HL: primary working register, return values
- IX: frame pointer
- IY: reserved for OS/runtime
- SP: stack pointer

---

## Implementation Phases

### Phase 1: Minimal Viable Compiler
Goal: Compile "Hello, World!" to working CP/M executable

#### 1.1 Project Setup
- [ ] Directory structure (src/, tests/, lib/, docs/)
- [ ] Build system (Makefile or Python script)
- [ ] Test harness using cpmemu

#### 1.2 Lexer (src/lexer.py)
Implement tokenization per C24 Section 6.4:
- [ ] Keywords (6.4.2) - all 50+ C24 keywords
- [ ] Identifiers (6.4.3)
- [ ] Integer constants (6.4.5)
- [ ] Character constants (6.4.5.5)
- [ ] String literals (6.4.6)
- [ ] Punctuators (6.4.7)
- [ ] Comments (6.4.10) - // and /* */

#### 1.3 Preprocessor (src/preprocessor.py)
Implement per C24 Section 6.10:
- [ ] #include (6.10.3) - basic file inclusion
- [ ] #define/#undef (6.10.5) - object-like macros only initially
- [ ] #ifdef/#ifndef/#endif (6.10.2)
- [ ] Predefined macros: __FILE__, __LINE__, __DATE__, __TIME__

#### 1.4 Parser (src/parser.py)
Recursive descent parser producing AST:
- [ ] External definitions (6.9)
- [ ] Function definitions (6.9.2)
- [ ] Declarations (6.7) - basic types only
- [ ] Compound statements (6.8.3)
- [ ] Expression statements (6.8.4)
- [ ] Return statement (6.8.7)

#### 1.5 AST (src/ast.py)
Node types for Phase 1:
- [ ] Program, Function, Parameter
- [ ] Declaration, Assignment
- [ ] BinaryOp, UnaryOp, Call
- [ ] Literal (int, char, string)
- [ ] Identifier, Return

#### 1.6 Type System (src/types.py)
- [ ] Basic types: char, int, void
- [ ] Pointer types
- [ ] Function types
- [ ] Type checking for expressions

#### 1.7 Code Generator (src/codegen.py)
Generate um80 assembly:
- [ ] Function prologue/epilogue
- [ ] Local variable allocation (stack)
- [ ] Integer arithmetic (+, -, *, /)
- [ ] Function calls
- [ ] Return values
- [ ] String literals in data section

#### 1.8 Runtime Library (lib/crt0.mac)
- [ ] CP/M startup code
- [ ] Stack setup
- [ ] Call main()
- [ ] Exit to CP/M

#### 1.9 Minimal libc (lib/)
- [ ] putchar() - BDOS call
- [ ] puts() - using putchar
- [ ] Basic printf() - %s, %d, %c only

---

### Phase 2: Core Language Features
Goal: Support typical embedded C programs

#### 2.1 Complete Expression Support (6.5)
- [ ] All arithmetic operators
- [ ] Bitwise operators (&, |, ^, ~, <<, >>)
- [ ] Logical operators (&&, ||, !)
- [ ] Comparison operators
- [ ] Conditional operator (?:)
- [ ] Comma operator
- [ ] sizeof operator
- [ ] Cast expressions

#### 2.2 Complete Statement Support (6.8)
- [ ] if/else (6.8.5.1)
- [ ] switch/case/default (6.8.5.2)
- [ ] while (6.8.6.1)
- [ ] do-while (6.8.6.2)
- [ ] for (6.8.6.3)
- [ ] break, continue (6.8.7.3, 6.8.7.4)
- [ ] goto, labels (6.8.7.1, 6.8.2)

#### 2.3 Complete Type Support (6.7.3)
- [ ] All integer types (char, short, int, long)
- [ ] signed/unsigned modifiers
- [ ] const, volatile qualifiers (6.7.4)
- [ ] Arrays (6.7.7.2)
- [ ] Pointers (6.7.7.1)
- [ ] struct (6.7.3.3)
- [ ] union (6.7.3.3)
- [ ] enum (6.7.3.4)
- [ ] typedef (6.7.9)

#### 2.4 Storage Classes (6.7.2)
- [ ] auto (default)
- [ ] static
- [ ] extern
- [ ] register (hint only)

#### 2.5 Enhanced Preprocessor
- [ ] Function-like macros (6.10.5)
- [ ] Macro arguments
- [ ] Stringification (#)
- [ ] Token pasting (##)
- [ ] #if/#elif/#else
- [ ] defined() operator

---

### Phase 3: Advanced Features
Goal: Full C89/C99 core compliance

#### 3.1 Advanced Types
- [ ] Bit-fields (6.7.3.3)
- [ ] Flexible array members
- [ ] Variable-length arrays (optional in C24)
- [ ] _Bool type (C99)
- [ ] Complex numbers (optional, likely skip for Z80)

#### 3.2 Initialization (6.7.11)
- [ ] Scalar initialization
- [ ] Array initialization
- [ ] Struct initialization
- [ ] Designated initializers

#### 3.3 Standard Library (Section 7)
Priority headers for Z80/CP/M:
- [ ] <stdio.h> - file I/O via BDOS
- [ ] <stdlib.h> - malloc, free, atoi, etc.
- [ ] <string.h> - memcpy, strlen, strcmp, etc.
- [ ] <ctype.h> - character classification
- [ ] <stdint.h> - fixed-width types
- [ ] <stddef.h> - NULL, size_t, etc.
- [ ] <limits.h> - type limits
- [ ] <stdarg.h> - varargs
- [ ] <setjmp.h> - non-local jumps
- [ ] <assert.h> - debugging

Lower priority (complex or less useful on Z80):
- [ ] <math.h> - software floating point
- [ ] <time.h> - limited CP/M support
- [ ] <signal.h> - limited usefulness
- [ ] <locale.h> - minimal implementation

Skip for Z80:
- <threads.h> - no OS threading
- <stdatomic.h> - no SMP
- <complex.h> - too expensive
- <fenv.h> - software float has no env

---

### Phase 4: C24-Specific Features
Goal: Support new C24 additions where practical

#### 4.1 Feasible for Z80
- [ ] true/false/bool keywords (was _Bool)
- [ ] Binary literals (0b prefix)
- [ ] Digit separators (1'000'000)
- [ ] [[]] attributes (6.7.13)
- [ ] typeof (6.7.3.6)
- [ ] nullptr (7.21.3)
- [ ] constexpr (6.7.2)
- [ ] static_assert (6.7.12)
- [ ] _Generic (6.5.2.1)

#### 4.2 Probably Skip
- [ ] Decimal floating point
- [ ] _BitInt(N)
- [ ] Checked integer arithmetic
- [ ] Most Annex K (bounds-checking)

---

### Phase 5: Optimization
Goal: Generate efficient Z80 code

#### 5.1 Peephole Optimizations
- [ ] Redundant load/store elimination
- [ ] Strength reduction (shift vs multiply)
- [ ] Constant folding
- [ ] Dead code elimination

#### 5.2 Register Allocation
- [ ] Track register contents
- [ ] Minimize stack spills
- [ ] Use BC, DE effectively

#### 5.3 Z80-Specific
- [ ] Use Z80 block instructions (LDIR, etc.)
- [ ] Optimize 16-bit operations
- [ ] Short jumps (JR vs JP)

---

## File Structure

```
uc80/
├── src/
│   ├── __init__.py
│   ├── main.py           # CLI entry point
│   ├── lexer.py          # Tokenizer
│   ├── preprocessor.py   # C preprocessor
│   ├── parser.py         # Recursive descent parser
│   ├── ast.py            # AST node definitions
│   ├── types.py          # Type system
│   ├── semantic.py       # Semantic analysis
│   ├── ir.py             # Intermediate representation
│   ├── codegen.py        # Z80 code generator
│   └── errors.py         # Error handling
├── lib/
│   ├── crt0.mac          # C runtime startup
│   ├── libc/             # Standard library in C/asm
│   │   ├── stdio.c
│   │   ├── stdlib.c
│   │   ├── string.c
│   │   └── ...
│   └── include/          # Standard headers
│       ├── stdio.h
│       ├── stdlib.h
│       └── ...
├── tests/
│   ├── lexer/
│   ├── parser/
│   ├── codegen/
│   └── integration/
├── docs/
│   └── paid/             # C24 standard (gitignored)
├── CLAUDE.md
├── todo.txt
└── Makefile
```

---

## Testing Strategy

### Unit Tests
- Lexer: token streams for various inputs
- Parser: AST structure verification
- Type checker: type inference and errors
- Code gen: assembly output comparison

### Integration Tests
- Compile → assemble → link → run via cpmemu
- Compare output with expected
- Test suite of C programs

### Conformance Tests
- Port subset of GCC torture tests
- Create Z80-specific edge case tests

---

## Build Commands

```bash
# Compile C to assembly
python -m src.main input.c -o output.mac

# Assemble
um80 output.mac

# Link with runtime
ul80 output.rel lib/crt0.rel lib/libc.rel -o program.com

# Test
../cpmemu/src/cpmemu program.com
```

---

## Milestones

1. **M1**: Lexer tokenizes valid C - can run lexer tests
2. **M2**: Parser produces AST for simple functions
3. **M3**: "Hello, World!" compiles and runs on cpmemu
4. **M4**: Can compile simple programs with if/while/for
5. **M5**: Structs and pointers working
6. **M6**: Self-hosting consideration (compile parts of itself)

---

## References

- ISO/IEC 9899:2024 (docs/paid/)
- Z80 CPU User Manual
- CP/M BDOS documentation
- um80, ul80 documentation (../projectname/)
