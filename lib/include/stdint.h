/* stdint.h - Integer types
 *
 * Typedefs and limits derive from the compiler-supplied __SIZEOF_*__ and
 * __*_MAX__ macros, so uc80 switches like --int=32 or --ptr=32 reshape
 * these types without editing this header.
 */
#ifndef _STDINT_H
#define _STDINT_H

/* Exact-width integer types */
typedef signed char int8_t;
typedef unsigned char uint8_t;

#if __SIZEOF_SHORT__ == 2
typedef short int16_t;
typedef unsigned short uint16_t;
#elif __SIZEOF_INT__ == 2
typedef int int16_t;
typedef unsigned int uint16_t;
#endif

#if __SIZEOF_INT__ == 4
typedef int int32_t;
typedef unsigned int uint32_t;
#elif __SIZEOF_LONG__ == 4
typedef long int32_t;
typedef unsigned long uint32_t;
#endif

#if __SIZEOF_LONG__ == 8
typedef long int64_t;
typedef unsigned long uint64_t;
#else
typedef long long int64_t;
typedef unsigned long long uint64_t;
#endif

/* Minimum-width integer types */
typedef int8_t  int_least8_t;
typedef uint8_t uint_least8_t;
typedef int16_t int_least16_t;
typedef uint16_t uint_least16_t;
typedef int32_t int_least32_t;
typedef uint32_t uint_least32_t;
typedef int64_t int_least64_t;
typedef uint64_t uint_least64_t;

/* Fastest minimum-width integer types
 * On Z80, 16-bit HL is the fastest pair; with --int=32, int is 32-bit and
 * still the most direct fast type.  Keep the "fast" aliases on the native
 * int width.
 */
typedef int int_fast8_t;
typedef unsigned int uint_fast8_t;
typedef int int_fast16_t;
typedef unsigned int uint_fast16_t;
typedef int32_t int_fast32_t;
typedef uint32_t uint_fast32_t;
typedef int64_t int_fast64_t;
typedef uint64_t uint_fast64_t;

/* Integer types capable of holding object pointers */
#if __SIZEOF_POINTER__ == __SIZEOF_INT__
typedef int intptr_t;
typedef unsigned int uintptr_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_LONG__
typedef long intptr_t;
typedef unsigned long uintptr_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_SHORT__
typedef short intptr_t;
typedef unsigned short uintptr_t;
#endif

/* GCC/glibc internal types */
typedef intptr_t __intptr_t;

/* Greatest-width integer types */
typedef int64_t intmax_t;
typedef uint64_t uintmax_t;

/* Limits of exact-width integer types */
#define INT8_MIN   (-__SCHAR_MAX__ - 1)
#define INT8_MAX   __SCHAR_MAX__
#define UINT8_MAX  (__SCHAR_MAX__ * 2 + 1)

#if __SIZEOF_SHORT__ == 2
#define INT16_MIN  (-__SHRT_MAX__ - 1)
#define INT16_MAX  __SHRT_MAX__
/* Use unsigned arithmetic so 32767 * 2 doesn't overflow signed int.
 * The U suffix on the literals also keeps the multiplication in
 * unsigned int (16-bit on Z80), so the result is exactly 65535. */
#define UINT16_MAX (__SHRT_MAX__ * 2U + 1U)
#else
#define INT16_MIN  (-__INT_MAX__ - 1)
#define INT16_MAX  __INT_MAX__
#define UINT16_MAX (__INT_MAX__ * 2U + 1U)
#endif

#if __SIZEOF_INT__ == 4
#define INT32_MIN  (-__INT_MAX__ - 1)
#define INT32_MAX  __INT_MAX__
#define UINT32_MAX (__INT_MAX__ * 2U + 1U)
#else
#define INT32_MIN  (-__LONG_MAX__ - 1L)
#define INT32_MAX  __LONG_MAX__
#define UINT32_MAX (__LONG_MAX__ * 2UL + 1UL)
#endif

#define INT64_MIN  (-__LONG_LONG_MAX__ - 1LL)
#define INT64_MAX  __LONG_LONG_MAX__
#define UINT64_MAX (__LONG_LONG_MAX__ * 2ULL + 1ULL)

/* Limits of minimum-width integer types */
#define INT_LEAST8_MIN   INT8_MIN
#define INT_LEAST8_MAX   INT8_MAX
#define UINT_LEAST8_MAX  UINT8_MAX
#define INT_LEAST16_MIN  INT16_MIN
#define INT_LEAST16_MAX  INT16_MAX
#define UINT_LEAST16_MAX UINT16_MAX
#define INT_LEAST32_MIN  INT32_MIN
#define INT_LEAST32_MAX  INT32_MAX
#define UINT_LEAST32_MAX UINT32_MAX
#define INT_LEAST64_MIN  INT64_MIN
#define INT_LEAST64_MAX  INT64_MAX
#define UINT_LEAST64_MAX UINT64_MAX

/* Limits of fastest minimum-width integer types */
#define INT_FAST8_MIN    (-__INT_MAX__ - 1)
#define INT_FAST8_MAX    __INT_MAX__
#define UINT_FAST8_MAX   (__INT_MAX__ * 2U + 1U)
#define INT_FAST16_MIN   (-__INT_MAX__ - 1)
#define INT_FAST16_MAX   __INT_MAX__
#define UINT_FAST16_MAX  (__INT_MAX__ * 2U + 1U)
#define INT_FAST32_MIN   INT32_MIN
#define INT_FAST32_MAX   INT32_MAX
#define UINT_FAST32_MAX  UINT32_MAX
#define INT_FAST64_MIN   INT64_MIN
#define INT_FAST64_MAX   INT64_MAX
#define UINT_FAST64_MAX  UINT64_MAX

/* Limits of integer types capable of holding object pointers */
#if __SIZEOF_POINTER__ == 2
#define INTPTR_MIN   INT16_MIN
#define INTPTR_MAX   INT16_MAX
#define UINTPTR_MAX  UINT16_MAX
#elif __SIZEOF_POINTER__ == 4
#define INTPTR_MIN   INT32_MIN
#define INTPTR_MAX   INT32_MAX
#define UINTPTR_MAX  UINT32_MAX
#endif

/* Greatest-width integer types */
#define INTMAX_MIN   INT64_MIN
#define INTMAX_MAX   INT64_MAX
#define UINTMAX_MAX  UINT64_MAX

/* Size limits — size_t/ptrdiff_t are pointer-width */
#if __SIZEOF_POINTER__ == 2
#define SIZE_MAX     UINT16_MAX
#define PTRDIFF_MIN  INT16_MIN
#define PTRDIFF_MAX  INT16_MAX
#elif __SIZEOF_POINTER__ == 4
#define SIZE_MAX     UINT32_MAX
#define PTRDIFF_MIN  INT32_MIN
#define PTRDIFF_MAX  INT32_MAX
#endif

#endif /* _STDINT_H */
