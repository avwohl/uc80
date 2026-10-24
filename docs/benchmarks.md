# Benchmarks and Test Results

Back to the [README](../README.md). The tables are in [benchmarks_speed.md](benchmarks_speed.md). The plan and the status of the benchmark work are in [benchmark_proposal.md](benchmark_proposal.md). [bench_harness.md](bench_harness.md) describes the benchmark harness in `bench/`.

## Speed and Size Summary

The numbers below come from the benchmark harness in `bench/` (see [bench_harness.md](bench_harness.md)). The harness builds 112 programs with 9 compilers and runs 105 of them under `z88dk-ticks`. The other 7 programs are big games that the harness only builds, to see if they fit in 64 KB. The full tables, the compiler versions and the list for each program are in [benchmarks_speed.md](benchmarks_speed.md). `bench/report.py` generates that file, so the file is always the source of the numbers.

How the means work: a ratio is uc80 divided by the other compiler. A ratio below 1.00 means that uc80 is smaller or faster. The mean is the geometric mean over the programs that are OK on both compilers. uc80 has one mode only.

The size ratio compares with the size mode of the other compiler. The T-state ratio compares with the speed mode.

| Other compiler | Programs (size) | Size ratio | Programs (T-states) | T-state ratio | T-state ratio without sort-* |
|---|---|---|---|---|---|
| sdcc 4.6.0 | 96 | 1.53 | 78 | 0.63 | 0.77 |
| sdcc 4.4.0 | 92 | 1.50 | 75 | 0.61 | 0.77 |
| z88dk zsdcc | 91 | 1.57 | 74 | 0.77 | 0.92 |
| z88dk sccz80 | 83 | 1.43 | 57 | 0.54 | 0.65 |
| hitech 3.09 | 69 | 0.76 | 58 | 0.48 | 0.64 |
| dcc 3.6.0 | 55 | 1.55 | 43 | 0.50 | 0.62 |
| llvm-z80 | 98 | 1.50 | 79 | 0.77 | 0.97 |
| z88dk 80cc (nightly) | 65 | 1.48 | 51 | 0.84 | 1.20 |

The T-state ratio uses only programs where both counts are TIMER-region counts. The games have no TIMER labels, so the games are in the size ratio but not in the T-state ratio. The games have their own numbers below.

What the data supports:

- **Size.** uc80 makes larger programs than SDCC, zsdcc, sccz80, dcc, llvm-z80 and z88dk 80cc. Against SDCC 4.6.0, uc80 is smaller on 19 of 96 programs and bigger on 77. Only HI-TECH C makes bigger programs than uc80 (ratio 0.76). The claim "smallest known binaries for Z80/CP/M" is not true and this page does not make the claim.
- **Speed.** uc80 is faster than SDCC 4.6.0 (speed mode) on 55 of 78 programs. The mean ratio is 0.63. Much of the gain comes from the library: `qsort()` of SDCC is up to 5000 times slower than the one of uc80 on sorted input. The 32-bit programs `pi`, `pi_ldiv` and `davidly-tpi` are also 4 to 5 times faster on uc80 than on SDCC. Without the eight `sort-*` programs the mean ratio is 0.77. uc80 is slower than SDCC on small integer loops and on code with many small functions (for example `sieve`, `queens`, `tak`, `stanford-perm`, `stanford-towers`, `davidly-ttt`).
- **llvm-z80 and z88dk 80cc are the closest rivals on speed.** Without the `sort-*` programs the mean ratio against llvm-z80 is 0.97 (uc80 wins on 38 programs and loses on 33). z88dk 80cc is the only compiler that is faster than uc80 on the mean without `sort-*`: the ratio is 1.20 (uc80 wins on 19 of 43 programs). With the `sort-*` programs the ratio is 0.84.
- **Games (tier 3).** 25 game programs run with scripted input. uc80 runs 24 of them, sdcc 18, sdcc-4.4 17, zsdcc 17, sccz80 16, llvm-z80 19, 80cc 14, dcc 12 and HI-TECH C 11. The numbers below are the geometric mean of uc80 divided by the other compiler over the games that run on both. The T-state counts are whole-run counts (the games have no TIMER labels), compared with the speed mode of the other compiler.

| Other compiler | Games on both | Size ratio | T-state ratio |
|---|---|---|---|
| sdcc 4.6.0 | 18 | 1.34 | 1.02 |
| sdcc 4.4.0 | 17 | 1.34 | 0.77 |
| z88dk zsdcc | 17 | 1.45 | 0.69 |
| z88dk sccz80 | 16 | 1.46 | 0.44 |
| hitech 3.09 | 11 | 0.94 | 0.21 |
| dcc 3.6.0 | 12 | 1.44 | 0.71 |
| llvm-z80 | 19 | 1.22 | 1.09 |
| z88dk 80cc (nightly) | 14 | 1.39 | 0.63 |

  On the games the uc80 programs are 34 percent bigger than SDCC 4.6.0 and 22 percent bigger than llvm-z80. The mean T-state ratio of 1.02 hides two groups. Most stdio games that use `printf` and `scanf` are faster on uc80 (for example `fuzix-startrek` 40 M against 68 M T-states), because the library is faster. The curses games are 37 to 59 percent slower on uc80 than on SDCC 4.6.0 (`robots`, `worm`, `canfield`, `invaders` and `mille`). `snake` is 14 percent slower. [bench_games_stdio.md](bench_games_stdio.md) and [bench_games_curses.md](bench_games_curses.md) describe these games.
- **Big games (size only).** `monop` and `mojozork` fit in 64 KB with every compiler that links them (uc80: 43,297 and 37,301 bytes, SDCC 4.6.0: 40,385 and 28,505). `trek` and `adventure` do not fit with any compiler. `battlestar` (120 KB of room data) does not fit with any compiler that links it. Rogue 3.6 builds its objects only with uc80 and llvm-z80. For `battlestar` and Rogue 3.6 the whole-program uc80 link stops at the time limit (BUILD-TIMEOUT). [bench_big_games.md](bench_big_games.md) has the details.
- **Programs that fail on uc80** (1 of 105 that run): `empire-trefonas` does not exit, because the floating-point code makes an image of 69,888 bytes, which is more than 64 KB. All other compilers fail to build this program. Earlier versions of this page named four more failures (`embench-tarfind`, `whetstone`, `whetstone+static` and `coremark-unofficial`). Code generator fixes in the "Unreleased" part of the [CHANGELOG](../CHANGELOG.md) made these four programs pass.
- All numbers are for one version of each compiler on one machine. They are T-states of the Z80 program, not host time, so they do not change from run to run.

How the size changed since the first run (commit 24fdef8, 61 programs that are OK on uc80 and SDCC 4.6.0 in both runs):

| Measure | First run | Now |
|---|---|---|
| Size ratio against SDCC 4.6.0 (size mode) | 2.01 | 1.66 |
| T-state ratio against SDCC 4.6.0 (size mode) | 0.87 | 0.58 |
| Size ratio without `sort-*` (53 programs) | 1.96 | 1.61 |
| T-state ratio without `sort-*` (53 programs) | 1.20 | 0.75 |
| Programs that are OK on uc80 | 61 | 104 |

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
