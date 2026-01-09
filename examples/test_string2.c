/* Simpler string test */

int putchar(int c);
int strlen(const char *s);
char *strcpy(char *dest, const char *src);
int strcmp(const char *s1, const char *s2);

void print_num(int n) {
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    char buf[16];
    int len;

    /* Test strlen */
    len = strlen("Hello");
    print_num(len);
    putchar('\r');
    putchar('\n');

    /* Test strcpy */
    strcpy(buf, "World");
    putchar(buf[0]);  /* W */
    putchar(buf[1]);  /* o */
    putchar(buf[2]);  /* r */
    putchar('\r');
    putchar('\n');

    /* Test strcmp */
    if (strcmp("abc", "abc") == 0) {
        putchar('Y');
    } else {
        putchar('N');
    }
    putchar('\r');
    putchar('\n');

    return 0;
}
