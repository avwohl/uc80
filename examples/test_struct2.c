/* Test struct pointer access with -> */

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
    struct Point *ptr;

    ptr = &p;
    ptr->x = 100;
    ptr->y = 200;

    print_num(ptr->x);
    putchar(' ');
    print_num(ptr->y);
    putchar(' ');
    print_num(ptr->x + ptr->y);
    putchar('\r');
    putchar('\n');

    return 0;
}
