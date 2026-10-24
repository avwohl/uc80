/* bgstub.h - the BSD system headers that the "does it fit" games
 * (monop, trek, adventure, battlestar) need, for the uc80 benchmark
 * harness.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * A manifest "edit" replaces #include <sys/cdefs.h> of each NetBSD source
 * with #include "bgstub.h" and deletes the includes of the Unix-only system
 * headers (<unistd.h>, <err.h>, <sys/types.h>, <signal.h>, ...).  This
 * header then gives the same C text to every compiler.  It includes the
 * ISO C headers first and posix_shim.h last (see posix_shim.h: the shim
 * defines function-like macros that would break a later system header).
 *
 * The games are compiled for size only.  Nothing here has to work well:
 * the file-system calls (stat, ctime, ...) are small stand-ins.
 */
#ifndef BGSTUB_H
#define BGSTUB_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <limits.h>
#include <stddef.h>
#include <stdarg.h>
#include <math.h>
#include <float.h>

#include "posix_shim.h"

/* z88dk defines putchar() as a macro that uses its own stdout, which our
 * stdout below would replace: use the console call that the macro ends in. */
#ifdef __Z88DK
#undef putchar
#define putchar(c) fputc_cons(c)
#endif

/* The SDCC library (and llvm-z80, which uses its headers) has only the float
 * math functions, with an f suffix.  double is 32 bits there, so the
 * double names map to them.  Other libraries have both. */
#if (defined(__SDCC) && !defined(__Z88DK)) \
    || (defined(__z80__) && defined(__clang__))
#undef sin
#undef cos
#undef tan
#undef atan
#undef atan2
#undef sqrt
#undef pow
#undef fabs
#undef exp
#undef log
#undef floor
#undef ceil
#undef fmod
#define sin sinf
#define cos cosf
#define tan tanf
#define atan atanf
#define atan2 atan2f
#define sqrt sqrtf
#define pow powf
#define fabs fabsf
#define exp expf
#define log logf
#define floor floorf
#define ceil ceilf
#define fmod fmodf
#endif

/* The SDCC float.h has FLT_MAX only; double is 32 bits there. */
#ifndef DBL_MAX
#define DBL_MAX 3.40282347e+38
#endif

/* NetBSD <sys/cdefs.h> */
#define __RCSID(x) extern int bg_rcsid
#define __KERNEL_RCSID(n, x) extern int bg_rcsid
#define __COPYRIGHT(x) extern int bg_rcsid
#define __dead
#define __unused
#define __noinline
#define __printflike(a, b)
#define __arraycount(a) (sizeof(a) / sizeof(*a))

/* The stdio calls on files and the Unix file-descriptor calls.  The size-only
 * games use them for saved games, score files and text data.  The SDCC
 * library has no FILE at all, so every compiler gets the same stand-ins
 * (bgstub.c): the names are macros, and FILE is a private type.  A write to
 * stdout or stderr goes to putchar(); a read from stdin goes to getchar();
 * fopen() and open() fail.  The names are 5 characters after the "_", for
 * dcc and L80 (see posix_shim.h). */
