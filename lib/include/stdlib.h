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
#define EXIT_SUCCESS 0
#define EXIT_FAILURE 1

/* Integer limits (Z80 16-bit) */
#define RAND_MAX 32767

/* Absolute value */
#define abs(x) ((x) < 0 ? -(x) : (x))

#endif /* _STDLIB_H */
