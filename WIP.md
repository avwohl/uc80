# Work in Progress - uc80 C24 Compiler

## Current Status: Phase 1 - Lexer & Parser Complete

### Completed
- [x] Step 0: GitHub repo created (private)
- [x] Step 1: C24 standard PDF converted to text
- [x] Step 2: Implementation plan created
- [x] Lexer (src/lexer.py) - 42 tests passing
- [x] Parser (src/parser.py) - 78 tests passing

### Next Steps (Phase 1 continued)
1. **Code Generator** (src/codegen.py)
   - Generate Z80 assembly (.mac files) for um80
   - Function prologue/epilogue
   - Stack frame management (IX = frame pointer)
   - Expression evaluation
   - Control flow (if/while/for)

2. **Runtime Library** (lib/crt0.mac)
   - CP/M startup code
   - Stack setup
   - Call main()
   - Exit handling

3. **Minimal libc** (lib/)
   - putchar() via BDOS
   - puts()
   - Basic printf()

4. **Integration Test**
   - Compile "Hello, World!"
   - Assemble with um80
   - Link with ul80
   - Run on cpmemu

### Architecture Reference
```
C source → Lexer → Parser → AST → CodeGen → .mac
                                              ↓
                                         um80 → .rel
                                              ↓
                                         ul80 → .com
                                              ↓
                                         cpmemu (test)
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
- `docs/implementation_plan.md` - Full roadmap
- `docs/paid/ISO+IEC+9899-2024.txt` - C24 standard (gitignored)
