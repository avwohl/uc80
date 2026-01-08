/* Test unsigned type comparisons */

int putchar(int c);

void print_num(int n) {
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    unsigned int a = 50000;  /* Larger than 32767, would be negative if signed */
    unsigned int b = 100;
    int sa = -100;  /* Signed negative */
    int sb = 50;

    /* Unsigned comparison: 50000 > 100 should be true */
    if (a > b) {
        putchar('Y');
    } else {
        putchar('N');
    }

    /* Signed comparison: -100 < 50 should be true */
    if (sa < sb) {
        putchar('Y');
    } else {
        putchar('N');
    }

    putchar(' ');

    /* Unsigned: a should print as 50000 */
    print_num(a / 1000);
    print_num((a / 100) % 10);
    print_num((a / 10) % 10);
    print_num(a % 10);

    putchar('\r');
    putchar('\n');

    return 0;
}
