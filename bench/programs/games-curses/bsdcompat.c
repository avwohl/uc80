/* bsdcompat.c - see bsdcompat.h.  Linked by the games that need it.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include "tinycurses.h"
#include "bsdcompat.h"

/* unctrl(): the printable form of a character: ^X for a control
 * character, ^? for DEL, else the character itself. */
const char *bc_un(int c)
{
    static char buf[4];

    c &= 255;
    if (c < 32) {
        buf[0] = '^';
        buf[1] = (char)(c + 64);
        buf[2] = 0;
    } else if (c == 127) {
        buf[0] = '^';
        buf[1] = '?';
        buf[2] = 0;
    } else {
        buf[0] = (char)c;
        buf[1] = 0;
    }
    return buf;
}

/* vwprintw(): printw into a window with a va_list. */
int bc_vw(void *w, const char *fmt, va_list ap)
{
    char buf[100];

    px_vf(buf, 100, fmt, ap);
    return tc_ws((WINDOW *)w, buf);
}

/* deleteln(): delete the line of the cursor, move the lines below it up
 * by one, and blank the last line.  The cursor stays where it was. */
int bc_dl(void)
{
    int y, x, cy, cx, c;

    cy = stdscr->cury;
    cx = stdscr->curx;
    for (y = cy; y < LINES - 1; y++) {
        for (x = 0; x < COLS; x++) {
            c = mvinch(y + 1, x) & 255;
            mvaddch(y, x, c);
        }
    }
    for (x = 0; x < COLS; x++)
        mvaddch(LINES - 1, x, ' ');
    move(cy, cx);
    return OK;
}

/* The curses calls that do nothing (see bsdcompat.h). */
int bc_ok(void)
{
    return OK;
}
