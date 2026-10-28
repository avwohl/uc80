#include <stdint.h>
typedef volatile uint16_t vreg;
void addt(vreg *q) { *q += 0x101; }
static volatile uint16_t *const cp = (volatile uint16_t *)0x4010;
void c1(void) { *cp += 0x101; }
volatile uint16_t *gp;
void c2(void) { (*gp)--; }
int main(void){return 0;}
