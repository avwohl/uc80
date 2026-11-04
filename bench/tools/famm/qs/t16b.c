#include <stdio.h>
typedef unsigned char uint8; typedef unsigned short uint16;
#define READUI16(ptr) ((((uint16) ptr[0]) << 8) | ((uint16) ptr[1])); ptr += sizeof (uint16)
#define READUI8(ptr) *(ptr++)
static uint8 buf[64];
struct H { uint8 v, f; uint16 a, b, c, d, e; uint8 s[3]; uint16 t; } *G;
static struct H hh;
static void init(const uint8 *p)
{
    G->v = READUI8(p); G->f = READUI8(p);
    G->a = READUI16(p); G->b = READUI16(p); G->c = READUI16(p); G->d = READUI16(p); G->e = READUI16(p);
    G->s[0] = READUI8(p); G->s[1] = READUI8(p); G->s[2] = READUI8(p);
    G->t = READUI16(p);
}
int main(void) {
    int i; G = &hh;
    for (i = 0; i < 64; i++) buf[i] = (uint8)(i * 37 + 11);
    init(buf);
    printf("%u %u %04x %04x %04x %04x %04x %u %u %u %04x\n", G->v, G->f, G->a, G->b, G->c, G->d, G->e, G->s[0], G->s[1], G->s[2], G->t);
    return 0;
}
