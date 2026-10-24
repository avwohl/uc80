/* tinycurses.h - a small VT100/ANSI curses subset for the uc80
 * benchmark harness.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * The screen is 24 lines of 80 columns.  The library keeps a shadow
 * screen and, at refresh, writes only the cells that changed, with
 * putchar().  Input comes from getchar().  Every compiler and the host
 * compiler run the same C, so the output bytes are the same.
 *
 * What is there:
 *   initscr endwin move mvaddch mvaddstr addch addstr printw mvprintw
 *   clear erase refresh clrtoeol clrtobot getch cbreak nocbreak raw
 *   noraw echo noecho nl nonl standout standend attron attroff
 *   attrset inch mvinch box beep, and the window forms newwin delwin
 *   wmove waddch waddstr wprintw mvwprintw mvwaddch mvwaddstr
 *   wrefresh werase wclear wclrtoeol wclrtobot wgetch wstandout
 *   wstandend winch.  getyx, getmaxyx and getbegyx are macros.
 *   keypad, curs_set, nodelay, leaveok, scrollok, idlok, clearok and
 *   touchwin do nothing and return OK.
 *
 * What is not there: colors, other attributes than standout, function
 * keys, scrolling (a write past the last cell returns ERR), pads, and
 * a terminal database.  A window made by newwin() is a rectangle of the
 * one screen array, as a subwindow is: windows do not have their own
 * text, and they do not overlap in a managed way.  There are at most
 * two such windows.  Only characters 32 to 126 are drawn.  printw()
 * uses px_vf() of posix_shim.c, so link posix_shim.c too.
 *
 * Names: dcc and L80 keep 6 characters of an external name, so the
 * library functions have short names (tc_xx, each with a different
 * 5-character prefix) and the curses names are function-like macros.
 * The printw family are object-like macros, because they take a
 * variable number of arguments.
 */
#ifndef TINYCURSES_H
#define TINYCURSES_H

#include "posix_shim.h"

#define ERR (-1)
#define OK 0
#ifndef TRUE
#define TRUE 1
#define FALSE 0
#endif
#define A_NORMAL 0
#define A_STANDOUT 256

struct tc_win {
    int cury, curx;             /* cursor, relative to the window */
    int begy, begx;             /* top left corner on the screen */
    int maxy, maxx;             /* number of lines and columns */
    int attr;                   /* 1 while standout is on */
    int used;
};
typedef struct tc_win WINDOW;

extern WINDOW *stdscr;
extern int LINES;
extern int COLS;

WINDOW *tc_is(void);
int tc_en(void);
WINDOW *tc_nw(int nlines, int ncols, int begy, int begx);
int tc_dw(WINDOW *w);
int tc_wm(WINDOW *w, int y, int x);
int tc_wa(WINDOW *w, int c);
int tc_ws(WINDOW *w, const char *s);
int tc_pw(const char *fmt, ...) PX_VA;
int tc_wp(WINDOW *w, const char *fmt, ...) PX_VA;
int tc_mp(int y, int x, const char *fmt, ...) PX_VA;
int tc_mq(WINDOW *w, int y, int x, const char *fmt, ...) PX_VA;
int tc_we(WINDOW *w);
int tc_wc(WINDOW *w);
int tc_wl(WINDOW *w);
int tc_wb(WINDOW *w);
int tc_wr(WINDOW *w);
int tc_wg(WINDOW *w);
int tc_cb(int on);
int tc_ec(int on);
int tc_nl(int on);
int tc_so(WINDOW *w);
int tc_se(WINDOW *w);
int tc_ao(WINDOW *w, int a);
int tc_af(WINDOW *w, int a);
int tc_as(WINDOW *w, int a);
int tc_wi(WINDOW *w);
int tc_bx(WINDOW *w, int v, int h);
int tc_bp(void);

#define initscr() tc_is()
#define endwin() tc_en()
#define newwin(nl, nc, by, bx) tc_nw(nl, nc, by, bx)
#define delwin(w) tc_dw(w)
#define wmove(w, y, x) tc_wm(w, y, x)
#define move(y, x) tc_wm(stdscr, y, x)
#define waddch(w, c) tc_wa(w, c)
#define addch(c) tc_wa(stdscr, c)
#define mvaddch(y, x, c) (tc_wm(stdscr, y, x) == ERR ? ERR : tc_wa(stdscr, c))
#define mvwaddch(w, y, x, c) (tc_wm(w, y, x) == ERR ? ERR : tc_wa(w, c))
#define waddstr(w, s) tc_ws(w, s)
#define addstr(s) tc_ws(stdscr, s)
#define mvaddstr(y, x, s) (tc_wm(stdscr, y, x) == ERR ? ERR : tc_ws(stdscr, s))
#define mvwaddstr(w, y, x, s) (tc_wm(w, y, x) == ERR ? ERR : tc_ws(w, s))
#define printw tc_pw
#define wprintw tc_wp
#define mvprintw tc_mp
#define mvwprintw tc_mq
#define werase(w) tc_we(w)
#define erase() tc_we(stdscr)
#define wclear(w) tc_wc(w)
#define clear() tc_wc(stdscr)
#define wclrtoeol(w) tc_wl(w)
#define clrtoeol() tc_wl(stdscr)
#define wclrtobot(w) tc_wb(w)
#define clrtobot() tc_wb(stdscr)
#define wrefresh(w) tc_wr(w)
#define refresh() tc_wr(stdscr)
#define wgetch(w) tc_wg(w)
#define getch() tc_wg(stdscr)
#define cbreak() tc_cb(1)
#define nocbreak() tc_cb(0)
#define raw() tc_cb(1)
#define noraw() tc_cb(0)
#define echo() tc_ec(1)
#define noecho() tc_ec(0)
#define nl() tc_nl(1)
#define nonl() tc_nl(0)
#define wstandout(w) tc_so(w)
#define standout() tc_so(stdscr)
#define wstandend(w) tc_se(w)
#define standend() tc_se(stdscr)
#define wattron(w, a) tc_ao(w, a)
#define attron(a) tc_ao(stdscr, a)
#define wattroff(w, a) tc_af(w, a)
#define attroff(a) tc_af(stdscr, a)
#define wattrset(w, a) tc_as(w, a)
#define attrset(a) tc_as(stdscr, a)
#define winch(w) tc_wi(w)
#define inch() tc_wi(stdscr)
#define mvinch(y, x) (tc_wm(stdscr, y, x) == ERR ? ERR : tc_wi(stdscr))
#define box(w, v, h) tc_bx(w, v, h)
#define beep() tc_bp()
#define flash() tc_bp()
#define keypad(w, b) OK
#define curs_set(v) OK
#define nodelay(w, b) OK
#define leaveok(w, b) OK
#define scrollok(w, b) OK
#define idlok(w, b) OK
#define clearok(w, b) OK
#define touchwin(w) OK
#define getyx(w, y, x) ((y) = (w)->cury, (x) = (w)->curx)
#define getmaxyx(w, y, x) ((y) = (w)->maxy, (x) = (w)->maxx)
#define getbegyx(w, y, x) ((y) = (w)->begy, (x) = (w)->begx)

#endif
