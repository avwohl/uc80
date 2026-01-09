/* Simple file I/O debug test */
#include <stdio.h>

void print_hex(int n) {
    int hi = (n >> 4) & 0xF;
    int lo = n & 0xF;
    putchar(hi < 10 ? '0' + hi : 'A' + hi - 10);
    putchar(lo < 10 ? '0' + lo : 'A' + lo - 10);
}

int main() {
    FILE *fp;
    int c, count;

    puts("Simple File Test");

    /* Write test */
    puts("Opening for write...");
    fp = fopen("T.TXT", "w");
    if (!fp) {
        puts("FAIL: Cannot open for write");
        return 1;
    }
    puts("Writing ABC...");
    fputc('A', fp);
    fputc('B', fp);
    fputc('C', fp);
    fputc('\n', fp);
    puts("Closing...");
    fclose(fp);
    puts("Write done.");

    /* Read test */
    puts("Opening for read...");
    fp = fopen("T.TXT", "r");
    if (!fp) {
        puts("FAIL: Cannot open for read");
        return 1;
    }

    puts("Reading chars:");
    count = 0;
    while (count < 10) {
        c = fgetc(fp);
        printf("  [%d] = ", count);
        if (c == -1) {
            puts("EOF");
            break;
        }
        print_hex(c);
        putchar(' ');
        if (c >= 32 && c < 127) {
            putchar('\'');
            putchar(c);
            putchar('\'');
        }
        putchar('\n');
        count++;
    }

    fclose(fp);
    puts("Done.");
    return 0;
}