typedef struct bg_file {
    int fd;
    int eof;
    int err;
} BG_FILE;
extern BG_FILE bg_in, bg_out, bg_err;
#undef FILE
#define FILE BG_FILE
#undef stdin
#undef stdout
#undef stderr
#define stdin (&bg_in)
#define stdout (&bg_out)
#define stderr (&bg_err)
#undef EOF
#define EOF (-1)
int bg_fp(BG_FILE *f, const char *fmt, ...) PX_VA;
BG_FILE *bg_fo(const char *name, const char *mode);
int bg_fc(BG_FILE *f);
int bg_ff(BG_FILE *f);
int bg_fu(const char *s, BG_FILE *f);
int bg_fk(int c, BG_FILE *f);
char *bg_fg(char *buf, int n, BG_FILE *f);
int bg_fa(BG_FILE *f);
unsigned bg_fr(void *p, unsigned size, unsigned n, BG_FILE *f);
unsigned bg_fw(const void *p, unsigned size, unsigned n, BG_FILE *f);
int bg_ug(int c, BG_FILE *f);
int bg_fe(BG_FILE *f);
int bg_fx(BG_FILE *f);
void bg_rw(BG_FILE *f);
int bg_fq(BG_FILE *f, long off, int whence);
void bg_pe(const char *s);
char *bg_gs(char *buf);
#undef fprintf
#undef fopen
#undef fclose
#undef fflush
#undef fputs
#undef fputc
#undef putc
#undef fgets
#undef fgetc
#undef getc
#undef fread
#undef fwrite
#undef ungetc
#undef feof
#undef ferror
#undef rewind
#undef fseek
#undef perror
#undef gets
#undef snprintf
#undef vsnprintf
#undef strdup
#undef clearerr
#define fprintf bg_fp
#define fopen(n, m) bg_fo(n, m)
#define fclose(f) bg_fc(f)
#define fflush(f) bg_ff(f)
#define fputs(s, f) bg_fu(s, f)
#define fputc(c, f) bg_fk(c, f)
#define putc(c, f) bg_fk(c, f)
#define fgets(b, n, f) bg_fg(b, n, f)
#define fgetc(f) bg_fa(f)
#define getc(f) bg_fa(f)
#define fread(p, s, n, f) bg_fr(p, s, n, f)
#define fwrite(p, s, n, f) bg_fw(p, s, n, f)
#define ungetc(c, f) bg_ug(c, f)
#define feof(f) bg_fe(f)
#define ferror(f) bg_fx(f)
#define clearerr(f) ((f)->err = (f)->eof = 0)
#define rewind(f) bg_rw(f)
#define fseek(f, o, w) bg_fq(f, o, w)
#define perror(s) bg_pe(s)
#define gets(b) bg_gs(b)
int bg_sn(char *buf, unsigned n, const char *fmt, ...) PX_VA;
int bg_vs(char *buf, unsigned n, const char *fmt, va_list ap);
char *bg_sd(const char *s);
#define snprintf bg_sn
#define vsnprintf(b, n, f, a) bg_vs(b, n, f, a)
#define strdup(s) bg_sd(s)
#undef fileno
#define fileno(f) ((f)->fd)
#undef O_RDONLY
#undef O_WRONLY
#undef O_RDWR
#undef O_CREAT
#undef O_TRUNC
#undef O_EXCL
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_CREAT 64
#define O_TRUNC 512
#define O_EXCL 128
int bg_op(const char *name, int flags, ...) PX_VA;
int bg_cl(int fd);
int bg_rd(int fd, void *buf, unsigned n);
int bg_wr(int fd, const void *buf, unsigned n);
int bg_ul(const char *name);
#undef open
#define open bg_op
#undef close
#define close(fd) bg_cl(fd)
#undef read
#define read(fd, b, n) bg_rd(fd, b, n)
#undef write
#define write(fd, b, n) bg_wr(fd, b, n)
#undef unlink
#define unlink(n) bg_ul(n)

/* errno, and the user and group calls (the games drop privileges). */
extern int bg_en;
#undef errno
#define errno bg_en
#undef ERANGE
#undef EINVAL
#undef ENOENT
#undef EEXIST
#undef EINTR
#define ERANGE 34
#define EINVAL 22
#define ENOENT 2
#define EEXIST 17
#define EINTR 4
#undef getuid
#define getuid() 0
#undef getgid
#define getgid() 0
#undef setuid
#define setuid(u) 0
#undef setgid
#define setgid(g) 0

/* Signals: none comes (see signal() in posix_shim.h). */
typedef int sig_atomic_t;
typedef void *sig_t;
typedef int sigset_t;
#undef sigemptyset
#undef sigaddset
#undef sigprocmask
#undef SIG_BLOCK
#undef SIG_SETMASK
#define sigemptyset(s) 0
#define sigaddset(s, n) 0
#define sigprocmask(h, s, o) 0
#define SIG_BLOCK 0
#define SIG_SETMASK 2

