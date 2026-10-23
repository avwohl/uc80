/* support.h - stand-in for Embench-IoT support/support.h in the uc80 bench.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Declares the Embench benchmark API (the same functions as the upstream
 * support.h), fixes the board values that the Embench build system passes
 * on the command line, and removes __attribute__ for the compilers that
 * do not have it (SDCC and z88dk).  Every compiler reads this same file.
 */
#ifndef SUPPORT_H
#define SUPPORT_H

#if !defined(__GNUC__) && !defined(__UC80__)
#define __attribute__(x)
#define __attribute(x)
#endif

#ifndef GLOBAL_SCALE_FACTOR
#define GLOBAL_SCALE_FACTOR 1
#endif
#ifndef WARMUP_HEAT
#define WARMUP_HEAT 0
#endif

int verify_benchmark (int result);
void initialise_benchmark (void);
void warm_caches (int temperature);
int benchmark (void) __attribute__ ((noinline));

/* stdlib.h first: beebsc.h redefines RAND_MAX, and a later stdlib.h
 * must not define it again (z88dk stops on that). */
#include <stdlib.h>
#include "beebsc.h"

#endif
