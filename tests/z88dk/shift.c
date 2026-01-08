/* Adapted from z88dk test/suites/sccz80/lshift.c and rshift.c */
#include "test.h"

/* Test 16-bit left shift with variable count */
void test_lshift16_var()
{
    int val = 1;
    int v = 0;

    assertEqual(val << v, 1 << 0);
    ++v;
    assertEqual(val << v, 1 << 1);
    ++v;
    assertEqual(val << v, 1 << 2);
    ++v;
    assertEqual(val << v, 1 << 3);
    ++v;
    assertEqual(val << v, 1 << 4);
    ++v;
    assertEqual(val << v, 1 << 5);
    ++v;
    assertEqual(val << v, 1 << 6);
    ++v;
    assertEqual(val << v, 1 << 7);
    ++v;
    assertEqual(val << v, 1 << 8);
    ++v;
    assertEqual(val << v, 1 << 9);
    ++v;
    assertEqual(val << v, 1 << 10);
    ++v;
    assertEqual(val << v, 1 << 11);
    ++v;
    assertEqual(val << v, 1 << 12);
    ++v;
    assertEqual(val << v, 1 << 13);
    ++v;
    assertEqual(val << v, 1 << 14);
    ++v;
    assertEqual(val << v, 1 << 15);
}

/* Test 16-bit left shift with constant count */
void test_lshift16_const()
{
    int val = 1;

    assertEqual(val << 0, 1 << 0);
    assertEqual(val << 1, 1 << 1);
    assertEqual(val << 2, 1 << 2);
    assertEqual(val << 3, 1 << 3);
    assertEqual(val << 4, 1 << 4);
    assertEqual(val << 5, 1 << 5);
    assertEqual(val << 6, 1 << 6);
    assertEqual(val << 7, 1 << 7);
    assertEqual(val << 8, 1 << 8);
    assertEqual(val << 9, 1 << 9);
    assertEqual(val << 10, 1 << 10);
    assertEqual(val << 11, 1 << 11);
    assertEqual(val << 12, 1 << 12);
    assertEqual(val << 13, 1 << 13);
    assertEqual(val << 14, 1 << 14);
    assertEqual(val << 15, (int)(1 << 15));
}

/* Test 32-bit left shift with variable count */
void test_lshift32_var()
{
    long val = 1;
    int v = 0;

    assertEqualLong(val << v, 1L << 0);
    ++v;
    assertEqualLong(val << v, 1L << 1);
    ++v;
    assertEqualLong(val << v, 1L << 2);
    ++v;
    assertEqualLong(val << v, 1L << 3);
    ++v;
    assertEqualLong(val << v, 1L << 4);
    v = 7;
    assertEqualLong(val << v, 1L << 7);
    v = 8;
    assertEqualLong(val << v, 1L << 8);
    v = 15;
    assertEqualLong(val << v, 1L << 15);
    v = 16;
    assertEqualLong(val << v, 1L << 16);
    v = 23;
    assertEqualLong(val << v, 1L << 23);
    v = 24;
    assertEqualLong(val << v, 1L << 24);
    v = 31;
    assertEqualLong(val << v, 1L << 31);
}

/* Test 32-bit left shift with constant count */
void test_lshift32_const()
{
    long val = 1;

    assertEqualLong(val << 0, 1L << 0);
    assertEqualLong(val << 1, 1L << 1);
    assertEqualLong(val << 2, 1L << 2);
    assertEqualLong(val << 7, 1L << 7);
    assertEqualLong(val << 8, 1L << 8);
    assertEqualLong(val << 15, 1L << 15);
    assertEqualLong(val << 16, 1L << 16);
    assertEqualLong(val << 23, 1L << 23);
    assertEqualLong(val << 24, 1L << 24);
    assertEqualLong(val << 31, 1L << 31);
}

