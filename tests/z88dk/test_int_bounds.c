/* Exact replica of test_int_boundaries */
#include "test.h"

void test_int_boundaries()
{
    int a = 20000;

    Assert( a < 30000, "a < 30000");
    Assert( (a < 20000) == 0, "a < 20000");
    Assert( (a < 10000) == 0, "a < 10000");
    Assert( (a < -10000) == 0, "a < -10000");

    a = -10000;
    Assert( a < 30000, "a2 < 30000");
    Assert( a < 20000, "a2 < 20000");
    Assert( a < 10000, "a2 < 10000");
    Assert( a < -5000, "a2 < -5000");
    Assert( (a < -10000) == 0, "a2 < -10000");
}

int main()
{
    suite_setup("Int Boundaries");
    suite_add_test(test_int_boundaries);
    suite_run();
    return suite_result();
}
