/* Test 32-bit long type operations - simple version */

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
    long a = 50000;
    long b = 30000;
    long c;
    int low;

    /* Test addition: 50000 + 30000 = 80000, low word = -50000+65536 = 14464 */
    c = a + b;
    low = c;
    print_num(low);
    putchar(' ');

    /* Test subtraction: 50000 - 30000 = 20000 (fits in signed 16-bit) */
    c = a - b;
    low = c;
    print_num(low);
    putchar(' ');

    /* Test multiplication: 200 * 100 = 20000 (fits in signed 16-bit) */
    a = 200;
    b = 100;
    c = a * b;
    low = c;
    print_num(low);

    putchar('\r');
    putchar('\n');

    return 0;
}
