# Benchmarks and Test Results

Back to the [README](../README.md). The tables are in [benchmarks_speed.md](benchmarks_speed.md). The plan and the status of the benchmark work are in [benchmark_proposal.md](benchmark_proposal.md). [bench_harness.md](bench_harness.md) describes the benchmark harness in `bench/`.

## Speed and Size Summary

The numbers below come from the benchmark harness in `bench/` (see [bench_harness.md](bench_harness.md)). The harness builds 79 programs with 8 compilers and runs each program under `z88dk-ticks`. The full tables, the compiler versions and the list for each program are in [benchmarks_speed.md](benchmarks_speed.md). `bench/report.py` generates that file, so the file is always the source of the numbers.

How the means work: a ratio is uc80 divided by the other compiler. A ratio below 1.00 means that uc80 is smaller or faster. The mean is the geometric mean over the programs that are OK on both compilers. uc80 has one mode only.

The size ratio compares with the size mode of the other compiler. The T-state ratio compares with the speed mode.

| Other compiler | Programs (size) | Size ratio | Programs (T-states) | T-state ratio | T-state ratio without sort-* |
|---|---|---|---|---|---|
| sdcc 4.6.0 | 74 | 1.58 | 74 | 0.62 | 0.77 |
| sdcc 4.4.0 | 72 | 1.55 | 72 | 0.61 | 0.77 |
| z88dk zsdcc | 71 | 1.57 | 71 | 0.76 | 0.92 |
| z88dk sccz80 | 64 | 1.41 | 54 | 0.53 | 0.64 |
| hitech 3.09 | 58 | 0.73 | 58 | 0.48 | 0.64 |
| dcc 3.6.0 | 42 | 1.62 | 42 | 0.49 | 0.61 |
| llvm-z80 | 75 | 1.58 | 75 | 0.76 | 0.98 |

What the data supports:

- **Size.** uc80 makes larger programs than SDCC, zsdcc, sccz80, dcc and llvm-z80. Against SDCC 4.6.0, uc80 is smaller on 15 of 74 programs and bigger on 59. Only HI-TECH C makes bigger programs than uc80 (ratio 0.73). The claim "smallest known binaries for Z80/CP/M" is not true and this page does not make the claim.
- **Speed.** uc80 is faster than SDCC 4.6.0 (speed mode) on 52 of 74 programs. The mean ratio is 0.62. Much of the gain comes from the library: `qsort()` of SDCC is up to 5000 times slower than the one of uc80 on sorted input. The 32-bit programs `pi`, `pi_ldiv` and `davidly-tpi` are also 4 to 5 times faster on uc80 than on SDCC. Without the eight `sort-*` programs the mean ratio is 0.77. uc80 is slower than SDCC on small integer loops and on code with many small functions (for example `sieve`, `queens`, `tak`, `stanford-perm`, `stanford-towers`, `davidly-ttt`).
- **llvm-z80 is the closest rival on speed.** Without the `sort-*` programs the mean ratio is 0.98, and uc80 wins on 36 programs and loses on 31.
- **Programs that fail on uc80** (4 of 79): `embench-tarfind` (internal error in the code generator, `'TypeNameWithDeclarator' object has no attribute 'kind'`, raised while it evaluates a `sizeof (type)` inside a call argument), `whetstone` and `whetstone+static` (the timed build fails: an `asm` statement directly after a label is a parse error, and `whetstone` has `TIMER_START();` after the label `LCONT:`), and `coremark-unofficial` (the CRC values are wrong).
- All numbers are for one version of each compiler on one machine. They are T-states of the Z80 program, not host time, so they do not change from run to run.

How the size changed since the first run (commit 24fdef8, 61 programs that are OK on uc80 and SDCC 4.6.0 in both runs):

| Measure | First run | Now |
|---|---|---|
| Size ratio against SDCC 4.6.0 (size mode) | 2.01 | 1.66 |
| T-state ratio against SDCC 4.6.0 (size mode) | 0.87 | 0.58 |
| Size ratio without `sort-*` (53 programs) | 1.96 | 1.61 |
| T-state ratio without `sort-*` (53 programs) | 1.20 | 0.75 |
| Programs that are OK on uc80 | 61 | 75 |

An earlier note gave "1.93 times bigger and 2.47 times slower than SDCC" for 53 programs. The data of the first run gives 1.96 for the size ratio, which is close. The same data gives 1.20, not 2.47, for the time ratio. The old time figure is not reproduced and is not used.

[size_analysis_0053_0456.md](size_analysis_0053_0456.md) analyzes the worst-case size comparison against z88dk. We removed the size table that earlier versions of this page showed (uc80 against z88dk on the Fujitsu tests, with a "46%" ratio). The script that made the table is not in the repository. Most of the "46%" came from the 4.5 KB of start-up code and library that z88dk always links (see the `empty` row of [benchmarks_speed.md](benchmarks_speed.md)), not from code generation.

## Test Results

Tested against multiple external test suites:

| Suite | Pass Rate | Notes |
|-------|-----------|-------|
| [c-testsuite](https://github.com/c-testsuite/c-testsuite) | 220/220 | full pass |
| c-testsuite `--int=32` | 219/220 | 00200 (long-long shift) overflows 64K TPA |
| c-testsuite `--int=32 --long=64` | 218/220 | same as above + marginal timeout |
| [Fujitsu compiler-test-suite](https://github.com/fujitsu/compiler-test-suite) 0003 | 371/374 | |
| Fujitsu 0010 | 58/75 | 9 int16, 1 float, 2 timeout |
| Fujitsu 0011 | 287/335 | 14 int16, 5 large struct |
| Fujitsu 0012 | 4/9 | 4 int16/long long, 1 static DCE |
| [SDCC regression tests](https://sourceforge.net/projects/sdcc/) | 514/523 | 3 fail, 4 sdcc ext, 2 multi-file link |

Remaining tests that do not pass are environmental, not codegen bugs:
- **sdcc ext**: SDCC-specific extensions (`__asm`, `#pragma save/restore`)
- **multi-file**: tests that require separate compilation units
- **float precision**: ACOSF/TANF near asymptotes (single-precision IEEE 754 limit)
- **malloc OOM**: SDCC test asserts `malloc(2000) == NULL`; we have plenty of TPA
- **00200**: 67KB binary exceeds 64KB CP/M TPA
