/* sglue.h - console input for the stdio game group of the uc80 benchmark
 * harness (bench/programs.d/games_stdio.toml).
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * Include this header AFTER posix_shim.h and link sglue.c.  The same text
 * is compiled by every benchmark compiler and by the host compiler that
 * makes the golden file.
 *
 * z88dk-ticks reads the end of the input file as the byte 0FFh (and a CP/M
 * text file ends with 1Ah), not as EOF.  sg_getc() turns both bytes into
 * EOF, skips CR, and so a filter that reads "until EOF" ends at the end of
 * the script.  sg_fgets() is fgets() on top of sg_getc(): the HI-TECH C
 * library reads a line with BDOS function 10, which z88dk-ticks does not
 * implement, and getchar() of the shim does not use that function.
 *
 * C89 only, no variadic macros, no // comments.
 */
#ifndef SGLUE_H
#define SGLUE_H

/* SDCC (and llvm-z80, which uses the SDCC headers) have no FILE, stdin,
 * stdout, stderr, fflush, fprintf, ferror, feof or perror.  A game that
 * writes "fflush(stdout)" or "fprintf(stderr, ...)" would then not compile.
 * For those two compilers the glue maps the calls to the console: stdout
 * and stderr both go to the console (the golden file holds only the
 * standard output, and no game of this group writes to stderr in a normal
 * run).  The other compilers have the real functions. */
#if (defined(__SDCC) && !defined(__Z88DK)) \
    || (defined(__z80__) && defined(__clang__))
#define SG_NOFILE 1
#endif

#ifdef SG_NOFILE
#undef stdin
#undef stdout
#undef stderr
#define stdin ((void *)0)
#define stdout ((void *)1)
#define stderr ((void *)2)
#undef fflush
#define fflush(f) 0
#undef ferror
#define ferror(f) 0
#undef feof
#define feof(f) 0
#undef fprintf
#define fprintf sg_fprintf
#undef fputs
#define fputs(s, f) sg_fputs(s)
#undef perror
#define perror(s) sg_perror(s)
int sg_fprintf(void *f, const char *fmt, ...) PX_VA;
void sg_perror(const char *s);
int sg_fputs(const char *s);
#endif

#ifndef SGLUE_C
int sg_getc(void);
char *sg_fgets(char *buf, int n);

#undef getchar
#define getchar() sg_getc()
#undef fgets
#define fgets(b, n, f) sg_fgets(b, n)
#endif

#endif
