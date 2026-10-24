/* guess.c - a small stdio guessing game, as a benchmark of a scripted
 * interactive program.  The commands come from stdin (input.txt), the
 * random numbers from the portable generator of posix_shim.c, so the
 * output is the same on every compiler.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 *
 * Commands: a number (a guess), hint, new (give up and start again),
 * score, quit (any case).  Anything else gets a warning.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "posix_shim.h"

#define LOW 1
#define HIGH 100

static int secret;
static int lo, hi, tries, wins, rounds;

static void start_round(void)
{
    secret = LOW + rand() % (HIGH - LOW + 1);
    lo = LOW;
    hi = HIGH;
    tries = 0;
    rounds++;
    printf("Round %d: I picked a number from %d to %d.\n", rounds, LOW, HIGH);
}

/* Read one line without the newline.  Return 0 at end of input. */
static int read_line(char *buf, int size)
{
    int c, n = 0;

    for (;;) {
        c = getchar();
        if (c == EOF || c == 26 || c == 255)
            return 0;
        if (c == '\r')
            continue;
        if (c == '\n')
            break;
        if (n < size - 1)
            buf[n++] = (char)c;
    }
    buf[n] = 0;
    return 1;
}

int main(int argc, char **argv)
{
    char line[40];
    char *p;
    int g, quit = 0;

    bench_args(&argc, &argv);
    /* the mask keeps the seed the same for a 16-bit and a 32-bit unsigned */
    srand((unsigned)((getpid() + time(NULL)) & 0x7fff));
    if (getenv("GUESS_QUIET") != NULL)
        puts("quiet mode");
    signal(SIGINT, SIG_IGN);
    printf("guess the number (argv ok: %d)\n", argc >= 1 && argv[0] != NULL);
    start_round();
    while (!quit) {
        printf("guess> ");
        if (!read_line(line, sizeof line)) {
            puts("end of input");
            break;
        }
        p = line;
        while (*p == ' ')
            p++;
        if (isdigit(*p & 255)) {
            g = atoi(p);
            tries++;
            if (g < LOW || g > HIGH) {
                warnx("%d is out of range", g);
            } else if (g < secret) {
                if (g >= lo)
                    lo = g + 1;
                printf("%d is too low.\n", g);
            } else if (g > secret) {
                if (g <= hi)
                    hi = g - 1;
                printf("%d is too high.\n", g);
            } else {
                printf("%d is right, in %d tries.\n", g, tries);
                wins++;
                start_round();
            }
        } else if (strcasecmp(p, "quit") == 0) {
            quit = 1;
        } else if (strcasecmp(p, "hint") == 0) {
            printf("It is between %d and %d.\n", lo, hi);
        } else if (strcasecmp(p, "new") == 0) {
            printf("It was %d.\n", secret);
            start_round();
        } else if (strcasecmp(p, "score") == 0) {
            printf("%d won in %d rounds.\n", wins, rounds);
        } else if (index(p, '?') != NULL) {
            puts("Try: a number, hint, new, score or quit.");
        } else {
            warnx("what is '%s'?", p);
        }
    }
    printf("Final: %d won in %d rounds. Bye.\n", wins, rounds);
    return 0;
}
