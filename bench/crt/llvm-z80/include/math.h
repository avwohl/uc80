/* math.h for llvm-z80 (bench/cc_llvmz80.py): the SDCC 4.6.0 math.h,
 * which declares only the float functions (sinf, sqrtf, ...) that the
 * SDCC z80.lib has.  The adapter compiles with -mdouble=32, so double is
 * float and the float function IS the double function; map the double
 * names to it.  This is the same mapping that the program edits make
 * for standalone SDCC under "#if defined(__SDCC)", which clang does not
 * define.
 */
#include_next <math.h>
#if __SIZEOF_DOUBLE__ == 4
#define sin(x)      sinf(x)
#define cos(x)      cosf(x)
#define tan(x)      tanf(x)
#define asin(x)     asinf(x)
#define acos(x)     acosf(x)
#define atan(x)     atanf(x)
#define atan2(y, x) atan2f(y, x)
#define sinh(x)     sinhf(x)
#define cosh(x)     coshf(x)
#define tanh(x)     tanhf(x)
#define exp(x)      expf(x)
#define log(x)      logf(x)
#define log10(x)    log10f(x)
#define pow(x, y)   powf(x, y)
#define sqrt(x)     sqrtf(x)
#define fabs(x)     fabsf(x)
#define frexp(x, e) frexpf(x, e)
#define ldexp(x, e) ldexpf(x, e)
#define ceil(x)     ceilf(x)
#define floor(x)    floorf(x)
#define modf(x, i)  modff(x, i)
#endif
