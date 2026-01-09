/* limits.h - Implementation limits for uc80 (Z80 target) */
#ifndef _LIMITS_H
#define _LIMITS_H

/* Number of bits in a char */
#define CHAR_BIT    8

/* Minimum and maximum values for signed char */
#define SCHAR_MIN   (-128)
#define SCHAR_MAX   127

/* Maximum value for unsigned char */
#define UCHAR_MAX   255

/* Minimum and maximum values for char (signed on Z80) */
#define CHAR_MIN    SCHAR_MIN
#define CHAR_MAX    SCHAR_MAX

/* Minimum and maximum values for short int */
#define SHRT_MIN    (-32768)
#define SHRT_MAX    32767

/* Maximum value for unsigned short int */
#define USHRT_MAX   65535

/* Minimum and maximum values for int (16-bit on Z80) */
#define INT_MIN     (-32768)
#define INT_MAX     32767

/* Maximum value for unsigned int */
#define UINT_MAX    65535U

/* Minimum and maximum values for long int (32-bit) */
#define LONG_MIN    (-2147483647L - 1)
#define LONG_MAX    2147483647L

/* Maximum value for unsigned long int */
#define ULONG_MAX   4294967295UL

/* Minimum and maximum values for long long (same as long on Z80) */
#define LLONG_MIN   LONG_MIN
#define LLONG_MAX   LONG_MAX
#define ULLONG_MAX  ULONG_MAX

/* Maximum number of bytes in a multibyte character */
#define MB_LEN_MAX  1

#endif /* _LIMITS_H */
