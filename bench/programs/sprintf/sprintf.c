/* sprintf.c - speed of the library sprintf() integer and string
 * conversions (uc80 benchmark suite).
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Aaron Wohl
 *
 * z88dk support/benchmarks/sprintf has only a readme ("Test the speed of
 * stdio format conversions") and no source, so this program is our own.
 *
 * The timed region formats a table of int, unsigned and long values and
 * strings with sprintf(), nrep times, and keeps a checksum of every
 * character and every return value.  After the timed region the program
 * prints the checksum and one line of each format.  It uses only the
 * conversions that the default printf of each compiler under test
 * supports: d u x X o c s ld lu lx, with width, '-' and '0' flags.
 * The '0' flag is used on unsigned values only: the z88dk 2.4 classic
 * printf puts the zeros before the sign ("%06d" of -1 gives "0000-1").
 * No float.  The program is 16-bit int clean.
 *
 * Defines:
 *   -DTIMER      put the TIMER_START/TIMER_STOP labels around the work
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

#define NVAL 8

/* volatile, so the work is done at run time. */
volatile unsigned char nrep = 4;

static const int ival[NVAL] = { 0, 1, -1, 42, -1234, 32767, -32767, 9 };
static const unsigned uval[NVAL] = { 0, 1, 255, 4096, 40000, 65535, 12345, 7 };
static const long lval[NVAL] = { 0L, 1L, -1L, 65536L, -100000L,
                                 2147483647L, -2147483647L, 123456789L };
static const unsigned long ulval[NVAL] = { 0UL, 1UL, 4294967295UL, 65536UL,
                                          4294867296UL, 2147483647UL,
                                          2147483649UL, 123456789UL };
static const char *const sval[NVAL] = { "", "a", "Z80", "CP/M", "uc80",
                                        "hello, world", "0123456789", "x y" };

char buf[64];
uint16_t sum;
uint16_t nchars;

static void add(int n)
{
    const char *p = buf;
    nchars += (uint16_t)n;
    while (*p)
        /* sum * 33 + c.  Written as a multiply: z88dk 2.4 zsdcc -SO3
         * miscompiles the (sum << 5) + sum form of the same hash. */
        sum = (uint16_t)(sum * 33u + (unsigned char)*p++);
}

static void one(unsigned char i)
{
    add(sprintf(buf, "%d", ival[i]));
    add(sprintf(buf, "%6d|%-6d|%06u", ival[i], ival[i], uval[i]));
    add(sprintf(buf, "%u %x %X %o", uval[i], uval[i], uval[i], uval[i]));
    add(sprintf(buf, "%04x:%8u", uval[i], uval[i]));
    add(sprintf(buf, "%ld", lval[i]));
    add(sprintf(buf, "%lu %lx", ulval[i], ulval[i]));
    add(sprintf(buf, "%12ld|%-12ld|", lval[i], lval[i]));
    add(sprintf(buf, "%s|%12s|%-12s|", sval[i], sval[i], sval[i]));
    add(sprintf(buf, "%c%c%c", 'A' + i, 'a' + i, '0' + i));
    add(sprintf(buf, "[%s=%d,%u,%ld]", sval[i], ival[i], uval[i], lval[i]));
}

int main(void)
{
    unsigned char r, i;

    TIMER_START();
    for (r = 0; r < nrep; r++)
        for (i = 0; i < NVAL; i++)
            one(i);
    TIMER_STOP();

    printf("chars %u sum %04x\n", nchars, sum);
    for (i = 0; i < NVAL; i++) {
        sprintf(buf, "%6d|%-6d|%06u", ival[i], ival[i], uval[i]);
        printf("%s\n", buf);
        sprintf(buf, "%u %x %X %o", uval[i], uval[i], uval[i], uval[i]);
        printf("%s\n", buf);
        sprintf(buf, "%12ld|%-12ld|", lval[i], lval[i]);
        printf("%s\n", buf);
        sprintf(buf, "%lu %lx", ulval[i], ulval[i]);
        printf("%s\n", buf);
        sprintf(buf, "%s|%12s|%-12s|", sval[i], sval[i], sval[i]);
        printf("%s\n", buf);
    }
    return 0;
}
