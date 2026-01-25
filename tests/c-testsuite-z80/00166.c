/* Z80 16-bit adaptation: use long instead of int for values > 32767 */
#include <stdio.h>

int main()
{
   long a = 24680;
   long b = 01234567;
   long c = 0x2468ac;
   long d = 0x2468AC;

   printf("%ld\n", a);
   printf("%ld\n", b);
   printf("%ld\n", c);
   printf("%ld\n", d);

   return 0;
}
