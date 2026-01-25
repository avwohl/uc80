/* Z80 16-bit adaptation: use Z80-specific type sizes */
/* Original test expects 32-bit int and 64-bit double */
/* Z80/uc80 has: 8-bit char, 16-bit int, 32-bit double */
#include <stdio.h>

int main()
{
   char a;
   int b;
   double c;

   printf("%d\n", sizeof(a));    /* 1 */
   printf("%d\n", sizeof(b));    /* 2 (Z80: 16-bit int) */
   printf("%d\n", sizeof(c));    /* 4 (Z80: 32-bit float/double) */

   printf("%d\n", sizeof(!a));   /* 2 (Z80: result of ! is int) */

   return 0;
}
