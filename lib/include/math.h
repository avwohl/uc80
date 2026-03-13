/* math.h - Mathematical functions (C89/C99/C11)
 *
 * This implementation provides IEEE 754 single-precision (32-bit)
 * floating-point math functions for Z80. All float, double, and
 * long double types are implemented as 32-bit floats.
 *
 * Transcendental functions use polynomial approximations based on
 * the Cephes Math Library algorithms.
 */
#ifndef _MATH_H
#define _MATH_H

/* Constants */
#define M_E        2.71828182845904523536
#define M_LOG2E    1.44269504088896340736
#define M_LOG10E   0.43429448190325182765
#define M_LN2      0.69314718055994530942
#define M_LN10     2.30258509299404568402
#define M_PI       3.14159265358979323846
#define M_PI_2     1.57079632679489661923
#define M_PI_4     0.78539816339744830962
#define M_1_PI     0.31830988618379067154
#define M_2_PI     0.63661977236758134308
#define M_2_SQRTPI 1.12837916709551257390
#define M_SQRT2    1.41421356237309504880
#define M_SQRT1_2  0.70710678118654752440

#define HUGE_VAL   ((double)0x7FFFFFFF)
#define INFINITY   HUGE_VAL
#define NAN        ((double)0)

/* Trigonometric functions */
double sin(double x);
double cos(double x);
double tan(double x);
double asin(double x);
double acos(double x);
double atan(double x);
double atan2(double y, double x);

/* Hyperbolic functions */
double sinh(double x);
double cosh(double x);
double tanh(double x);
double asinh(double x);
double acosh(double x);
double atanh(double x);

/* Exponential and logarithmic functions */
double exp(double x);
double exp2(double x);
double expm1(double x);
double log(double x);
double log10(double x);
double log2(double x);
double log1p(double x);
double logb(double x);
int ilogb(double x);
double scalbn(double x, int n);
double scalbln(double x, long n);

/* Power functions */
double pow(double base, double exponent);
double sqrt(double x);
double cbrt(double x);
double hypot(double x, double y);

/* Rounding and remainder functions */
double ceil(double x);
double floor(double x);
double trunc(double x);
double round(double x);
double fmod(double x, double y);
double remainder(double x, double y);

/* Floating-point manipulation */
double fabs(double x);
double copysign(double x, double y);
double frexp(double x, int *exp);
double ldexp(double x, int exp);
double modf(double x, double *iptr);
double nan(const char *tagp);
double nextafter(double x, double y);
double fdim(double x, double y);
double fma(double x, double y, double z);
double fmax(double x, double y);
double fmin(double x, double y);

/* Classification macros (C99) */
#define FP_NAN       0
#define FP_INFINITE  1
#define FP_ZERO      2
#define FP_SUBNORMAL 3
#define FP_NORMAL    4

int fpclassify(double x);
int isfinite(double x);
int isinf(double x);
int isnan(double x);
int isnormal(double x);
int signbit(double x);

/* Comparison macros (C99) */
#define isgreater(x, y)      ((x) > (y))
#define isgreaterequal(x, y) ((x) >= (y))
#define isless(x, y)         ((x) < (y))
#define islessequal(x, y)    ((x) <= (y))
#define islessgreater(x, y)  (((x) < (y)) || ((x) > (y)))
#define isunordered(x, y)    (isnan(x) || isnan(y))

/* Min/max - see function declarations above */

/* Float versions (C99) */
float sinf(float x);
float cosf(float x);
float tanf(float x);
float asinf(float x);
float acosf(float x);
float atanf(float x);
float atan2f(float y, float x);
float sinhf(float x);
float coshf(float x);
float tanhf(float x);
float asinhf(float x);
float acoshf(float x);
float atanhf(float x);
float sqrtf(float x);
float cbrtf(float x);
float hypotf(float x, float y);
float powf(float base, float exponent);
float fabsf(float x);
float copysignf(float x, float y);
float floorf(float x);
float ceilf(float x);
float truncf(float x);
float roundf(float x);
float nearbyintf(float x);
float rintf(float x);
long lrintf(float x);
long long llrintf(float x);
long lroundf(float x);
long long llroundf(float x);
float fmodf(float x, float y);
float remainderf(float x, float y);
float remquof(float x, float y, int *quo);
float expf(float x);
float exp2f(float x);
float expm1f(float x);
float logf(float x);
float log2f(float x);
float log10f(float x);
float log1pf(float x);
float logbf(float x);
int ilogbf(float x);
float scalbnf(float x, int n);
float scalblnf(float x, long n);
float frexpf(float x, int *exp);
float ldexpf(float x, int exp);
float modff(float x, float *iptr);
float nanf(const char *tagp);
float nextafterf(float x, float y);
float fmaxf(float x, float y);
float fminf(float x, float y);
float fdimf(float x, float y);
float fmaf(float x, float y, float z);

/* Long double versions (same as double on Z80) */
long double sinl(long double x);
long double cosl(long double x);
long double tanl(long double x);
long double sqrtl(long double x);
long double powl(long double base, long double exponent);
long double fabsl(long double x);
long double floorl(long double x);
long double ceill(long double x);
long double truncl(long double x);
long double roundl(long double x);
long double fmodl(long double x, long double y);
long double expl(long double x);
long double logl(long double x);
long double log10l(long double x);

#endif /* _MATH_H */
