/* tgmath.h - Type-generic math (C99)
 *
 * This header provides type-generic macros that select the appropriate
 * math function based on the argument type. Since this compiler has
 * limited _Generic support, we provide simple implementations.
 *
 * For this implementation, all floating-point types are treated as
 * single-precision (32-bit float), so the macros simply call the
 * base functions.
 */
#ifndef _TGMATH_H
#define _TGMATH_H

#include <math.h>
#include <complex.h>

/* Trigonometric functions */
#define sin(x)   sinf(x)
#define cos(x)   cosf(x)
#define tan(x)   tanf(x)
#define asin(x)  asinf(x)
#define acos(x)  acosf(x)
#define atan(x)  atanf(x)
#define atan2(y, x) atan2f(y, x)

/* Hyperbolic functions */
#define sinh(x)  sinhf(x)
#define cosh(x)  coshf(x)
#define tanh(x)  tanhf(x)
#define asinh(x) asinhf(x)
#define acosh(x) acoshf(x)
#define atanh(x) atanhf(x)

/* Exponential and logarithmic functions */
#define exp(x)   expf(x)
#define exp2(x)  exp2f(x)
#define expm1(x) expm1f(x)
#define log(x)   logf(x)
#define log10(x) log10f(x)
#define log2(x)  log2f(x)
#define log1p(x) log1pf(x)
#define logb(x)  logbf(x)

/* Power and absolute value functions */
#define pow(x, y)  powf(x, y)
#define sqrt(x)    sqrtf(x)
#define cbrt(x)    cbrtf(x)
#define hypot(x, y) hypotf(x, y)
#define fabs(x)    fabsf(x)

/* Error and gamma functions */
#define erf(x)     erff(x)
#define erfc(x)    erfcf(x)
#define lgamma(x)  lgammaf(x)
#define tgamma(x)  tgammaf(x)

/* Nearest integer functions */
#define ceil(x)    ceilf(x)
#define floor(x)   floorf(x)
#define trunc(x)   truncf(x)
#define round(x)   roundf(x)
#define nearbyint(x) nearbyintf(x)
#define rint(x)    rintf(x)
#define lrint(x)   lrintf(x)
#define llrint(x)  llrintf(x)
#define lround(x)  lroundf(x)
#define llround(x) llroundf(x)

/* Remainder functions */
#define fmod(x, y)      fmodf(x, y)
#define remainder(x, y) remainderf(x, y)
#define remquo(x, y, q) remquof(x, y, q)

/* Manipulation functions */
#define copysign(x, y) copysignf(x, y)
#define nan(s)         nanf(s)
#define nextafter(x, y) nextafterf(x, y)
#define fdim(x, y)     fdimf(x, y)
#define fmax(x, y)     fmaxf(x, y)
#define fmin(x, y)     fminf(x, y)
#define fma(x, y, z)   fmaf(x, y, z)

/* Comparison macros - already type-generic */
/* isgreater, isgreaterequal, isless, islessequal, islessgreater, isunordered */
/* These are defined in math.h */

/* Complex type-generic macros */
#define carg(z)  cargf(z)
#define cimag(z) cimagf(z)
#define conj(z)  conjf(z)
#define cproj(z) cprojf(z)
#define creal(z) crealf(z)

/* Note: For complex versions of real functions (csin, ccos, etc.),
 * _Generic selection would be needed. For simplicity, the complex
 * versions must be called explicitly (csinf, ccosf, etc.).
 */

#endif /* _TGMATH_H */
