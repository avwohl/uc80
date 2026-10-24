/* robots_score.c - replaces NetBSD games/robots/score.c in the
 * benchmark.  The original needs a score file and getpwuid().  When it
 * cannot open the score file, the original score() sets Newscore to
 * false and returns, and the game does not show a score table.  This
 * file does the same for every run.
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include "robots.h"

const char *Scorefile = "robots_roll";

int Max_per_uid = 0;

void score(int score_wfd)
{
    Newscore = false;
}

void show_score(void)
{
}
