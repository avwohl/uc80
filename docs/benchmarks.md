# Benchmarks and Test Results

Back to the [README](../README.md). The tables are in [benchmarks_speed.md](benchmarks_speed.md). The plan and the status of the benchmark work are in [benchmark_proposal.md](benchmark_proposal.md). [bench_harness.md](bench_harness.md) describes the benchmark harness in `bench/`.

## Speed and Size Summary

The numbers below come from the benchmark harness in `bench/` (see [bench_harness.md](bench_harness.md)). The harness builds 112 programs with 9 compilers and runs 105 of them under `z88dk-ticks`. The other 7 programs are big games that the harness only builds, to see if they fit in 64 KB. The full tables, the compiler versions and the list for each program are in [benchmarks_speed.md](benchmarks_speed.md). `bench/report.py` generates that file, so the file is always the source of the numbers.

How the means work: a ratio is uc80 divided by the other compiler. A ratio below 1.00 means that uc80 is smaller or faster. The mean is the geometric mean over the programs that are OK on both compilers. uc80 has one mode only.

The size ratio compares with the size mode of the other compiler. The T-state ratio compares with the speed mode.

| Other compiler | Programs (size) | Size ratio | Programs (T-states) | T-state ratio | T-state ratio without sort-* |
|---|---|---|---|---|---|
| sdcc 4.6.0 | 96 | 0.92 | 78 | 0.55 | 0.66 |
| sdcc 4.4.0 | 92 | 0.89 | 75 | 0.53 | 0.66 |
| z88dk zsdcc | 91 | 0.93 | 74 | 0.67 | 0.79 |
| z88dk sccz80 | 83 | 0.88 | 57 | 0.47 | 0.55 |
| hitech 3.09 | 69 | 0.45 | 58 | 0.42 | 0.55 |
| dcc 3.6.0 | 55 | 0.77 | 43 | 0.43 | 0.52 |
| llvm-z80 | 98 | 0.90 | 79 | 0.67 | 0.83 |
| z88dk 80cc (nightly) | 65 | 0.83 | 51 | 0.73 | 1.03 |

The T-state ratio uses only programs where both counts are TIMER-region counts. The games have no TIMER labels, so the games are in the size ratio but not in the T-state ratio. The games have their own numbers below.

What the data supports:

- **Size.** On the mean, uc80 makes programs that are about as big as those of SDCC, zsdcc, sccz80 and llvm-z80 (ratios 0.89 to 0.95). The programs of uc80 are smaller than those of dcc (0.79), z88dk 80cc (0.84) and HI-TECH C (0.46). Against SDCC 4.6.0, uc80 is smaller on 66 of 96 programs and bigger on 30. The mean size ratio against SDCC 4.6.0 was 1.53 before the library work. Three changes made the ratio 0.99 (measured again on the tree before the register calling convention: 0.987): the `printf` to `puts` rewrite, the split of the float printer and the split of libc into one module for each public function. The register calling convention (family F, steps 1 and 2) and the integration with the C library rewrite made the ratio 0.96. Register locals (family G: two locals in BC and DE, one pointer in IY) made the ratio 0.93. See the "Unreleased" part of the [CHANGELOG](../CHANGELOG.md) and [codegen_gap.md](codegen_gap.md). The claim "smallest known binaries for Z80/CP/M" is not true and this page does not make the claim.
- **Speed.** uc80 is faster than SDCC 4.6.0 (speed mode) on 63 of 78 programs. The mean ratio is 0.55. Much of the gain comes from the library: `qsort()` of SDCC is up to 5000 times slower than the one of uc80 on sorted input. The 32-bit programs `pi`, `pi_ldiv` and `davidly-tpi` are also 4 to 5 times faster on uc80 than on SDCC. Without the eight `sort-*` programs the mean ratio is 0.66. The speed families D and E of [codegen_gap.md](codegen_gap.md) made the `snd-*` kernels and `davidly-ttt` faster, and they moved the mean from 0.629 to 0.623. The register calling convention (family F, steps 1 and 2: parameters in registers, byte results in A, tail calls, push and pop for parameters across a call) moved the mean to 0.585 on the tree that has the library work (0.606 on the same tree without the register calling convention). Register locals (family G, steps 1 and 2) moved the mean to 0.563: step 1 gave 0.565 and the pointer in IY gave the rest (see [register_locals.md](register_locals.md)). uc80 is slower than SDCC on small integer loops and on code with many small functions (for example `sieve`, `queens`, `tak`, `stanford-perm`, `stanford-towers`, `davidly-ttt`).
- **llvm-z80 and z88dk 80cc are the closest rivals on speed.** Without the `sort-*` programs the mean ratio against llvm-z80 is 0.86 (uc80 wins on 45 programs and loses on 26). z88dk 80cc is the only compiler that is faster than uc80 on the mean without `sort-*`: the ratio is 1.06 (uc80 wins on 23 of 43 programs). With the `sort-*` programs the ratio is 0.75.
- **Games (tier 3).** 25 game programs run with scripted input. uc80 runs 24 of them, sdcc 18, sdcc-4.4 17, zsdcc 17, sccz80 16, llvm-z80 19, 80cc 14, dcc 12 and HI-TECH C 11. The numbers below are the geometric mean of uc80 divided by the other compiler over the games that run on both. The T-state counts are whole-run counts (the games have no TIMER labels), compared with the speed mode of the other compiler. The table is from before the register calling convention (steps 1 and 2) and before the library rewrite in C. It is not recomputed: the games method is not in the repository. The register calling convention made uc80 programs a little smaller and faster, so the ratios are a little too high.

