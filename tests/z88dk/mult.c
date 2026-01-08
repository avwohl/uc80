/* mult.c - Multiply tests adapted from z88dk test suite */
#include "test.h"

void test_mult_int()
{
    int val1, val2;
    val1 = 3;
    val2 = 5;

    Assert(val1 * val2 == 15, "3 * 5");
    Assert(val1 * -val2 == -15, "3 * -5");
    Assert(-val1 * val2 == -15, "-3 * 5");
    Assert(-val1 * -val2 == 15, "-3 * -5");
    Assert(val2 * val1 == 15, "5 * 3");
    Assert(val2 * -val1 == -15, "5 * -3");
    Assert(-val2 * val1 == -15, "-5 * 3");
    Assert(-val2 * -val1 == 15, "-5 * -3");
}

void test_mult_unsigned_int()
{
    unsigned int val1, val2;
    val1 = 3;
    val2 = 5;

    Assert(val1 * val2 == 15, "3 * 5");
    Assert(val2 * val1 == 15, "5 * 3");

    /* Larger values */
    val1 = 100;
    val2 = 200;
    Assert(val1 * val2 == 20000, "100 * 200");
    Assert(val2 * val1 == 20000, "200 * 100");

    val1 = 256;
    val2 = 128;
    Assert(val1 * val2 == 32768, "256 * 128");
}

void test_mult_long()
{
    long val1, val2, val3, val5;
    val1 = 3;
    val2 = 5;
    val3 = 10923;
    val5 = 32769;

    Assert(val1 * val2 == 15L, "3 * 5");
    Assert(val1 * -val2 == -15L, "3 * -5");
    Assert(-val1 * val2 == -15L, "-3 * 5");
    Assert(-val1 * -val2 == 15L, "-3 * -5");
    Assert(val2 * val1 == 15L, "5 * 3");
    Assert(val2 * -val1 == -15L, "5 * -3");
    Assert(-val2 * val1 == -15L, "-5 * 3");
    Assert(-val2 * -val1 == 15L, "-5 * -3");

    Assert(val1 * val3 == 32769L, "3 * 10923");
    Assert(val1 * -val3 == -32769L, "3 * -10923");
    Assert(-val1 * val3 == -32769L, "-3 * 10923");
    Assert(-val1 * -val3 == 32769L, "-3 * -10923");
    Assert(val3 * val1 == 32769L, "10923 * 3");
    Assert(val3 * -val1 == -32769L, "10923 * -3");
    Assert(-val3 * val1 == -32769L, "-10923 * 3");
    Assert(-val3 * -val1 == 32769L, "-10923 * -3");

    Assert(val5 * val5 == 1073807361L, "32769 * 32769");
    Assert(val5 * -val5 == -1073807361L, "32769 * -32769");
    Assert(-val5 * val5 == -1073807361L, "-32769 * 32769");
    Assert(-val5 * -val5 == 1073807361L, "-32769 * -32769");
}

void test_mult_unsigned_long()
{
    unsigned long val1, val2, val3, val5;
    val1 = 3;
    val2 = 5;
    val3 = 0x2AAB;
    val5 = 0x8001;

    Assert(val1 * val2 == 15L, "3 * 5");
    Assert(val2 * val1 == 15L, "5 * 3");

    Assert(val1 * val3 == 0x8001L, "3 * 0x2AAB");
    Assert(val3 * val1 == 0x8001L, "0x2AAB * 3");

    Assert(val5 * val5 == 0x40010001L, "0x8001 * 0x8001");
}

void test_quickmult_long()
{
    long val;
    val = 3;

    Assert(val * 256 == 768L, "3 * 256");
    Assert(val * 64 == 192L, "3 * 64");
    Assert(val * 2 == 6L, "3 * 2");
    Assert(val * 3 == 9L, "3 * 3");
    Assert(val * 4 == 12L, "3 * 4");
    Assert(val * 5 == 15L, "3 * 5");
    Assert(val * 6 == 18L, "3 * 6");
    Assert(val * 7 == 21L, "3 * 7");
    Assert(val * 8 == 24L, "3 * 8");
    Assert(val * 9 == 27L, "3 * 9");
    Assert(val * 40 == 120L, "3 * 40");

    Assert(-val * 256 == -768L, "-3 * 256");
    Assert(-val * 64 == -192L, "-3 * 64");
    Assert(-val * 2 == -6L, "-3 * 2");
    Assert(-val * 3 == -9L, "-3 * 3");
    Assert(-val * 4 == -12L, "-3 * 4");
    Assert(-val * 5 == -15L, "-3 * 5");
    Assert(-val * 6 == -18L, "-3 * 6");
    Assert(-val * 7 == -21L, "-3 * 7");
    Assert(-val * 8 == -24L, "-3 * 8");
    Assert(-val * 9 == -27L, "-3 * 9");
    Assert(-val * 40 == -120L, "-3 * 40");
}

int main()
{
    suite_setup("Multiplication Tests");

    suite_add_test(test_mult_int);
    suite_add_test(test_mult_unsigned_int);
    suite_add_test(test_mult_long);
    suite_add_test(test_mult_unsigned_long);
    suite_add_test(test_quickmult_long);

    suite_run();
    return suite_result();
}
