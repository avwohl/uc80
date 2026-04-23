/* inttypes.h - Format conversion of integer types (C99)
 *
 * The PRI and SCN macros pick a length modifier (none, h, hh, l, ll)
 * based on the compiler's __SIZEOF_*__ macros, so --int=32, --long=8,
 * or --ptr=32 reshape the format strings automatically.
 */
#ifndef _INTTYPES_H
#define _INTTYPES_H

#include <stdint.h>

/* imaxdiv_t - result type for imaxdiv() */
typedef struct {
    intmax_t quot;
    intmax_t rem;
} imaxdiv_t;

/* Pick the printf length modifier for each exact width.
 * 8-bit  → "hh" (char-width)
 * 16-bit → "" if int is 16-bit, else "h" (short)
 * 32-bit → "" if int is 32-bit, else "l" (long is 32-bit)
 * 64-bit → "l" if long is 64-bit, else "ll"
 * ptr    → depends on __SIZEOF_POINTER__
 */

#define __UC80_PRI8   "hh"

#if __SIZEOF_INT__ == 2
#define __UC80_PRI16  ""
#else
#define __UC80_PRI16  "h"
#endif

#if __SIZEOF_INT__ == 4
#define __UC80_PRI32  ""
#else
#define __UC80_PRI32  "l"
#endif

#if __SIZEOF_LONG__ == 8
#define __UC80_PRI64  "l"
#else
#define __UC80_PRI64  "ll"
#endif

#define __UC80_PRIMAX __UC80_PRI64

#if __SIZEOF_POINTER__ == __SIZEOF_INT__
#define __UC80_PRIPTR ""
#elif __SIZEOF_POINTER__ == __SIZEOF_LONG__
#define __UC80_PRIPTR "l"
#elif __SIZEOF_POINTER__ == __SIZEOF_SHORT__
#define __UC80_PRIPTR "h"
#endif

/* printf signed */
#define PRId8   __UC80_PRI8  "d"
#define PRId16  __UC80_PRI16 "d"
#define PRId32  __UC80_PRI32 "d"
#define PRId64  __UC80_PRI64 "d"
#define PRIdLEAST8  PRId8
#define PRIdLEAST16 PRId16
#define PRIdLEAST32 PRId32
#define PRIdLEAST64 PRId64
#define PRIdFAST8   PRId16
#define PRIdFAST16  PRId16
#define PRIdFAST32  PRId32
#define PRIdFAST64  PRId64
#define PRIdMAX     __UC80_PRIMAX "d"
#define PRIdPTR     __UC80_PRIPTR "d"

#define PRIi8   __UC80_PRI8  "i"
#define PRIi16  __UC80_PRI16 "i"
#define PRIi32  __UC80_PRI32 "i"
#define PRIi64  __UC80_PRI64 "i"
#define PRIiLEAST8  PRIi8
#define PRIiLEAST16 PRIi16
#define PRIiLEAST32 PRIi32
#define PRIiLEAST64 PRIi64
#define PRIiFAST8   PRIi16
#define PRIiFAST16  PRIi16
#define PRIiFAST32  PRIi32
#define PRIiFAST64  PRIi64
#define PRIiMAX     __UC80_PRIMAX "i"
#define PRIiPTR     __UC80_PRIPTR "i"

/* printf unsigned */
#define PRIo8   __UC80_PRI8  "o"
#define PRIo16  __UC80_PRI16 "o"
#define PRIo32  __UC80_PRI32 "o"
#define PRIo64  __UC80_PRI64 "o"
#define PRIoLEAST8  PRIo8
#define PRIoLEAST16 PRIo16
#define PRIoLEAST32 PRIo32
#define PRIoLEAST64 PRIo64
#define PRIoFAST8   PRIo16
#define PRIoFAST16  PRIo16
#define PRIoFAST32  PRIo32
#define PRIoFAST64  PRIo64
#define PRIoMAX     __UC80_PRIMAX "o"
#define PRIoPTR     __UC80_PRIPTR "o"

#define PRIu8   __UC80_PRI8  "u"
#define PRIu16  __UC80_PRI16 "u"
#define PRIu32  __UC80_PRI32 "u"
#define PRIu64  __UC80_PRI64 "u"
#define PRIuLEAST8  PRIu8
#define PRIuLEAST16 PRIu16
#define PRIuLEAST32 PRIu32
#define PRIuLEAST64 PRIu64
#define PRIuFAST8   PRIu16
#define PRIuFAST16  PRIu16
#define PRIuFAST32  PRIu32
#define PRIuFAST64  PRIu64
#define PRIuMAX     __UC80_PRIMAX "u"
#define PRIuPTR     __UC80_PRIPTR "u"

