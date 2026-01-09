/* Test static keyword support */

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

/* Static function (internal linkage) */
static int add(int a, int b) {
    return a + b;
}

/* Function with static local variable */
int counter(void) {
    static int count = 0;
    count = count + 1;
    return count;
}

int main(void) {
    int i;
    int result;

    /* Test static function */
    result = add(10, 20);
    print_num(result);
    putchar(' ');

    /* Test static local - should increment each call */
    i = 0;
    while (i < 5) {
        print_num(counter());
        putchar(' ');
        i = i + 1;
    }

    putchar('\n');
    return 0;
}
