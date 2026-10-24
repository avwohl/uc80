/* tinycurses.c - see tinycurses.h.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include "tinycurses.h"

#define NR 24                   /* LINES */
#define NC 80                   /* COLS */

/* tc_vi: the screen the program drew.  tc_ph: the screen the terminal
 * shows.  A cell is the character in bits 0 to 6 and the standout flag
 * in bit 7. */
static unsigned char tc_vi[NR * NC];
static unsigned char tc_ph[NR * NC];
static int tc_py = -1;          /* terminal cursor line, -1 if unknown */
static int tc_px;               /* terminal cursor column */
static int tc_pa;               /* 1 while the terminal has standout on */
static int tc_dr = 1;           /* 1: clear the terminal at next refresh */
static int tc_nm = 1;           /* nl() mode */
static int tc_eo = 1;           /* echo() mode */
static char tc_bf[160];
static struct tc_win tc_pl[3];  /* [0] is stdscr, [1] and [2] newwin() */

WINDOW *stdscr;
int LINES = NR;
int COLS = NC;

static void tc_pn(int n)
{
    char b[6];
    int i = 0;

    if (n == 0) {
        putchar('0');
        return;
    }
    while (n > 0) {
        b[i++] = (char)('0' + n % 10);
        n = n / 10;
    }
    while (i > 0)
        putchar(b[--i]);
}

/* ESC [ y ; x H, with 0-based y and x */
static void tc_gt(int y, int x)
{
    putchar(27);
    putchar('[');
    tc_pn(y + 1);
    putchar(';');
    tc_pn(x + 1);
    putchar('H');
    tc_py = y;
    tc_px = x;
}

static void tc_sg(int a)
{
    putchar(27);
    putchar('[');
    putchar(a ? '7' : '0');
    putchar('m');
    tc_pa = a;
}

static unsigned char *tc_ce(WINDOW *w, int y, int x)
{
    return &tc_vi[(w->begy + y) * NC + w->begx + x];
}

WINDOW *tc_is(void)
{
    px_ti();
    stdscr = &tc_pl[0];
    stdscr->cury = stdscr->curx = stdscr->begy = stdscr->begx = 0;
    stdscr->maxy = NR;
    stdscr->maxx = NC;
    stdscr->attr = 0;
    stdscr->used = 1;
    tc_pl[1].used = tc_pl[2].used = 0;
    memset(tc_vi, ' ', NR * NC);
    tc_dr = 1;
    tc_py = -1;
    return stdscr;
}

int tc_en(void)
{
    tc_gt(NR - 1, 0);
    tc_sg(0);
    putchar('\n');
    tc_py = -1;
    tc_dr = 1;
    return OK;
}

WINDOW *tc_nw(int nlines, int ncols, int begy, int begx)
{
    int i;
    WINDOW *w;

    if (begy < 0 || begx < 0 || begy >= NR || begx >= NC)
        return 0;
    if (nlines <= 0)
        nlines = NR - begy;
    if (ncols <= 0)
        ncols = NC - begx;
    if (begy + nlines > NR || begx + ncols > NC)
        return 0;
    for (i = 1; i < 3; i++) {
        if (!tc_pl[i].used) {
            w = &tc_pl[i];
            w->cury = w->curx = w->attr = 0;
            w->begy = begy;
            w->begx = begx;
            w->maxy = nlines;
            w->maxx = ncols;
            w->used = 1;
            return w;
        }
    }
    return 0;
}

int tc_dw(WINDOW *w)
{
    if (w == 0 || w == &tc_pl[0])
        return ERR;
    w->used = 0;
    return OK;
}

int tc_wm(WINDOW *w, int y, int x)
{
    if (y < 0 || x < 0 || y >= w->maxy || x >= w->maxx)
        return ERR;
    w->cury = y;
    w->curx = x;
    return OK;
}

int tc_wl(WINDOW *w)
{
    int x;

    for (x = w->curx; x < w->maxx; x++)
        *tc_ce(w, w->cury, x) = ' ';
    return OK;
}

int tc_wb(WINDOW *w)
{
    int y, x;

    tc_wl(w);
    for (y = w->cury + 1; y < w->maxy; y++)
        for (x = 0; x < w->maxx; x++)
            *tc_ce(w, y, x) = ' ';
    return OK;
}

int tc_we(WINDOW *w)
{
    int y, x;

    for (y = 0; y < w->maxy; y++)
        for (x = 0; x < w->maxx; x++)
            *tc_ce(w, y, x) = ' ';
    w->cury = w->curx = 0;
    return OK;
}

/* clear(): erase, and also repaint the whole terminal at next refresh */
int tc_wc(WINDOW *w)
{
    tc_we(w);
    tc_dr = 1;
    return OK;
}

int tc_wa(WINDOW *w, int c)
{
    int so = w->attr;
    int n;

    if (c & A_STANDOUT)
        so = 1;
    c &= 255;
    if (c == '\n') {
        tc_wl(w);
        if (w->cury + 1 >= w->maxy)
            return ERR;
        w->cury++;
        if (tc_nm)
            w->curx = 0;
        return OK;
    }
    if (c == '\r') {
        w->curx = 0;
        return OK;
    }
    if (c == '\b') {
        if (w->curx > 0)
            w->curx--;
        return OK;
    }
    if (c == '\t') {
        n = 8 - (w->curx & 7);
        while (n > 0) {
            if (tc_wa(w, ' ') == ERR)
                return ERR;
            n--;
        }
        return OK;
    }
    if (c < 32 || c > 126)
        return OK;
    *tc_ce(w, w->cury, w->curx) = (unsigned char)(c | (so ? 128 : 0));
    w->curx++;
    if (w->curx >= w->maxx) {
        if (w->cury + 1 >= w->maxy) {
            w->curx = w->maxx - 1;
            return ERR;
        }
        w->curx = 0;
        w->cury++;
    }
    return OK;
}

