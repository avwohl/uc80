/* posix_shim.c - see posix_shim.h.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include "posix_shim.h"

#ifdef HI_TECH_C
#include <cpm.h>

#define PX_IN 2048
static char px_ib[PX_IN];
static int px_in, px_ip, px_ig;

/* BDOS 6 with E=0FFh returns 0 when no character is there, which for an
 * input file means the end.  z88dk-ticks swaps LF and CR in that call,
 * so swap them back. */
void px_ti(void)
{
    int c;

    if (px_ig)
        return;
    px_ig = 1;
    while (px_in < PX_IN) {
        c = bdos(6, 255) & 255;
        if (c == 0)
            break;
        if (c == 13)
            c = 10;
        else if (c == 10)
            c = 13;
        px_ib[px_in++] = (char)c;
    }
}

int px_gc(void)
{
    px_ti();
    if (px_ip >= px_in)
        return -1;
    return px_ib[px_ip++] & 255;
}
#else
void px_ti(void)
{
}
#endif

static unsigned long px_sd = 1;
static char *px_av[2] = {"game", 0};

long px_tm(void *p)
{
    if (p)
        *(long *)p = 1000000000L;
    return 1000000000L;
}

int px_gp(void)
{
    return 1;
}

unsigned px_sl(unsigned n)
{
    return 0;
}

char *px_ge(const char *name)
{
    return 0;
}

void *px_si(int sig, void *handler)
{
    return 0;
}

void px_sr(unsigned seed)
{
    px_sd = seed;
}

/* The ANSI C sample generator.  The mask keeps 32 bits where long is
 * wider (the host); unsigned long is 32 bits on the Z80 compilers. */
int px_ra(void)
{
    px_sd = (px_sd * 1103515245L + 12345L) & 0xFFFFFFFFL;
    return (int)((px_sd >> 16) & 0x7fff);
}

static int lc(int c)
{
    if (c >= 'A' && c <= 'Z')
        return c + 32;
    return c;
}

int px_ca(const char *a, const char *b)
{
    while (*a && lc(*a & 255) == lc(*b & 255)) {
        a++;
        b++;
    }
    return lc(*a & 255) - lc(*b & 255);
}

int px_cn(const char *a, const char *b, unsigned n)
{
    while (n > 0 && *a && lc(*a & 255) == lc(*b & 255)) {
        a++;
        b++;
        n--;
    }
    if (n == 0)
        return 0;
    return lc(*a & 255) - lc(*b & 255);
}

void px_ar(int *argc, char ***argv)
{
    px_ti();
    if (*argc < 1 || *argv == 0 || (*argv)[0] == 0) {
        *argc = 1;
        *argv = px_av;
    }
}

#define PUT(ch) do { if (n < cap - 1) buf[n++] = (ch); } while (0)

int px_vf(char *buf, int cap, const char *fmt, va_list ap)
{
    int n = 0, left, zero, width, lng, neg, len, i, c, ii, padc;
    unsigned uu;
    char tmp[12];
    const char *s;
    unsigned long v;
    long sv;

    while (*fmt) {
        if (*fmt != '%') {
            PUT(*fmt);
            fmt++;
            continue;
        }
        fmt++;
        left = zero = width = lng = neg = 0;
        for (;;) {
            if (*fmt == '-')
                left = 1;
            else if (*fmt == '0')
                zero = 1;
            else
                break;
            fmt++;
        }
        while (*fmt >= '0' && *fmt <= '9') {
            width = width * 10 + (*fmt - '0');
            fmt++;
        }
        if (*fmt == 'l') {
            lng = 1;
            fmt++;
        }
        c = *fmt;
        if (c == 0)
            break;
        fmt++;
        s = tmp;
        len = 0;
        if (c == 'd' || c == 'i') {
            /* dcc rejects "sv = va_arg (ap, long)" and "sv = va_arg (ap,
             * int)" in the two arms of one if, so int has a temporary. */
            if (lng) {
                sv = va_arg(ap, long);
            } else {
                ii = va_arg(ap, int);
                sv = ii;
            }
            if (sv < 0) {
                neg = 1;
                v = -sv;
            } else
                v = sv;
            c = 'u';
        } else if (c == 'u' || c == 'x' || c == 'X') {
            if (lng) {
                v = va_arg(ap, unsigned long);
            } else {
                uu = va_arg(ap, unsigned);
                v = uu;
            }
        } else if (c == 'c') {
            tmp[0] = (char)va_arg(ap, int);
            len = 1;
        } else if (c == 's') {
            s = va_arg(ap, const char *);
            if (s == 0)
                s = "(null)";
            len = strlen(s);
        } else {
            tmp[0] = (char)c;
            len = 1;
        }
        if (c == 'u' || c == 'x' || c == 'X') {
            i = 0;
            do {
                int d;
                if (c == 'u') {
                    /* Not "v % 10": sccz80 gets that wrong for long.  Not
                     * a temporary for "v / 10": uc80 (AST optimizer)
                     * then runs the assignment of the other branch. */
                    d = (int)(v - (v / 10) * 10);
                    v = v / 10;
                } else {
                    d = (int)(v & 15);
                    v = v >> 4;
                }
                tmp[i++] = (char)(d < 10 ? '0' + d
                                         : (c == 'X' ? 'A' : 'a') + d - 10);
            } while (v != 0);
            len = i;
            /* tmp holds the digits in reverse order */
            s = 0;
        }
        i = width - len - neg;
        /* zero padding is for numbers (s == 0) that are not left adjusted */
        padc = ' ';
        if (zero && !left && s == 0)
            padc = '0';
        if (neg && padc == '0')
            PUT('-');
        while (i > 0 && !left) {
            PUT(padc);
            i--;
        }
        if (neg && padc != '0')
            PUT('-');
        if (s == 0) {
            int k;
            for (k = len - 1; k >= 0; k--)
                PUT(tmp[k]);
        } else {
            int k;
            for (k = 0; k < len; k++)
                PUT(s[k]);
        }
        while (i > 0 && left) {
            PUT(' ');
            i--;
        }
    }
    buf[n] = 0;
    return n;
}

static void report(int fatal, int status, int sys, const char *fmt,
                   va_list ap)
{
    char msg[160];

    if (fmt)
        px_vf(msg, 160, fmt, ap);
    else
        msg[0] = 0;
    printf("game: %s%s\n", msg, sys ? ": error" : "");
    if (fatal)
        exit(status);
}

void err(int status, const char *fmt, ...) PX_VA
{
    va_list ap;
    va_start(ap, fmt);
    report(1, status, 1, fmt, ap);
    va_end(ap);
}

void errx(int status, const char *fmt, ...) PX_VA
{
    va_list ap;
    va_start(ap, fmt);
    report(1, status, 0, fmt, ap);
    va_end(ap);
}

void warn(const char *fmt, ...) PX_VA
{
    va_list ap;
    va_start(ap, fmt);
    report(0, 0, 1, fmt, ap);
    va_end(ap);
}

void warnx(const char *fmt, ...) PX_VA
{
    va_list ap;
    va_start(ap, fmt);
    report(0, 0, 0, fmt, ap);
    va_end(ap);
}
