/* stdlib.h - Standard library for uc80 */
#ifndef _STDLIB_H
#define _STDLIB_H

/* Size type */
#ifndef _SIZE_T_DEFINED
#define _SIZE_T_DEFINED
typedef unsigned int size_t;
#endif

/* NULL pointer */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* Program termination */
void exit(int status);
void abort(void);
int atexit(void (*func)(void));
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

/* String conversion */
int atoi(const char *nptr);
long atol(const char *nptr);
long strtol(const char *nptr, char **endptr, int base);
unsigned long strtoul(const char *nptr, char **endptr, int base);

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

#endif /* _STDLIB_H */
