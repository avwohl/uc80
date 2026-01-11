# UC80 C Standard Compliance Report

This document compares uc80 compiler features against ANSI C (C89/C90) and C99.

## Summary

UC80 targets C24 (ISO/IEC 9899:2024) but has significant gaps in implementation.
This document lists what is actually working vs what is missing.

---

## FULLY IMPLEMENTED

### Data Types
- [x] char (signed/unsigned)
- [x] short (16-bit)
- [x] int (16-bit)
- [x] long (32-bit)
- [x] void
- [x] struct
- [x] union
- [x] enum
- [x] Pointers
- [x] Arrays
- [x] typedef

### Type Qualifiers
- [x] const
- [x] volatile

### Storage Classes
- [x] auto
- [x] static (global and local)
- [x] extern
- [x] register (parsed as hint)

### Operators
- [x] All arithmetic (+, -, *, /, %)
- [x] All bitwise (&, |, ^, ~, <<, >>)
- [x] All logical (&&, ||, !)
- [x] All comparison (==, !=, <, >, <=, >=)
- [x] All assignment (=, +=, -=, etc.)
- [x] Increment/decrement (++, --)
- [x] Ternary conditional (?:)
- [x] Comma operator
- [x] sizeof
- [x] Type casts
- [x] Address-of (&) and dereference (*)
- [x] Array subscript ([])
- [x] Member access (. and ->)

### Control Flow
- [x] if / else
- [x] switch / case / default
- [x] while
- [x] do / while
- [x] for
- [x] break
- [x] continue
- [x] goto and labels
- [x] return

