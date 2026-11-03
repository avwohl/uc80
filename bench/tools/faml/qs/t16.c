#include <stdio.h>
typedef unsigned char uint8; typedef unsigned short uint16;
#define READUI16(ptr) ((((uint16) ptr[0]) << 8) | ((uint16) ptr[1])); ptr += sizeof (uint16)
static uint8 buf[200];
struct H { uint16 a, b, c, d; uint8 e; } hh;
int main(void) {
    int i; unsigned s = 0; const uint8 *ptr = buf; uint16 v;
    for (i = 0; i < 200; i++) buf[i] = (uint8)(i * 37 + 11);
    hh.a = READUI16(ptr); hh.b = READUI16(ptr); hh.c = READUI16(ptr); hh.d = READUI16(ptr);
    printf("%04x %04x %04x %04x\n", hh.a, hh.b, hh.c, hh.d);
    for (i = 0; i < 40; i++) { v = READUI16(ptr); s = s * 31 + v; }
    printf("%u\n", s);
    return 0;
}
