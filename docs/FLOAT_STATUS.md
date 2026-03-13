# Floating Point Implementation Status

## WORKING - All Core Operations

IEEE 754 single-precision (32-bit) floating point is fully functional.
`double` and `long double` are the same as `float` (32-bit) on this Z80 target.

### Arithmetic (lib/rt/rt_float.mac)
- `__fadd` - Addition (DEHL + __tmp32)
- `__fsub` - Subtraction (DEHL - __tmp32)
- `__fmul` - Multiplication (24x24 partial products)
- `__fdiv` - Division (non-restoring long division, 24-bit quotient)
- `__fneg` - Negation (sign bit flip)
- `__fcmp` - Comparison (Z flag = equal, C flag = less than)

### Conversions (lib/rt/rt_float.mac)
- `__itof` / `__ltof` - Signed 32-bit int to float
- `__uitof` - Unsigned 32-bit int to float
- `__ftoi` - Float to signed int (truncate toward zero)

### Internal Helpers
- `__funpack` - Extract sign, exponent, mantissa from IEEE 754
- `__fpack` - Combine sign, exponent, mantissa into IEEE 754
- `__fnorm` - Normalize mantissa and adjust exponent

### Printf %f Support (lib/lc/lc_printf_f.mac)
- Prints integer and fractional parts
- Respects precision modifier (default 6 decimal places)
- Enabled via `#pragma printf float` or `#pragma printf all`

### Math Library (lib/lc/lc_*.mac)
Full single-precision math library:
- Trigonometric: sin, cos, tan, asin, acos, atan, atan2
- Hyperbolic: sinh, cosh, tanh
- Exponential: exp, log, log10, log2, pow
- Roots: sqrt, cbrt, hypot
- Rounding: floor, ceil, trunc, round
- Utilities: fabs, fmod, remainder, copysign, frexp, ldexp, modf

All functions have float (`sinf` etc.) and long double (`sinl` etc.) variants
that map to the same 32-bit implementation.

## 64-bit Integer Arithmetic (lib/rt/rt_arith64.mac)

Full `long long` (64-bit) support via memory-based accumulator/temporary:
- `__add64`, `__sub64`, `__neg64` - arithmetic
- `__mul64`, `__div64`, `__mod64` - multiply/divide/modulo
- `__sdiv64`, `__smod64` - signed divide/modulo
- `__shl64`, `__shr64`, `__sar64` - shifts
- `__and64`, `__or64`, `__xor64`, `__not64` - bitwise
- `__cmp64`, `__ucmp64` - signed/unsigned compare
- `__sext64`, `__zext64` - sign/zero extend from 32-bit
- `__load64`, `__store64` - memory access
- Printf: %lld, %llu, %llx via lc_prt_dec64.mac

Convention: 64-bit values in `__acc64` (accumulator) and `__tmp64` (operand), 8 bytes each in DSEG.

## 48-bit Double Library (lib/math48/)

Converted from z88dk's Math48 library (Anders Hejlsberg, 1980).
48-bit format: BCDEH = 40-bit mantissa, L = 8-bit exponent (bias 128).
Core mm48_* routines assemble; am48_* alias wrappers need JP trampolines
due to MACRO-80 EQU/EXTRN limitation. Not currently used by default
(compiler uses 32-bit IEEE 754 float for all FP types).

## Register Convention

- Float values passed in DEHL (D=sign+exp high, E=exp low+mantissa high, HL=mantissa low)
- Second operand stored in `__tmp32` (DSEG)
- 64-bit values use `__acc64` / `__tmp64` (DSEG, 8 bytes each)
- Working variables: `__fman1/2`, `__fexp1/2`, `__fsgn1/2` in DSEG
