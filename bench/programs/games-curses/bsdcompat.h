/* bsdcompat.h - glue for the curses games in
 * bench/programs.d/games_curses.toml.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * A manifest edit replaces "#include <sys/cdefs.h>" and
 * "#include <curses.h>" in each game file with "#include "bsdcompat.h"",
 * and drops the other BSD headers (unistd.h, termios.h, err.h, fcntl.h,
 * ...).  This header supplies what the games take from those headers.
 * Every compiler and the host compiler read the same text.  C89 only, no
 * variadic macros.  The functions are in bsdcompat.c.
 *
 * "lint" is defined, so the games skip their __COPYRIGHT and __RCSID
 * lines (HI-TECH C 3.09 cannot read the multi-line string argument).
 */
#ifndef BSDCOMPAT_H
#define BSDCOMPAT_H

#define lint 1

/* z88dk classic malloc() needs a heap (the readme switch). */
#ifdef __Z88DK
#pragma output CRT_HEAP_AMALLOC = 1
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "posix_shim.h"

/* stdbool.h, stdint.h, sys/types.h, time.h */
#define bool int
#define true 1
#define false 0
#define uint32_t unsigned long
#define time_t long
#define uid_t int

/* sys/cdefs.h */
#define __dead
#define __unused
#define __printflike(a, b)

int bc_ok(void);

/* Some libraries (z88dk) define these names as macros. */
#undef alarm
#undef getuid
#undef getgid
#undef setgid
#undef signal
#undef open
#undef close
#undef read
#undef write
#undef lseek
#undef kill
#undef fflush

/* signal.h, unistd.h: no signal ever comes, there are no users. */
#ifndef SIGALRM
#define SIGALRM 14
#endif
#define signal(s, h) bc_ok()
#define alarm(n) bc_ok()
#define getuid() 0
#define getgid() 0
#define setgid(g) bc_ok()

/* NetBSD libc: the game files call this once for each coordinate. */
#define arc4random_uniform(n) (rand() % (n))

/* fcntl.h, unistd.h: there is no score file and no process control. */
#define O_RDWR 2
#define SIGTSTP 18
#define open(p, m) (-1)
#define close(f) bc_ok()
#define read(f, b, n) (-1)
#define write(f, b, n) (-1)
#define lseek(f, o, w) (-1L)
#define kill(p, s) bc_ok()

/* stdlib.h, stdio.h: SDCC and HI-TECH C lack these.  fflush() is not
 * needed (the console is not buffered), and the macro keeps stdout out
 * of the call. */
#ifndef EXIT_FAILURE
#define EXIT_FAILURE 1
#endif
#ifndef EXIT_SUCCESS
#define EXIT_SUCCESS 0
#endif
#define fflush(f) bc_ok()

/* sys/ttydefaults.h */
#define CTRL(c) ((c) & 037)

/* curses.h: tinycurses.h comes here, so that the macros below replace
 * its "OK" macros.  dcc cannot compile the expression statement "0;",
 * and each game calls scrollok() and leaveok() as a statement. */
#include "tinycurses.h"
#undef keypad
#undef curs_set
#undef nodelay
#undef leaveok
#undef scrollok
#undef idlok
#undef clearok
#undef touchwin
#define keypad(w, b) bc_ok()
#define curs_set(v) bc_ok()
#define nodelay(w, b) bc_ok()
#define leaveok(w, b) bc_ok()
#define scrollok(w, b) bc_ok()
#define idlok(w, b) bc_ok()
#define clearok(w, b) bc_ok()
#define touchwin(w) bc_ok()

/* curses functions that tinycurses does not have (bsdcompat.c).  curscr
 * is only an argument of clearok() and wrefresh(); mille calls
 * wrefresh(curscr) to redraw, which is a refresh here. */
#define curscr stdscr
#define killchar() 21
#define erasechar() 8
const char *bc_un(int c);
int bc_vw(void *w, const char *fmt, va_list ap);
int bc_dl(void);
#define deleteln() bc_dl()
#define unctrl(c) bc_un(c)
#define vwprintw(w, f, ap) bc_vw(w, f, ap)

#endif
