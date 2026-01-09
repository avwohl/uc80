/* wctype.h - Wide character classification and mapping (C95/C99)
 *
 * This implementation provides wide character classification functions
 * for the C locale. Since this is a single-byte locale implementation,
 * wide characters in the 0-127 range behave like their char equivalents.
 */
#ifndef _WCTYPE_H
#define _WCTYPE_H

#include <wchar.h>  /* for wint_t, wchar_t, WEOF */

/* wctype_t - Scalar type for character class identifiers */
typedef unsigned int wctype_t;

/* wctrans_t - Scalar type for character mapping identifiers */
typedef unsigned int wctrans_t;

/* Character class constants (implementation-defined values) */
#define _WC_ALNUM   1
#define _WC_ALPHA   2
#define _WC_BLANK   3
#define _WC_CNTRL   4
#define _WC_DIGIT   5
#define _WC_GRAPH   6
#define _WC_LOWER   7
#define _WC_PRINT   8
#define _WC_PUNCT   9
#define _WC_SPACE   10
#define _WC_UPPER   11
#define _WC_XDIGIT  12

/* Character transformation constants */
#define _WC_TOLOWER 1
#define _WC_TOUPPER 2

/* Wide character classification functions */
int iswalnum(wint_t wc);
int iswalpha(wint_t wc);
int iswblank(wint_t wc);
int iswcntrl(wint_t wc);
int iswdigit(wint_t wc);
int iswgraph(wint_t wc);
int iswlower(wint_t wc);
int iswprint(wint_t wc);
int iswpunct(wint_t wc);
int iswspace(wint_t wc);
int iswupper(wint_t wc);
int iswxdigit(wint_t wc);

/* Extensible character classification */
int iswctype(wint_t wc, wctype_t desc);
wctype_t wctype(const char *property);

/* Wide character case mapping functions */
wint_t towlower(wint_t wc);
wint_t towupper(wint_t wc);

/* Extensible character mapping */
wint_t towctrans(wint_t wc, wctrans_t desc);
wctrans_t wctrans(const char *property);

#endif /* _WCTYPE_H */
