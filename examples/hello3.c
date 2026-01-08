/* Hello World with array indexing */

int putchar(int c);

int main(void) {
    char *s = "Hi\r\n";
    int i = 0;
    while (s[i]) {
        putchar(s[i]);
        i = i + 1;
    }
    return 0;
}
