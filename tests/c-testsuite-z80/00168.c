/* Z80 16-bit adaptation: use long for factorial to avoid overflow */
#include <stdio.h>

long factorial(int i)
{
   if (i < 2)
      return i;
   else
      return i * factorial(i - 1);
}

int main()
{
   int Count;

   for (Count = 1; Count <= 10; Count++)
      printf("%ld\n", factorial(Count));

   return 0;
}
