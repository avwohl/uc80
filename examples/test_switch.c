/* Test switch/case */

int putchar(int c);

int main(void) {
    int x;

    /* Test switch with break */
    x = 1;
    switch (x) {
        case 0:
            putchar('A');
            break;
        case 1:
            putchar('B');
            break;
        case 2:
            putchar('C');
            break;
        default:
            putchar('D');
    }

    /* Test fall-through */
    x = 0;
    switch (x) {
        case 0:
            putchar('X');
        case 1:
            putchar('Y');
            break;
        case 2:
            putchar('Z');
            break;
    }

    putchar('\r');
    putchar('\n');

    return 0;
}
