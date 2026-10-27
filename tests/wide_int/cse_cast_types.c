#include <stdio.h>
#ifdef HOST
typedef short I16; typedef unsigned short U16;
#else
typedef int I16; typedef unsigned int U16;
#endif
volatile unsigned char vp = 0x81;
int a1(unsigned char p0){ return (U16)((unsigned long)(char)p0) == (U16)((unsigned long)(short)p0 & (unsigned long)(U16)p0); }
int a2(unsigned char p0){ return (U16)((unsigned long)(char)p0 & (unsigned long)(signed char)p0) == (U16)((unsigned long)(short)p0 & (unsigned long)(U16)p0); }
int a3(unsigned char p0){ return (U16)((unsigned long)(char)p0) == (U16)((unsigned long)(short)p0); }
int a4(unsigned char p0){ return (U16)((unsigned long)(char)p0) == (U16)p0; }
int a5(unsigned char p0){ return (U16)(U16)((unsigned long)(char)p0) == (U16)(U16)((unsigned long)(short)p0 & (unsigned long)(U16)p0); }
int a6(unsigned char p0){ return (U16)((unsigned long)(char)p0) == (U16)((unsigned long)p0 & 0xffffUL); }
int b1(unsigned char p0){ return (signed char)p0 == (char)p0; }
int b2(unsigned char p0){ return ((short)(char)p0 & 0x1ff) == ((short)(signed char)p0 & 0x1ff); }
int main(void){
  printf("%d %d\n", b1(vp), b2(vp));
  printf("%d %d %d %d %d %d\n", a1(vp), a2(vp), a3(vp), a4(vp), a5(vp), a6(vp));
  return 0;
}
