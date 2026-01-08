/* Adapted from z88dk test/suites/sccz80/compare.c */
#include "test.h"

void test_uint_boundaries()
{
    unsigned int a = 20000;

    Assert( a < 30000, "a < 30000");
    Assert( (a < 20000) == 0, "a < 20000");
    Assert( (a < 10000) == 0, "a < 10000");
}

void test_int_boundaries()
{
    int a = 20000;

    Assert( a < 30000, "a < 30000");
    Assert( (a < 20000) == 0, "a < 20000");
    Assert( (a < 10000) == 0, "a < 10000");
    Assert( (a < -10000) == 0, "a < -10000");

    a = -10000;
    Assert( a < 30000, "a < 30000");
    Assert( a < 20000, "a < 20000");
    Assert( a < 10000, "a < 10000");
    Assert( a < -5000, "a < -5000");
    Assert( (a < -10000) == 0, "a < -10000");
}

void test_uint_compare()
{
    unsigned int a = 10;
    unsigned int b = 0xc012;

    Assert( a < b, "a < b");
    if ( a < b ) {} else { Assert(0, "a < b if"); }
    Assert( a <= b, "a <= b");
    if ( a <= b ) {} else { Assert(0, "a <= b if"); }
    Assert( a != b, "a != b");
    if ( a != b ) {} else { Assert(0, "a != b if"); }
    Assert( a == a, "a == a");
    if ( a == a ) {} else { Assert(0, "a == a if"); }
    Assert( (a > b) == 0, "a > b == 0");
    if ( a > b ) { Assert(0, "a > b should be false"); }
    Assert( (a >= b) == 0, "a >= b == 0");
    if ( a >= b ) { Assert(0, "a >= b should be false"); }

    Assert( b > a, "b > a");
    if ( b > a ) {} else { Assert(0, "b > a if"); }
    Assert( b >= a, "b >= a");
    if ( b >= a ) {} else { Assert(0, "b >= a if"); }
}

void test_long_compare()
{
    long a = -10;
    long b = 20;

    Assert( a < b, "a < b");
    if ( a < b ) {} else { Assert(0, "a < b if"); }
    Assert( a <= b, "a <= b");
    if ( a <= b ) {} else { Assert(0, "a <= b if"); }
    Assert( a != b, "a != b");
    if ( a != b ) {} else { Assert(0, "a != b if"); }
    Assert( a == a, "a == a");
    if ( a == a ) {} else { Assert(0, "a == a if"); }
    Assert( b > a, "b > a");

    Assert( (a > b) == 0, "a > b == 0");
    if ( a > b ) { Assert(0, "a > b should be false"); }
    Assert( (a >= b) == 0, "a >= b == 0");
    if ( a >= b ) { Assert(0, "a >= b should be false"); }

    if ( b > a ) {} else { Assert(0, "b > a if"); }
    Assert( b >= a, "b >= a");
    if ( b >= a ) {} else { Assert(0, "b >= a if"); }
}

int main()
{
    suite_setup("Compare Tests");
    suite_add_test(test_uint_boundaries);
    suite_add_test(test_int_boundaries);
    suite_add_test(test_uint_compare);
    suite_add_test(test_long_compare);
    suite_run();
    return suite_result();
}
