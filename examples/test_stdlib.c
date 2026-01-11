/* Test stdlib functions */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main() {
    int i;
    char *p;

    puts("stdlib Test");

    /* Test atoi */
    if (atoi("123") == 123) puts("atoi positive: OK");
    else puts("atoi positive: FAIL");

    if (atoi("-42") == -42) puts("atoi negative: OK");
    else puts("atoi negative: FAIL");

    if (atoi("0") == 0) puts("atoi zero: OK");
    else puts("atoi zero: FAIL");

    /* Test abs */
    if (abs(-5) == 5) puts("abs negative: OK");
    else puts("abs negative: FAIL");

    if (abs(7) == 7) puts("abs positive: OK");
    else puts("abs positive: FAIL");

    /* Test rand/srand */
    srand(12345);
    i = rand();
    if (i >= 0) puts("rand: OK");
    else puts("rand: FAIL");

    /* Test malloc/free */
    p = malloc(32);
    if (p) {
        strcpy(p, "dynamic");
        if (strcmp(p, "dynamic") == 0) puts("malloc: OK");
        else puts("malloc: FAIL");
        free(p);
        puts("free: OK");
    } else {
        puts("malloc: FAIL (null)");
    }

    /* Test calloc */
    p = calloc(4, 8);
    if (p) {
        /* calloc should zero memory */
        if (p[0] == 0 && p[15] == 0) puts("calloc: OK");
        else puts("calloc: FAIL (not zero)");
        free(p);
    } else {
        puts("calloc: FAIL (null)");
    }

    puts("Done!");
    return 0;
}
