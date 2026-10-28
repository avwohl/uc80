/* mmio_ym.c - a memory-mapped sound chip driver, for the stage B proof
 * (bench/rom/mmio.py).  Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Same job as the snd-ymreg kernel: write a table of (bank, register,
 * value) entries to a YM2610, with a busy wait on the status bit before the
 * address and before the data write.  The chip is at 4000h and the Z80 sees
 * it as memory, so the C code reaches it through pointers that hold a
 * constant: a struct pointer, a plain pointer and a pointer that the
 * start-up function sets once.
 */
#include <stdint.h>

struct ym {
    volatile uint8_t addr_a;   /* 4000h: read = status, write = register */
    volatile uint8_t data_a;
    volatile uint8_t addr_b;
    volatile uint8_t data_b;
};

static struct ym *const ym = (struct ym *)0x4000;
static volatile uint8_t *const reply = (volatile uint8_t *)0x4010;
static volatile uint8_t *kick;               /* set once in setup () */

struct regw { uint8_t bank, reg, val; };

#define E(b, r, v) { b, r, v }
static const struct regw table[16] = {
    E(0, 0x31, 0x71), E(0, 0x35, 0x0D), E(0, 0x39, 0x33), E(0, 0x3D, 0x01),
    E(0, 0x41, 0x23), E(0, 0x45, 0x2D), E(0, 0x49, 0x26), E(0, 0x4D, 0x00),
    E(1, 0x31, 0x71), E(1, 0x35, 0x0D), E(1, 0x39, 0x33), E(1, 0x3D, 0x01),
    E(1, 0xA5, 0x22), E(1, 0xA1, 0x69), E(1, 0xB1, 0x32), E(1, 0xB5, 0xC0)
};

static void setup(void) { kick = (volatile uint8_t *)0x4020; }

static void ym_write(uint8_t bank, uint8_t reg, uint8_t val)
{
    if (bank == 0) {
        while (ym->addr_a & 0x80)
            ;
        ym->addr_a = reg;
        while (ym->addr_a & 0x80)
            ;
        ym->data_a = val;
    } else {
        while (ym->addr_b & 0x80)
            ;
        ym->addr_b = reg;
        while (ym->addr_b & 0x80)
            ;
        ym->data_b = val;
    }
}

int main(void)
{
    uint8_t i, frame;

    setup();
    for (frame = 0; frame < 8; frame++) {
        for (i = 0; i < 16; i++)
            ym_write(table[i].bank, table[i].reg,
                     (uint8_t)(table[i].val + frame));
        *kick = frame;
        *reply = (uint8_t)(*reply + 1);
    }
    return 0;
}
