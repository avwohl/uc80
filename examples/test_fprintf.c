/* Test fprintf */
#include <stdio.h>

int main() {
    FILE *fp;

    puts("fprintf Test");

    fp = fopen("OUT.TXT", "w");
    if (!fp) {
        puts("Cannot open file");
        return 1;
    }

    fprintf(fp, "Hello, file!\n");
    fprintf(fp, "Number: %d\n", 42);
    fprintf(fp, "Hex: %x\n", 255);
    fprintf(fp, "String: %s\n", "test");
    fprintf(fp, "Char: %c\n", 'X');
    fprintf(fp, "Percent: %%\n");

    fclose(fp);
    puts("File written.");

    /* Read back */
    fp = fopen("OUT.TXT", "r");
    if (!fp) {
        puts("Cannot reopen");
        return 1;
    }

    puts("Contents:");
    int c;
    while ((c = fgetc(fp)) != -1) {
        putchar(c);
    }
    fclose(fp);

    puts("Done!");
    return 0;
}
