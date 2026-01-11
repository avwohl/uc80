/* inttypes.h - Format conversion of integer types (C99) */
#ifndef _INTTYPES_H
#define _INTTYPES_H

#include <stdint.h>

/* imaxdiv_t - result type for imaxdiv() */
typedef struct {
    intmax_t quot;
    intmax_t rem;
} imaxdiv_t;

/* Format macros for fprintf() - signed integers */
#define PRId8       "d"
#define PRId16      "d"
#define PRId32      "ld"
#define PRIdLEAST8  "d"
#define PRIdLEAST16 "d"
#define PRIdLEAST32 "ld"
#define PRIdFAST8   "d"
#define PRIdFAST16  "d"
#define PRIdFAST32  "ld"
#define PRIdMAX     "ld"
#define PRIdPTR     "d"

#define PRIi8       "i"
#define PRIi16      "i"
#define PRIi32      "li"
#define PRIiLEAST8  "i"
#define PRIiLEAST16 "i"
#define PRIiLEAST32 "li"
#define PRIiFAST8   "i"
#define PRIiFAST16  "i"
#define PRIiFAST32  "li"
#define PRIiMAX     "li"
#define PRIiPTR     "i"

/* Format macros for fprintf() - unsigned integers */
#define PRIo8       "o"
#define PRIo16      "o"
#define PRIo32      "lo"
#define PRIoLEAST8  "o"
#define PRIoLEAST16 "o"
#define PRIoLEAST32 "lo"
#define PRIoFAST8   "o"
#define PRIoFAST16  "o"
#define PRIoFAST32  "lo"
#define PRIoMAX     "lo"
#define PRIoPTR     "o"

#define PRIu8       "u"
#define PRIu16      "u"
#define PRIu32      "lu"
#define PRIuLEAST8  "u"
#define PRIuLEAST16 "u"
#define PRIuLEAST32 "lu"
#define PRIuFAST8   "u"
#define PRIuFAST16  "u"
#define PRIuFAST32  "lu"
#define PRIuMAX     "lu"
#define PRIuPTR     "u"

#define PRIx8       "x"
#define PRIx16      "x"
#define PRIx32      "lx"
#define PRIxLEAST8  "x"
#define PRIxLEAST16 "x"
#define PRIxLEAST32 "lx"
#define PRIxFAST8   "x"
#define PRIxFAST16  "x"
#define PRIxFAST32  "lx"
#define PRIxMAX     "lx"
#define PRIxPTR     "x"

#define PRIX8       "X"
#define PRIX16      "X"
#define PRIX32      "lX"
#define PRIXLEAST8  "X"
#define PRIXLEAST16 "X"
#define PRIXLEAST32 "lX"
#define PRIXFAST8   "X"
#define PRIXFAST16  "X"
#define PRIXFAST32  "lX"
#define PRIXMAX     "lX"
#define PRIXPTR     "X"

/* Format macros for fscanf() - signed integers */
#define SCNd8       "hhd"
#define SCNd16      "hd"
#define SCNd32      "ld"
#define SCNdLEAST8  "hhd"
#define SCNdLEAST16 "hd"
#define SCNdLEAST32 "ld"
#define SCNdFAST8   "hhd"
#define SCNdFAST16  "hd"
#define SCNdFAST32  "ld"
#define SCNdMAX     "ld"
#define SCNdPTR     "d"

#define SCNi8       "hhi"
#define SCNi16      "hi"
#define SCNi32      "li"
#define SCNiLEAST8  "hhi"
#define SCNiLEAST16 "hi"
#define SCNiLEAST32 "li"
#define SCNiFAST8   "hhi"
#define SCNiFAST16  "hi"
#define SCNiFAST32  "li"
#define SCNiMAX     "li"
#define SCNiPTR     "i"

/* Format macros for fscanf() - unsigned integers */
#define SCNo8       "hho"
#define SCNo16      "ho"
#define SCNo32      "lo"
#define SCNoLEAST8  "hho"
#define SCNoLEAST16 "ho"
#define SCNoLEAST32 "lo"
#define SCNoFAST8   "hho"
#define SCNoFAST16  "ho"
#define SCNoFAST32  "lo"
#define SCNoMAX     "lo"
#define SCNoPTR     "o"

#define SCNu8       "hhu"
#define SCNu16      "hu"
#define SCNu32      "lu"
#define SCNuLEAST8  "hhu"
#define SCNuLEAST16 "hu"
#define SCNuLEAST32 "lu"
#define SCNuFAST8   "hhu"
#define SCNuFAST16  "hu"
#define SCNuFAST32  "lu"
#define SCNuMAX     "lu"
#define SCNuPTR     "u"

#define SCNx8       "hhx"
#define SCNx16      "hx"
#define SCNx32      "lx"
#define SCNxLEAST8  "hhx"
#define SCNxLEAST16 "hx"
#define SCNxLEAST32 "lx"
#define SCNxFAST8   "hhx"
#define SCNxFAST16  "hx"
#define SCNxFAST32  "lx"
#define SCNxMAX     "lx"
#define SCNxPTR     "x"

/* Functions for greatest-width integer types */
intmax_t imaxabs(intmax_t n);
imaxdiv_t imaxdiv(intmax_t numer, intmax_t denom);
intmax_t strtoimax(const char *nptr, char **endptr, int base);
uintmax_t strtoumax(const char *nptr, char **endptr, int base);

/* Wide string conversion (if wchar.h available) */
/* intmax_t wcstoimax(const wchar_t *nptr, wchar_t **endptr, int base); */
/* uintmax_t wcstoumax(const wchar_t *nptr, wchar_t **endptr, int base); */

#endif /* _INTTYPES_H */
