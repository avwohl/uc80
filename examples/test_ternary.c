/* Test ternary operator */

int putchar(int c);

void print_num(int n) {
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    int x;

    /* Test ternary with true condition */
    x = (5 > 3) ? 10 : 20;
    print_num(x);  /* 10 */
    putchar(' ');

    /* Test ternary with false condition */
    x = (3 > 5) ? 10 : 20;
    print_num(x);  /* 20 */
    putchar(' ');

    /* Test nested ternary */
    x = 7;
    print_num(x > 10 ? 1 : x > 5 ? 2 : 3);  /* 2 */
    putchar(' ');

    x = 12;
    print_num(x > 10 ? 1 : x > 5 ? 2 : 3);  /* 1 */
    putchar(' ');

    x = 3;
    print_num(x > 10 ? 1 : x > 5 ? 2 : 3);  /* 3 */

    putchar('\r');
    putchar('\n');

    return 0;
}
