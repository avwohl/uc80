/* Test global variables */

int putchar(int c);

int counter = 42;
int result;

void print_num(int n) {
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    print_num(counter);
    putchar(' ');
    result = counter * 2;
    print_num(result);
    putchar('\r');
    putchar('\n');
    return 0;
}
