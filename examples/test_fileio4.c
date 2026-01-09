/* Debug fgetc after fopen */
#include <stdio.h>

void print_hex(int n) {
    int hi, lo;
    hi = (n >> 12) & 0xF;
    lo = (n >> 8) & 0xF;
    putchar(hi < 10 ? '0' + hi : 'A' + hi - 10);
    putchar(lo < 10 ? '0' + lo : 'A' + lo - 10);
    hi = (n >> 4) & 0xF;
    lo = n & 0xF;
    putchar(hi < 10 ? '0' + hi : 'A' + hi - 10);
    putchar(lo < 10 ? '0' + lo : 'A' + lo - 10);
}

int main() {
    FILE *fp;
    int c;

    puts("Debug fgetc");

    /* Write */
    fp = fopen("T.TXT", "w");
    if (!fp) { puts("Write open fail"); return 1; }
    fputc('X', fp);
    fputc('Y', fp);
    fputc('Z', fp);
    fclose(fp);
    puts("Written XYZ");

    /* Read */
    fp = fopen("T.TXT", "r");
    if (!fp) { puts("Read open fail"); return 1; }
    puts("Opened for read");

    puts("First fgetc:");
    c = fgetc(fp);
    print_hex(c);
    putchar('\n');

    puts("Second fgetc:");
    c = fgetc(fp);
    print_hex(c);
    putchar('\n');

    fclose(fp);
    puts("Done");
    return 0;
}