#define PRIx8   __UC80_PRI8  "x"
#define PRIx16  __UC80_PRI16 "x"
#define PRIx32  __UC80_PRI32 "x"
#define PRIx64  __UC80_PRI64 "x"
#define PRIxLEAST8  PRIx8
#define PRIxLEAST16 PRIx16
#define PRIxLEAST32 PRIx32
#define PRIxLEAST64 PRIx64
#define PRIxFAST8   PRIx16
#define PRIxFAST16  PRIx16
#define PRIxFAST32  PRIx32
#define PRIxFAST64  PRIx64
#define PRIxMAX     __UC80_PRIMAX "x"
#define PRIxPTR     __UC80_PRIPTR "x"

#define PRIX8   __UC80_PRI8  "X"
#define PRIX16  __UC80_PRI16 "X"
#define PRIX32  __UC80_PRI32 "X"
#define PRIX64  __UC80_PRI64 "X"
#define PRIXLEAST8  PRIX8
#define PRIXLEAST16 PRIX16
#define PRIXLEAST32 PRIX32
#define PRIXLEAST64 PRIX64
#define PRIXFAST8   PRIX16
#define PRIXFAST16  PRIX16
#define PRIXFAST32  PRIX32
#define PRIXFAST64  PRIX64
#define PRIXMAX     __UC80_PRIMAX "X"
#define PRIXPTR     __UC80_PRIPTR "X"

/* scanf signed */
#define SCNd8   __UC80_PRI8  "d"
#define SCNd16  __UC80_PRI16 "d"
#define SCNd32  __UC80_PRI32 "d"
#define SCNd64  __UC80_PRI64 "d"
#define SCNdLEAST8  SCNd8
#define SCNdLEAST16 SCNd16
#define SCNdLEAST32 SCNd32
#define SCNdLEAST64 SCNd64
#define SCNdFAST8   SCNd16
#define SCNdFAST16  SCNd16
#define SCNdFAST32  SCNd32
#define SCNdFAST64  SCNd64
#define SCNdMAX     __UC80_PRIMAX "d"
#define SCNdPTR     __UC80_PRIPTR "d"

#define SCNi8   __UC80_PRI8  "i"
#define SCNi16  __UC80_PRI16 "i"
#define SCNi32  __UC80_PRI32 "i"
#define SCNi64  __UC80_PRI64 "i"
#define SCNiLEAST8  SCNi8
#define SCNiLEAST16 SCNi16
#define SCNiLEAST32 SCNi32
#define SCNiLEAST64 SCNi64
#define SCNiFAST8   SCNi16
#define SCNiFAST16  SCNi16
#define SCNiFAST32  SCNi32
#define SCNiFAST64  SCNi64
#define SCNiMAX     __UC80_PRIMAX "i"
#define SCNiPTR     __UC80_PRIPTR "i"

/* scanf unsigned */
#define SCNo8   __UC80_PRI8  "o"
#define SCNo16  __UC80_PRI16 "o"
#define SCNo32  __UC80_PRI32 "o"
#define SCNo64  __UC80_PRI64 "o"
#define SCNoLEAST8  SCNo8
#define SCNoLEAST16 SCNo16
#define SCNoLEAST32 SCNo32
#define SCNoLEAST64 SCNo64
#define SCNoFAST8   SCNo16
#define SCNoFAST16  SCNo16
#define SCNoFAST32  SCNo32
#define SCNoFAST64  SCNo64
#define SCNoMAX     __UC80_PRIMAX "o"
#define SCNoPTR     __UC80_PRIPTR "o"

#define SCNu8   __UC80_PRI8  "u"
#define SCNu16  __UC80_PRI16 "u"
#define SCNu32  __UC80_PRI32 "u"
#define SCNu64  __UC80_PRI64 "u"
#define SCNuLEAST8  SCNu8
#define SCNuLEAST16 SCNu16
#define SCNuLEAST32 SCNu32
#define SCNuLEAST64 SCNu64
#define SCNuFAST8   SCNu16
#define SCNuFAST16  SCNu16
#define SCNuFAST32  SCNu32
#define SCNuFAST64  SCNu64
#define SCNuMAX     __UC80_PRIMAX "u"
#define SCNuPTR     __UC80_PRIPTR "u"

#define SCNx8   __UC80_PRI8  "x"
#define SCNx16  __UC80_PRI16 "x"
#define SCNx32  __UC80_PRI32 "x"
#define SCNx64  __UC80_PRI64 "x"
#define SCNxLEAST8  SCNx8
#define SCNxLEAST16 SCNx16
#define SCNxLEAST32 SCNx32
#define SCNxLEAST64 SCNx64
#define SCNxFAST8   SCNx16
#define SCNxFAST16  SCNx16
#define SCNxFAST32  SCNx32
#define SCNxFAST64  SCNx64
#define SCNxMAX     __UC80_PRIMAX "x"
#define SCNxPTR     __UC80_PRIPTR "x"

/* Functions for greatest-width integer types */
intmax_t imaxabs(intmax_t n);
imaxdiv_t imaxdiv(intmax_t numer, intmax_t denom);
intmax_t strtoimax(const char *nptr, char **endptr, int base);
uintmax_t strtoumax(const char *nptr, char **endptr, int base);

#endif /* _INTTYPES_H */
