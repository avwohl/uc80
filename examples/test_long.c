/* Test 32-bit long type operations */

int putchar(int c);

void print_hex(long val) {
    int i;
    char c;
    int digit;

    for (i = 7; i >= 0; i--) {
        digit = (val >> (i * 4)) & 0xF;
        if (digit < 10) {
            c = '0' + digit;
        } else {
            c = 'A' + digit - 10;
        }
        putchar(c);
    }
}

int main(void) {
    long a = 100000;
    long b = 50000;
    long c;

    /* Test addition: 100000 + 50000 = 150000 = 0x000249F0 */
    c = a + b;
    print_hex(c);
    putchar(' ');

    /* Test subtraction: 100000 - 50000 = 50000 = 0x0000C350 */
    c = a - b;
    print_hex(c);
    putchar(' ');

    /* Test multiplication: 1000 * 1000 = 1000000 = 0x000F4240 */
    a = 1000;
    b = 1000;
    c = a * b;
    print_hex(c);

    putchar('\r');
    putchar('\n');

    return 0;
}
