/* stdnoreturn.h - _Noreturn specifier macro (C11)
 *
 * This header provides the noreturn macro for functions that
 * do not return to their caller (e.g., exit(), abort(), longjmp()).
 *
 * Note: This is deprecated in C23 in favor of the [[noreturn]] attribute.
 */
#ifndef _STDNORETURN_H
#define _STDNORETURN_H

/* noreturn - Specifies that a function does not return
 * Usage: noreturn void exit(int status);
 *
 * The _Noreturn function specifier tells the compiler that the
 * function will not return normally. This allows optimizations
 * and better warning diagnostics.
 */
#ifndef __noreturn_is_defined
#define noreturn _Noreturn
#define __noreturn_is_defined 1
#endif

/* _Noreturn is a keyword in C11 */
/* If the compiler doesn't support it natively, provide an empty fallback */
#ifndef _Noreturn
#define _Noreturn
#endif

#endif /* _STDNORETURN_H */
