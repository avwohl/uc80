# Work in Progress - uc80 C24 Compiler

## Current Status: Phase 1 - Code Generation Complete

### Completed
- [x] Step 0: GitHub repo created (private)
- [x] Step 1: C24 standard PDF converted to text
- [x] Step 2: Implementation plan created
- [x] Lexer (src/lexer.py) - 42 tests passing
- [x] Parser (src/parser.py) - 78 tests passing
- [x] Code Generator (src/codegen.py) - 33 tests passing
- [x] Runtime Library (lib/runtime.mac) - 16-bit mul/div/mod/shift
- [x] CP/M Startup (lib/crt0.mac) - Stack setup, main() call, exit
- [x] Minimal libc (lib/libc.mac) - putchar, getchar, puts, printf, strlen
- [x] CLI Entry Point (src/main.py) - Command-line compiler interface
- [x] End-to-End Test - Hello World and arithmetic working!

### Working Features
- Basic types: char, int
- Pointers and pointer arithmetic
- Arrays and array indexing
- Control flow: if/else, while, do-while, for, switch/case
- break/continue in loops and switch
- Arithmetic: +, -, *, /, %
- Comparisons: <, <=, >, >=, ==, !=
- Logical: &&, ||, !
- Bitwise: &, |, ^, ~, <<, >>
- Function calls and recursion
- Local variables and parameters
- Global variables with initialization
- String literals
- Compound assignment (+=, -=, *=, /=, %=, etc.)
- Pre/post increment/decrement (++, --)
- Ternary operator (?:)
- Structs and unions with member access (. and ->)

### Bugs Fixed
- Parser: Parameter names were not captured for function definitions
- Linker (ul80): DSEG data placed at wrong address when linking multiple modules

### Next Steps (Phase 2)
1. **More Types**
   - long (32-bit) support
   - unsigned types

2. **Standard Library**
   - Full printf with format specifiers
   - scanf
   - Memory functions (memcpy, memset)
   - String functions (strcpy, strcmp, etc.)

3. **Optimizations**
   - Register allocation improvements
   - Peephole optimization

### Architecture Reference
```
C source -> Lexer -> Parser -> AST -> CodeGen -> .mac
                                              |
                                         um80 -> .rel
                                              |
                                         ul80 -> .com
                                              |
                                         cpmemu (test)
```

### Example Usage
```bash
# Compile C to assembly
python -m src.main examples/hello.c -o examples/hello.mac

# Assemble
um80 examples/hello.mac

# Link with runtime
ul80 lib/crt0.rel examples/hello.rel lib/libc.rel -o hello.com

# Run on CP/M emulator
cpmemu hello.com
```

### Z80 Type Sizes
- char: 8 bits
- int/short: 16 bits
- long: 32 bits
- pointer: 16 bits

### Test Command
```bash
python -m pytest tests/ -v
```

## Files
- `src/tokens.py` - Token types
- `src/lexer.py` - Tokenizer
- `src/ast.py` - AST nodes
- `src/parser.py` - Recursive descent parser
- `src/codegen.py` - Z80 code generator
- `src/main.py` - CLI entry point
- `lib/crt0.mac` - CP/M runtime startup
- `lib/runtime.mac` - 16-bit arithmetic library
- `lib/libc.mac` - Minimal C library
- `docs/implementation_plan.md` - Full roadmap
- `docs/paid/ISO+IEC+9899-2024.txt` - C24 standard (gitignored)
