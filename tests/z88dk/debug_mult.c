/* Debug unsigned int multiply */
#include <stdio.h>

int main()
{
    unsigned int val1, val2;
    int sval1, sval2;

    val1 = 256;
    val2 = 128;

    printf("unsigned: 256 * 128 = %u\n", val1 * val2);
    printf("Expected: 32768\n");
    printf("val1 * val2 == 32768? ");
    if (val1 * val2 == 32768) printf("YES\n"); else printf("NO\n");

    /* Try signed */
    sval1 = 256;
    sval2 = 128;
    printf("signed: 256 * 128 = %d\n", sval1 * sval2);

    return 0;
}
