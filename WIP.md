# Work in Progress - uc80 C24 Compiler

## Current Status: Test Suite Validation

### Test Results (Jan 8, 2025)
- **z88dk tests**: 22/22 passed (100%)
  - mult: 5/5, shift: 8/8, compare: 4/4, bitwise: 5/5
- **c-testsuite**: 162/220 passed (73.6%)

### Recent Fixes
- Function pointer declaration parsing (`int (*fptr)()` now correctly parsed)
- Tentative definitions (multiple `int x;` declarations merge properly)
- Struct array member access (`jones[0].member` fixed)
- 32-bit signed operations (unary minus, bitwise NOT)
- Type inference for BinaryOp, UnaryOp, Index, Cast expressions

### External Test Suites (~/src/external)
- `c-testsuite/` - 220 single-exec tests with runners
- `compiler-test-suite/` - Fujitsu C test suite (C99 features like `restrict`)
- `z88dk/` - Full z88dk testsuite (tests assembly output)

### Remaining c-testsuite Failures (58 tests)
Common patterns:
- Anonymous structs/unions (00017, 00018, 00019, 00046, etc.)
- Nested struct definitions inside declarations
- `calloc` not implemented (00040)
- Preprocessor ternary `?:` in `#if` (00075)
- Float support missing

### Completed Features
- [x] Lexer, Parser, Code Generator
- [x] Runtime Library (16-bit and 32-bit arithmetic)
- [x] CP/M Startup and minimal libc
- [x] Basic types: char, int, long, unsigned variants
- [x] Pointers, arrays, structs, unions, enums, typedef
- [x] Control flow: if/else, while, do-while, for, switch/case
- [x] All arithmetic, comparison, logical, bitwise operators
- [x] Function calls, recursion, function pointers
- [x] Global/local variables, static locals
- [x] Compound assignment, pre/post increment/decrement
- [x] Ternary operator, string literals

### Next Steps
1. Support anonymous structs/unions
2. Support nested struct definitions in declarations
3. Add `calloc` to libc
4. Preprocessor ternary support

### Architecture
```
C source -> Lexer -> Parser -> AST -> CodeGen -> .mac
                                              |
                                         um80 -> .rel
                                              |
                                         ul80 -> .com
                                              |
                                         cpmemu (test)
```

### Quick Test Commands
```bash
# Run z88dk tests
for t in mult shift compare bitwise; do cpmemu tests/z88dk/$t.com; done

# Run c-testsuite (from ~/src/external/c-testsuite)
./runners/single-exec/uc80 tests/single-exec/00001.c

# Compile and run a C file
python -m src.main examples/hello.c -o examples/hello.mac
um80 examples/hello.mac
ul80 lib/crt0.rel examples/hello.rel lib/libc.rel lib/runtime.rel -o hello.com
cpmemu hello.com
```

### Z80 Type Sizes
- char: 8 bits
- int/short: 16 bits
- long: 32 bits
- pointer: 16 bits

## Files
- `src/tokens.py` - Token types
- `src/lexer.py` - Tokenizer
- `src/ast.py` - AST nodes
- `src/parser.py` - Recursive descent parser
- `src/codegen.py` - Z80 code generator
- `src/main.py` - CLI entry point
- `lib/crt0.mac` - CP/M runtime startup
- `lib/runtime.mac` - Arithmetic library
- `lib/libc.mac` - Minimal C library
