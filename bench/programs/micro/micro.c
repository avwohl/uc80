/* micro.c - micro benchmarks for Z80 C compilers (uc80 benchmark suite).
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Aaron Wohl
 *
 * Each section runs one kind of operation in a loop and prints one
 * checksum line.  The loop counts come from volatile variables, so no
 * compiler can fold the work away.  The program is 16-bit int clean:
 * it uses <stdint.h> types, never lets a signed value overflow, never
 * shifts a negative value and never depends on the signedness of plain
 * char, so a 32-bit int host build (the golden file) prints the same
 * text as a 16-bit int Z80 build.
 *
 * Signed % always has a non-negative dividend, and the signed 32-bit
 * compares use operands whose difference fits in 32 bits.  z88dk 2.4
 * +cpm (both back ends) gives -7 % 3 as 1, and sccz80 compares signed
 * longs by the sign of their difference.  Those library bugs are not
 * what this program measures, so the operands stay clear of them.
 *
 * Defines:
 *   -DTIMER      put the TIMER_START/TIMER_STOP labels in main
 *   -DTSEC=n     time one group only (0 = all groups, the default):
 *                1 a8, 2 a16, 3 a32, 4 loop, 5 copy, 6 switch, 7 call,
 *                8 bits, 9 volatile
 * All groups always run and print, so the output is the same for every
 * TSEC value.
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

#ifndef TSEC
#define TSEC 0
#endif

/* Problem sizes.  volatile, so they are read at run time. */
volatile uint16_t n8 = 500;
volatile uint16_t n16 = 400;
volatile uint16_t n32 = 100;
volatile uint8_t nloop = 200;
volatile uint16_t nouter = 10;
volatile uint16_t ncopy = 400;
volatile uint16_t nswitch = 1500;
volatile uint16_t ncall = 1000;
volatile uint16_t nbits = 1500;
volatile uint16_t nvol = 1500;

/* Input data, filled by a 16-bit xorshift before the timed region. */
uint8_t d8[64];
uint16_t d16[64];
uint32_t d32[64];

static uint16_t rng = 0xACE1u;

static uint16_t next16(void)
{
    rng ^= (uint16_t)(rng << 7);
    rng ^= (uint16_t)(rng >> 9);
    rng ^= (uint16_t)(rng << 8);
    return rng;
}

static void fill(void)
{
    uint8_t i;
    for (i = 0; i < 64; i++) {
        uint16_t a = next16();
        uint16_t b = next16();
        d8[i] = (uint8_t)a;
        d16[i] = b;
        d32[i] = ((uint32_t)a << 16) | b;
    }
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

static void line16(const char *name, uint16_t v)
{
    put_str(name);
    putchar(' ');
    put_hex(v, 4);
    putchar('\n');
}

static void line32(const char *name, uint32_t v)
{
    put_str(name);
    putchar(' ');
    put_hex(v, 8);
    putchar('\n');
}

/* ---- group 1: 8-bit arithmetic ---- */

static uint16_t add8(uint16_t n)
{
    uint8_t a = 0, b = 1;
    uint16_t i;
    for (i = 0; i < n; i++) {
        a = (uint8_t)(a + d8[i & 63]);
        b = (uint8_t)(b - a);
        b = (uint8_t)(b + d8[(i + 7) & 63]);
    }
    return (uint16_t)(((uint16_t)a << 8) | b);
}

static uint16_t mul8(uint16_t n)
{
    uint8_t p = 1;
    uint16_t w = 0, i;
    for (i = 0; i < n; i++) {
        uint8_t x = d8[i & 63], y = d8[(i + 3) & 63];
        p = (uint8_t)(p * x + 1);
        w = (uint16_t)(w + (uint16_t)x * y);
    }
    return (uint16_t)(w ^ p);
}

static uint16_t div8(uint16_t n)
{
    uint8_t q = 0, r = 0;
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint8_t x = d8[i & 63], y = (uint8_t)(d8[(i + 5) & 63] | 1);
        int8_t sx = (int8_t)(x & 127), sy = (int8_t)((y & 15) - 8);
        q = (uint8_t)(q + x / y);
        r = (uint8_t)(r ^ x % y);
        if (sy != 0)
            q = (uint8_t)(q + (uint8_t)(sx / sy) + (uint8_t)(sx % sy));
    }
    return (uint16_t)(((uint16_t)q << 8) | r);
}

