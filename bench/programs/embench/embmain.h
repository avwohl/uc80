/* embmain.h - main () for the Embench-IoT kernels in the uc80 bench.
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Takes the place of Embench support/main.c and a board file.  It runs
 * initialise_benchmark () and warm_caches (WARMUP_HEAT), times one call of
 * benchmark () between the TIMER_START and TIMER_STOP labels, and prints
 * PASS or FAIL from verify_benchmark ().  Each embench-* program has a
 * main.c that includes this file; the harness copies it next to main.c
 * and prepends bench.h to main.c.  The empty board hooks of Embench
 * (initialise_board, start_trigger, stop_trigger) are left out: they do
 * no work, and their names collide in 6-character linkers.
 */
#include <stdio.h>
#include "support.h"

int main (void)
{
    volatile int result;
    int correct;

    initialise_benchmark ();
    warm_caches (WARMUP_HEAT);
    intrinsic_label(TIMER_START);
    result = benchmark ();
    intrinsic_label(TIMER_STOP);
    correct = verify_benchmark (result);
    if (correct)
        printf ("PASS\n");
    else
        printf ("FAIL\n");
    return 0;
}
