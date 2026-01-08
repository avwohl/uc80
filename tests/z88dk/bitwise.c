/* Adapted from z88dk test/suites/sccz80/bitwise.c */
#include "test.h"

/* Test basic bitwise operations with int */
void test_bitwise_int()
{
    int left, right;

    left = 0x3df7;
    right = 0xc1ec;

    /* AND tests */
    assertEqual((int)(left & right), (int)0x01E4);
    assertEqual((int)(right & left), (int)0x01E4);
    assertEqual((int)(left & 0xc1ec), (int)0x01E4);
    assertEqual((int)(0x3df7 & right), (int)0x01E4);

    /* OR tests */
    assertEqual((int)(left | right), (int)0xFDFF);
    assertEqual((int)(right | left), (int)0xFDFF);
    assertEqual((int)(left | 0xc1ec), (int)0xFDFF);
    assertEqual((int)(0x3df7 | right), (int)0xFDFF);

    /* XOR tests */
    assertEqual((int)(left ^ right), (int)0xFC1B);
    assertEqual((int)(right ^ left), (int)0xFC1B);
    assertEqual((int)(left ^ 0xc1ec), (int)0xFC1B);
    assertEqual((int)(0x3df7 ^ right), (int)0xFC1B);

    /* NOT test - result depends on int size (16-bit for us) */
    assertEqual((int)(~left), (int)0xC208);
}

/* Test bitwise AND in conditionals */
void test_and_conditional()
{
    int a;
    int res;

    a = 0x1234;

    if (a & 0x4321)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    if (!(a & 0x4321))
        res = 1;
    else
        res = 0;
    assertEqual(res, 0);

    /* bitmask tests */
    a = 0xffff;
    if (a & 0x1004)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    a = 0x0000;
    if (a & 0x1004)
        res = 1;
    else
        res = 0;
    assertEqual(res, 0);

    if (!(a & 0x1004))
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    a = 0x00ff;
    if (a & 0x1004)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    a = 0xff00;
    if (a & 0x1004)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    /* literal with zero bytes */
    a = 0x1234;
    if (a & 0x4300)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    if (a & 0x0012)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);
}

/* Test bitwise OR in conditionals */
void test_or_conditional()
{
    int a;
    int res;

    a = 0x1234;
    res = 1;

    if (a | 0x4321)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    if (!(a | 0x4321))
        res = 1;
    else
        res = 0;
    assertEqual(res, 0);

    /* or with zero: result is left */
    if (a | 0)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    if (!(a | 0))
        res = 1;
    else
        res = 0;
    assertEqual(res, 0);
}

/* Test bitwise XOR in conditionals */
void test_xor_conditional()
{
    int a;
    int res;

    a = 0x1234;

    if (a ^ 0x4321)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    if (!(a ^ 0x4321))
        res = 1;
    else
        res = 0;
    assertEqual(res, 0);

    /* literal with 0xff bytes */
    if (a ^ 0xff04)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);

    /* literal with zero bytes */
    if (a ^ 0x0004)
        res = 1;
    else
        res = 0;
    assertEqual(res, 1);
}

/* Test 32-bit bitwise operations */
void test_bitwise_long()
{
    long left, right;

    left = 0x3df7c1ec;
    right = 0xc1ec3df7;

    /* AND */
    assertEqual((long)(left & right), (long)0x01E401E4);

    /* OR */
    assertEqual((long)(left | right), (long)0xFDFFFDFF);

    /* XOR */
    assertEqual((long)(left ^ right), (long)0xFC1BFC1B);
}

int main()
{
    suite_setup("Bitwise Tests");
    suite_add_test(test_bitwise_int);
    suite_add_test(test_and_conditional);
    suite_add_test(test_or_conditional);
    suite_add_test(test_xor_conditional);
    suite_add_test(test_bitwise_long);
    suite_run();
    return suite_result();
}
