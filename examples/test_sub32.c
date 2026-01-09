/* Minimal test of 32-bit subtraction */

int putchar(int c);

void print_num(int n) {
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    long a = 100000;
    long b = 50000;
    long c;
    int low;

    /* Just subtraction */
    c = a - b;
    low = c;
    print_num(low);
    putchar('\r');
    putchar('\n');

    return 0;
}
