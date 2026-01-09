/* Test NULL and EOF macros */
#include <stdio.h>

int main() {
    FILE *fp;
    int c;

    puts("NULL/EOF Test");

    fp = fopen("T.TXT", "w");
    if (fp == NULL) {
        puts("Cannot open - NULL check works");
        return 1;
    }
    fputs("Test\n", fp);
    fclose(fp);

    fp = fopen("T.TXT", "r");
    if (fp == NULL) {
        puts("Cannot reopen");
        return 1;
    }

    while ((c = fgetc(fp)) != EOF) {
        putchar(c);
    }
    puts("EOF reached - EOF check works");

    fclose(fp);
    puts("Success!");
    return 0;
}
