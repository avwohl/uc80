/* ymreg.c - YM2610 register write table with shadow registers.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Each frame, a sound driver walks a table of (bank, register, value)
 * entries: here a 4-operator FM patch for channel 1 of each YM2610 port,
 * 64 entries.  The total-level registers get an envelope offset that
 * changes every frame.  ym_write () compares each value with a shadow
 * copy of the register file and writes only changed registers, with a
 * busy-wait on the status bit before the address and the data write.
 */
#include "snd.h"

struct regw {
    uint8_t bank;
    uint8_t reg;
    uint8_t val;
};

/* One FM operator set: DT/MUL, TL, KS/AR, AM/DR, SR, SL/RR, SSG-EG for
 * the 4 operators (register offsets 0, 4, 8, 0Ch) of channel 1. */
#define OP(b, base, v0, v1, v2, v3) \
    { b, base + 0x01, v0 }, { b, base + 0x05, v1 }, \
    { b, base + 0x09, v2 }, { b, base + 0x0D, v3 }
#define PATCH(b, f_hi, f_lo) \
    OP(b, 0x30, 0x71, 0x0D, 0x33, 0x01), \
    OP(b, 0x40, 0x23, 0x2D, 0x26, 0x00), \
    OP(b, 0x50, 0x5F, 0x99, 0x5F, 0x94), \
    OP(b, 0x60, 0x05, 0x05, 0x05, 0x07), \
    OP(b, 0x70, 0x02, 0x02, 0x02, 0x02), \
    OP(b, 0x80, 0x11, 0x11, 0x11, 0xA6), \
    OP(b, 0x90, 0x00, 0x00, 0x00, 0x00), \
    { b, 0xB1, 0x32 }, { b, 0xB5, 0xC0 }, \
    { b, 0xA5, f_hi }, { b, 0xA1, f_lo }

static const struct regw patch_table[64] = {
    PATCH(0, 0x22, 0x69),
    PATCH(1, 0x1A, 0x8E)
};

/* Envelope offsets added to the total-level registers, one per frame. */
static const uint8_t env[16] = {
    0, 1, 2, 4, 6, 8, 10, 12, 14, 12, 10, 8, 6, 4, 2, 1
};

static uint8_t shadow[2][256];
static uint16_t written, skipped;

/* Read the frame count through a volatile, so no compiler can fold the
 * work. */
volatile uint16_t frames = 300;

static void ym_wait(void)
{
    while (in(PORT_YM_ADDR_A) & 0x80)
        ;
}

void ym_write(uint8_t bank, uint8_t reg, uint8_t val)
{
    uint8_t *s = &shadow[bank][reg];

    if (*s == val) {
        skipped++;
        return;
    }
    *s = val;
    ym_wait();
    out(bank ? PORT_YM_ADDR_B : PORT_YM_ADDR_A, reg);
    ym_wait();
    out(bank ? PORT_YM_DATA_B : PORT_YM_DATA_A, val);
    written++;
}

static void frame_update(uint16_t frame)
{
    const struct regw *t = patch_table;
    uint8_t i, val;

    for (i = 0; i < 64; i++, t++) {
        val = t->val;
        if ((t->reg & 0xF0) == 0x40)
            val = (uint8_t) ((val + env[(frame + i) & 15]) & 0x7F);
        else if ((t->reg & 0xF0) == 0xA0 && (frame & 7) == 0)
            val = (uint8_t) (val ^ (frame >> 3));       /* pitch change */
        ym_write(t->bank, t->reg, val);
    }
    /* Key on every 16 frames, key off 8 frames later. */
    if ((frame & 15) == 0) {
        ym_write(0, 0x28, 0xF1);
        ym_write(0, 0x28, 0xF5);
    } else if ((frame & 15) == 8) {
        ym_write(0, 0x28, 0x01);
        ym_write(0, 0x28, 0x05);
    }
}

int main(void)
{
    uint16_t f, n = frames;

    intrinsic_label(TIMER_START);
    for (f = 0; f < n; f++)
        frame_update(f);
    intrinsic_label(TIMER_STOP);
    printf("written %u, skipped %u\n", (unsigned int) written,
           (unsigned int) skipped);
    snd_report("ymreg");
    return 0;
}
