# Floating Point Implementation Status

## WORKING - All Core Operations

IEEE 754 single-precision (32-bit) floating point is fully functional.
`double` and `long double` are the same as `float` (32-bit) on this Z80 target.

### Arithmetic (lib/rt/rt_float.mac)
- `__fadd` - Addition (DEHL + __tmp32), rounds to nearest even, handles inf and NaN
- `__fsub` - Subtraction (DEHL - __tmp32)
- `__fmul` - Multiplication (24x24 partial products)
- `__fdiv` - Division (long division, 26-bit quotient, rounds to nearest even), handles inf, NaN and the sign of a zero result
- `__fcmp` - Comparison. It gives the Z flag for equal and the C flag for less than. When an operand is a NaN, it sets Z and C together (unordered). The code generator tests that pair, so `==`, `<`, `<=`, `>` and `>=` are false and `!=` is true for a NaN, as Annex F says. The check costs about 59 T-states in each call.
- `__fneg` - Negation (sign bit flip)
- `__fcmp` - Comparison (Z flag = equal, C flag = less than)

### Conversions (lib/rt/rt_float.mac)
- `__itof` / `__ltof` - Signed 32-bit int to float (rounds to nearest even)
- `__uitof` - Unsigned 32-bit int to float
- `__ftoi` - Float to signed int (truncate toward zero)
- `__lltof` / `__ulltof` - Signed and unsigned 64-bit int (in `__acc64`) to float (rounds to nearest even; lib/rt/rt_lltof.mac)
- `__ftoll` / `__ftoull` - Float to signed and unsigned 64-bit int in `__acc64`, truncated, with the nearest limit for a value that does not fit (lib/rt/rt_ftoll.mac). `__ftoull` also converts to a 32-bit unsigned long.

### Internal Helpers
- `__funpack` - Extract sign, exponent, mantissa from IEEE 754
- `__fpack` - Combine sign, exponent, mantissa into IEEE 754
- `__fnorm` - Normalize mantissa and adjust exponent

### Printf float conversions (lib/lc/lc_prt_float.mac)
- `%f %F %e %E %g %G` with flags, width and precision
- The digits are exact and rounded to nearest, a tie to even: the output is the
  output of the host `printf` for the same float value ([printf](printf.md))
- No float arithmetic, so no soft float runtime is linked for `printf`
- Enabled via `#pragma printf float` or `#pragma printf all`

### Math Library (lib/lc/lc_*.mac, from lib/vmsrc/*.vc and lib/csrc/*.c)
Full single-precision math library; each function is within 2 ulp of the
exact result ([docs/libm.md](libm.md)):
- Trigonometric: sin, cos, tan, asin, acos, atan, atan2
- Hyperbolic: sinh, cosh, tanh
- Exponential: exp, log, log10, log2, pow
- Roots: sqrt (correctly rounded), cbrt, hypot
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
