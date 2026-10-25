# The Math Library

Back to the [README](../README.md).

`double`, `float` and `long double` are the same 32-bit IEEE single type in
uc80. Each `math.h` function has one body. The names that end in `f` or `l`
(`sinf`, `sinl`) are other entry points of the same code. The sources are in
`src/uc80/lib/csrc/`. [Library modules written in C](libc_c_sources.md)
describes them. `sqrt` is an assembly file with an integer algorithm.

## Accuracy

The test `tests/test_libm.py` compares each function with the exact value
from Python. The test uses special values, the edges of the argument
reduction and random points. A run with ten times more random points found
these largest errors. The unit is the last place (ulp) of the 24-bit
mantissa:

- 0 ulp (exact or rounded correctly): `sqrt`, `cbrt`, `hypot`,
  `fmod`, `remainder`, `remquo`, `floor`, `ceil`, `trunc`, `round`, `rint`,
  `nearbyint`, `lround`, `lrint`, `llround`, `llrint`, `frexp`, `ldexp`,
  `scalbn`, `modf`, `nextafter`, `fmin`, `fmax`, `fdim`, `copysign`, `fabs`
- 1 ulp: `exp`, `exp2`, `log`, `log10`, `acos`, `atan`, `atan2`, `sinh`,
  `cosh`, `pow`
- 2 ulp: `sin`, `cos`, `log1p`, `log2`, `asin`, `tanh`, `asinh`, `acosh`,
  `atanh`
- 3 ulp: `tan`, `expm1`

`pow` is exact when the result is a float. For example `pow(3, 2)` is 9.
`fma` rounds once, except in a rare case of double rounding. `sin`, `cos`
and `tan` reduce the argument exactly for any size (Payne-Hanek above
6000), so `sin(1e30)` is correct.

The functions follow Annex F of the C standard for infinity, NaN and the sign
of zero.

## Float arithmetic

The accuracy of the library depends on the runtime routines `__fadd`,
`__fsub`, `__fmul`, `__fdiv`, `__itof`, `__ltof` and `__uitof`. These
routines round to nearest, with ties to even. `__fadd` and `__fdiv` give the
IEEE result for an infinity or a NaN operand.

## Limits

- The runtime has no subnormal numbers. Arithmetic takes a subnormal operand
  as zero. It changes a subnormal result to a zero. The functions that only
  move bits work with subnormal numbers: `frexp`, `ldexp`, `scalbn`,
  `ilogb`, `logb`, `modf`, `nextafter`, `fmod`, `remainder`, `fpclassify`.
  `sqrt`, `cbrt`, `hypot` and `atan2` scale a subnormal argument. `exp` and
  `pow` round a subnormal result.
- The math functions do not set `errno` and do not raise a floating-point
  exception.
- A conversion between `long long` and `float` uses only the low 32 bits of
  the integer. The code generator causes this limit. `llround` and `llrint`
  build the 64-bit value from the bits, so they do not have this limit.
- `lround` and `lrint` return `LONG_MAX` or `LONG_MIN` for an argument
  outside the range of a `long`.
- The functions use static storage. They are not reentrant.
- The complex functions of `complex.h` (`lc_complex`) were not changed. They
  give wrong results, as they did before.

## Size

The functions are larger than the assembly functions that they replace. A
program that calls `sin` is about 2.5 KB larger. A program that calls `pow` is
about 4.5 KB larger. `sqrt` is smaller than before. Each function is a module,
so a program links only the functions that it calls.