/* Test 16-bit right shift with variable count (signed) */
void test_rshift16_var()
{
    int val = 0x4000;  /* Use positive value to avoid sign extension issues */
    int v = 0;

    assertEqual(val >> v, 0x4000 >> 0);
    ++v;
    assertEqual(val >> v, 0x4000 >> 1);
    ++v;
    assertEqual(val >> v, 0x4000 >> 2);
    ++v;
    assertEqual(val >> v, 0x4000 >> 3);
    ++v;
    assertEqual(val >> v, 0x4000 >> 4);
    ++v;
    assertEqual(val >> v, 0x4000 >> 5);
    ++v;
    assertEqual(val >> v, 0x4000 >> 6);
    ++v;
    assertEqual(val >> v, 0x4000 >> 7);
    ++v;
    assertEqual(val >> v, 0x4000 >> 8);
    ++v;
    assertEqual(val >> v, 0x4000 >> 9);
    ++v;
    assertEqual(val >> v, 0x4000 >> 10);
    ++v;
    assertEqual(val >> v, 0x4000 >> 11);
    ++v;
    assertEqual(val >> v, 0x4000 >> 12);
    ++v;
    assertEqual(val >> v, 0x4000 >> 13);
    ++v;
    assertEqual(val >> v, 0x4000 >> 14);
}

/* Test 16-bit right shift with constant count */
void test_rshift16_const()
{
    int val = 0x4000;

    assertEqual(val >> 0, 0x4000 >> 0);
    assertEqual(val >> 1, 0x4000 >> 1);
    assertEqual(val >> 2, 0x4000 >> 2);
    assertEqual(val >> 3, 0x4000 >> 3);
    assertEqual(val >> 4, 0x4000 >> 4);
    assertEqual(val >> 5, 0x4000 >> 5);
    assertEqual(val >> 6, 0x4000 >> 6);
    assertEqual(val >> 7, 0x4000 >> 7);
    assertEqual(val >> 8, 0x4000 >> 8);
    assertEqual(val >> 9, 0x4000 >> 9);
    assertEqual(val >> 10, 0x4000 >> 10);
    assertEqual(val >> 11, 0x4000 >> 11);
    assertEqual(val >> 12, 0x4000 >> 12);
    assertEqual(val >> 13, 0x4000 >> 13);
    assertEqual(val >> 14, 0x4000 >> 14);
}

/* Test 32-bit right shift with variable count */
void test_rshift32_var()
{
    long val = 0x40000000L;
    int v = 0;

    assertEqualLong(val >> v, 0x40000000L >> 0);
    ++v;
    assertEqualLong(val >> v, 0x40000000L >> 1);
    ++v;
    assertEqualLong(val >> v, 0x40000000L >> 2);
    ++v;
    assertEqualLong(val >> v, 0x40000000L >> 3);
    ++v;
    assertEqualLong(val >> v, 0x40000000L >> 4);
    v = 7;
    assertEqualLong(val >> v, 0x40000000L >> 7);
    v = 8;
    assertEqualLong(val >> v, 0x40000000L >> 8);
    v = 15;
    assertEqualLong(val >> v, 0x40000000L >> 15);
    v = 16;
    assertEqualLong(val >> v, 0x40000000L >> 16);
    v = 23;
    assertEqualLong(val >> v, 0x40000000L >> 23);
    v = 24;
    assertEqualLong(val >> v, 0x40000000L >> 24);
    v = 30;
    assertEqualLong(val >> v, 0x40000000L >> 30);
}

/* Test 32-bit right shift with constant count */
void test_rshift32_const()
{
    long val = 0x40000000L;

    assertEqualLong(val >> 0, 0x40000000L >> 0);
    assertEqualLong(val >> 1, 0x40000000L >> 1);
    assertEqualLong(val >> 2, 0x40000000L >> 2);
    assertEqualLong(val >> 7, 0x40000000L >> 7);
    assertEqualLong(val >> 8, 0x40000000L >> 8);
    assertEqualLong(val >> 15, 0x40000000L >> 15);
    assertEqualLong(val >> 16, 0x40000000L >> 16);
    assertEqualLong(val >> 23, 0x40000000L >> 23);
    assertEqualLong(val >> 24, 0x40000000L >> 24);
    assertEqualLong(val >> 30, 0x40000000L >> 30);
}

int main()
{
    suite_setup("Shift Tests");
    suite_add_test(test_lshift16_var);
    suite_add_test(test_lshift16_const);
    suite_add_test(test_lshift32_var);
    suite_add_test(test_lshift32_const);
    suite_add_test(test_rshift16_var);
    suite_add_test(test_rshift16_const);
    suite_add_test(test_rshift32_var);
    suite_add_test(test_rshift32_const);
    suite_run();
    return suite_result();
}
