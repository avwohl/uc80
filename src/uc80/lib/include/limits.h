/* limits.h - Implementation limits
 *
 * Values track the compiler's TypeConfig via the __*_MAX__ /
 * __SIZEOF_*__ macros supplied by the preprocessor, so
 * passing --int=32 on the uc80 command line automatically
 * widens INT_MAX etc. here.
 */
#ifndef _LIMITS_H
#define _LIMITS_H

#define CHAR_BIT    __CHAR_BIT__

#define SCHAR_MIN   (-__CHAR_MAX__ - 1)
#define SCHAR_MAX   __CHAR_MAX__
#define UCHAR_MAX   (__CHAR_MAX__ * 2 + 1)

/* char is signed on this target */
#define CHAR_MIN    SCHAR_MIN
#define CHAR_MAX    SCHAR_MAX

#define SHRT_MIN    (-__SHRT_MAX__ - 1)
#define SHRT_MAX    __SHRT_MAX__
#define USHRT_MAX   (__SHRT_MAX__ * 2 + 1)

#define INT_MIN     (-__INT_MAX__ - 1)
#define INT_MAX     __INT_MAX__
#define UINT_MAX    (__INT_MAX__ * 2U + 1U)

#define LONG_MIN    (-__LONG_MAX__ - 1L)
#define LONG_MAX    __LONG_MAX__
#define ULONG_MAX   (__LONG_MAX__ * 2UL + 1UL)

#define LLONG_MIN   (-__LONG_LONG_MAX__ - 1LL)
#define LLONG_MAX   __LONG_LONG_MAX__
#define ULLONG_MAX  (__LONG_LONG_MAX__ * 2ULL + 1ULL)

#define MB_LEN_MAX  1

#endif /* _LIMITS_H */
