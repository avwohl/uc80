/* tak.c - recursive call benchmarks: Takeuchi, Ackermann and Fibonacci
 * (uc80 benchmark suite).
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Aaron Wohl
 *
 * tak(18,12,6) = 7 with 63,609 calls.  ack(2,n) = 2n+3 and
 * ack(3,n) = 2^(n+3)-3; the sizes keep each result and call count in
 * 16 bits and the recursion depth under about 260 frames, so the
 * program fits the CP/M stack.  fib(20) = 6765 with 21,891 calls.
 * Each function counts its calls in a 16-bit global.  The arguments
 * come from volatile variables.
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

volatile int tx = 18, ty = 12, tz = 6;
volatile int a2n = 40, a3n = 5;
volatile int fn = 20;

uint16_t tak_calls, ack_calls, fib_calls;

static int tak(int x, int y, int z)
{
    tak_calls++;
    if (y < x)
        return tak(tak(x - 1, y, z), tak(y - 1, z, x), tak(z - 1, x, y));
    return z;
}

static int ack(int m, int n)
{
    ack_calls++;
    if (m == 0)
        return n + 1;
    if (n == 0)
        return ack(m - 1, 1);
    return ack(m - 1, ack(m, n - 1));
}

static int fib(int n)
{
    fib_calls++;
    if (n < 2)
        return n;
    return fib(n - 1) + fib(n - 2);
}

/* ---- output, with putchar only (printf differs between libraries) ---- */

static void put_str(const char *s)
{
    while (*s)
        putchar(*s++);
}

static void put_dec(uint16_t v)
{
    char d[6];
    uint8_t i = 0;
    do {
        d[i++] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v);
    while (i)
        putchar(d[--i]);
}

static void line(const char *name, int value, uint16_t calls)
{
    put_str(name);
    put_str(" = ");
    put_dec((uint16_t)value);
    put_str(", calls ");
    put_dec(calls);
    putchar('\n');
}

int r_tak, r_ack2, r_ack3, r_fib;
uint16_t c_tak, c_ack2, c_ack3, c_fib;

int main(void)
{
    TIMER_START();
    tak_calls = 0;
    r_tak = tak(tx, ty, tz);
    c_tak = tak_calls;
    ack_calls = 0;
    r_ack2 = ack(2, a2n);
    c_ack2 = ack_calls;
    ack_calls = 0;
    r_ack3 = ack(3, a3n);
    c_ack3 = ack_calls;
    fib_calls = 0;
    r_fib = fib(fn);
    c_fib = fib_calls;
    TIMER_STOP();

    line("tak(18,12,6)", r_tak, c_tak);
    line("ack(2,40)", r_ack2, c_ack2);
    line("ack(3,5)", r_ack3, c_ack3);
    line("fib(20)", r_fib, c_fib);
    return 0;
}
