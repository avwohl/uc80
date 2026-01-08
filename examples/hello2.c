/* Hello World with putchar */

int putchar(int c);

int main(void) {
    char *s = "Hello, World!\r\n";
    while (*s) {
        putchar(*s);
        s = s + 1;
    }
    return 0;
}
