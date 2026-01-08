/* test.h - Simple test framework for uc80 */
#ifndef _TEST_H
#define _TEST_H

#include <stdio.h>

/* Test counters */
static int _test_passed = 0;
static int _test_failed = 0;
static const char *_test_suite_name = "Tests";

/* Assert macro - prints file/line on failure */
#define Assert(cond, msg) do { \
    if (!(cond)) { \
        printf("FAIL: %s\n", msg); \
        _test_failed++; \
    } else { \
        _test_passed++; \
    } \
} while(0)

#define ASSERT(cond) Assert(cond, #cond)
#define assertEqual(a, b) Assert((a) == (b), #a " == " #b)
#define assertNotEqual(a, b) Assert((a) != (b), #a " != " #b)

/* Suite management (simplified - no setup/teardown) */
#define suite_setup(name) do { \
    _test_suite_name = name; \
    _test_passed = 0; \
    _test_failed = 0; \
    printf("Starting suite: %s\n", name); \
} while(0)

#define suite_add_test(func) do { \
    printf("  Running %s...", #func); \
    func(); \
    printf("done\n"); \
} while(0)

#define suite_run() (_test_failed)

#define suite_summary() do { \
    printf("Results: %d passed, %d failed\n", _test_passed, _test_failed); \
} while(0)

#endif /* _TEST_H */
