# UC80 C Standard Compliance Report

This document compares uc80 compiler features against ANSI C (C89/C90), C99, and C24.

## Summary

UC80 targets C24 (ISO/IEC 9899:2024). Core language is complete.
Standard library is substantially implemented. Main gaps are `_Complex`
codegen and multibyte/wide character functions.

Test suite results: 219/220 c-testsuite, 488/523 SDCC.

---

## FULLY IMPLEMENTED

### Data Types
- [x] char (signed/unsigned)
- [x] short (16-bit)
- [x] int (16-bit)
- [x] long (32-bit)
- [x] long long (64-bit)
- [x] float (32-bit IEEE 754, software)
- [x] double (32-bit, same as float)
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

### Arithmetic
- [x] 16-bit integer arithmetic (int, short)
- [x] 32-bit integer arithmetic (long)
- [x] 64-bit integer arithmetic (long long) - add, sub, mul, div, mod, shifts, bitwise, compare
- [x] IEEE 754 single-precision float - add, sub, mul, div, compare, conversions
- [x] Printf %f format with fractional printing

---

## Standard Library

### Fully Implemented
- [x] `<assert.h>` - assert macro with _assert_fail
- [x] `<ctype.h>` - all character classification functions + toupper/tolower
- [x] `<errno.h>` - POSIX error codes, extern errno
- [x] `<float.h>` - IEEE 754 32-bit constants (FLT_*, DBL_*, LDBL_*)
- [x] `<inttypes.h>` - PRId/PRIu/PRIx format macros
- [x] `<iso646.h>` - alternative operator spellings
- [x] `<limits.h>` - type limits for Z80 (8/16/16/32-bit)
- [x] `<stdalign.h>` - alignas/alignof (alignof always 1 on Z80)
- [x] `<stdarg.h>` - va_list, va_start, va_arg, va_end, va_copy
- [x] `<stdbool.h>` - bool, true, false
- [x] `<stddef.h>` - NULL, size_t, ptrdiff_t, offsetof
- [x] `<stdint.h>` - exact-width types (int8_t through int64_t)
- [x] `<stdnoreturn.h>` - noreturn macro
- [x] `<string.h>` - all functions (strlen, strcmp, strcpy, strcat, memcpy, memmove, memset, memcmp, memchr, strchr, strrchr, strstr, strspn, strcspn, strpbrk, strtok, strdup, strerror)
- [x] `<tgmath.h>` - type-generic macros (all map to float versions)

### `<math.h>` - Implemented
All functions use IEEE 754 single-precision (32-bit). Taylor series, Newton-Raphson, and reduction algorithms.

- [x] `sin()`, `cos()`, `tan()` - with range reduction
- [x] `asin()`, `acos()`, `atan()`, `atan2()`
- [x] `sinh()`, `cosh()`, `tanh()`
- [x] `exp()`, `log()`, `log10()`, `log2()`
- [x] `pow()`, `sqrt()`, `cbrt()`, `hypot()`
- [x] `ceil()`, `floor()`, `trunc()`, `round()`
- [x] `fabs()`, `fmod()`, `remainder()`, `copysign()`
- [x] `frexp()`, `ldexp()`, `modf()`
- [x] Float (`sinf`, `cosf`, etc.) and long double (`sinl`, `cosl`, etc.) variants

- [x] `exp2()`, `expm1()`, `log1p()`, `logb()`, `scalbn()`
- [x] `fmax()`, `fdim()`

Not implemented: `asinh`, `acosh`, `atanh`, `ilogb`, `nan`, `fma`, `fmin`, `nextafter`, classification macros.

### `<stdio.h>` - Implemented
Full file I/O via CP/M BDOS FCB calls. 128-byte buffering. Max 4 regular files + 3 standard streams.

