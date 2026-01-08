/* Test string functions */

int putchar(int c);
int puts(const char *s);
int strlen(const char *s);
char *strcpy(char *dest, const char *src);
int strcmp(const char *s1, const char *s2);
char *strcat(char *dest, const char *src);
char *strchr(const char *s, int c);
void *memcpy(void *dest, const void *src, int n);
void *memset(void *s, int c, int n);

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

int main(void) {
    char buf[32];
    char *p;
    int len;

    /* Test strlen */
    len = strlen("Hello");
    print_num(len);
    putchar(' ');

    /* Test strcpy */
    strcpy(buf, "World");
    puts(buf);

    /* Test strcat */
    strcpy(buf, "Hello");
    strcat(buf, " World");
    puts(buf);

    /* Test strcmp */
    if (strcmp("abc", "abc") == 0) {
        putchar('Y');
    } else {
        putchar('N');
    }

    if (strcmp("abc", "abd") < 0) {
        putchar('Y');
    } else {
        putchar('N');
    }
    putchar(' ');

    /* Test strchr */
    p = strchr("Hello", 'l');
    if (p) {
        putchar(*p);
    } else {
        putchar('?');
    }
    putchar(' ');

    /* Test memset */
    memset(buf, 'X', 5);
    buf[5] = 0;
    puts(buf);

    /* Test memcpy */
    memcpy(buf, "Test!", 6);
    puts(buf);

    return 0;
}
