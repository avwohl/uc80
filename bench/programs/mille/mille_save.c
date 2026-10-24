/* mille_save.c - replaces NetBSD games/mille/save.c and varpush.c in
 * the benchmark.  The originals save and restore a game in a file
 * (open, writev, stat, ctime).  The scripted run never saves.  These
 * stubs have the same names and return "not saved".
 *
 * SPDX-License-Identifier: GPL-3.0-or-later
 * Copyright (C) uc80 authors.
 */
#include "mille.h"

bool save(void)
{
    return FALSE;
}

bool rest_f(const char *file)
{
    return FALSE;
}
