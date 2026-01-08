/* Test enum support */

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

enum Color {
    RED,
    GREEN,
    BLUE
};

enum Status {
    OK = 0,
    ERROR = 1,
    PENDING = 100,
    DONE
};

int main(void) {
    int c;
    int s;

    /* Test basic enum values */
    c = RED;
    print_num(c);
    putchar(' ');

    c = GREEN;
    print_num(c);
    putchar(' ');

    c = BLUE;
    print_num(c);
    putchar(' ');

    /* Test enum with explicit values */
    s = OK;
    print_num(s);
    putchar(' ');

    s = ERROR;
    print_num(s);
    putchar(' ');

    s = PENDING;
    print_num(s);
    putchar(' ');

    s = DONE;
    print_num(s);
    putchar(' ');

    /* Test enum in expressions */
    if (RED == 0) {
        putchar('Y');
    } else {
        putchar('N');
    }

    if (BLUE == 2) {
        putchar('Y');
    } else {
        putchar('N');
    }

    if (DONE == 101) {
        putchar('Y');
    } else {
        putchar('N');
    }

    putchar('\n');
    return 0;
}
