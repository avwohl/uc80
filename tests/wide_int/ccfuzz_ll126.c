#include <stdio.h>
#ifdef HOST
typedef short I16; typedef int L32;
#else
typedef int I16; typedef long L32;
#endif
typedef long long LL; typedef unsigned long long ULL;
ULL G=0x123456789ULL; L32 GL=77777; I16 GI=-5;
static char f0(void){
  return (char)(((ULL)(29013LL)) + (ULL)(GI));
}
static I16 f1(LL p0, I16 p1, I16 p2){
  return (I16)((((ULL)(p0) ^ (ULL)(7661)) + (ULL)(GL)));
}
static char f2(LL p0, L32 p1, LL p2){
  return (char)(((((ULL)(91995) & (ULL)(p1)) - ((ULL)(p1) * (ULL)(33153))) | ((ULL)(G) | (ULL)(f0()))));
}
static LL f3(void){
  LL v0 = (LL)(((ULL)(G) & (ULL)(41467)));
  return (LL)(((ULL)(GI) ^ (ULL)(75293)));
}
static char f4(I16 p0){
  return (char)((((ULL)(p0) * (ULL)(23895LL)) | ((ULL)(GI) | (ULL)(GL))));
}
static I16 f5(void){
  return (I16)(((ULL)(G)) + (ULL)(GI));
}
static char f6(I16 p0){
  return (char)(((((ULL)((ULL)(2518LL)+(ULL)(p0)) >> 0) & ((ULL)(G) ^ (ULL)(p0)))) + (ULL)(GI));
}
static LL f7(L32 p0, I16 p1){
  return (LL)(((ULL)(f1((LL)((ULL)(p1)),(I16)(((ULL)(18125LL) + (ULL)(29008))),(I16)((ULL)(f2((LL)((ULL)(p1)),(L32)((ULL)(GL)),(LL)((ULL)(p1)))))))) + (ULL)(GI));
}
int main(void){
  printf("%ld\n",(long)(L32)(f6((I16)(2828LL))));
  printf("%ld\n",(long)(L32)(f1((LL)(45826LL),(I16)(54717LL),(I16)(16364LL))));
  printf("%ld\n",(long)(L32)(f6((I16)(18135LL))));
  printf("%ld\n",(long)(L32)(f5()));
  printf("%ld\n",(long)(L32)(f1((LL)(58154LL),(I16)(22490LL),(I16)(4743LL))));
  printf("%ld\n",(long)(L32)(f7((L32)(65517LL),(I16)(66841LL))));
  printf("%ld\n",(long)(L32)(f3()));
  printf("%ld\n",(long)(L32)(f2((LL)(41561LL),(L32)(12267LL),(LL)(62224LL))));
  printf("%ld\n",(long)(L32)(f5()));
  printf("%ld\n",(long)(L32)(f7((L32)(61703LL),(I16)(16766LL))));
  printf("%ld\n",(long)(L32)(f6((I16)(62864LL))));
  return 0;
}
