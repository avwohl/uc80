#include <stdio.h>
int arr[4], x; char cs[3]; int m[2][2];
_Bool g1=arr, g2=cs, g3=m, g4=m[1];
_Bool g5 = &x, g6 = arr+1, g7 = &arr[2], g8 = (_Bool)arr, g9 = (long)arr, g10 = (char*)arr;
_Bool g11 = arr != 0, g12 = 0 ? 0x100 : 0x200, g13 = (_Bool)(&arr[1]-&arr[0]);
_Bool g14 = (_Bool)(&arr[3]-&arr[0]), g15 = &arr[3]-&arr[0];
_Bool ga[3] = {arr, cs, 0};
struct S { _Bool b; int i; } gs = { arr, 3 };
int main(void){
  static _Bool l1 = arr, l2 = arr+1, l3 = &arr[2]-&arr[2], l4 = (long)arr;
  printf("%d %d %d %d\n", l1,l2,l3,l4);
  printf("%d %d %d %d\n", g1,g2,g3,g4);
  printf("%d %d %d %d %d %d\n", g5,g6,g7,g8,g9,g10);
  printf("%d %d %d %d %d\n", g11,g12,g13,g14,g15);
  printf("%d %d %d %d %d\n", ga[0],ga[1],ga[2],gs.b,gs.i);
  return 0;
}
