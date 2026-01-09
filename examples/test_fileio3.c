/* Focused fgets test */
#include <stdio.h>

int main() {
    FILE *fp;
    char buf[64];

    puts("fgets Test");

    /* Write test file */
    fp = fopen("T.TXT", "w");
    if (!fp) {
        puts("Cannot open for write");
        return 1;
    }
    fputs("Line one\n", fp);
    fputs("Line two\n", fp);
    fclose(fp);
    puts("File written.");

    /* Read with fgets */
    puts("Opening for read...");
    fp = fopen("T.TXT", "r");
    if (!fp) {
        puts("Cannot open for read");
        return 1;
    }
    puts("Opened successfully.");

    puts("Calling fgets...");
    if (fgets(buf, 64, fp)) {
        puts("Got line:");
        puts(buf);
    } else {
        puts("fgets returned NULL");
    }

    puts("Calling fgets again...");
    if (fgets(buf, 64, fp)) {
        puts("Got line:");
        puts(buf);
    } else {
        puts("fgets returned NULL");
    }

    fclose(fp);
    puts("Done.");
    return 0;
}
