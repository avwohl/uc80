/* complex.h - Complex number support (C99/C11) */
#ifndef _COMPLEX_H
#define _COMPLEX_H

/* C99 complex number support */
#define complex _Complex

/* The imaginary unit I - represented as a complex constant
 * For this implementation, _Complex is 8 bytes (two floats)
 * Stored as: bytes 0-3 = real part, bytes 4-7 = imaginary part
 */
extern double _Complex __I_const;  /* Defined in libc as (0.0, 1.0) */
#define _Complex_I __I_const
#define I _Complex_I

/* Type definitions */
typedef float _Complex float_complex;
typedef double _Complex double_complex;
typedef long double _Complex long_double_complex;

/* Complex component extraction */
double creal(double _Complex z);
double cimag(double _Complex z);
float crealf(float _Complex z);
float cimagf(float _Complex z);
long double creall(long double _Complex z);
long double cimagl(long double _Complex z);

/* Complex absolute value and argument */
double cabs(double _Complex z);
double carg(double _Complex z);
float cabsf(float _Complex z);
float cargf(float _Complex z);
long double cabsl(long double _Complex z);
long double cargl(long double _Complex z);

/* Complex conjugate and projection */
double _Complex conj(double _Complex z);
double _Complex cproj(double _Complex z);
float _Complex conjf(float _Complex z);
float _Complex cprojf(float _Complex z);
long double _Complex conjl(long double _Complex z);
long double _Complex cprojl(long double _Complex z);

/* Complex exponential and power functions */
double _Complex cexp(double _Complex z);
double _Complex clog(double _Complex z);
double _Complex cpow(double _Complex x, double _Complex y);
double _Complex csqrt(double _Complex z);
float _Complex cexpf(float _Complex z);
float _Complex clogf(float _Complex z);
float _Complex cpowf(float _Complex x, float _Complex y);
float _Complex csqrtf(float _Complex z);

/* Complex trigonometric functions */
double _Complex csin(double _Complex z);
double _Complex ccos(double _Complex z);
double _Complex ctan(double _Complex z);
double _Complex casin(double _Complex z);
double _Complex cacos(double _Complex z);
double _Complex catan(double _Complex z);
float _Complex csinf(float _Complex z);
float _Complex ccosf(float _Complex z);
float _Complex ctanf(float _Complex z);

/* Complex hyperbolic functions */
double _Complex csinh(double _Complex z);
double _Complex ccosh(double _Complex z);
double _Complex ctanh(double _Complex z);
double _Complex casinh(double _Complex z);
double _Complex cacosh(double _Complex z);
double _Complex catanh(double _Complex z);

/* Macros for complex number construction
 * CMPLX(x, y) creates complex with real part x, imaginary part y
 */
double _Complex __make_complex(double r, double i);
float _Complex __make_complexf(float r, float i);
#define CMPLX(x, y)  __make_complex((double)(x), (double)(y))
#define CMPLXF(x, y) __make_complexf((float)(x), (float)(y))
#define CMPLXL(x, y) __make_complex((long double)(x), (long double)(y))

#endif /* _COMPLEX_H */
