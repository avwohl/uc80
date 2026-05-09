/* string.h - String functions for uc80 */
#ifndef _STRING_H
#define _STRING_H

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

/* String examination */
size_t strlen(const char *s);
int strcmp(const char *s1, const char *s2);
int strncmp(const char *s1, const char *s2, size_t n);
char *strchr(const char *s, int c);
char *strrchr(const char *s, int c);
char *strstr(const char *haystack, const char *needle);
size_t strcspn(const char *s, const char *reject);
size_t strspn(const char *s, const char *accept);
char *strpbrk(const char *s, const char *accept);

/* String manipulation */
char *strcpy(char *dest, const char *src);
char *strncpy(char *dest, const char *src, size_t n);
char *strcat(char *dest, const char *src);
char *strncat(char *dest, const char *src, size_t n);
char *strdup(const char *s);
char *strtok(char *str, const char *delim);

/* Memory functions */
void *memcpy(void *dest, const void *src, size_t n);
void *memmove(void *dest, const void *src, size_t n);
/* C99 declares c as int; we use unsigned char so that under --int=32 the
 * stack offset of n stays where the asm libc expects it (libc only reads the
 * low byte of c anyway). */
void *memset(void *s, unsigned char c, size_t n);
int memcmp(const void *s1, const void *s2, size_t n);
void *memchr(const void *s, unsigned char c, size_t n);

/* Error string */
char *strerror(int errnum);

#endif /* _STRING_H */
