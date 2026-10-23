/* seq.c - sound-driver sequencer tick.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * One tick of a music driver for 4 FM and 3 SSG channels: fetch pattern
 * bytes through a const uint8_t pointer, decode them with a dense switch,
 * apply vibrato from an 8-bit sine table with a 16-bit fixed-point add
 * (pitch in 12.4 fixed point), and write only the registers that changed
 * (shadow compare).
 *
 * Pattern bytes: 00h-5Fh note (then a duration byte); 60h rest (then a
 * duration); 61h key off (then a duration); 62h instrument; 63h volume;
 * 64h vibrato (depth << 4 | speed); 65h transpose (signed); 66h loop
 * point; 67h jump to the loop point.
 */
#include "snd.h"

#define NCH 7                   /* 0-3 FM, 4-6 SSG */

enum { P_REST = 0x60, P_KEYOFF, P_INST, P_VOL, P_VIB, P_TRANS, P_LOOP,
       P_JUMP };

struct chan {
    const uint8_t *pos;
    const uint8_t *loop;
    uint8_t wait;
    uint8_t note;
    uint8_t inst;
    uint8_t vol;
    int8_t transpose;
    uint8_t vib_depth;
    uint8_t vib_speed;
    uint8_t vib_phase;
    uint8_t keyon;              /* 1: key on at this tick, 2: key off */
    uint16_t base;              /* FM block/F-number or SSG period */
};

static struct chan ch[NCH];
static uint8_t shadow[2][256];

/* Read the tick count through a volatile, so no compiler can fold the
 * work. */
volatile uint16_t ticks = 500;

static const uint8_t pat_fm0[] = {
    P_INST, 1, P_VOL, 0x10, P_VIB, 0x23, P_LOOP,
    36, 12, 43, 6, 48, 6, P_KEYOFF, 4, 46, 8, 43, 4, P_REST, 8,
    P_TRANS, 2, 36, 12, 43, 6, 48, 6, P_TRANS, 0, 51, 16, P_JUMP
};
static const uint8_t pat_fm1[] = {
    P_INST, 2, P_VOL, 0x18, P_VIB, 0x00, P_LOOP,
    24, 3, 24, 3, 36, 3, 24, 3, 31, 3, 24, 3, 34, 3, 36, 3, P_JUMP
};
static const uint8_t pat_fm2[] = {
    P_INST, 3, P_VOL, 0x20, P_VIB, 0x41, P_LOOP,
    60, 24, 63, 24, P_VIB, 0x62, 67, 48, P_KEYOFF, 24, P_VIB, 0x41, P_JUMP
};
static const uint8_t pat_fm3[] = {
    P_INST, 4, P_VOL, 0x08, P_VIB, 0x15, P_REST, 5, P_LOOP,
    72, 2, 75, 2, 79, 2, 84, 2, 79, 2, 75, 2, P_TRANS, 0xFE,
    72, 2, 75, 2, 79, 2, 84, 2, P_TRANS, 0, P_JUMP
};
static const uint8_t pat_ssg0[] = {
    P_VOL, 0x0F, P_VIB, 0x34, P_LOOP,
    48, 6, 50, 6, 52, 6, 53, 6, 55, 12, P_KEYOFF, 6, P_JUMP
};
static const uint8_t pat_ssg1[] = {
    P_VOL, 0x0C, P_VIB, 0x00, P_LOOP,
    P_REST, 3, 36, 1, P_KEYOFF, 2, 38, 1, P_KEYOFF, 2, P_JUMP
};
static const uint8_t pat_ssg2[] = {
    P_VOL, 0x0A, P_VIB, 0x27, P_LOOP,
    84, 4, 88, 4, 91, 4, 96 - 1, 4, P_VOL, 0x08, 91, 8, P_VOL, 0x0A, P_JUMP
};

static const uint8_t *const patterns[NCH] = {
    pat_fm0, pat_fm1, pat_fm2, pat_fm3, pat_ssg0, pat_ssg1, pat_ssg2
};

/* 8-bit sine, 64 entries per quarter wave folded at run time. */
static const int8_t sine_q[65] = {
    0, 3, 6, 9, 12, 16, 19, 22, 25, 28, 31, 34, 37, 40, 43, 46,
    49, 51, 54, 57, 60, 63, 65, 68, 71, 73, 76, 78, 81, 83, 85, 88,
    90, 92, 94, 96, 98, 100, 102, 104, 106, 107, 109, 111, 112, 113, 115,
    116, 117, 118, 120, 121, 122, 122, 123, 124, 125, 125, 126, 126, 126,
    127, 127, 127, 127
};

static int8_t sine8(uint8_t phase)
{
    uint8_t q = phase & 63;
    int8_t v;

    if (phase & 64)
        q = (uint8_t) (64 - q);
    v = sine_q[q];
    return (phase & 128) ? (int8_t) -v : v;
}

/* YM2610 F-numbers for one octave (block 4 range) and SSG periods for
 * octave 0. */
