/* Test compound assignment operators */

int putchar(int c);

void print_num(int n) {
    if (n < 0) {
        putchar('-');
        n = -n;
    }
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    int x = 10;

    /* Test += */
    x += 5;
    print_num(x);  /* 15 */
    putchar(' ');

    /* Test -= */
    x -= 3;
    print_num(x);  /* 12 */
    putchar(' ');

    /* Test *= */
    x *= 2;
    print_num(x);  /* 24 */
    putchar(' ');

    /* Test /= */
    x /= 4;
    print_num(x);  /* 6 */
    putchar(' ');

    /* Test %= */
    x %= 4;
    print_num(x);  /* 2 */

    putchar('\r');
    putchar('\n');

    return 0;
}
