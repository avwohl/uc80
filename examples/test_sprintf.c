/* Test sprintf */
#include <stdio.h>

int main() {
    char buf[128];

    puts("sprintf Test");

    sprintf(buf, "Hello, %s!", "world");
    puts(buf);

    sprintf(buf, "Number: %d", 42);
    puts(buf);

    sprintf(buf, "Hex: %x", 255);
    puts(buf);

    sprintf(buf, "%d + %d = %d", 10, 20, 30);
    puts(buf);

    puts("Done!");
    return 0;
}
