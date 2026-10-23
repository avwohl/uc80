/* cmdq.c - sound-driver command queue kernel.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The shape of a Neo Geo sound driver's command path: the NMI handler
 * takes a command byte from the 68k, puts it in a 16-entry ring buffer
 * (volatile head and tail) and sends a reply; the main loop takes
 * commands out and dispatches them with a switch.  Here a 16-bit LFSR
 * makes the command bytes and the size of each burst, so the queue is
 * sometimes empty and sometimes full.
 */
#include "snd.h"

#define QLEN 16

static uint8_t q_buf[QLEN];
static volatile uint8_t q_head;         /* written by the NMI side */
static volatile uint8_t q_tail;         /* written by the main loop */
static uint16_t q_drops;

static uint16_t rng = 0xACE1;

/* Read the frame count through a volatile, so no compiler can fold the
 * work. */
volatile uint16_t frames = 2000;

static uint8_t rng_step(void)
{
    if (rng & 1)
        rng = (rng >> 1) ^ 0xB400;
    else
        rng >>= 1;
    return (uint8_t) rng;
}

/* NMI side: queue one command and acknowledge it. */
static void nmi_cmd(uint8_t cmd)
{
    uint8_t h = q_head;
    uint8_t next = (uint8_t) ((h + 1) & (QLEN - 1));

    if (next == q_tail) {
        q_drops++;
    } else {
        q_buf[h] = cmd;
        q_head = next;
    }
    out(PORT_REPLY, cmd);
}

/* Main-loop side: return the next command, or -1 if the queue is empty. */
static int cmd_pop(void)
{
    uint8_t t = q_tail;
    uint8_t c;

    if (t == q_head)
        return -1;
    c = q_buf[t];
    q_tail = (uint8_t) ((t + 1) & (QLEN - 1));
    return c;
}

static uint8_t master_vol = 0x3F;
static uint8_t adpcm_vol[6];
static uint8_t music_on;
static uint8_t tempo = 120;

static void dispatch(uint8_t cmd)
{
    uint8_t n = cmd & 7;

    switch (cmd >> 4) {
    case 0x0:                   /* nop */
        break;
    case 0x1:                   /* ADPCM-A sample start, channel n % 6 */
        if (n > 5)
            n -= 6;
        out(PORT_YM_ADDR_B, 0x08 + n);
        out(PORT_YM_DATA_B, (uint8_t) (0xC0 | adpcm_vol[n]));
        out(PORT_YM_ADDR_B, 0x00);
        out(PORT_YM_DATA_B, (uint8_t) (1 << n));
        break;
    case 0x2:                   /* ADPCM-A stop */
        if (n > 5)
            n -= 6;
        out(PORT_YM_ADDR_B, 0x00);
        out(PORT_YM_DATA_B, (uint8_t) (0x80 | (1 << n)));
        break;
    case 0x3:                   /* ADPCM-A channel volume */
        if (n > 5)
            n -= 6;
        adpcm_vol[n] = (uint8_t) ((cmd & 0x0F) << 1);
        break;
    case 0x4:                   /* master volume down */
        if (master_vol > 3)
            master_vol -= 4;
        out(PORT_YM_ADDR_B, 0x01);
        out(PORT_YM_DATA_B, master_vol);
        break;
    case 0x5:                   /* master volume up */
        if (master_vol < 0x3C)
            master_vol += 4;
        out(PORT_YM_ADDR_B, 0x01);
        out(PORT_YM_DATA_B, master_vol);
        break;
    case 0x6:                   /* music start */
        music_on = 1;
        out(PORT_YM_ADDR_A, 0x27);
        out(PORT_YM_DATA_A, 0x3F);
        break;
    case 0x7:                   /* music stop: key off all FM channels */
        music_on = 0;
        for (n = 0; n < 4; n++) {
            out(PORT_YM_ADDR_A, 0x28);
            out(PORT_YM_DATA_A, (uint8_t) (n < 2 ? n + 1 : n + 3));
        }
        break;
    case 0x8:                   /* tempo */
    case 0x9:
        tempo = (uint8_t) (60 + ((cmd & 0x1F) << 2));
        out(PORT_YM_ADDR_A, 0x26);
        out(PORT_YM_DATA_A, (uint8_t) (256 - tempo));
        break;
    case 0xA:                   /* SSG tone, channel n % 3 */
    case 0xB:
        n = (uint8_t) (cmd % 3);
        out(PORT_YM_ADDR_A, (uint8_t) (n << 1));
        out(PORT_YM_DATA_A, cmd);
        out(PORT_YM_ADDR_A, (uint8_t) (8 + n));
        out(PORT_YM_DATA_A, 0x0F);
        break;
    case 0xC:                   /* sound effect: FM channel 4 */
        out(PORT_YM_ADDR_B, 0xA6);
        out(PORT_YM_DATA_B, (uint8_t) (0x20 | n));
        out(PORT_YM_ADDR_B, 0xA2);
        out(PORT_YM_DATA_B, cmd);
        out(PORT_YM_ADDR_A, 0x28);
        out(PORT_YM_DATA_A, 0xF6);
        break;
    case 0xD:                   /* reset request: drain the queue */
        while (cmd_pop() >= 0)
            ;
        break;
    default:                    /* 0xE, 0xF: reply with status */
        out(PORT_REPLY, (uint8_t) (music_on | (master_vol << 1)));
        break;
    }
}

int main(void)
{
    uint16_t f;
    uint8_t burst, k, v;
    int c;

    intrinsic_label(TIMER_START);
    for (f = frames; f != 0; f--) {
        /* Commands that arrive during this frame: 0 to 3, and one frame
         * in 8 a burst of 12 that fills the queue. */
        burst = rng_step();
        burst = (burst & 0x70) == 0 ? 12 : (uint8_t) (burst & 3);
        for (k = 0; k < burst; k++) {
            v = rng_step();
            nmi_cmd((uint8_t) (v ^ (uint8_t) (rng >> 8)));
        }
        /* The main loop handles at most 3 commands per frame. */
        for (k = 0; k < 3; k++) {
            c = cmd_pop();
            if (c < 0)
                break;
            dispatch((uint8_t) c);
        }
    }
    intrinsic_label(TIMER_STOP);
    printf("drops %u, head %u, tail %u\n", (unsigned int) q_drops,
           (unsigned int) q_head, (unsigned int) q_tail);
    snd_report("cmdq");
    return 0;
}
