/* posix_shim.h - the few Unix calls that games need, for the uc80
 * benchmark harness.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * Portable C89 only.  The same text is compiled by every benchmark
 * compiler and by the host compiler that makes the golden output, so a
 * game gives the same output everywhere.  A game includes this header
 * LAST (after stdio.h, stdlib.h, string.h and any other system header)
 * and is linked with posix_shim.c.  A manifest "edit" replaces the
 * #include of <unistd.h>, <time.h>, <signal.h>, <err.h> and
 * <strings.h> in the game.
 *
 * Rules for the names (see docs/bench_harness.md):
 *   - dcc and L80 keep only 6 characters of an external name (the _
 *     and 5 more).  Each external name here has a different 5-character
 *     prefix.  The calls that games make are function-like macros that
 *     map to the short names, so a variable named "index" or "time"
 *     still works.
 *   - No variadic macros, no // comments, no # or ## (HI-TECH C 3.09).
 */
#ifndef POSIX_SHIM_H
#define POSIX_SHIM_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>

/* Variadic functions under z88dk sccz80: its default calling convention
 * pushes the arguments left to right, and then a function with "..." cannot
 * find its named parameters.  PX_VA (placed after the declarator, in the
 * prototype and in the definition) selects the standard right-to-left
 * convention, and the va_ macros of stdarg.h must then be the right-to-left
 * ones.  The other compilers need neither. */
#ifdef __SCCZ80
#undef va_start
#undef va_arg
#undef va_end
#define va_start(ap, last) { ap = (va_list)&last + sizeof(last); }
#define va_arg(ap, type) (*((type *)((ap += sizeof(type)) - sizeof(type))))
#define va_end(ap)
#define PX_VA __stdc
#else
#define PX_VA
#endif

/* Some libraries (z88dk) already define some of these names as macros. */
#undef time
#undef getpid
#undef sleep
#undef usleep
#undef getenv
#undef signal
#undef rand
#undef srand
#undef random
#undef srandom
#undef index
#undef rindex
#undef bzero
#undef bcopy
#undef strcasecmp
#undef strncasecmp

/* exit(): the SDCC stdlib.h does not declare exit(), but the CP/M start-up
 * code of the harness (bench/crt/sdcc, label _exit) defines it.  SDCC and
 * llvm-z80 (which uses the SDCC headers) get the declaration here.  z88dk
 * defines __SDCC for its zsdcc back end too, and declares exit() itself. */
#if (defined(__SDCC) && !defined(__Z88DK)) \
    || (defined(__z80__) && defined(__clang__))
void exit(int status);
#endif

/* getchar() under z88dk: the classic console driver echoes each
 * character that getchar() reads, as a terminal does, so a scripted run
 * would print the script text into its output.  fgetc_cons() is the
 * same BDOS call without the echo.  The other compilers read the
 * console without echo.  A program that reads its input with another
 * function (fgets, scanf) would need a similar mapping. */
#ifdef __Z88DK
#undef getchar
#define getchar() fgetc_cons()
#endif

/* getchar() under HI-TECH C: the library reads the console with BDOS
 * function 10 (buffered line input), which z88dk-ticks does not
 * implement.  Its console output also polls the keyboard, and under
 * z88dk-ticks each poll takes one character out of the input file, so
 * the characters of the script would disappear while the program prints.
 * px_ti() therefore reads the whole input at the start of the program
 * (bench_args() and initscr() call px_ti()), and px_gc() hands out the
 * characters, with BDOS function 6.  Other compilers: px_ti() does
 * nothing. */
#ifdef HI_TECH_C
int px_gc(void);
#undef getchar
#define getchar() px_gc()
#endif

/* time(): always 1000000000.  The program must not read the clock. */
long px_tm(void *p);
#define time(p) px_tm(p)

/* getpid(): always 1.  A game that seeds srand() with it, or with
 * time(), gets the same seed on every compiler. */
int px_gp(void);
#define getpid() px_gp()

/* sleep(), usleep(): no wait, return 0. */
unsigned px_sl(unsigned n);
#define sleep(n) px_sl(n)
#define usleep(n) px_sl(n)

/* getenv(): always NULL (no variable is set). */
char *px_ge(const char *name);
#define getenv(s) px_ge(s)

/* signal(): no signal ever comes.  The handler type is void *, because
 * HI-TECH C 3.09 cannot take a function pointer in a prototype. */
void *px_si(int sig, void *handler);
#define signal(s, h) px_si(s, h)
#ifndef SIGINT
#define SIGHUP 1
#define SIGINT 2
#define SIGQUIT 3
#define SIGTERM 15
#endif
#ifndef SIG_IGN
#define SIG_IGN ((void *)1)
#define SIG_DFL ((void *)0)
#endif

/* rand(), srand(), random(), srandom(): one linear congruential
 * generator for every compiler, with RAND_MAX 32767.  The library
 * generators differ from one compiler to the next. */
int px_ra(void);
void px_sr(unsigned seed);
#define rand() px_ra()
#define srand(s) px_sr(s)
#define random() px_ra()
#define srandom(s) px_sr(s)
#undef RAND_MAX
#define RAND_MAX 32767

/* BSD string and memory calls, as function-like macros. */
#define index(s, c) strchr(s, c)
#define rindex(s, c) strrchr(s, c)
#define bzero(p, n) memset(p, 0, n)
#define bcopy(a, b, n) memmove(b, a, n)
int px_ca(const char *a, const char *b);
int px_cn(const char *a, const char *b, unsigned n);
#define strcasecmp(a, b) px_ca(a, b)
#define strncasecmp(a, b, n) px_cn(a, b, n)

/* argv: when the start-up code gives no program name (argc is 0), set
 * argc to 1 and argv to {"game", NULL}.  Call bench_args(&argc, &argv)
 * at the top of main(). */
void px_ar(int *argc, char ***argv);
void px_ti(void);
#define bench_args(pc, pv) px_ar(pc, pv)

/* err(), errx(), warn(), warnx(): print "game: message[: error]" and a
 * newline to the standard OUTPUT (the golden file holds only standard
 * output), then err() and errx() call exit(status). */
void err(int status, const char *fmt, ...) PX_VA;
void errx(int status, const char *fmt, ...) PX_VA;
void warn(const char *fmt, ...) PX_VA;
void warnx(const char *fmt, ...) PX_VA;

/* A small formatter that every compiler runs the same way: %% %c %s %d
 * %i %u %x %X and the l modifier, with the flags - and 0 and a width.
 * No floating point.  Writes at most cap-1 characters and a final NUL,
 * and returns the number of characters written.  tinycurses uses it for
 * printw(). */
int px_vf(char *buf, int cap, const char *fmt, va_list ap);

#endif
