/* math.h - Mathematical functions */
#ifndef _MATH_H
#define _MATH_H

/* Note: This is a minimal math.h for Z80 which has no FPU.
 * Floating point functions are stubbed out - they will compile
 * but return 0 or the input value unchanged.
 * Integer math functions are fully implemented.
 */

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

/* Trigonometric functions - stub implementations */
double sin(double x);
double cos(double x);
double tan(double x);
double asin(double x);
double acos(double x);
double atan(double x);
double atan2(double y, double x);

/* Hyperbolic functions - stub implementations */
double sinh(double x);
double cosh(double x);
double tanh(double x);

/* Exponential and logarithmic functions - stub implementations */
double exp(double x);
double log(double x);
double log10(double x);
double log2(double x);

/* Power functions - stub implementations */
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

/* Comparison macros */
#define isnan(x) (0)
#define isinf(x) (0)
#define isfinite(x) (1)
#define isnormal(x) (1)
#define signbit(x) ((x) < 0)

/* Min/max - implemented as macros */
#define fmax(x, y) ((x) > (y) ? (x) : (y))
#define fmin(x, y) ((x) < (y) ? (x) : (y))

/* Float versions */
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
float fmodf(float x, float y);
float remainderf(float x, float y);
float expf(float x);
float logf(float x);
float log2f(float x);
float log10f(float x);
float frexpf(float x, int *exp);
float ldexpf(float x, int exp);
float modff(float x, float *iptr);

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