static uint16_t shift8(uint16_t n)
{
    uint8_t a = 0, b = 0;
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint8_t x = d8[i & 63], s = (uint8_t)(i & 7);
        a = (uint8_t)(a + (uint8_t)(x << s) + (uint8_t)(x >> s));
        b = (uint8_t)(b ^ (uint8_t)(x << 1) ^ (uint8_t)(x >> 3));
    }
    return (uint16_t)(((uint16_t)a << 8) | b);
}

static uint16_t cmp8(uint16_t n)
{
    uint16_t c = 0, i;
    for (i = 0; i < n; i++) {
        uint8_t x = d8[i & 63], y = d8[(i + 1) & 63];
        if (x < y)
            c += 1;
        if ((int8_t)x > (int8_t)y)
            c += 3;
        if (x == (uint8_t)(y ^ 0x55))
            c += 7;
        if ((int8_t)x >= 0)
            c += 11;
    }
    return c;
}

/* ---- group 2: 16-bit arithmetic ---- */

static uint16_t add16(uint16_t n)
{
    uint16_t a = 0, b = 1, i;
    for (i = 0; i < n; i++) {
        a = (uint16_t)(a + d16[i & 63]);
        b = (uint16_t)(b - a);
        b = (uint16_t)(b + d16[(i + 7) & 63]);
    }
    return (uint16_t)(a ^ b);
}

static uint16_t mul16(uint16_t n)
{
    uint16_t p = 1, s = 0, i;
    for (i = 0; i < n; i++) {
        uint16_t x = d16[i & 63];
        int16_t sx = (int16_t)(x & 255) - 128;
        int16_t sy = (int16_t)(d16[(i + 3) & 63] & 127) - 64;
        p = (uint16_t)(p * (unsigned)x + 1u);
        s = (uint16_t)(s + (uint16_t)(sx * sy));
    }
    return (uint16_t)(p ^ s);
}

static uint16_t div16(uint16_t n)
{
    uint16_t q = 0, r = 0, i;
    for (i = 0; i < n; i++) {
        uint16_t x = d16[i & 63], y = (uint16_t)((d16[(i + 5) & 63] >> (i & 7)) | 1u);
        int16_t sx = (int16_t)(x >> 1) - 16384;
        int16_t mx = (int16_t)(x >> 2);
        int16_t sy = (int16_t)(y & 1023) - 512;
        q = (uint16_t)(q + x / y);
        r = (uint16_t)(r ^ x % y);
        if (sy != 0)
            q = (uint16_t)(q + (uint16_t)(sx / sy) + (uint16_t)(mx % sy));
    }
    return (uint16_t)(q ^ (uint16_t)(r << 3));
}

static uint16_t shift16(uint16_t n)
{
    uint16_t a = 0, b = 0, i;
    for (i = 0; i < n; i++) {
        uint16_t x = d16[i & 63];
        uint8_t s = (uint8_t)(i & 15);
        a = (uint16_t)(a + (uint16_t)(x << s) + (uint16_t)(x >> s));
        b = (uint16_t)(b ^ (uint16_t)(x << 1) ^ (uint16_t)(x >> 3) ^ (uint16_t)(x << 8));
    }
    return (uint16_t)(a ^ (uint16_t)(b << 1));
}

static uint16_t cmp16(uint16_t n)
{
    uint16_t c = 0, i;
    for (i = 0; i < n; i++) {
        uint16_t x = d16[i & 63], y = d16[(i + 1) & 63];
        if (x < y)
            c += 1;
        if ((int16_t)x > (int16_t)y)
            c += 3;
        if (x == (uint16_t)(y ^ 0x5555u))
            c += 7;
        if ((int16_t)x >= 0)
            c += 11;
        if (x <= 1000u)
            c += 13;
    }
    return c;
}

/* ---- group 3: 32-bit arithmetic ---- */

static uint32_t add32(uint16_t n)
{
    uint32_t a = 0, b = 1;
    uint16_t i;
    for (i = 0; i < n; i++) {
        a += d32[i & 63];
        b -= a;
        b += d32[(i + 7) & 63];
    }
    return a ^ b;
}

static uint32_t mul32(uint16_t n)
{
    uint32_t p = 1, s = 0;
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint32_t x = d32[i & 63];
        int32_t sx = (int32_t)(x & 0xFFFFu) - 32768;
        int32_t sy = (int32_t)(d32[(i + 3) & 63] & 0x7FFFu) - 16384;
        p = p * x + 1u;
        s += (uint32_t)(sx * sy);
        s += (uint32_t)d16[i & 63] * d16[(i + 9) & 63];
    }
    return p ^ s;
}

