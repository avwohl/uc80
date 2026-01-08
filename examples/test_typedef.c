/* Test typedef support */

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

/* Basic typedef */
typedef int MyInt;
typedef unsigned char Byte;

/* Pointer typedef */
typedef int *IntPtr;

/* Struct typedef */
typedef struct {
    int x;
    int y;
} Point;

int main(void) {
    MyInt a;
    Byte b;
    IntPtr p;
    Point pt;
    int val;

    /* Test basic typedef */
    a = 42;
    print_num(a);
    putchar(' ');

    /* Test unsigned char typedef */
    b = 255;
    print_num(b);
    putchar(' ');

    /* Test pointer typedef */
    val = 100;
    p = &val;
    print_num(*p);
    putchar(' ');

    /* Test struct typedef */
    pt.x = 10;
    pt.y = 20;
    print_num(pt.x);
    putchar(' ');
    print_num(pt.y);

    putchar('\n');
    return 0;
}
