/* fnum.c - note to YM2610 F-number and block, and SSG period.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * A sound driver turns a note number (0-95) plus a signed pitch bend into
 * register values.  FM: block = note / 12, F-number from a 12-entry
 * table, bend scaled by the F-number, renormalised so the F-number stays
 * in 11 bits, then written as A4h (block << 3 | F-number bits 10-8) and
 * A0h (bits 7-0).  SSG: a 12-bit period from a table, shifted right by
 * the octave, then written as fine and coarse registers.
 */
#include "snd.h"

static const uint16_t fnum_tab[12] = {
    617, 654, 693, 734, 778, 824, 873, 925, 980, 1038, 1100, 1165
};
static const uint16_t ssg_tab[12] = {
    3822, 3608, 3405, 3214, 3034, 2863, 2703, 2551, 2408, 2273, 2145, 2025
};

/* Signed 8-bit bend values, one period of a triangle-ish wave. */
static const int8_t bend_tab[16] = {
    0, 16, 32, 48, 64, 48, 32, 16, 0, -16, -32, -48, -64, -48, -32, -16
};

/* Read the repeat count through a volatile, so no compiler can fold the
 * work. */
volatile uint16_t reps = 25;

void fm_note(uint8_t chan, uint8_t note, int8_t bend)
{
    uint8_t block = note / 12;
    uint8_t semi = note % 12;
    uint16_t f = fnum_tab[semi];
    uint8_t bank = chan >> 1;
    uint8_t r = (uint8_t) (1 + (chan & 1));

    /* bend / 128 of a semitone is about bend * f / 2048. */
    if (bend >= 0)
        f += (uint16_t) ((uint16_t) (f >> 4) * (uint8_t) bend) >> 7;
    else
        f -= (uint16_t) ((uint16_t) (f >> 4) * (uint8_t) -bend) >> 7;
    if (f >= 2048) {
        f >>= 1;
        block++;
    }
    if (block > 7)
        block = 7;
    out(bank ? PORT_YM_ADDR_B : PORT_YM_ADDR_A, (uint8_t) (0xA4 + r));
    out(bank ? PORT_YM_DATA_B : PORT_YM_DATA_A,
        (uint8_t) ((block << 3) | (f >> 8)));
    out(bank ? PORT_YM_ADDR_B : PORT_YM_ADDR_A, (uint8_t) (0xA0 + r));
    out(bank ? PORT_YM_DATA_B : PORT_YM_DATA_A, (uint8_t) f);
}

void ssg_note(uint8_t chan, uint8_t note)
{
    uint8_t oct = note / 12;
    uint16_t period = ssg_tab[note % 12] >> oct;

    out(PORT_YM_ADDR_A, (uint8_t) (chan << 1));
    out(PORT_YM_DATA_A, (uint8_t) period);
    out(PORT_YM_ADDR_A, (uint8_t) ((chan << 1) + 1));
    out(PORT_YM_DATA_A, (uint8_t) ((period >> 8) & 0x0F));
}

int main(void)
{
    uint16_t k, n = reps;
    uint8_t note, sc = 0;

    intrinsic_label(TIMER_START);
    for (k = 0; k < n; k++) {
        for (note = 0; note < 96; note++) {
            fm_note(note & 3, note, bend_tab[(note + k) & 15]);
            ssg_note(sc, note);
            if (++sc == 3)
                sc = 0;
        }
    }
    intrinsic_label(TIMER_STOP);
    snd_report("fnum");
    return 0;
}