static uint32_t div32(uint16_t n)
{
    uint32_t q = 0, r = 0;
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint32_t x = d32[i & 63];
        uint32_t y = (d32[(i + 5) & 63] >> (i & 15)) | 1u;
        int32_t sx = (int32_t)(x >> 1) - 0x40000000L;
        int32_t mx = (int32_t)(x >> 2);
        int32_t sy = (int32_t)(y & 0xFFFFu) - 32768;
        q += x / y;
        r ^= x % y;
        q += x / 10u;
        if (sy != 0)
            q += (uint32_t)(sx / sy) + (uint32_t)(mx % sy);
    }
    return q ^ (r << 3);
}

static uint32_t shift32(uint16_t n)
{
    uint32_t a = 0, b = 0;
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint32_t x = d32[i & 63];
        uint8_t s = (uint8_t)(i & 31);
        a += (x << s) + (x >> s);
        b ^= (x << 1) ^ (x >> 3) ^ (x << 8) ^ (x >> 16);
    }
    return a ^ (b << 1);
}

static uint16_t cmp32(uint16_t n)
{
    uint16_t c = 0, i;
    for (i = 0; i < n; i++) {
        uint32_t x = d32[i & 63], y = d32[(i + 1) & 63];
        int32_t sx = (int32_t)(x >> 1) - 0x40000000L;
        int32_t sy = (int32_t)(y >> 1) - 0x40000000L;
        if (x < y)
            c += 1;
        if (sx > sy)
            c += 3;
        if (x == (y ^ 0x55555555UL))
            c += 7;
        if ((int32_t)x >= 0)
            c += 11;
        if (x <= 100000UL)
            c += 13;
    }
    return c;
}

/* ---- group 4: loop counters ---- */

static uint16_t loop_uchar(void)
{
    uint16_t s = 0, k;
    uint8_t m = nloop, i;
    for (k = 0; k < nouter; k++)
        for (i = 0; i < m; i++)
            s = (uint16_t)(s + d8[i & 63]);
    return s;
}

static uint16_t loop_schar(void)
{
    uint16_t s = 0, k;
    int8_t m = (int8_t)(nloop / 2), i;
    for (k = 0; k < nouter; k++)
        for (i = 0; i < m; i++)
            s = (uint16_t)(s + d8[i & 63]);
    return s;
}

static uint16_t loop_uint(void)
{
    uint16_t s = 0, k;
    unsigned int m = nloop, i;
    for (k = 0; k < nouter; k++)
        for (i = 0; i < m; i++)
            s = (uint16_t)(s + d8[i & 63]);
    return s;
}

static uint16_t loop_int(void)
{
    uint16_t s = 0, k;
    int m = nloop, i;
    for (k = 0; k < nouter; k++)
        for (i = 0; i < m; i++)
            s = (uint16_t)(s + d8[i & 63]);
    return s;
}

static uint16_t loop_down(void)
{
    uint16_t s = 0, k;
    for (k = 0; k < nouter; k++) {
        uint8_t i = nloop;
        while (i--)
            s = (uint16_t)(s + d8[i & 63]);
    }
    return s;
}

/* ---- group 5: struct copy ---- */

struct s4 { uint8_t b[4]; };
struct s16 { uint8_t b[16]; };
struct s64 { uint8_t b[64]; };

struct s4 g4[4];
struct s16 g16[4];
struct s64 g64[4];

static void copy_init(void)
{
    uint8_t i, j;
    for (i = 0; i < 4; i++) {
        for (j = 0; j < 4; j++)
            g4[i].b[j] = d8[(i * 4 + j) & 63];
        for (j = 0; j < 16; j++)
            g16[i].b[j] = d8[(i * 16 + j) & 63];
        for (j = 0; j < 64; j++)
            g64[i].b[j] = d8[(i + j) & 63];
    }
}

static uint16_t sum_bytes(const uint8_t *p, uint16_t len)
{
    uint16_t s = 0, i;
    for (i = 0; i < len; i++)
        s = (uint16_t)(s * 31u + p[i]);
    return s;
}

static uint16_t copy4(uint16_t n)
{
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint8_t k = (uint8_t)(i & 3);
        g4[k] = g4[(k + 1) & 3];
        g4[k].b[i & 3] = (uint8_t)(g4[k].b[i & 3] + (uint8_t)i);
    }
    return sum_bytes(&g4[0].b[0], sizeof g4);
}

