/* assert.h - Assertion macros for uc80 */
#ifndef _ASSERT_H
#define _ASSERT_H

#ifdef NDEBUG

/* Assertions disabled */
#define assert(expr) ((void)0)

#else

/* Assertions enabled */
void _assert_fail(const char *file, int line);

#define assert(expr) \
    ((expr) ? (void)0 : _assert_fail(__FILE__, __LINE__))

#endif /* NDEBUG */

/* C11 static_assert */
#define static_assert _Static_assert

#endif /* _ASSERT_H */
