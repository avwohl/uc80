/* Test 32-bit array initialization and return values */
#include <stdio.h>

/* Test 1: Global array with initializer list */
long arr[] = {1, 2, 3};

/* Test 2: Function returning long */
long get_long(void) {
    return 0x12345678L;
}

/* Test 3: Cast chain char -> short -> long */
long cast_chain(char c) {
    return (long)(short)c;
}

int main() {
    long val;

    /* Test 1: Check array initialization */
    printf("arr[0]=%lx (expect 1)\n", arr[0]);
    printf("arr[1]=%lx (expect 2)\n", arr[1]);
    printf("arr[2]=%lx (expect 3)\n", arr[2]);

    /* Test 2: Check long return value */
    val = get_long();
    printf("get_long=%lx (expect 12345678)\n", val);

    /* Test 3: Cast chain with negative value */
    val = cast_chain(-1);
    printf("cast(-1)=%lx (expect ffffffff)\n", val);

    return 0;
}
