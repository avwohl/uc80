/* Z80 16-bit adaptation: test Z80-specific type sizes */
/* Original test used __ILP32__/__LP64__/__LLP64__ which don't match Z80 */
/* Z80/uc80 has: 16-bit short, 16-bit int, 32-bit long, 64-bit long long, 16-bit pointers */
#include <stdio.h>

int main()
{
    /* Verify Z80-specific sizes */
    if (sizeof(short) == 2
        && sizeof(int) == 2
        && sizeof(long int) == 4
        && sizeof(long long int) == 8
        && sizeof(void*) == 2) {
        (void)printf("Ok\n");
    } else {
        (void)printf("KO Z80 type sizes\n");
        printf("short=%d int=%d long=%d long long=%d ptr=%d\n",
               (int)sizeof(short), (int)sizeof(int), (int)sizeof(long int),
               (int)sizeof(long long int), (int)sizeof(void*));
    }
    return 0;
}