static uint16_t copy16(uint16_t n)
{
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint8_t k = (uint8_t)(i & 3);
        g16[k] = g16[(k + 1) & 3];
        g16[k].b[i & 15] = (uint8_t)(g16[k].b[i & 15] + (uint8_t)i);
    }
    return sum_bytes(&g16[0].b[0], sizeof g16);
}

static uint16_t copy64(uint16_t n)
{
    uint16_t i;
    for (i = 0; i < n; i++) {
        uint8_t k = (uint8_t)(i & 3);
        g64[k] = g64[(k + 1) & 3];
        g64[k].b[i & 63] = (uint8_t)(g64[k].b[i & 63] + (uint8_t)i);
    }
    return sum_bytes(&g64[0].b[0], sizeof g64);
}

/* ---- group 6: switch ---- */

static uint16_t sw_dense(uint16_t n)
{
    uint16_t s = 0, i;
    for (i = 0; i < n; i++) {
        uint16_t x = d16[i & 63];
        switch (x & 15) {
        case 0: s += 1; break;
        case 1: s ^= x; break;
        case 2: s = (uint16_t)(s + (x >> 2)); break;
        case 3: s = (uint16_t)(s - 7); break;
        case 4: s = (uint16_t)(s << 1); break;
        case 5: s = (uint16_t)(s >> 1); break;
        case 6: s = (uint16_t)(s + 100); break;
        case 7: s ^= 0x1234u; break;
        case 8: s = (uint16_t)(s + i); break;
        case 9: s = (uint16_t)(s - i); break;
        case 10: s = (uint16_t)(s * 3u); break;
        case 11: s = (uint16_t)~s; break;
        case 12: s = (uint16_t)(s + 12345u); break;
        case 13: s ^= (uint16_t)(i << 4); break;
        case 14: s = (uint16_t)(s + (x & 0xFF)); break;
        default: s = (uint16_t)(s - (x >> 8)); break;
        }
    }
    return s;
}

static const uint16_t sparse_keys[16] = {
    1, 7, 20, 100, 255, 1000, 4096, 30000,
    2, 50, 333, 999, 5000, 40000u, 65535u, 12
};

static uint16_t sw_sparse(uint16_t n)
{
    uint16_t s = 0, i;
    for (i = 0; i < n; i++) {
        uint16_t x = sparse_keys[d8[i & 63] & 15];
        switch (x) {
        case 1: s += 1; break;
        case 7: s ^= 0x0F0Fu; break;
        case 20: s = (uint16_t)(s + 20); break;
        case 100: s = (uint16_t)(s << 1); break;
        case 255: s = (uint16_t)(s - 255); break;
        case 1000: s = (uint16_t)(s >> 1); break;
        case 4096: s = (uint16_t)(s + i); break;
        case 30000: s = (uint16_t)~s; break;
        default: s = (uint16_t)(s + 3); break;
        }
    }
    return s;
}

/* ---- group 7: call cost ---- */

uint16_t calls;

static uint16_t f0(void) { return ++calls; }
static uint16_t f1(uint16_t a) { return (uint16_t)(a + 1); }
static uint16_t f2(uint16_t a, uint16_t b) { return (uint16_t)(a ^ b); }
static uint16_t f3(uint16_t a, uint16_t b, uint16_t c)
{
    return (uint16_t)(a + b - c);
}
static uint16_t f4(uint16_t a, uint16_t b, uint16_t c, uint16_t d)
{
    return (uint16_t)((a ^ b) + (c ^ d));
}

static uint16_t call_args(uint16_t n)
{
    uint16_t s = 0, i;
    for (i = 0; i < n; i++) {
        s = (uint16_t)(s + f0());
        s = f1(s);
        s = f2(s, i);
        s = f3(s, i, d16[i & 63]);
        s = f4(s, i, d16[i & 63], 0x55AAu);
    }
    return s;
}

/* ---- group 8: bitfields ---- */

struct bits {
    unsigned int a : 3;
    unsigned int b : 5;
    unsigned int c : 1;
    unsigned int d : 7;
};

struct bits gb;

static uint16_t bitfields(uint16_t n)
{
    uint16_t s = 0, i;
    for (i = 0; i < n; i++) {
        gb.a = (unsigned)(i & 7u);
        gb.b = (unsigned)((gb.b + gb.a) & 31u);
        gb.c = (unsigned)(!gb.c);
        gb.d = (unsigned)((gb.d + (i >> 3)) & 127u);
        s = (uint16_t)(s + gb.a + gb.b + gb.c + gb.d);
        if (gb.c)
            s ^= (uint16_t)gb.d;
    }
    return s;
}

/* ---- group 9: volatile access ---- */

