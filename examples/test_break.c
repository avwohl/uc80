/* Test break and continue */

int putchar(int c);

int main(void) {
    int i;

    /* Test break: should print 012 */
    i = 0;
    while (1) {
        if (i >= 3) {
            break;
        }
        putchar('0' + i);
        i = i + 1;
    }
    putchar(' ');

    /* Test continue: should print 02468 (skip odd numbers) */
    i = 0;
    while (i < 10) {
        if (i % 2 != 0) {
            i = i + 1;
            continue;
        }
        putchar('0' + i);
        i = i + 1;
    }
    putchar('\r');
    putchar('\n');

    return 0;
}