/* Signals that the games name.  No signal ever comes. */
#ifndef SIGTSTP
#define SIGILL 4
#define SIGTRAP 5
#define SIGIOT 6
#define SIGEMT 7
#define SIGFPE 8
#define SIGBUS 10
#define SIGSEGV 11
#define SIGSYS 12
#define SIGPIPE 13
#define SIGALRM 14
#define SIGTSTP 18
#endif
#undef BUFSIZ
#define BUFSIZ 128
#undef SEEK_SET
#undef SEEK_CUR
#undef SEEK_END
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2

/* struct tm and localtime(): a constant time. */
struct tm {
    int tm_sec, tm_min, tm_hour, tm_mday, tm_mon, tm_year, tm_wday, tm_yday;
};
struct tm *bg_lt(const long *t);
#undef localtime
#define localtime(t) bg_lt(t)

/* getpwuid(): one user. */
struct passwd {
    char *pw_name;
    char *pw_dir;
    int pw_uid;
};
struct passwd *bg_pw(int uid);
#undef getpwuid
#define getpwuid(u) bg_pw(u)

/* random(): the shim's generator returns 0 to RAND_MAX. */
#undef RANDOM_MAX
#define RANDOM_MAX 32767

/* creat(), getopt(), htonl(), ntohl(), strerror(), NSIG */
int bg_go(int argc, char **argv, const char *opts);
long bg_hl(long x);
char *bg_sr(int e);
extern int bg_oi;
extern char *bg_oa;
#undef creat
#undef getopt
#undef optind
#undef optarg
#undef htonl
#undef ntohl
#undef strerror
#undef NSIG
#define creat(n, m) bg_op(n, 577, m)
#define getopt(c, v, o) bg_go(c, v, o)
#define optind bg_oi
#define optarg bg_oa
#define htonl(x) bg_hl(x)
#define ntohl(x) bg_hl(x)
#define strerror(e) bg_sr(e)
#define NSIG 32

/* alarm(), kill(), mvcur(), fgetln(), strlcpy() */
char *bg_fl(BG_FILE *f, unsigned *len);
unsigned bg_sc(char *dst, const char *src, unsigned n);
#undef alarm
#undef kill
#undef mvcur
#undef fgetln
#undef strlcpy
#define alarm(n) 0
#define kill(p, s) 0
#define mvcur(a, b, c, d) 0
#define fgetln(f, lp) bg_fl(f, lp)
#define strlcpy(d, s, n) bg_sc(d, s, n)

/* setjmp(), longjmp(): the SDCC setjmp.h cannot be read by clang, so every
 * compiler gets the same stand-ins.  The size-only games never run. */
typedef int jmp_buf[1];
int bg_sj(jmp_buf e);
void bg_lj(jmp_buf e, int v);
#undef setjmp
#undef longjmp
#define setjmp(e) bg_sj(e)
#define longjmp(e, v) bg_lj(e, v)

/* scanf(), sscanf(): the SDCC library has neither.  The stand-ins read
 * nothing and return 0. */
int bg_sf(const char *fmt, ...) PX_VA;
int bg_ss(const char *str, const char *fmt, ...) PX_VA;
#undef scanf
#undef sscanf
#define scanf bg_sf
#define sscanf bg_ss

/* ftell(), vfprintf(), abort() */
long bg_ft(BG_FILE *f);
int bg_vp(BG_FILE *f, const char *fmt, va_list ap);
#undef ftell
#undef vfprintf
#undef abort
#define ftell(f) bg_ft(f)
#define vfprintf(f, fmt, ap) bg_vp(f, fmt, ap)
#define abort() exit(134)

/* clock_gettime(): a constant. */
struct timespec {
    long tv_sec;
    long tv_nsec;
};
#undef CLOCK_REALTIME
#undef clock_gettime
#define CLOCK_REALTIME 0
int bg_cg(int clk, struct timespec *ts);
#define clock_gettime(c, t) bg_cg(c, t)

/* File status and time: stand-ins.  "struct stat" has only st_mtime.
 * time_t is long. */
typedef long time_t;
struct stat {
    long st_mtime;
};
int bg_st(const char *path, struct stat *sb);
int bg_fz(int fd, struct stat *sb);
char *bg_ct(const long *t);
#undef ctime
#define ctime(t) bg_ct(t)

#endif