volatile uint8_t vb;
volatile uint16_t vw;
volatile uint32_t vl;

static uint16_t volatiles(uint16_t n)
{
    uint16_t i;
    vb = 0;
    vw = 0;
    vl = 0;
    for (i = 0; i < n; i++) {
        vw = (uint16_t)(vw + i);
        vb = (uint8_t)(vb ^ (uint8_t)vw);
        vl = vl + vb;
        vw = (uint16_t)(vw + vb);
    }
    return (uint16_t)(vw ^ (uint16_t)vl ^ vb);
}

/* ---- results ---- */

uint16_t r_add8, r_mul8, r_div8, r_shift8, r_cmp8;
uint16_t r_add16, r_mul16, r_div16, r_shift16, r_cmp16;
uint32_t r_add32, r_mul32, r_div32, r_shift32;
uint16_t r_cmp32;
uint16_t r_luchar, r_lschar, r_luint, r_lint, r_ldown;
uint16_t r_copy4, r_copy16, r_copy64;
uint16_t r_dense, r_sparse, r_call, r_bits, r_vol;

int main(void)
{
    fill();
    copy_init();

#if TSEC == 0
    TIMER_START();
#endif

#if TSEC == 1
    TIMER_START();
#endif
    r_add8 = add8(n8);
    r_mul8 = mul8(n8);
    r_div8 = div8(n8);
    r_shift8 = shift8(n8);
    r_cmp8 = cmp8(n8);
#if TSEC == 1
    TIMER_STOP();
#endif

#if TSEC == 2
    TIMER_START();
#endif
    r_add16 = add16(n16);
    r_mul16 = mul16(n16);
    r_div16 = div16(n16);
    r_shift16 = shift16(n16);
    r_cmp16 = cmp16(n16);
#if TSEC == 2
    TIMER_STOP();
#endif

#if TSEC == 3
    TIMER_START();
#endif
    r_add32 = add32(n32);
    r_mul32 = mul32(n32);
    r_div32 = div32(n32);
    r_shift32 = shift32(n32);
    r_cmp32 = cmp32(n32);
#if TSEC == 3
    TIMER_STOP();
#endif

#if TSEC == 4
    TIMER_START();
#endif
    r_luchar = loop_uchar();
    r_lschar = loop_schar();
    r_luint = loop_uint();
    r_lint = loop_int();
    r_ldown = loop_down();
#if TSEC == 4
    TIMER_STOP();
#endif

#if TSEC == 5
    TIMER_START();
#endif
    r_copy4 = copy4(ncopy);
    r_copy16 = copy16(ncopy);
    r_copy64 = copy64(ncopy);
#if TSEC == 5
    TIMER_STOP();
#endif

#if TSEC == 6
    TIMER_START();
#endif
    r_dense = sw_dense(nswitch);
    r_sparse = sw_sparse(nswitch);
#if TSEC == 6
    TIMER_STOP();
#endif

#if TSEC == 7
    TIMER_START();
#endif
    r_call = call_args(ncall);
#if TSEC == 7
    TIMER_STOP();
#endif

#if TSEC == 8
    TIMER_START();
#endif
    r_bits = bitfields(nbits);
#if TSEC == 8
    TIMER_STOP();
#endif

#if TSEC == 9
    TIMER_START();
#endif
    r_vol = volatiles(nvol);
#if TSEC == 9
    TIMER_STOP();
#endif

#if TSEC == 0
    TIMER_STOP();
#endif

    line16("add8", r_add8);
    line16("mul8", r_mul8);
    line16("div8", r_div8);
    line16("shift8", r_shift8);
    line16("cmp8", r_cmp8);
    line16("add16", r_add16);
    line16("mul16", r_mul16);
    line16("div16", r_div16);
    line16("shift16", r_shift16);
    line16("cmp16", r_cmp16);
    line32("add32", r_add32);
    line32("mul32", r_mul32);
    line32("div32", r_div32);
    line32("shift32", r_shift32);
    line16("cmp32", r_cmp32);
    line16("loop_uchar", r_luchar);
    line16("loop_schar", r_lschar);
    line16("loop_uint", r_luint);
    line16("loop_int", r_lint);
    line16("loop_down", r_ldown);
    line16("copy4", r_copy4);
    line16("copy16", r_copy16);
    line16("copy64", r_copy64);
    line16("switch_dense", r_dense);
    line16("switch_sparse", r_sparse);
    line16("call0_4", r_call);
    line16("bitfield", r_bits);
    line16("volatile", r_vol);
    return 0;
}