static const uint16_t fnum_tab[12] = {
    617, 654, 693, 734, 778, 824, 873, 925, 980, 1038, 1100, 1165
};
static const uint16_t ssg_tab[12] = {
    3822, 3608, 3405, 3214, 3034, 2863, 2703, 2551, 2408, 2273, 2145, 2025
};

static void reg_set(uint8_t bank, uint8_t reg, uint8_t val)
{
    if (shadow[bank][reg] != val) {
        shadow[bank][reg] = val;
        out(bank ? PORT_YM_ADDR_B : PORT_YM_ADDR_A, reg);
        out(bank ? PORT_YM_DATA_B : PORT_YM_DATA_A, val);
    }
}

static uint16_t note_base(uint8_t n, uint8_t note)
{
    uint8_t oct = 0;

    while (note >= 12) {
        note -= 12;
        oct++;
    }
    if (n < 4)
        return (uint16_t) ((uint16_t) (oct & 7) << 11) | fnum_tab[note];
    return ssg_tab[note] >> oct;
}

/* Run the pattern of channel n until the next note, rest or key off. */
static void fetch(uint8_t n, struct chan *c)
{
    uint8_t b;

    for (;;) {
        b = *c->pos++;
        if (b < P_REST) {
            c->note = (uint8_t) (b + c->transpose);
            c->base = note_base(n, c->note);
            c->wait = *c->pos++;
            c->vib_phase = 0;
            c->keyon = 1;
            return;
        }
        switch (b) {
        case P_REST:
            c->wait = *c->pos++;
            return;
        case P_KEYOFF:
            c->wait = *c->pos++;
            c->keyon = 2;
            return;
        case P_INST:
            c->inst = *c->pos++;
            break;
        case P_VOL:
            c->vol = *c->pos++;
            break;
        case P_VIB:
            b = *c->pos++;
            c->vib_depth = b >> 4;
            c->vib_speed = b & 15;
            break;
        case P_TRANS:
            c->transpose = (int8_t) *c->pos++;
            break;
        case P_LOOP:
            c->loop = c->pos;
            break;
        case P_JUMP:
            c->pos = c->loop;
            break;
        default:
            c->wait = 1;        /* bad byte: stop this channel */
            c->pos--;
            return;
        }
    }
}

static void tick(void)
{
    uint8_t n, bank, r, kc;
    uint16_t pitch;
    struct chan *c = ch;

    for (n = 0; n < NCH; n++, c++) {
        if (--c->wait == 0)
            fetch(n, c);
        /* Vibrato: 12.4 fixed-point pitch plus sine * depth. */
        c->vib_phase = (uint8_t) (c->vib_phase + c->vib_speed);
        if (n < 4) {
            pitch = (uint16_t) ((uint16_t) ((c->base & 0x7FF) << 4)
                    + (uint16_t) (sine8(c->vib_phase) * c->vib_depth));
            pitch >>= 4;
            bank = n >> 1;
            r = (uint8_t) (1 + (n & 1));
            kc = (uint8_t) (n < 2 ? n + 1 : n + 3);
            reg_set(bank, 0x4C + r, (uint8_t) (c->vol + 0x10 - c->inst));
            reg_set(bank, 0xA4 + r, (uint8_t) (((c->base >> 8) & 0x38)
                                               | (pitch >> 8)));
            reg_set(bank, 0xA0 + r, (uint8_t) pitch);
            if (c->keyon == 1) {
                out(PORT_YM_ADDR_A, 0x28);
                out(PORT_YM_DATA_A, kc);
                out(PORT_YM_ADDR_A, 0x28);
                out(PORT_YM_DATA_A, (uint8_t) (0xF0 | kc));
            } else if (c->keyon == 2) {
                out(PORT_YM_ADDR_A, 0x28);
                out(PORT_YM_DATA_A, kc);
            }
        } else {
            pitch = (uint16_t) (c->base
                    + (uint16_t) (sine8(c->vib_phase) * c->vib_depth / 16));
            r = (uint8_t) ((n - 4) << 1);
            reg_set(0, r, (uint8_t) pitch);
            reg_set(0, (uint8_t) (r + 1), (uint8_t) ((pitch >> 8) & 0x0F));
            reg_set(0, (uint8_t) (8 + n - 4),
                    c->keyon == 2 ? 0 : c->vol);
        }
        c->keyon = 0;
    }
}

int main(void)
{
    uint16_t t;
    uint8_t n;
    unsigned int sum = 0;

    for (n = 0; n < NCH; n++) {
        ch[n].pos = patterns[n];
        ch[n].loop = patterns[n];
        ch[n].wait = 1;
    }
    intrinsic_label(TIMER_START);
    for (t = ticks; t != 0; t--)
        tick();
    intrinsic_label(TIMER_STOP);
    for (n = 0; n < NCH; n++)
        sum = (sum * 31 + ch[n].note + ch[n].wait + ch[n].base) & 0xFFFF;
    printf("state %u\n", sum);
    snd_report("seq");
    return 0;
}
