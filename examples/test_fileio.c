/* Test file I/O operations */
#include <stdio.h>

int main() {
    FILE *fp;
    char buf[64];
    int c;

    puts("File I/O Test");
    puts("=============");

    /* Test 1: Write to a file */
    puts("Test 1: Writing to TEST.TXT...");
    fp = fopen("TEST.TXT", "w");
    if (!fp) {
        puts("ERROR: Cannot open file for writing");
        return 1;
    }

    fputs("Hello, CP/M!\n", fp);
    fputs("Line 2 of test file.\n", fp);
    fputs("Line 3: The End.\n", fp);
    fclose(fp);
    puts("Write complete.");

    /* Test 2: Read back the file */
    puts("Test 2: Reading TEST.TXT...");
    fp = fopen("TEST.TXT", "r");
    if (!fp) {
        puts("ERROR: Cannot open file for reading");
        return 1;
    }

    puts("--- File contents ---");
    while ((c = fgetc(fp)) != -1) {
        putchar(c);
    }
    puts("--- End of file ---");
    fclose(fp);

    /* Test 3: Read with fgets */
    puts("Test 3: Reading with fgets...");
    fp = fopen("TEST.TXT", "r");
    if (!fp) {
        puts("ERROR: Cannot reopen file");
        return 1;
    }

    while (fgets(buf, 64, fp)) {
        printf("Line: %s", buf);
    }
    fclose(fp);

    puts("All tests passed!");
    return 0;
}
