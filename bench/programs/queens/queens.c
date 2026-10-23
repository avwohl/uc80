/* queens.c - all solutions of the 8-queens problem, repeated
 * (uc80 benchmark suite).
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) 2026 Aaron Wohl
 *
 * A recursive backtracking search with column and diagonal flag arrays
 * finds all 92 solutions.  Each run also folds every solution into a
 * 16-bit checksum.  The board size and the repeat count come from
 * volatile variables.  The program is 16-bit int clean and prints with
 * putchar only.
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

#define NMAX 8

volatile uint8_t nq = 8;
volatile uint16_t nrep = 5;

uint8_t n;
uint8_t col[NMAX];
uint8_t diag1[2 * NMAX - 1];
uint8_t diag2[2 * NMAX - 1];
uint8_t pos[NMAX];
uint16_t solutions;
uint16_t sum;

static void place(uint8_t row)
{
    uint8_t c;
    if (row == n) {
        uint8_t i;
        uint16_t h = 0;
        solutions++;
        for (i = 0; i < n; i++)
            h = (uint16_t)(h * 9u + pos[i]);
        sum = (uint16_t)(sum + h);
        return;
    }
    for (c = 0; c < n; c++) {
        uint8_t d1 = (uint8_t)(row + c);
        uint8_t d2 = (uint8_t)(row - c + n - 1);
        if (!col[c] && !diag1[d1] && !diag2[d2]) {
            col[c] = diag1[d1] = diag2[d2] = 1;
            pos[row] = c;
            place((uint8_t)(row + 1));
            col[c] = diag1[d1] = diag2[d2] = 0;
        }
    }
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

uint16_t first, total, reps;

int main(void)
{
    uint16_t r;

    TIMER_START();
    n = nq;
    reps = nrep;
    total = 0;
    sum = 0;
    for (r = 0; r < reps; r++) {
        solutions = 0;
        place(0);
        if (r == 0)
            first = solutions;
        total = (uint16_t)(total + solutions);
    }
    TIMER_STOP();

    put_str("8 queens: ");
    put_dec(first);
    put_str(" solutions\nruns: ");
    put_dec(reps);
    put_str(", total: ");
    put_dec(total);
    put_str(", checksum: ");
    put_dec(sum);
    putchar('\n');
    return 0;
}
