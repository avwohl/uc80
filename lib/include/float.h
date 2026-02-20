/* float.h - Floating-point limits */
#ifndef _FLOAT_H
#define _FLOAT_H

/* Note: Z80 has no FPU. These values are for a hypothetical
 * 32-bit IEEE 754 single precision software implementation.
 */

/* Radix of exponent representation */
#define FLT_RADIX 2

/* Number of decimal digits of precision */
#define FLT_DIG 6
#define DBL_DIG 6
#define LDBL_DIG 6

/* Difference between 1 and the least value > 1 */
#define FLT_EPSILON 1.19209290e-07F
#define DBL_EPSILON 1.19209290e-07
#define LDBL_EPSILON 1.19209290e-07L

/* Number of base-FLT_RADIX digits in mantissa */
#define FLT_MANT_DIG 24
#define DBL_MANT_DIG 24
#define LDBL_MANT_DIG 24

/* Maximum representable finite value */
#define FLT_MAX 3.40282347e+38F
#define DBL_MAX 3.40282347e+38
#define LDBL_MAX 3.40282347e+38L

/* Maximum int such that FLT_RADIX^(e-1) is representable */
#define FLT_MAX_EXP 128
#define DBL_MAX_EXP 128
#define LDBL_MAX_EXP 128

/* Maximum int such that 10^e is representable */
#define FLT_MAX_10_EXP 38
#define DBL_MAX_10_EXP 38
#define LDBL_MAX_10_EXP 38

/* Minimum normalized positive value */
#define FLT_MIN 1.17549435e-38F
#define DBL_MIN 1.17549435e-38
#define LDBL_MIN 1.17549435e-38L

/* Minimum int such that FLT_RADIX^(e-1) is normalized */
#define FLT_MIN_EXP (-125)
#define DBL_MIN_EXP (-125)
#define LDBL_MIN_EXP (-125)

/* Minimum int such that 10^e is normalized */
#define FLT_MIN_10_EXP (-37)
#define DBL_MIN_10_EXP (-37)
#define LDBL_MIN_10_EXP (-37)

/* Rounding mode */
#define FLT_ROUNDS 1  /* Round to nearest */

/* Evaluation format */
#define FLT_EVAL_METHOD 0

/* Decimal digits needed for round-trip */
#define DECIMAL_DIG 9

/* C11: Per-type decimal digits for round-trip */
#define FLT_DECIMAL_DIG 9
#define DBL_DECIMAL_DIG 9
#define LDBL_DECIMAL_DIG 9

/* C11: Subnormal support (1 = yes) */
#define FLT_HAS_SUBNORM 1
#define DBL_HAS_SUBNORM 1
#define LDBL_HAS_SUBNORM 1

/* C11: Minimum positive subnormal value */
#define FLT_TRUE_MIN 1.40129846e-45F
#define DBL_TRUE_MIN 1.40129846e-45
#define LDBL_TRUE_MIN 1.40129846e-45L

#endif /* _FLOAT_H */
