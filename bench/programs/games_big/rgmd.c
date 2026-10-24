/* rgmd.c - stand-ins for the machine-dependent calls of Rogue 3.6 (the
 * parts of mdport.c, state.c and xcrypt.c that the game uses), for the uc80
 * benchmark harness.  See rgstub.h.  The game is built for size only, so
 * these functions are small, not complete.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include "rgstub.h"

char **environ;

int rg_ov(WINDOW *a, WINDOW *b)
{
    return OK;
}

char *rg_ut(int c)
{
    static char buf[3];

    if (c < 32) {
        buf[0] = '^';
        buf[1] = (char)(c + 64);
        buf[2] = 0;
    } else {
        buf[0] = (char)c;
        buf[1] = 0;
    }
    return buf;
}

int rg_ch(const char *filename, int mode)
{
    return 0;
}

int rg_ec(void)
{
    return 8;
}

int rg_kc(void)
{
    return 21;
}

char *rg_gh(void)
{
    return "";
}

char *rg_gu(void)
{
    return "rogue";
}

uid_t rg_gi(void)
{
    return 1;
}

char *rg_gp(char *prompt)
{
    static char pw[16];

    fputs(prompt, stdout);
    if (gets(pw) == 0)
        pw[0] = 0;
    return pw;
}

pid_t rg_gd(void)
{
    return 1;
}

void rg_in(int options)
{
}

void rg_nu(void)
{
}

int rg_rc(WINDOW *win)
{
    return wgetch(win);
}

int rg_se(void)
{
    return 0;
}

int rg_la(double *avg)
{
    return -1;
}

long rg_mu(void)
{
    return 0L;
}

int rg_uc(void)
{
    return 1;
}

int rg_uf(const char *file, FILE *inf)
{
    return 0;
}

int rg_ul(FILE *fp)
{
    return 0;
}

int rg_lk(FILE *fp)
{
    return 0;
}

/* The save and restore of the whole game state (state.c) is not part of
 * this build. */
int rg_sv(FILE *f)
{
    return -1;
}

int rg_rs(FILE *f)
{
    return -1;
}

char *rg_cr(const char *key, const char *setting)
{
    return (char *)key;
}
