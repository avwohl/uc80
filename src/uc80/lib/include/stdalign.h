/* stdalign.h - Alignment specifier macros (C11)
 *
 * This header provides macros for alignment specifications.
 * On Z80, all types are naturally aligned to byte boundaries,
 * so alignment is less critical than on modern processors.
 */
#ifndef _STDALIGN_H
#define _STDALIGN_H

/* alignas - Specifies alignment requirement for a type or object
 * Usage: alignas(type) or alignas(expression)
 *
 * Note: This compiler treats alignas as a hint since Z80
 * does not have strict alignment requirements.
 */
#ifndef __alignas_is_defined
#define alignas _Alignas
#define __alignas_is_defined 1
#endif

/* alignof - Queries the alignment requirement of a type
 * Usage: alignof(type)
 *
 * On Z80, all types are byte-aligned (alignment = 1).
 */
#ifndef __alignof_is_defined
#define alignof _Alignof
#define __alignof_is_defined 1
#endif

/* _Alignas and _Alignof are keywords in C11 */
/* If the compiler doesn't support them natively, provide fallbacks */
#ifndef _Alignas
#define _Alignas(x)
#endif

#ifndef _Alignof
#define _Alignof(type) (1)  /* Z80 has no alignment requirements */
#endif

#endif /* _STDALIGN_H */
