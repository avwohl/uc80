/* stddef.h - Standard definitions for uc80 */
#ifndef _STDDEF_H
#define _STDDEF_H

/* Size type (unsigned, can hold sizeof result) */
#ifndef _SIZE_T_DEFINED
#define _SIZE_T_DEFINED
typedef unsigned int size_t;
#endif

/* Pointer difference type (signed) */
typedef int ptrdiff_t;

/* NULL pointer constant */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* Maximum alignment type (on Z80, alignment is always 1) */
typedef long double max_align_t;

/* Offset of member in struct */
#define offsetof(type, member) ((size_t)&((type *)0)->member)

#endif /* _STDDEF_H */
