/* bgstub.c - stand-ins for the file and Unix calls of the BSD games, for
 * the uc80 benchmark harness.  The games are built for size only, so these
 * functions are small, not complete.  See bgstub.h.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include "bgstub.h"

int bg_en;
int bg_oi = 1;
char *bg_oa;
BG_FILE bg_in = {0, 0, 0};
BG_FILE bg_out = {1, 0, 0};
BG_FILE bg_err = {2, 0, 0};

static int bg_put(BG_FILE *f, int c)
{
    if (f == &bg_in)
        return -1;
    putchar(c);
    return c;
}

int bg_fp(BG_FILE *f, const char *fmt, ...) PX_VA
{
    char buf[128];
    va_list ap;
    int i, n;

    va_start(ap, fmt);
    n = px_vf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    for (i = 0; i < n; i++)
        bg_put(f, buf[i]);
    return n;
}

BG_FILE *bg_fo(const char *name, const char *mode)
{
    return 0;
}

int bg_fc(BG_FILE *f)
{
    return 0;
}

int bg_ff(BG_FILE *f)
{
    return 0;
}

int bg_fu(const char *s, BG_FILE *f)
{
    while (*s)
        bg_put(f, *s++);
    return 0;
}

int bg_fk(int c, BG_FILE *f)
{
    return bg_put(f, c);
}

int bg_fa(BG_FILE *f)
{
    int c;

    if (f != &bg_in)
        return -1;
    c = getchar();
    if (c < 0 || c == 255)
        f->eof = 1;
    return f->eof ? -1 : c;
}

char *bg_fg(char *buf, int n, BG_FILE *f)
{
    int i, c;

    for (i = 0; i < n - 1; ) {
        c = bg_fa(f);
        if (c < 0)
            break;
        buf[i++] = (char)c;
        if (c == '\n')
            break;
    }
    buf[i] = 0;
    return i ? buf : 0;
}

char *bg_gs(char *buf)
{
    char *p = bg_fg(buf, 80, &bg_in);
    int n;

    if (p == 0)
        return 0;
    n = strlen(buf);
    if (n && buf[n - 1] == '\n')
        buf[n - 1] = 0;
    return buf;
}

unsigned bg_fr(void *p, unsigned size, unsigned n, BG_FILE *f)
{
    f->eof = 1;
    return 0;
}

unsigned bg_fw(const void *p, unsigned size, unsigned n, BG_FILE *f)
{
    unsigned i;
    const char *s = (const char *)p;

    for (i = 0; i < size * n; i++)
        bg_put(f, s[i]);
    return n;
}

int bg_ug(int c, BG_FILE *f)
{
    return -1;
}

int bg_fe(BG_FILE *f)
{
    return f->eof;
}

int bg_fx(BG_FILE *f)
{
    return f->err;
}

void bg_rw(BG_FILE *f)
{
    f->eof = f->err = 0;
}

int bg_fq(BG_FILE *f, long off, int whence)
{
    return -1;
}

void bg_pe(const char *s)
{
    bg_fu(s, &bg_err);
}

int bg_op(const char *name, int flags, ...) PX_VA
{
    return -1;
}

int bg_cl(int fd)
{
    return 0;
}

int bg_rd(int fd, void *buf, unsigned n)
{
    return -1;
}

int bg_wr(int fd, const void *buf, unsigned n)
{
    return bg_fw(buf, 1, n, fd == 2 ? &bg_err : &bg_out);
}

int bg_ul(const char *name)
{
    return -1;
}

int bg_st(const char *path, struct stat *sb)
{
    sb->st_mtime = 0;
    return -1;
}

int bg_fz(int fd, struct stat *sb)
{
    sb->st_mtime = 0;
    return 0;
}

char *bg_ct(const long *t)
{
    return "Sat Sep  9 01:46:40 2001\n";
}

int bg_sn(char *buf, unsigned n, const char *fmt, ...) PX_VA
{
    va_list ap;
    int r;

    va_start(ap, fmt);
    r = px_vf(buf, (int)n, fmt, ap);
    va_end(ap);
    return r;
}

int bg_vs(char *buf, unsigned n, const char *fmt, va_list ap)
{
    return px_vf(buf, (int)n, fmt, ap);
}

char *bg_sd(const char *s)
{
    char *p = (char *)malloc(strlen(s) + 1);

    if (p)
        strcpy(p, s);
    return p;
}

int bg_cg(int clk, struct timespec *ts)
{
    ts->tv_sec = 1000000000L;
    ts->tv_nsec = 0L;
    return 0;
}

struct tm *bg_lt(const long *t)
{
    static struct tm tmv;

    tmv.tm_hour = 12;
    tmv.tm_mday = 1;
    return &tmv;
}

struct passwd *bg_pw(int uid)
{
    static struct passwd pw = {"player", "", 1};

    return &pw;
}

int bg_go(int argc, char **argv, const char *opts)
{
    return -1;
}

long bg_hl(long x)
{
    return ((x & 0xffL) << 24) | ((x & 0xff00L) << 8)
        | ((x >> 8) & 0xff00L) | ((x >> 24) & 0xffL);
}

char *bg_sr(int e)
{
    return "error";
}

char *bg_fl(BG_FILE *f, unsigned *len)
{
    static char line[80];
    char *p = bg_fg(line, sizeof(line), f);

    *len = p ? strlen(p) : 0;
    return p;
}

unsigned bg_sc(char *dst, const char *src, unsigned n)
{
    unsigned i;

    for (i = 0; i + 1 < n && src[i]; i++)
        dst[i] = src[i];
    if (n)
        dst[i] = 0;
    while (src[i])
        i++;
    return i;
}

int bg_sj(jmp_buf e)
{
    e[0] = 0;
    return 0;
}

void bg_lj(jmp_buf e, int v)
{
    e[0] = v;
    exit(v);
}

int bg_sf(const char *fmt, ...) PX_VA
{
    return 0;
}

int bg_ss(const char *str, const char *fmt, ...) PX_VA
{
    return 0;
}

long bg_ft(BG_FILE *f)
{
    return -1L;
}

int bg_vp(BG_FILE *f, const char *fmt, va_list ap)
{
    char buf[128];
    int i, n;

    n = px_vf(buf, sizeof(buf), fmt, ap);
    for (i = 0; i < n; i++)
        bg_put(f, buf[i]);
    return n;
}

/* The classic z88dk malloc() needs the C variable "heap" (4 bytes). */
#ifdef __Z88DK
long heap;
#endif
