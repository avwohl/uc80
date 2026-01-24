/* Main program for multi-file test */

/* Declarations for functions in util.c */
int add(int a, int b);
int multiply(int a, int b);

/* Simple helper - should be inlined */
int double_val(int x) {
    return add(x, x);
}

int main(void) {
    int a = double_val(5);   /* Should inline to add(5, 5) = 10 */
    int b = multiply(a, 2);  /* 10 * 2 = 20 */
    return b;
}
