/* complex.h - Complex number support */
#ifndef _COMPLEX_H
#define _COMPLEX_H

/* Note: This is a minimal complex.h for Z80 which has no FPU.
 * Complex number support is stubbed out - code will compile but
 * complex operations will not produce correct results.
 */

/* C99 complex number support */
#define complex _Complex
#define _Complex_I (0.0f)
#define I _Complex_I

/* Type definitions */
typedef float _Complex float_complex;
typedef double _Complex double_complex;
typedef long double _Complex long_double_complex;

/* Complex arithmetic functions - all return 0 (stub) */
double creal(double _Complex z);
double cimag(double _Complex z);
double cabs(double _Complex z);
double carg(double _Complex z);
double _Complex conj(double _Complex z);
double _Complex cproj(double _Complex z);

/* Complex exponential and power functions */
double _Complex cexp(double _Complex z);
double _Complex clog(double _Complex z);
double _Complex cpow(double _Complex x, double _Complex y);
double _Complex csqrt(double _Complex z);

/* Complex trigonometric functions */
double _Complex csin(double _Complex z);
double _Complex ccos(double _Complex z);
double _Complex ctan(double _Complex z);
double _Complex casin(double _Complex z);
double _Complex cacos(double _Complex z);
double _Complex catan(double _Complex z);

/* Complex hyperbolic functions */
double _Complex csinh(double _Complex z);
double _Complex ccosh(double _Complex z);
double _Complex ctanh(double _Complex z);
double _Complex casinh(double _Complex z);
double _Complex cacosh(double _Complex z);
double _Complex catanh(double _Complex z);

/* Float versions */
float crealf(float _Complex z);
float cimagf(float _Complex z);
float cabsf(float _Complex z);
float cargf(float _Complex z);
float _Complex conjf(float _Complex z);
float _Complex cprojf(float _Complex z);
float _Complex cexpf(float _Complex z);
float _Complex clogf(float _Complex z);
float _Complex cpowf(float _Complex x, float _Complex y);
float _Complex csqrtf(float _Complex z);
float _Complex csinf(float _Complex z);
float _Complex ccosf(float _Complex z);
float _Complex ctanf(float _Complex z);

/* Long double versions */
long double creall(long double _Complex z);
long double cimagl(long double _Complex z);
long double cabsl(long double _Complex z);
long double cargl(long double _Complex z);
long double _Complex conjl(long double _Complex z);
long double _Complex cprojl(long double _Complex z);

/* Macros for complex number construction */
#define CMPLX(x, y) ((double _Complex)((double)(x) + (double)(y) * I))
#define CMPLXF(x, y) ((float _Complex)((float)(x) + (float)(y) * I))
#define CMPLXL(x, y) ((long double _Complex)((long double)(x) + (long double)(y) * I))

#endif /* _COMPLEX_H */
