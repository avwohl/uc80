/* stddef.h - Standard definitions
 *
 * size_t and ptrdiff_t follow the target's pointer width (__SIZEOF_POINTER__),
 * not the C int width, so --int=32 with --ptr=16 still gives a 16-bit size_t.
 */
#ifndef _STDDEF_H
#define _STDDEF_H

/* size_t / ptrdiff_t track the pointer width */
#ifndef _SIZE_T_DEFINED
#define _SIZE_T_DEFINED
#if __SIZEOF_POINTER__ == __SIZEOF_INT__
typedef unsigned int size_t;
typedef int ptrdiff_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_LONG__
typedef unsigned long size_t;
typedef long ptrdiff_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_SHORT__
typedef unsigned short size_t;
typedef short ptrdiff_t;
#endif
#endif

/* NULL pointer constant */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* Maximum alignment type (on Z80, alignment is always 1) */
typedef long double max_align_t;

/* Offset of member in struct */
#define offsetof(type, member) ((size_t)&((type *)0)->member)

#endif /* _STDDEF_H */
