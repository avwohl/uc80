/* Test increment/decrement operators */

int putchar(int c);

void print_num(int n) {
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    int x = 5;

    /* Test prefix increment */
    print_num(++x);  /* 6 */
    putchar(' ');
    print_num(x);    /* 6 */
    putchar(' ');

    /* Test postfix increment */
    print_num(x++);  /* 6 */
    putchar(' ');
    print_num(x);    /* 7 */
    putchar(' ');

    /* Test prefix decrement */
    print_num(--x);  /* 6 */
    putchar(' ');

    /* Test postfix decrement */
    print_num(x--);  /* 6 */
    putchar(' ');
    print_num(x);    /* 5 */

    putchar('\r');
    putchar('\n');

    return 0;
}
