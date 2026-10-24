/* rgstub.h - the machine-dependent part of Rogue 3.6 (mdport.h) for the
 * uc80 benchmark harness.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * A manifest "edit" replaces #include "mdport.h" in rogue.h with this
 * header.  mdport.c, state.c (save and restore) and xcrypt.c (crypt) of
 * Rogue are not used.  rgmd.c has small stand-ins for the md_ calls that
 * the game makes, and for the three calls above.  Curses is tinycurses
 * (bench/support/tinycurses.h): "curses.h" is a file of this directory
 * that includes tinycurses.h.  The names are macros that map to 5-character
 * names, because dcc and L80 keep only 6 characters of an external name.
 *
 * Rogue is built for size only, so nothing here has to work well.
 */
#ifndef RGSTUB_H
#define RGSTUB_H

#include "bgstub.h"
#include "tinycurses.h"


/* Rogue calls wmove(cw, unc(hero)): a macro that expands to two arguments.
 * A function-like macro of tinycurses would see two arguments, not three,
 * so wmove is the plain function name. */
#undef wmove
#define wmove tc_wm

/* Curses calls that tinycurses has no macro for. */
#define curscr stdscr
#undef getmaxx
#define getmaxx(w) ((w)->maxx)
#define mvwinch(w, y, x) (tc_wm(w, y, x) == ERR ? ERR : tc_wi(w))
char *rg_ut(int c);
#define unctrl(c) rg_ut(c)
#define crmode() tc_cb(1)
#define nocrmode() tc_cb(0)
#define flushinp() OK
#define baudrate() 9600
int rg_ov(WINDOW *a, WINDOW *b);
#define overwrite(a, b) rg_ov(a, b)
#define overlay(a, b) rg_ov(a, b)

/* curses.h defines bool (as char). */
#undef bool
#define bool char

extern char **environ;

#define MD_STRIP_CTRL_KEYPAD 1
#ifndef PATH_MAX
#define PATH_MAX 255
#endif
typedef int uid_t;
typedef int pid_t;

/* The md_ calls that the game makes. */
int rg_ch(const char *filename, int mode);
int rg_ec(void);
char *rg_gh(void);
char *rg_gu(void);
uid_t rg_gi(void);
char *rg_gp(char *prompt);
pid_t rg_gd(void);
void rg_in(int options);
int rg_kc(void);
void rg_nu(void);
int rg_rc(WINDOW *win);
int rg_se(void);
int rg_la(double *avg);
long rg_mu(void);
int rg_uc(void);
int rg_uf(const char *file, FILE *inf);
int rg_ul(FILE *fp);
int rg_lk(FILE *fp);
int rg_sv(FILE *f);
int rg_rs(FILE *f);
char *rg_cr(const char *key, const char *setting);
#define md_chmod(f, m) rg_ch(f, m)
#define md_erasechar() rg_ec()
#define md_gethomedir() rg_gh()
#define md_getusername() rg_gu()
#define md_getuid() rg_gi()
#define md_getpass(p) rg_gp(p)
#define md_getpid() rg_gd()
#define md_init(o) rg_in(o)
#define md_killchar() rg_kc()
#define md_normaluser() rg_nu()
#define md_readchar(w) rg_rc(w)
#define md_shellescape() rg_se()
#define md_loadav(a) rg_la(a)
#define md_memused() rg_mu()
#define md_ucount() rg_uc()
#define md_unlink_open_file(f, i) rg_uf(f, i)
#define md_unlockfile(f) rg_ul(f)
#define md_lockfile(f) rg_lk(f)
#define rs_save_file(f) rg_sv(f)
#define rs_restore_file(f) rg_rs(f)
#define crypt(k, s) rg_cr(k, s)

#endif