| Other compiler | Games on both | Size ratio | T-state ratio |
|---|---|---|---|
| sdcc 4.6.0 | 18 | 0.79 | 1.00 |
| sdcc 4.4.0 | 17 | 0.78 | 0.75 |
| z88dk zsdcc | 17 | 0.81 | 0.68 |
| z88dk sccz80 | 16 | 0.84 | 0.43 |
| hitech 3.09 | 11 | 0.50 | 0.20 |
| dcc 3.6.0 | 12 | 0.80 | 0.70 |
| llvm-z80 | 19 | 0.74 | 1.06 |
| z88dk 80cc (nightly) | 14 | 0.81 | 0.62 |

  On the games the uc80 programs are 21 percent smaller than SDCC 4.6.0 and 26 percent smaller than llvm-z80. The mean T-state ratio of 1.00 hides two groups. Most stdio games that use `printf` and `scanf` are faster on uc80 (for example `fuzix-startrek` 40 M against 68 M T-states), because the library is faster. The curses games are 30 to 48 percent slower on uc80 than on SDCC 4.6.0 (`robots`, `worm`, `canfield`, `invaders` and `mille`). `snake` is 9 percent slower. [bench_games_stdio.md](bench_games_stdio.md) and [bench_games_curses.md](bench_games_curses.md) describe these games.
- **Big games (size only).** `monop` and `mojozork` fit in 64 KB with every compiler that links them (uc80: 41,371 and 39,772 bytes, SDCC 4.6.0: 42,636 and 34,063). `trek` and `adventure` do not fit with any compiler. `battlestar` (120 KB of room data) does not fit with any compiler that links it. Rogue 3.6 builds its objects only with uc80 and llvm-z80. For `battlestar` and Rogue 3.6 the whole-program uc80 link stops at the time limit (BUILD-TIMEOUT). [bench_big_games.md](bench_big_games.md) has the details. The register-convention tree is not smaller on every game: `mojozork` grows from 38,447 to 39,772 bytes (image plus BSS, +1,325) against `c2fcd87` run again with the same harness, and the `monop` image grows by 325 bytes while its BSS falls by 535 (41,581 to 41,371 in all). The cause is the call graph fix: a function that a call through a pointer can reach now keeps its locals on the stack and not in the shared `??AUTO` area, and these two games call through tables of function pointers. The fix is a correctness fix (see CHANGELOG).
- **Programs that fail on uc80** (1 of 105 that run): `empire-trefonas` prints wrong output. The image is 63,360 bytes, close to the 64 KB limit. All other compilers fail to build this program. Earlier versions of this page named four more failures (`embench-tarfind`, `whetstone`, `whetstone+static` and `coremark-unofficial`). Code generator fixes in the "Unreleased" part of the [CHANGELOG](../CHANGELOG.md) fix these four programs.
- All numbers are for one version of each compiler on one machine. They are T-states of the Z80 program, not host time, so they do not change from run to run.

How the size changed since the first run (commit 24fdef8, 61 programs that are OK on uc80 and SDCC 4.6.0 in both runs). The "Now" column is recomputed on the integrated tree (same method as the first run: image bytes minus the empty program, T-state region counts, SDCC 4.6.0 size mode). The size-mode means on the 109 programs are in the summary table at the top of this page.

| Measure | First run | Now |
|---|---|---|
| Size ratio against SDCC 4.6.0 (size mode) | 2.01 | 1.03 |
| T-state ratio against SDCC 4.6.0 (size mode) | 0.87 | 0.54 |
| Size ratio without `sort-*` (53 programs) | 1.96 | 0.95 |
| T-state ratio without `sort-*` (53 programs) | 1.20 | 0.69 |
| Programs that are OK on uc80 | 61 | 104 |

An earlier note gave "1.93 times bigger and 2.47 times slower than SDCC" for 53 programs. The data of the first run gives 1.96 for the size ratio, which is close. The same data gives 1.20, not 2.47, for the time ratio. The old time figure is not reproduced and is not used.

[size_analysis_0053_0456.md](size_analysis_0053_0456.md) analyzes the worst-case size comparison against z88dk. We removed the size table that earlier versions of this page showed (uc80 against z88dk on the Fujitsu tests, with a "46%" ratio). The script that made the table is not in the repository. Most of the "46%" came from the 4.5 KB of start-up code and library that z88dk always links (see the `empty` row of [benchmarks_speed.md](benchmarks_speed.md)), not from code generation.

## Test Results

Tested against multiple external test suites:

| Suite | Pass Rate | Notes |
|-------|-----------|-------|
| [c-testsuite](https://github.com/c-testsuite/c-testsuite) | 220/220 | |
| c-testsuite `--int=32` | 219/220 | 00200 (long-long shift overflows the 64K TPA) |
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
