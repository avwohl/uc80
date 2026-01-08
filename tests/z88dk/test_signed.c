/* Debug signed comparison */
#include <stdio.h>

int main() {
    int a;

    a = -10000;
    if (a < 30000) {
        puts("PASS: -10000 < 30000");
    } else {
        puts("FAIL: -10000 < 30000");
    }

    if (a < 10000) {
        puts("PASS: -10000 < 10000");
    } else {
        puts("FAIL: -10000 < 10000");
    }

    if (a < -5000) {
        puts("PASS: -10000 < -5000");
    } else {
        puts("FAIL: -10000 < -5000");
    }

    return 0;
}
