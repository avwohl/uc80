/* stdlib.h - Standard library for uc80 */
#ifndef _STDLIB_H
#define _STDLIB_H

/* Size type — pointer-width so libc stack offsets stay stable under --int=32 */
#ifndef _SIZE_T_DEFINED
#define _SIZE_T_DEFINED
#if __SIZEOF_POINTER__ == __SIZEOF_INT__
typedef unsigned int size_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_LONG__
typedef unsigned long size_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_SHORT__
typedef unsigned short size_t;
#endif
#endif

/* NULL pointer */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* Program termination */
void exit(int status);
void abort(void);
int atexit(void (*func)(void));
/* C11 */
_Noreturn void quick_exit(int status);
int at_quick_exit(void (*func)(void));
#define quick_exit(s) exit(s)
#define at_quick_exit(f) atexit(f)
#define EXIT_SUCCESS 0
#define EXIT_FAILURE 1

/* Integer limits (Z80 16-bit) */
#define RAND_MAX 32767

/* Random number generation */
int rand(void);
void srand(unsigned int seed);

/* Absolute value */
int abs(int n);
long labs(long n);
#define abs(x) ((x) < 0 ? -(x) : (x))

/* Division types */
typedef struct {
    int quot;
    int rem;
} div_t;

typedef struct {
    long quot;
    long rem;
} ldiv_t;

/* Division functions (return pointer to static storage) */
div_t *div(int numer, int denom);
ldiv_t *ldiv(long numer, long denom);

/* String conversion - integers */
int atoi(const char *nptr);
long atol(const char *nptr);
long strtol(const char *nptr, char **endptr, int base);
unsigned long strtoul(const char *nptr, char **endptr, int base);

/* String conversion - floating point */
double atof(const char *nptr);
double strtod(const char *nptr, char **endptr);
float strtof(const char *nptr, char **endptr);
long double strtold(const char *nptr, char **endptr);

/* Memory allocation */
void *malloc(size_t size);
void *calloc(size_t nmemb, size_t size);
void *realloc(void *ptr, size_t size);
void free(void *ptr);

/* Environment */
char *getenv(const char *name);
int system(const char *command);

/* Searching and sorting */
void qsort(void *base, size_t nmemb, size_t size,
           int (*compar)(const void *, const void *));
void *bsearch(const void *key, const void *base, size_t nmemb,
              size_t size, int (*compar)(const void *, const void *));

/* Wide character type (for multibyte functions) */
#ifndef _WCHAR_T_DEFINED
#define _WCHAR_T_DEFINED
typedef unsigned int wchar_t;  /* 16-bit for Z80 */
#endif

/* Multibyte/wide character conversion limits */
#define MB_CUR_MAX 1  /* C locale - single byte characters only */

/* Multibyte character functions (minimal - C locale only) */
int mblen(const char *s, size_t n);
int mbtowc(wchar_t *pwc, const char *s, size_t n);
int wctomb(char *s, wchar_t wc);
size_t mbstowcs(wchar_t *dest, const char *src, size_t n);
size_t wcstombs(char *dest, const wchar_t *src, size_t n);

#endif /* _STDLIB_H */
