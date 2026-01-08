/* Simple test to verify test framework */
#include "test.h"

void test_true() {
    Assert(1, "true should be true");
}

void test_math() {
    int a = 5;
    int b = 3;
    Assert(a + b == 8, "5 + 3 == 8");
    Assert(a - b == 2, "5 - 3 == 2");
    Assert(a * b == 15, "5 * 3 == 15");
}

void test_compare() {
    int x = 10;
    Assert(x < 20, "10 < 20");
    Assert(x > 5, "10 > 5");
    Assert(x == 10, "10 == 10");
    Assert(x != 11, "10 != 11");
}

int main() {
    suite_setup("Simple Tests");
    suite_add_test(test_true);
    suite_add_test(test_math);
    suite_add_test(test_compare);
    suite_run();
    return suite_result();
}