int tc_ws(WINDOW *w, const char *s)
{
    while (*s) {
        if (tc_wa(w, *s & 255) == ERR)
            return ERR;
        s++;
    }
    return OK;
}

int tc_pw(const char *fmt, ...) PX_VA
{
    va_list ap;

    va_start(ap, fmt);
    px_vf(tc_bf, 160, fmt, ap);
    va_end(ap);
    return tc_ws(stdscr, tc_bf);
}

int tc_wp(WINDOW *w, const char *fmt, ...) PX_VA
{
    va_list ap;

    va_start(ap, fmt);
    px_vf(tc_bf, 160, fmt, ap);
    va_end(ap);
    return tc_ws(w, tc_bf);
}

int tc_mp(int y, int x, const char *fmt, ...) PX_VA
{
    va_list ap;

    if (tc_wm(stdscr, y, x) == ERR)
        return ERR;
    va_start(ap, fmt);
    px_vf(tc_bf, 160, fmt, ap);
    va_end(ap);
    return tc_ws(stdscr, tc_bf);
}

int tc_mq(WINDOW *w, int y, int x, const char *fmt, ...) PX_VA
{
    va_list ap;

    if (tc_wm(w, y, x) == ERR)
        return ERR;
    va_start(ap, fmt);
    px_vf(tc_bf, 160, fmt, ap);
    va_end(ap);
    return tc_ws(w, tc_bf);
}

/* Write the cells that differ from the terminal, then put the terminal
 * cursor at the cursor of window w. */
int tc_wr(WINDOW *w)
{
    int y, x, i, c, a;

    if (tc_dr) {
        putchar(27);
        putchar('[');
        putchar('2');
        putchar('J');
        tc_sg(0);
        memset(tc_ph, ' ', NR * NC);
        tc_py = -1;
        tc_dr = 0;
    }
    for (y = 0; y < NR; y++) {
        for (x = 0; x < NC; x++) {
            i = y * NC + x;
            c = tc_vi[i];
            if (c == tc_ph[i])
                continue;
            if (tc_py != y || tc_px != x)
                tc_gt(y, x);
            a = (c & 128) ? 1 : 0;
            if (a != tc_pa)
                tc_sg(a);
            putchar(c & 127);
            tc_ph[i] = (unsigned char)c;
            tc_px++;
            if (tc_px >= NC)
                tc_py = -1;     /* the cursor wrapped: position again */
        }
    }
    if (tc_pa)
        tc_sg(0);
    y = w->begy + w->cury;
    x = w->begx + w->curx;
    if (tc_py != y || tc_px != x)
        tc_gt(y, x);
    return OK;
}

int tc_wg(WINDOW *w)
{
    int c;

    tc_wr(w);
    c = getchar();
    if (c == EOF)
        return ERR;
    c &= 255;
    if (tc_eo) {
        tc_wa(w, c);
        tc_wr(w);
    }
    return c;
}

int tc_cb(int on)
{
    return OK;
}

int tc_ec(int on)
{
    tc_eo = on;
    return OK;
}

int tc_nl(int on)
{
    tc_nm = on;
    return OK;
}

int tc_so(WINDOW *w)
{
    w->attr = 1;
    return OK;
}

int tc_se(WINDOW *w)
{
    w->attr = 0;
    return OK;
}

int tc_ao(WINDOW *w, int a)
{
    if (a & A_STANDOUT)
        w->attr = 1;
    return OK;
}

int tc_af(WINDOW *w, int a)
{
    if (a & A_STANDOUT)
        w->attr = 0;
    return OK;
}

int tc_as(WINDOW *w, int a)
{
    w->attr = (a & A_STANDOUT) ? 1 : 0;
    return OK;
}

int tc_wi(WINDOW *w)
{
    int c = *tc_ce(w, w->cury, w->curx);

    return (c & 127) | ((c & 128) ? A_STANDOUT : 0);
}

/* v = 0: '|' for the sides, h = 0: '-' for top and bottom, corners '+' */
int tc_bx(WINDOW *w, int v, int h)
{
    int y, x;

    if (v == 0)
        v = '|';
    if (h == 0)
        h = '-';
    for (x = 1; x < w->maxx - 1; x++) {
        *tc_ce(w, 0, x) = (unsigned char)h;
        *tc_ce(w, w->maxy - 1, x) = (unsigned char)h;
    }
    for (y = 1; y < w->maxy - 1; y++) {
        *tc_ce(w, y, 0) = (unsigned char)v;
        *tc_ce(w, y, w->maxx - 1) = (unsigned char)v;
    }
    *tc_ce(w, 0, 0) = '+';
    *tc_ce(w, 0, w->maxx - 1) = '+';
    *tc_ce(w, w->maxy - 1, 0) = '+';
    *tc_ce(w, w->maxy - 1, w->maxx - 1) = '+';
    return OK;
}

int tc_bp(void)
{
    putchar(7);
    return OK;
}
