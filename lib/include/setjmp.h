/* setjmp.h - Non-local jumps for uc80 */
#ifndef _SETJMP_H
#define _SETJMP_H

/* jmp_buf stores: IX (2), SP (2), return address (2) = 6 bytes */
typedef unsigned char jmp_buf[6];

/* setjmp - Save calling environment for non-local goto
 * Returns 0 when called directly, non-zero when returning via longjmp
 */
extern int setjmp(jmp_buf env);

/* longjmp - Non-local goto to saved environment
 * val is returned by setjmp (if val is 0, setjmp returns 1)
 */
extern void longjmp(jmp_buf env, int val);

#endif /* _SETJMP_H */
