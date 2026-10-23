/*
Copyright 2018 Embedded Microprocessor Benchmark Consortium (EEMBC)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Original Author: Shay Gal-on

Modified for the uc80 benchmark harness (bench/), from simple/core_portme.c
of eembc/coremark commit 1f483d5b.  The harness prepends
#include "bench.h" to this file.  start_time() and stop_time() hold the
TIMER_START and TIMER_STOP labels, and z88dk-ticks counts the T-states
between the two labels.  There is no clock on CP/M, so get_time() and
time_in_secs() return 0.  core_main.c then prints "Total ticks : 0" and
"ERROR! Must execute for at least 10 secs", the same text for every
compiler.  This is NOT a valid CoreMark score.
*/
#include <stdio.h>
#include <stdlib.h>
#include "coremark.h"

#if VALIDATION_RUN
volatile ee_s32 seed1_volatile = 0x3415;
volatile ee_s32 seed2_volatile = 0x3415;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PERFORMANCE_RUN
volatile ee_s32 seed1_volatile = 0x0;
volatile ee_s32 seed2_volatile = 0x0;
volatile ee_s32 seed3_volatile = 0x66;
#endif
#if PROFILE_RUN
volatile ee_s32 seed1_volatile = 0x8;
volatile ee_s32 seed2_volatile = 0x8;
volatile ee_s32 seed3_volatile = 0x8;
#endif
volatile ee_s32 seed4_volatile = ITERATIONS;
volatile ee_s32 seed5_volatile = 0;

void
start_time(void)
{
    intrinsic_label(TIMER_START);
}

void
stop_time(void)
{
    intrinsic_label(TIMER_STOP);
}

CORE_TICKS
get_time(void)
{
    return 0;
}

secs_ret
time_in_secs(CORE_TICKS ticks)
{
    (void)ticks;
    return 0;
}

ee_u32 default_num_contexts = 1;

void
portable_init(core_portable *p, int *argc, char *argv[])
{
    (void)argc;
    (void)argv;
    if (sizeof(ee_ptr_int) != sizeof(ee_u8 *))
    {
        ee_printf(
            "ERROR! Please define ee_ptr_int to a type that holds a "
            "pointer!\n");
    }
    if (sizeof(ee_u32) != 4)
    {
        ee_printf("ERROR! Please define ee_u32 to a 32b unsigned type!\n");
    }
    p->portable_id = 1;
}

void
portable_fini(core_portable *p)
{
    p->portable_id = 0;
}
