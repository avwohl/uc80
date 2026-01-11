/* fenv.h - Floating-point environment (C99)
 *
 * Note: Z80 has no hardware FPU, so this implementation provides
 * software-managed floating-point exception flags and rounding modes.
 * All operations use round-to-nearest by default.
 */
#ifndef _FENV_H
#define _FENV_H

/* Floating-point exception flags */
#define FE_INVALID    0x01  /* Invalid operation */
#define FE_DIVBYZERO  0x04  /* Division by zero */
#define FE_OVERFLOW   0x08  /* Result too large */
#define FE_UNDERFLOW  0x10  /* Result too small */
#define FE_INEXACT    0x20  /* Inexact result */

/* All exceptions */
#define FE_ALL_EXCEPT (FE_INVALID | FE_DIVBYZERO | FE_OVERFLOW | \
                       FE_UNDERFLOW | FE_INEXACT)

/* Rounding direction modes */
#define FE_TONEAREST  0     /* Round to nearest (default) */
#define FE_DOWNWARD   1     /* Round toward negative infinity */
#define FE_UPWARD     2     /* Round toward positive infinity */
#define FE_TOWARDZERO 3     /* Round toward zero */

/* Type representing floating-point status flags collectively */
typedef unsigned char fexcept_t;

/* Type representing entire floating-point environment */
typedef struct {
    unsigned char exceptions;  /* Exception flags */
    unsigned char rounding;    /* Rounding mode */
} fenv_t;

/* Default floating-point environment */
extern const fenv_t __fe_dfl_env;
#define FE_DFL_ENV (&__fe_dfl_env)

/* Exception flag functions */
int feclearexcept(int excepts);
int fegetexceptflag(fexcept_t *flagp, int excepts);
int feraiseexcept(int excepts);
int fesetexceptflag(const fexcept_t *flagp, int excepts);
int fetestexcept(int excepts);

/* Rounding mode functions */
int fegetround(void);
int fesetround(int round);

/* Environment functions */
int fegetenv(fenv_t *envp);
int feholdexcept(fenv_t *envp);
int fesetenv(const fenv_t *envp);
int feupdateenv(const fenv_t *envp);

/* Pragma stubs - these are normally compiler directives */
/* FENV_ACCESS ON/OFF - controls FP optimization */
/* FENV_ROUND - controls rounding mode */

#endif /* _FENV_H */
