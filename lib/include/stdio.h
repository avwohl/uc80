/* stdio.h - Standard I/O for uc80/CP-M */
#ifndef _STDIO_H
#define _STDIO_H

/* Size type */
#ifndef _SIZE_T_DEFINED
#define _SIZE_T_DEFINED
typedef unsigned int size_t;
#endif

/* NULL pointer */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* EOF indicator */
#define EOF (-1)

/* Standard I/O functions */
int putchar(int c);
int getchar(void);
int puts(const char *s);
int printf(const char *format, ...);

/* String I/O (not yet implemented) */
int sprintf(char *str, const char *format, ...);
int snprintf(char *str, size_t size, const char *format, ...);

#endif /* _STDIO_H */
