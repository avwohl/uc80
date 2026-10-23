/* lz4dec.c - LZ4 block decoder benchmark.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * A small LZ4 block decoder written for this benchmark (the format is
 * described in lz4/doc/lz4_Block_format.md).  It decodes lz4_blob, one
 * LZ4 block made by bench/programs/zx0/mkblobs.py from an 8000-byte
 * sample, and prints the size and CRC-16/CCITT-FALSE of the output; the
 * zx0 program decodes the same sample.  The copy loops are plain byte
 * loops, so the benchmark measures compiled code, not a library memcpy.
 */
#include <stdio.h>
#include <stddef.h>
#include <stdint.h>
#include "lz4blob.h"

static uint8_t out[LZ4_OUT_SIZE];

/* Read the count through a volatile, so no compiler can fold the work. */
volatile unsigned char lz4_reps = 4;

/* Decode one block; return the output length, or 0 for a bad block. */
static size_t lz4_decode(const uint8_t *src, size_t srclen, uint8_t *dst,
                         size_t dstcap)
{
    const uint8_t *ip = src;
    const uint8_t *iend = src + srclen;
    uint8_t *op = dst;
    uint8_t *oend = dst + dstcap;
    const uint8_t *match;
    uint8_t token, b;
    uint16_t len, off;

    for (;;) {
        token = *ip++;
        /* Literals. */
        len = token >> 4;
        if (len == 15) {
            do {
                b = *ip++;
                len += b;
            } while (b == 255);
        }
        if (len > (size_t) (iend - ip) || len > (size_t) (oend - op))
            return 0;
        while (len--)
            *op++ = *ip++;
        if (ip >= iend)
            break;              /* the last sequence has no match */
        /* Match: 16-bit little-endian offset, length + 4. */
        off = (uint16_t) (ip[0] | ((uint16_t) ip[1] << 8));
        ip += 2;
        if (off == 0 || off > (size_t) (op - dst))
            return 0;
        match = op - off;
        len = token & 15;
        if (len == 15) {
            do {
                b = *ip++;
                len += b;
            } while (b == 255);
        }
        len += 4;
        if (len > (size_t) (oend - op))
            return 0;
        while (len--)
            *op++ = *match++;
    }
    return (size_t) (op - dst);
}

static unsigned int crc16(const uint8_t *p, size_t n)
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
    size_t n = 0;

    intrinsic_label(TIMER_START);
    for (r = lz4_reps; r != 0; r--)
        n = lz4_decode(lz4_blob, sizeof lz4_blob, out, sizeof out);
    intrinsic_label(TIMER_STOP);
    printf("lz4: %u bytes, CRC %u\n", (unsigned int) n, crc16(out, n));
    return 0;
}
