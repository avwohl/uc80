/* test.h - Test framework for uc80 (z88dk compatible) */
#ifndef _TEST_H
#define _TEST_H

#include <stdio.h>
#include <setjmp.h>

/* Test state - globals to avoid static arrays of function pointers */
jmp_buf _test_jmpbuf;
int _test_passed;
int _test_failed;
const char *_failed_msg;

/* Assert macros - jump on failure for test isolation */
#define Assert(cond, msg) do { \
    if (!(cond)) { \
        _failed_msg = msg; \
        longjmp(_test_jmpbuf, 1); \
    } \
} while(0)

#define ASSERT(cond) Assert(cond, #cond)
#define assertEqual(a, b) Assert((a) == (b), #a " == " #b)
#define assertEqualLong(a, b) Assert((a) == (b), #a " == " #b)
#define assertNotEqual(a, b) Assert((a) != (b), #a " != " #b)

/* Suite management - simple version */
#define suite_setup(name) do { \
    _test_passed = 0; \
    _test_failed = 0; \
    printf("Starting suite: %s\n", name); \
} while(0)

#define suite_add_test(func) do { \
    printf("Running %s..", #func); \
    _failed_msg = 0; \
    if (setjmp(_test_jmpbuf) == 0) { \
        func(); \
        printf("passed\n"); \
        _test_passed++; \
    } else { \
        printf("FAILED: %s\n", _failed_msg); \
        _test_failed++; \
    } \
} while(0)

#define suite_run() do { \
    printf("%d passed, %d failed\n", _test_passed, _test_failed); \
} while(0)

#define suite_result() (_test_failed)

#endif /* _TEST_H */