### Preprocessor
- [x] #include "..." and <...>
- [x] #define (object-like)
- [x] #define (function-like)
- [x] #undef
- [x] #if / #elif / #else / #endif
- [x] #ifdef / #ifndef
- [x] #error
- [x] #pragma
- [x] #line
- [x] Stringification (#)
- [x] Token pasting (##)
- [x] defined() operator
- [x] __FILE__, __LINE__, __DATE__, __TIME__, __STDC__

### Functions
- [x] Function definitions
- [x] Function prototypes
- [x] Variadic functions (...)
- [x] Function pointers
- [x] Recursive functions

### Standard Library - Fully Implemented
- [x] `<assert.h>` - assert macro
- [x] `<ctype.h>` - character classification (all functions)
- [x] `<errno.h>` - error codes
- [x] `<limits.h>` - type limits
- [x] `<stdarg.h>` - variadic arguments
- [x] `<stddef.h>` - NULL, size_t, ptrdiff_t, offsetof
- [x] `<string.h>` - string operations (all major functions)

---

## NOT IMPLEMENTED - MUST BE ADDED

### 1. `_Complex` type (C99) - NOT IMPLEMENTED
The `_Complex` keyword is parsed but does NOT work.
Code compiles but produces incorrect results.

Header `<complex.h>` has declarations only. None of these work:
- `_Complex` type itself (no codegen support)
- `creal()`, `cimag()`, `cabs()`, `carg()`
- `conj()`, `cproj()`
- `cexp()`, `clog()`, `cpow()`, `csqrt()`
- `csin()`, `ccos()`, `ctan()` and inverse trig
- `csinh()`, `ccosh()`, `ctanh()` and inverse hyperbolic
- All float and long double variants

**Needs**: Implement `_Complex` as a struct with real/imaginary parts,
generate proper code for complex arithmetic.

### 2. `<math.h>` - NOT IMPLEMENTED
The header exists with declarations but functions are NOT implemented.
They return 0 or pass through input unchanged.

ANSI C requires:
- [ ] `sin()`, `cos()`, `tan()`
- [ ] `asin()`, `acos()`, `atan()`, `atan2()`
- [ ] `sinh()`, `cosh()`, `tanh()`
- [ ] `exp()`, `log()`, `log10()`
- [ ] `pow()`, `sqrt()`
- [ ] `ceil()`, `floor()`, `fabs()`, `fmod()`
- [ ] `frexp()`, `ldexp()`, `modf()`

**Needs**: Real floating-point math library. Z80 FP libraries exist
(e.g., z88dk's math library, or port from other sources).

### 3. `float` / `double` arithmetic - NOT IMPLEMENTED
The types exist and are parsed, but:
- [ ] Floating-point literals not properly handled
- [ ] FP arithmetic operations not code-generated
- [ ] FP comparison operations not implemented
- [ ] printf %f/%e/%g format specifiers incomplete

**Needs**: Software floating-point library integration.

### 4. `<locale.h>` - NOT IMPLEMENTED
ANSI C requires:
- [ ] `setlocale()`
- [ ] `localeconv()`
- [ ] `struct lconv`
- [ ] LC_ALL, LC_COLLATE, LC_CTYPE, LC_MONETARY, LC_NUMERIC, LC_TIME

### 5. `scanf()` family - NOT IMPLEMENTED
- [ ] `scanf()`
- [ ] `fscanf()`
- [ ] `sscanf()`

### 6. `vprintf()` family - NOT IMPLEMENTED
- [ ] `vprintf()`
- [ ] `vfprintf()`
- [ ] `vsprintf()`

### 7. `<stdio.h>` gaps
- [ ] `tmpfile()`, `tmpnam()`
- [ ] `remove()`, `rename()`
- [ ] `setbuf()`, `setvbuf()`
- [ ] `perror()`
- [ ] Full format specifier compliance (%n, width/precision for all types)

### 8. `<stdlib.h>` gaps
- [ ] `atof()` - requires FP parsing
- [ ] `strtod()` - requires FP parsing
- [ ] `mblen()`, `mbtowc()`, `wctomb()` - multibyte
- [ ] `mbstowcs()`, `wcstombs()` - multibyte strings

### 9. `<signal.h>` - MINIMAL
- [ ] `signal()` - declaration only
- [ ] `raise()` - declaration only

### 10. `<setjmp.h>` - NOT VERIFIED
- [ ] `setjmp()` - needs testing
- [ ] `longjmp()` - needs testing

---

## COMPLIANCE STATUS

| Feature | Status |
|---------|--------|
| Core Language Syntax | DONE |
| Integer arithmetic (16/32-bit) | DONE |
| Floating-point types | PARSED ONLY |
| Floating-point arithmetic | NOT IMPLEMENTED |
| `_Complex` type | NOT IMPLEMENTED |
| `<string.h>` | DONE |
| `<ctype.h>` | DONE |
| `<stdio.h>` | PARTIAL (no scanf) |
| `<stdlib.h>` | PARTIAL (no FP funcs) |
| `<math.h>` | NOT IMPLEMENTED |
| `<locale.h>` | NOT IMPLEMENTED |
| `<complex.h>` | NOT IMPLEMENTED |

---

## IMPLEMENTATION PRIORITIES

### Priority 1 - Core gaps
1. **Floating-point arithmetic** - Basic +, -, *, / for float/double
2. **`<math.h>` functions** - At minimum: sin, cos, sqrt, pow, exp, log
3. **`scanf()` family** - Essential for input parsing

### Priority 2 - Completeness
4. **`_Complex` type** - Full complex number support
5. **`vprintf()` family** - Easy with existing printf
6. **`atof()`, `strtod()`** - FP string conversion

### Priority 3 - Full compliance
7. **`<locale.h>`** - Can be minimal "C" locale only
8. **`<setjmp.h>`** - Verify or implement
9. **Remaining stdio functions**

---

## C99/C11/C24 FEATURES IMPLEMENTED

These are implemented beyond ANSI C:
- `//` comments
- `_Bool` / `bool`
- `long long` (as 32-bit)
- Designated initializers
- `inline` functions (parsed)
- `restrict` qualifier (parsed)
- `<stdint.h>` with exact-width types
- `<stdbool.h>`
- Binary literals (0b...)
- Digit separators (1'000)
- `nullptr`
- `static_assert`
- `_Generic`

---

## C99/C11/C24 FEATURES NOT IMPLEMENTED

- [ ] `_Complex` / `_Imaginary` - declarations only, no codegen
- [ ] VLA (Variable Length Arrays) - parsed but no codegen
- [ ] `<tgmath.h>` - type-generic math (needs _Complex and math.h)
- [ ] `<fenv.h>` - floating-point environment
- [ ] `<threads.h>` - threading (not applicable to Z80/CP-M)
- [ ] `<stdatomic.h>` - atomics (not applicable to Z80)
