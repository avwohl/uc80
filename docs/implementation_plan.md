# uc80: C24 Compiler for Z80 - Implementation Plan

## Overview

A C compiler targeting the Z80 processor, implementing ISO/IEC 9899:2024 (C24).
Output: Z80 assembly for um80 assembler, linked with ul80, tested via cpmemu.

## Architecture

```
C source -> Preprocessor -> Lexer -> Parser -> AST -> Semantic Analysis -> Code Gen -> .mac file
                                                                                        |
                                                                                  um80 -> .rel
                                                                                        |
                                                                                  ul80 -> .com
                                                                                        |
                                                                                  cpmemu (test)
```

## Z80-Specific Design Decisions

### Type Sizes (Z80 is 8-bit, 16-bit address space)

C Type	Size	Notes
char	8 bits	signed by default
short	16 bits
int	16 bits	Z80 native word size
long	32 bits
long long	64 bits	full arithmetic via __acc64/__tmp64
pointer	16 bits	64KB address space
float	32 bits	IEEE 754 (software emulated)
double	32 bits	same as float for Z80
long double	32 bits	same as float for Z80

### Calling Convention
- Parameters pushed right-to-left on stack
- Return value in A (8-bit), HL (16-bit), DEHL (32-bit), or memory (64-bit)
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

## Implementation Status

### Phase 1: Minimal Viable Compiler - COMPLETE
- [x] Project setup, build system, test harness
- [x] Lexer (tokenization per C24 Section 6.4)
- [x] Preprocessor (#include, #define, #if, macros, stringification, token pasting)
- [x] Parser (recursive descent, full expression/statement/declaration support)
- [x] AST node definitions
- [x] Type system (all basic types, pointers, arrays, structs, unions, enums, typedef)
- [x] Code generator (um80 assembly output)
- [x] Runtime library (crt0, CP/M startup, stack setup)
- [x] Minimal libc (putchar, puts, printf)

### Phase 2: Core Language Features - COMPLETE
- [x] All arithmetic, bitwise, logical, comparison operators
- [x] Conditional, comma, sizeof, cast expressions
- [x] if/else, switch/case, while, do-while, for, break, continue, goto
- [x] All integer types with signed/unsigned, const/volatile
- [x] Arrays, pointers, struct, union, enum, typedef
- [x] Storage classes: auto, static, extern, register
- [x] Function-like macros, stringification, token pasting, #if/#elif, defined()

### Phase 3: Advanced Features - COMPLETE
- [x] Bit-fields
- [x] Flexible array members
- [x] _Bool type
- [x] Scalar/array/struct initialization
- [x] Designated initializers
- [x] IEEE 754 single-precision float (add, sub, mul, div, compare, conversions)
- [x] 64-bit long long arithmetic (add, sub, mul, div, mod, shifts, bitwise, compare)

### Phase 3b: Standard Library - MOSTLY COMPLETE
- [x] `<stdio.h>` - full printf/fprintf/sprintf/snprintf, vprintf/vfprintf/vsprintf, scanf/fscanf/sscanf, file I/O via BDOS
- [x] `<stdlib.h>` - atoi/atol/atof, strtol/strtod, malloc/calloc/realloc/free, abs/labs/div/ldiv, rand/srand, exit/abort/atexit
- [x] `<string.h>` - all functions (memcpy, memmove, memset, memcmp, strlen, strcmp, strcpy, strcat, strchr, strstr, strtok, strdup, etc.)
- [x] `<ctype.h>` - all classification functions + toupper/tolower
- [x] `<math.h>` - trig, inverse trig, hyperbolic, exp/log/pow, sqrt/cbrt/hypot, floor/ceil/trunc/round, fabs/fmod/frexp/ldexp/modf
- [x] `<stdint.h>`, `<stddef.h>`, `<limits.h>`, `<float.h>`, `<stdarg.h>`
- [x] `<stdbool.h>`, `<stdalign.h>`, `<stdnoreturn.h>`, `<iso646.h>`, `<inttypes.h>`
- [x] `<assert.h>`, `<errno.h>`
- [x] `<setjmp.h>` - setjmp/longjmp (6-byte jmp_buf: IX, SP, return addr)
- [x] `<signal.h>` - software signal system (7 slots)
- [x] `<locale.h>` - minimal C locale
- [x] `<tgmath.h>` - type-generic macros
- [ ] `<time.h>` - stubs (CP/M has no RTC)
- [ ] `<complex.h>` - declarations only, no codegen
- [ ] `<wchar.h>` / `<wctype.h>` - type definitions, stubs
- [ ] `<fenv.h>` - constants only, stubs

### Phase 4: C24-Specific Features - MOSTLY COMPLETE
- [x] true/false/bool keywords
- [x] Binary literals (0b prefix)
- [x] Digit separators (1'000'000)
- [x] nullptr
- [x] static_assert
- [x] _Generic
- [ ] typeof (6.7.3.6)
- [ ] [[]] attributes (6.7.13)
- [ ] constexpr (6.7.2)
- Skip: Decimal floating point, _BitInt(N), Annex K

### Phase 5: Optimization - IMPLEMENTED
- [x] Constant folding
- [x] Dead code elimination (ASM-level DCE)
- [x] Peephole optimizations
- [x] Shared storage (??AUTO) for non-recursive functions
- [x] Printf auto-detection (links only needed format handlers)
- [x] BSS optimization
- [ ] Advanced register allocation
- [ ] Z80 block instructions (LDIR, etc.)

---

## Modular Library Architecture

### Runtime (lib/rt/)
- rt_arith16.mac - 16-bit arithmetic helpers
- rt_arith32.mac - 32-bit arithmetic (mul, div, mod, shifts)
- rt_arith64.mac - 64-bit arithmetic (all operations via __acc64/__tmp64)
- rt_float.mac - IEEE 754 float (add, sub, mul, div, compare, conversions)
- rt_setjmp.mac - setjmp/longjmp

### Libc (lib/lc/)
44+ modules, each a separate .mac file. Key modules:
- lc_printf_core.mac + lc_printf_*.mac - modular printf with table dispatch
- lc_scanf.mac - scanf family
- lc_file.mac - fopen/fclose/fread/fwrite/fseek/ftell and all file ops
- lc_string.mac - all string.h functions
- lc_math_helper.mac + lc_sin/cos/tan/exp/log/sqrt/pow/etc. - math library
- lc_malloc.mac - bump allocator
- lc_atoi.mac - integer string parsing
- lc_atof.mac - float string parsing (strtod/strtof)

### 48-bit Double Library (lib/math48/)
Converted from z88dk Math48 (Anders Hejlsberg, 1980). 185 files, ~148 assemble.
Not currently used; available for future true double support.

### Build
- lib/build_libs.py - builds .lib archives and monolithic .rel from modular sources
- Monolithic .rel files used by test runners; .lib files for production linking

---

## Testing

### Test Suites
- c-testsuite: 218/220 passing (00040 flaky timeout, 00219 _Generic edge case)
- SDCC: 488/523 passing (6 sdcc extensions, 8 float precision, 5 libc gaps, ~10 real bugs)
- Fujitsu suites 0000-0052

### Test Infrastructure
- cpmemu runs compiled .com files, translates CP/M system calls
- Test runner uses tempfile.TemporaryDirectory() for isolation
- Automated comparison against expected output

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
