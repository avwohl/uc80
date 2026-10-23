/* snd.h - port log shared by the snd-* sound-driver kernels.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The kernels have the shape of Z80 sound-driver code (a Neo Geo YM2610
 * driver, for example), but run as CP/M programs.  Port I/O is a call to
 * the tiny function out (port, value), which stores the pair in a ring of
 * the last LOG_LEN writes.  in (port) returns a YM2610 status byte whose
 * busy bit is set for the first polls after each write.  After the timed
 * region, snd_report () prints the write count and a CRC-16/CCITT-FALSE
 * of the ring, so the work cannot be folded away.
 *
 * Ports: 04h/05h YM2610 address/data port A, 06h/07h port B, 0Ch reply
 * to the 68k (as on the Neo Geo).  in (04h) is the YM2610 status.
 */
#include <stdio.h>
#include <stdint.h>

#define PORT_YM_ADDR_A 0x04
#define PORT_YM_DATA_A 0x05
#define PORT_YM_ADDR_B 0x06
#define PORT_YM_DATA_B 0x07
#define PORT_REPLY     0x0C

#define LOG_LEN 256             /* (port, value) pairs, a power of 2 */

uint8_t snd_log[LOG_LEN * 2];
uint16_t snd_writes;            /* all writes, mod 65536 */
uint8_t snd_busy;               /* polls left before the YM2610 is ready */
uint16_t snd_polls;

void out(uint8_t port, uint8_t value)
{
    uint8_t *p = &snd_log[(snd_writes & (LOG_LEN - 1)) * 2];

    p[0] = port;
    p[1] = value;
    snd_writes++;
    if ((port & 0xF8) == 0)
        snd_busy = 2;
}

uint8_t in(uint8_t port)
{
    snd_polls++;
    if (port == PORT_YM_ADDR_A && snd_busy != 0) {
        snd_busy--;
        return 0x80;
    }
    return 0x00;
}

void snd_report(const char *name)
{
    unsigned int crc = 0xFFFF;
    unsigned int i;
    unsigned char b;

    for (i = 0; i < sizeof snd_log; i++) {
        crc ^= (unsigned int) snd_log[i] << 8;
        for (b = 0; b < 8; b++)
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
        crc &= 0xFFFF;
    }
    printf("%s: %u writes, %u polls, log CRC %u\n", name,
           (unsigned int) snd_writes, (unsigned int) snd_polls, crc);
}
