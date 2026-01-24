/* Utility functions for multi-file test */

int add(int a, int b) {
    return a + b;
}

int multiply(int a, int b) {
    int result = 0;
    while (b > 0) {
        result = add(result, a);
        b = b - 1;
    }
    return result;
}

/* This function is never called - should be eliminated */
static void unused_static(void) {
    int x = 1;
}
