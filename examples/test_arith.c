/* Test arithmetic operations */

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
    int a = 12;
    int b = 5;

    print_num(a + b);  /* 17 */
    putchar(' ');
    print_num(a - b);  /* 7 */
    putchar(' ');
    print_num(a * b);  /* 60 */
    putchar(' ');
    print_num(a / b);  /* 2 */
    putchar(' ');
    print_num(a % b);  /* 2 */
    putchar('\r');
    putchar('\n');

    return 0;
}
