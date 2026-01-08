/* Test struct support */

int putchar(int c);

void print_num(int n) {
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

struct Point {
    int x;
    int y;
};

int main(void) {
    struct Point p;

    p.x = 10;
    p.y = 20;

    print_num(p.x);
    putchar(' ');
    print_num(p.y);
    putchar(' ');
    print_num(p.x + p.y);
    putchar('\r');
    putchar('\n');

    return 0;
}
