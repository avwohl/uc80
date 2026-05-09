/* stdarg.h - Variable argument list handling for uc80 */
#ifndef _STDARG_H
#define _STDARG_H

/*
 * Z80 calling convention: arguments pushed right-to-left
 * Stack layout after function prologue (IX = frame pointer):
 *   IX+4: first argument
 *   IX+6: second argument
 *   etc.
 *
 * va_list is a pointer to the current argument position
 */

typedef char *va_list;

/*
 * va_start: Initialize va_list to point after the last fixed parameter
 * 'last' is the last fixed parameter before the ...
 * We need to point to the stack location after 'last'
 */
#define va_start(ap, last) \
    ((ap) = (va_list)&(last) + ((sizeof(last) + 1) & ~1))

/*
 * va_arg: Get next argument of type 'type' and advance pointer
 * For Z80: all arguments are at least 16-bit aligned on stack
 * Round up to 2-byte alignment since PUSH works in word units
 */
#define va_arg(ap, type) \
    (*(type *)((ap) += ((sizeof(type) + 1) & ~1), \
               (ap) - ((sizeof(type) + 1) & ~1)))

/*
 * va_end: Clean up (no-op on Z80)
 */
#define va_end(ap) ((void)0)

/*
 * va_copy: Copy va_list (C99)
 */
#define va_copy(dest, src) ((dest) = (src))

#endif /* _STDARG_H */