- [x] `printf()`, `fprintf()`, `sprintf()`, `snprintf()` - table-driven format dispatch
- [x] `vprintf()`, `vfprintf()`, `vsprintf()`, `vsnprintf()`
- [x] Format specifiers: %d %i %u %x %X %o %s %c %p %f %% with width, precision, `*` and the flags `- + space # 0`. The `l` and `ll` forms take the same width, precision and flags. Width and precision are 16-bit numbers (at most 32767). `%f` `%e` `%g` take a precision only (no width or flag). `%llX` and `%llo` are not implemented
- [x] Long (%ld %lu %lx %lo) and long long (%lld %llu %llx) format specifiers
- [x] `scanf()`, `fscanf()`, `sscanf()`, `vscanf()`, `vfscanf()`, `vsscanf()` - all conversions, widths, `%*`, `%n`, scan sets, the size modifiers and the float conversions (see [scanf.md](scanf.md))
- [x] `fopen()`, `fclose()`, `freopen()`
- [x] `fread()`, `fwrite()`
- [x] `fgetc()`, `fputc()`, `getc()`, `putc()`
- [x] `fgets()`, `fputs()`
- [x] `getchar()`, `putchar()`, `puts()`, `gets()`
- [x] `fseek()`, `ftell()`, `fgetpos()`, `fsetpos()`, `rewind()`
- [x] `feof()`, `ferror()`, `clearerr()`, `ungetc()`, `fflush()`
- [x] `remove()`, `rename()`, `tmpfile()`, `tmpnam()`
- [x] `setbuf()`, `setvbuf()` - no-op (CP/M uses fixed 128-byte buffers)
- [x] `perror()`

Printf links only needed format handlers via `#pragma printf int|long|llong|float|all`.

### `<stdlib.h>` - Implemented
- [x] `atoi()`, `atol()`, `atof()`
- [x] `strtol()`, `strtoul()`, `strtoll()`, `strtoull()`, `strtod()`, `strtof()`, `strtold()`, `atoll()` - bases 0 and 2 to 36, `errno`, correct rounding of every decimal float string (a 64-bit fast path and an exact big-integer comparison near a tie)
- [x] `malloc()`, `calloc()`, `realloc()`, `free()` - first-fit free list, sorted by address, merged on `free()`. The heap grows from `__END__` up to 255 bytes below the stack pointer
- [x] `abs()`, `labs()`, `div()`, `ldiv()`
- [x] `rand()`, `srand()` - 16-bit LCG
- [x] `exit()`, `abort()`, `atexit()` - supports 32 handlers; `main` returning runs them ([exit and files](exit_and_files.md))
- [ ] `getenv()` - stub (CP/M has no environment)
- [ ] `system()` - stub (CP/M limited)
- [x] `bsearch()` - binary search
- [x] `qsort()` - quicksort with median-of-3 pivot, no recursion, and an
  insertion sort for parts of 8 elements or fewer.  Sorted and reverse-sorted
  input takes nmemb-1 comparisons.  A comparator can call `qsort()`.  Do not
  call `qsort()` from an interrupt handler.  Details: `src/uc80/lib/lc/lc_qsort.mac`.
- [ ] `mblen()`, `mbtowc()`, `wctomb()`, `mbstowcs()`, `wcstombs()` - stubs

### `<setjmp.h>` - Implemented
- [x] `setjmp()` - saves IX, SP, return address (6-byte jmp_buf)
- [x] `longjmp()` - restores saved environment

### `<signal.h>` - Partial
- [x] `signal()` - software signal table (7 slots), set/get handlers
- [x] `raise()` - invokes handler
- CP/M has no hardware signals; this is a software-only system

### `<locale.h>` - Minimal
- [x] `setlocale()` - accepts "C" and "" only
- [x] `localeconv()` - returns struct lconv for C locale

### `<time.h>` - Stubs
- [ ] `time()` - returns 0 (CP/M has no RTC)
- [ ] `clock()` - returns 0
- [ ] `difftime()`, `localtime()`, `gmtime()`, `mktime()`, `asctime()`, `ctime()`, `strftime()` - stubs

---

## NOT IMPLEMENTED

### `_Complex` type (C99)
Keyword parsed. Header `<complex.h>` has declarations and partial assembly implementation.
No codegen support - code compiles but produces incorrect results.

### `<wchar.h>` / `<wctype.h>` - Minimal
Type definitions exist. Wide I/O functions declared but mostly stubs.

### `<fenv.h>` - Minimal
Constants defined. Runtime functions are stubs (no hardware FPU).

---

## COMPLIANCE STATUS

Feature	Status
Core language syntax	DONE
Integer arithmetic (16/32-bit)	DONE
64-bit integer arithmetic (long long)	DONE
Floating-point arithmetic (IEEE 754)	DONE
`<string.h>`	DONE
`<ctype.h>`	DONE
`<math.h>`	DONE (core functions)
`<stdio.h>`	DONE (printf, scanf, file I/O)
`<stdlib.h>`	DONE
`<setjmp.h>`	DONE
`<signal.h>`	PARTIAL (software only)
`<locale.h>`	MINIMAL (C locale only)
`<time.h>`	STUBS (CP/M limitation)
`_Complex` type	NOT IMPLEMENTED (parsed only)
`<wchar.h>`	MINIMAL

