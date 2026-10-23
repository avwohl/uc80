/* bench.h - portable timing labels for the uc80 benchmark harness.
 *
 * The z88dk benchmark sources mark the timed region with
 * intrinsic_label(TIMER_START) and intrinsic_label(TIMER_STOP), which
 * only z88dk provides.  Every program in bench/ includes this header, so
 * each compiler turns the same call into a global assembly label.  The
 * harness reads the label addresses from the link map and runs
 * z88dk-ticks with -start/-end on them.
 *
 * The #elif chain can stay: the HI-TECH adapter (bench/cc_hitech.py)
 * turns each #elif into #else + #if itself.  Every preprocessor still
 * scans the groups that it skips, so a branch must not contain a
 * directive that another compiler's preprocessor rejects even in a
 * skipped group (HI-TECH CPP rejects #pragma, #warning and #error).
 */
#ifndef BENCH_H
#define BENCH_H

#if defined(__Z88DK)
#include <intrinsic.h>
#elif defined(__UC80__)
#define intrinsic_label(name) asm(#name "::")
#elif defined(__SDCC)
#define intrinsic_label(name) __asm__(#name "::")
#elif defined(HI_TECH_C)
/* HI-TECH C 3.09 has no # or ## operator, but its preprocessor replaces
 * macro parameters inside string literals and joins tokens around an
 * empty comment.  The label gets a leading _ because OPTIM deletes every
 * label without one that no jump uses; bench/cc_hitech.py reads
 * _TIMER_START from the map as TIMER_START, and turns each asm("text");
 * into an #asm block, as the asm() statement of this P1 does not work. */
#define __HTC_LABEL(sym) asm("global sym"); asm("sym:")
#define intrinsic_label(name) __HTC_LABEL(_/**/name)
#elif defined(_DCC_)
/* dcc has no asm statement inside a function (an #asm block is emitted
 * between functions), so each label is the entry of an empty function
 * and the timed region includes one RET and one CALL (27 T-states).
 * L80 keeps 6 characters of a name; bench/cc_dcc.py maps _BT_BE and
 * _BT_EN to TIMER_START and TIMER_STOP. */
void bt_beg(void);
void bt_end(void);
#asm
	public	_bt_beg
_bt_beg:
	ret
	public	_bt_end
_bt_end:
	ret
#endasm
#define intrinsic_label(name) bench_dcc_##name()
#define bench_dcc_TIMER_START() bt_beg()
#define bench_dcc_TIMER_STOP() bt_end()
#elif defined(__z80__) && defined(__clang__)
/* llvm-z80 (--target=z80-unknown-none-sdcc, bench/cc_llvmz80.py): the
 * asm text goes to sdasz80 unchanged, so "NAME::" is a global label, as
 * for SDCC.  This branch must come before the host branch. */
#define intrinsic_label(name) __asm__ volatile(#name "::" ::: "memory")
#elif defined(__clang__) || defined(__GNUC__)
/* Host build, used to make the golden output files. */
#define intrinsic_label(name)
#else
#define intrinsic_label(name)
#endif

#endif
