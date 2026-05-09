/* wchar.h - Wide character support (C95/C99)
 *
 * This implementation provides wide character support for the C locale.
 * Wide characters are 16-bit values (same as unsigned int on Z80).
 */
#ifndef _WCHAR_H
#define _WCHAR_H

#include <stddef.h>  /* for wchar_t, size_t, NULL */
#include <stdarg.h>  /* for va_list */
#include <stdio.h>   /* for FILE */

/* Wide character type (if not already defined).  Pinned to 16 bits so that
 * L"..." literals (always emitted as 2 bytes per char by the codegen) match
 * the array element size under both --int=16 and --int=32. */
#ifndef _WCHAR_T_DEFINED
#define _WCHAR_T_DEFINED
typedef unsigned short wchar_t;
#endif

/* wint_t - type that can hold any wchar_t value plus WEOF */
typedef int wint_t;

/* mbstate_t - multibyte conversion state */
typedef struct {
    unsigned char state;  /* Conversion state (unused in C locale) */
    unsigned char count;  /* Bytes remaining in sequence */
} mbstate_t;

/* struct tm forward declaration */
struct tm;

/* Wide character EOF */
#define WEOF ((wint_t)-1)

/* Limits */
#ifndef WCHAR_MIN
#define WCHAR_MIN 0
#endif
#ifndef WCHAR_MAX
#define WCHAR_MAX 65535U
#endif

/* Formatted wide string I/O functions */
int fwprintf(FILE *stream, const wchar_t *format, ...);
int fwscanf(FILE *stream, const wchar_t *format, ...);
int swprintf(wchar_t *s, size_t n, const wchar_t *format, ...);
int swscanf(const wchar_t *s, const wchar_t *format, ...);
int vfwprintf(FILE *stream, const wchar_t *format, va_list arg);
int vfwscanf(FILE *stream, const wchar_t *format, va_list arg);
int vswprintf(wchar_t *s, size_t n, const wchar_t *format, va_list arg);
int vswscanf(const wchar_t *s, const wchar_t *format, va_list arg);
int vwprintf(const wchar_t *format, va_list arg);
int vwscanf(const wchar_t *format, va_list arg);
int wprintf(const wchar_t *format, ...);
int wscanf(const wchar_t *format, ...);

/* Wide character I/O functions */
wint_t fgetwc(FILE *stream);
wchar_t *fgetws(wchar_t *s, int n, FILE *stream);
wint_t fputwc(wchar_t c, FILE *stream);
int fputws(const wchar_t *s, FILE *stream);
int fwide(FILE *stream, int mode);
wint_t getwc(FILE *stream);
wint_t getwchar(void);
wint_t putwc(wchar_t c, FILE *stream);
wint_t putwchar(wchar_t c);
wint_t ungetwc(wint_t c, FILE *stream);

/* Wide string numeric conversion functions */
double wcstod(const wchar_t *nptr, wchar_t **endptr);
float wcstof(const wchar_t *nptr, wchar_t **endptr);
long double wcstold(const wchar_t *nptr, wchar_t **endptr);
long wcstol(const wchar_t *nptr, wchar_t **endptr, int base);
long long wcstoll(const wchar_t *nptr, wchar_t **endptr, int base);
unsigned long wcstoul(const wchar_t *nptr, wchar_t **endptr, int base);
unsigned long long wcstoull(const wchar_t *nptr, wchar_t **endptr, int base);

/* Wide string copying functions */
wchar_t *wcscpy(wchar_t *dest, const wchar_t *src);
wchar_t *wcsncpy(wchar_t *dest, const wchar_t *src, size_t n);
wchar_t *wmemcpy(wchar_t *dest, const wchar_t *src, size_t n);
wchar_t *wmemmove(wchar_t *dest, const wchar_t *src, size_t n);

/* Wide string concatenation functions */
wchar_t *wcscat(wchar_t *dest, const wchar_t *src);
wchar_t *wcsncat(wchar_t *dest, const wchar_t *src, size_t n);

/* Wide string comparison functions */
int wcscmp(const wchar_t *s1, const wchar_t *s2);
int wcscoll(const wchar_t *s1, const wchar_t *s2);
int wcsncmp(const wchar_t *s1, const wchar_t *s2, size_t n);
size_t wcsxfrm(wchar_t *dest, const wchar_t *src, size_t n);
int wmemcmp(const wchar_t *s1, const wchar_t *s2, size_t n);

/* Wide string search functions */
wchar_t *wcschr(const wchar_t *s, wchar_t c);
size_t wcscspn(const wchar_t *s, const wchar_t *reject);
wchar_t *wcspbrk(const wchar_t *s, const wchar_t *accept);
wchar_t *wcsrchr(const wchar_t *s, wchar_t c);
size_t wcsspn(const wchar_t *s, const wchar_t *accept);
wchar_t *wcsstr(const wchar_t *haystack, const wchar_t *needle);
wchar_t *wcstok(wchar_t *str, const wchar_t *delim, wchar_t **saveptr);
wchar_t *wmemchr(const wchar_t *s, wchar_t c, size_t n);

/* Wide string miscellaneous functions */
size_t wcslen(const wchar_t *s);
wchar_t *wmemset(wchar_t *s, wchar_t c, size_t n);

/* Wide string time conversion */
size_t wcsftime(wchar_t *s, size_t maxsize, const wchar_t *format, const struct tm *tm);

/* Restartable multibyte/wide conversion functions */
wint_t btowc(int c);
int wctob(wint_t c);
int mbsinit(const mbstate_t *ps);
size_t mbrlen(const char *s, size_t n, mbstate_t *ps);
size_t mbrtowc(wchar_t *pwc, const char *s, size_t n, mbstate_t *ps);
size_t wcrtomb(char *s, wchar_t wc, mbstate_t *ps);
size_t mbsrtowcs(wchar_t *dest, const char **src, size_t len, mbstate_t *ps);
size_t wcsrtombs(char *dest, const wchar_t **src, size_t len, mbstate_t *ps);

#endif /* _WCHAR_H */