---

## REMAINING WORK

### Priority 1 - Gaps
1. **`_Complex` codegen** - complex number arithmetic
2. **Math extras** - asinh/acosh/atanh, fma, fmin, classification macros

### Priority 2 - Nice to have
4. **`<time.h>`** - real implementation if RTC available
5. **Wide character support** - wchar.h / wctype.h
6. **`<fenv.h>`** - floating-point exception tracking

---

## C99/C11/C24 FEATURES IMPLEMENTED

Beyond ANSI C:
- `//` comments
- `_Bool` / `bool` / `true` / `false`
- `long long` (64-bit with full arithmetic)
- Designated initializers
- `inline` functions (parsed)
- `restrict` qualifier (parsed)
- `<stdint.h>` with exact-width types
- `<stdbool.h>`
- `<inttypes.h>` with format macros
- `<iso646.h>` alternative spellings
- `<stdalign.h>` / `<stdnoreturn.h>`
- `<tgmath.h>` type-generic math
- Binary literals (0b...)
- Digit separators (1'000)
- `nullptr`
- `static_assert`
- `_Generic`

## Errors that the compiler gives

The compiler checks each C file after the parse, before it makes code
(`src/uc80/semcheck.py`). Each message starts with `file:line:` of the
source line, also for a line in a header. The compiler reports all errors
of a file, then stops with exit status 1.

- **Undeclared name.** C23 has no implicit declarations. A name that no
  scope declares is an error, in every function, also in a function that
  no code calls. Example: `u.c:9: 'd6' undeclared (first use in function
  'main')`.
- **Call of an undeclared function.** `foo(3)` without a declaration of
  `foo` is an error. The option `--allow-implicit-functions` changes the
  error to a warning (the call then goes to an external function with the
  name). No program of the test suite or of `bench/` needs the option.
  The compiler knows each name that starts with `__builtin_`.
- **Change of a const object** (C23 6.5.16, 6.5.2.4, 6.5.3.1). The
  operators `=`, `op=`, `++` and `--` need a modifiable lvalue. These are
  errors:
  - a const variable or parameter, also through a typedef
  - a `T *const` pointer (`*p--` changes `p`)
  - a const member, or a member of a const structure
  - an object that a pointer to const points to
  - an element of a const array
  - the assignment of a structure that has a const member (6.3.2.1)

  A pointer to const that you move (`const int *p; p++`) is correct. The
  compiler cannot see the const of a type that a cast names through a
  typedef-name. It gives no error for that case.
- **Variable length array.** `short v[k + 2];` in a block is an error.
  uc80 does not implement VLAs: a silent version gave a stack slot with
  no size, and the slot could overlap other temporaries. Use an array with
  a constant size, or `malloc`. A parameter such as `int a[n]` is a
  pointer and is correct. The c-testsuite test 00207 declares
  `char test[argc]`. The file `tests/c-testsuite-z80/00207.c` has the same
  test with `char test[4]`.

  An array that has a size from a plain `const int` is a VLA in C23, so
  `const int k = 3; int a[k];` in a block is an error too. Older uc80
  versions accepted this. Use `constexpr int k = 3;`, an `enum` constant
  or `#define`. A `constexpr` object and `offsetof` are constants and are
  correct as an array size.

- **Initializer that is not constant.** An object with static storage
  (a file-scope object or a `static` local) needs a constant initializer.
  `const int e = 3; static int q = e;` is an error: the compiler gave 0
  or garbage before. The message names the variable. Use an enumerator,
  a `constexpr` object or a macro. An address (`&x`, an array name, a
  function name), `sizeof` and `offsetof` are constants and are correct.

## C99/C11/C24 FEATURES NOT IMPLEMENTED

- [ ] `_Complex` / `_Imaginary` - parsed but no codegen
- [ ] VLA (variable length arrays) - not implemented. C23 makes VLAs optional. uc80 defines `__STDC_NO_VLA__` as 1, and a block-scope array with a size that is not a constant is an error (see "Errors that the compiler gives")
- [ ] `<threads.h>` - threading (not applicable to Z80/CP-M single-tasking)
- [ ] `<stdatomic.h>` - atomics (Z80 is single-core; DI/EI stubs exist)
