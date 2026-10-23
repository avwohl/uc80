/* crc.c - CRC-16/CCITT-FALSE and CRC-32, bitwise and table driven
 * (uc80 benchmark suite).
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Aaron Wohl
 *
 * The timed region builds both tables and runs all four CRC routines
 * over a generated buffer.  After the timed region the program prints
 * the published check values for "123456789" (CRC-16/CCITT-FALSE 29b1,
 * CRC-32 cbf43926) and the buffer CRCs.  The program is 16-bit int
 * clean and prints with putchar only.
 *
 * Defines:
 *   -DTIMER      put the TIMER_START/TIMER_STOP labels in main
 */
#include <stdio.h>
#include <stdint.h>

#ifdef TIMER
#define TIMER_START() intrinsic_label(TIMER_START)
#define TIMER_STOP()  intrinsic_label(TIMER_STOP)
#else
#define TIMER_START()
#define TIMER_STOP()
#endif

#define BUFMAX 1024

/* Problem sizes.  volatile, so they are read at run time. */
volatile uint16_t buflen = 1024;
volatile uint16_t nrep = 1;

uint8_t buf[BUFMAX];
uint16_t tab16[256];
uint32_t tab32[256];

static const uint8_t check[9] = { '1', '2', '3', '4', '5', '6', '7', '8', '9' };

static void fill(void)
{
    uint16_t r = 0xACE1u, i;
    for (i = 0; i < BUFMAX; i++) {
        r ^= (uint16_t)(r << 7);
        r ^= (uint16_t)(r >> 9);
        r ^= (uint16_t)(r << 8);
        buf[i] = (uint8_t)(r >> 3);
    }
}

/* CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection. */
static uint16_t crc16_bit(const uint8_t *p, uint16_t len)
{
    uint16_t crc = 0xFFFFu;
    uint8_t k;
    while (len--) {
        crc ^= (uint16_t)((uint16_t)*p++ << 8);
        for (k = 0; k < 8; k++) {
            if (crc & 0x8000u)
                crc = (uint16_t)((uint16_t)(crc << 1) ^ 0x1021u);
            else
                crc = (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static void crc16_init(void)
{
    uint16_t i;
    uint8_t k;
    for (i = 0; i < 256; i++) {
        uint16_t crc = (uint16_t)(i << 8);
        for (k = 0; k < 8; k++) {
            if (crc & 0x8000u)
                crc = (uint16_t)((uint16_t)(crc << 1) ^ 0x1021u);
            else
                crc = (uint16_t)(crc << 1);
        }
        tab16[i] = crc;
    }
}

static uint16_t crc16_tab(const uint8_t *p, uint16_t len)
{
    uint16_t crc = 0xFFFFu;
    while (len--)
        crc = (uint16_t)((uint16_t)(crc << 8) ^ tab16[(uint8_t)((crc >> 8) ^ *p++)]);
    return crc;
}

/* CRC-32 (ISO-HDLC): reflected poly 0xEDB88320, init and xorout 0xFFFFFFFF. */
static uint32_t crc32_bit(const uint8_t *p, uint16_t len)
{
    uint32_t crc = 0xFFFFFFFFUL;
    uint8_t k;
    while (len--) {
        crc ^= *p++;
        for (k = 0; k < 8; k++) {
            if (crc & 1u)
                crc = (crc >> 1) ^ 0xEDB88320UL;
            else
                crc >>= 1;
        }
    }
    return crc ^ 0xFFFFFFFFUL;
}

static void crc32_init(void)
{
    uint16_t i;
    uint8_t k;
    for (i = 0; i < 256; i++) {
        uint32_t crc = i;
        for (k = 0; k < 8; k++) {
            if (crc & 1u)
                crc = (crc >> 1) ^ 0xEDB88320UL;
            else
                crc >>= 1;
        }
        tab32[i] = crc;
    }
}

static uint32_t crc32_tab(const uint8_t *p, uint16_t len)
{
    uint32_t crc = 0xFFFFFFFFUL;
    while (len--)
        crc = (crc >> 8) ^ tab32[(uint8_t)(crc ^ *p++)];
    return crc ^ 0xFFFFFFFFUL;
}

/* ---- output, with putchar only (printf differs between libraries) ---- */

static void put_str(const char *s)
{
    while (*s)
        putchar(*s++);
}

static void put_hex(uint32_t v, uint8_t digits)
{
    while (digits) {
        uint8_t d;
        digits--;
        d = (uint8_t)((v >> (digits * 4)) & 15u);
        putchar(d < 10 ? '0' + d : 'a' + d - 10);
    }
}

static void line(const char *name, uint32_t v, uint8_t digits)
{
    put_str(name);
    putchar(' ');
    put_hex(v, digits);
    putchar('\n');
}

uint16_t r16b, r16t;
uint32_t r32b, r32t;

int main(void)
{
    uint16_t rep, len;

    fill();

    TIMER_START();
    len = buflen;
    crc16_init();
    crc32_init();
    for (rep = 0; rep < nrep; rep++) {
        r16b = crc16_bit(buf, len);
        r16t = crc16_tab(buf, len);
        r32b = crc32_bit(buf, len);
        r32t = crc32_tab(buf, len);
    }
    TIMER_STOP();

    line("crc16 check bit", crc16_bit(check, 9), 4);
    line("crc16 check tab", crc16_tab(check, 9), 4);
    line("crc32 check bit", crc32_bit(check, 9), 8);
    line("crc32 check tab", crc32_tab(check, 9), 8);
    line("crc16 buffer bit", r16b, 4);
    line("crc16 buffer tab", r16t, 4);
    line("crc32 buffer bit", r32b, 8);
    line("crc32 buffer tab", r32t, 8);
    line("crc16 table[255]", tab16[255], 4);
    line("crc32 table[255]", tab32[255], 8);
    return 0;
}
