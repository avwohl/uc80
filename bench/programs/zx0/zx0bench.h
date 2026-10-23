/* zx0bench.h - turns the ZX0 decompressor (dzx0.c) into a benchmark.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The harness edits dzx0.c (github.com/einar-saukas/ZX0, BSD-3-Clause)
 * so that its includes become this file, its input buffer is the
 * embedded zx0_blob, its output buffer is zx0_out, its file write is
 * zx0_sink () and its main () is left out.  decompress () itself is not
 * changed.  main () below times the decompression and prints the size and
 * CRC-16/CCITT-FALSE of the output.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "zx0blob.h"

/* dzx0.c calls exit (1) on bad input.  The SDCC stdlib.h does not
 * declare exit, and z88dk makes exit a macro, so the calls go to
 * zx0_exit (), which only records the error; main () prints it.  A
 * correct build never gets here. */
#undef exit
#define exit(n) zx0_exit(n)

static int zx0_error;

static void zx0_exit(int status)
{
    zx0_error = status;
}

/* dzx0.c reports errors with fprintf (stderr, ...); SDCC has no FILE or
 * stderr, so the message goes to a function that drops it.  The exit (1)
 * after it stays.  (A plain variadic function, not a C99 variadic macro,
 * so that old compilers take it too.) */
#undef fprintf
#undef stderr
#define fprintf zx0_report
#define stderr 0

static int zx0_report(int unit, const char *fmt, ...)
{
    (void) unit;
    (void) fmt;
    return 0;
}

/* The output fits in the buffer, so the decoder never wraps around it. */
#define ZX0_BUFFER_SIZE 8192

static unsigned char zx0_out[ZX0_BUFFER_SIZE];
static size_t zx0_out_len;

static size_t zx0_sink(const unsigned char *data, size_t len)
{
    (void) data;
    zx0_out_len += len;
    return len;
}

/* Read the count through a volatile, so no compiler can fold the work. */
volatile unsigned char zx0_reps = 1;

void decompress(int classic_mode);

static unsigned int crc16(const unsigned char *p, size_t n)
{
    unsigned int crc = 0xFFFF;
    unsigned char i;

    while (n--) {
        crc ^= (unsigned int) *p++ << 8;
        for (i = 0; i < 8; i++)
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
        crc &= 0xFFFF;
    }
    return crc;
}

int main(void)
{
    unsigned char r;

    intrinsic_label(TIMER_START);
    for (r = zx0_reps; r != 0; r--) {
        zx0_out_len = 0;
        decompress(0);
    }
    intrinsic_label(TIMER_STOP);
    if (zx0_error)
        printf("zx0: bad data\n");
    printf("zx0: %u bytes, CRC %u\n", (unsigned int) zx0_out_len,
           crc16(zx0_out, zx0_out_len));
    return 0;
}
