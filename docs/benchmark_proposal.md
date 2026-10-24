# Benchmark Expansion Proposal

Status: proposal, revised 2026-10-24. Phases 0 to 2, 4, 5 and part of 6 are done in part or in full (see sections 1 and 7). The plan text of the other sections is the original plan. Starting point: issue
[avwohl/uc80#1](https://github.com/avwohl/uc80/issues/1), "No sdcc comparison in benchmarks".
The results are in [benchmarks.md](benchmarks.md) and [benchmarks_speed.md](benchmarks_speed.md).

Every number in this document comes from a cited source or from a test run in a scratch copy of the toolchain.
Items marked "unverified" were not confirmed. Effort figures are estimates, not measurements.

## 1. Summary

### Status on 2026-10-24

What is done:

- The harness `bench/` has 112 programs and 9 compilers (uc80, SDCC 4.6.0 and 4.4.0, z88dk zsdcc, sccz80 and 80cc, HI-TECH C 3.09, dcc 3.6.0, llvm-z80). It measures T-states with `z88dk-ticks`. 105 programs run, and 7 big games are built only ("size-only"). [bench_harness.md](bench_harness.md) describes the harness. [benchmarks_speed.md](benchmarks_speed.md) has the tables and a summary of uc80 against each compiler.
- Tier 1 (classic benchmarks) and tier 2 (Embench, Stanford, decompressors, sound-driver kernels, CoreMark) are in.
- z88dk 80cc is in (nightly tree, `-compiler=80cc -O3`, adapter `bench/cc_z88dk80cc.py`). It is the only compiler that is faster than uc80 on the mean without the `sort-*` programs (ratio 1.20).
- The games tier (tier 3, 32 programs) is in. 25 programs run with scripted input: our own `game-guess` and `curses-demo`, 6 curses games (robots, worm, snake, mille, canfield, invaders), 4 V7 games, 9 NetBSD stdio games, Super Star Trek (integer version), micro-Max chess, Hamurabi and Empire. 7 programs are size-only: Rogue 3.6, trek, adventure, battlestar, monop, mojozork and `curses-demo-size`. The shared glue is `bench/support/posix_shim.[ch]` and `bench/support/tinycurses.[ch]`. The details are in [bench_games_stdio.md](bench_games_stdio.md), [bench_games_curses.md](bench_games_curses.md) and [bench_big_games.md](bench_big_games.md).
- The unused-`EXTRN` blocker (6.1) is fixed. The heap bug, `qsort`, `printf` precision, `div()` and `ldiv()` are fixed (6.4, 6.5).
- The four programs that failed on uc80 earlier now pass: `embench-tarfind` (`sizeof` of a pointer type in a call argument), `whetstone` and `whetstone+static` (a label before an `asm` statement) and `coremark-unofficial` (pointer plus `long` index). The games found 6 more uc80 bugs. We fixed all of them: `atof`, `scanf`, `pow`, a string escape, a dead-code pass that kept two copies of `__tmp32`, a pointer difference that had the pointer type, and a call to a function whose name differs from another global only in case. See the CHANGELOG, "Unreleased".
- Eight families of code generator work and a faster runtime are in (see the CHANGELOG, "Unreleased").
- `bench/difftest.py` compares uc80, SDCC and a Python model on random programs. A run of 200 programs shows no uc80 failure.

Result against SDCC 4.6.0 (96 programs for size and 78 for T-states that are OK on both, geometric mean of uc80 divided by SDCC):

- Size: 1.53. uc80 is bigger. The first run gave 2.01 on 61 programs. uc80 is smaller on 19 programs and bigger on 77.
- T-states against the speed mode of SDCC: 0.63. uc80 is faster. The first run gave 0.87 on 61 programs. Without the `sort-*` programs the mean is 0.77. uc80 is faster on 55 programs and slower on 23.
- Games (18 games that run on both compilers, whole-run T-states): size 1.34, T-states 1.02. The stdio games are faster on uc80 and the curses games are 37 to 59 percent slower.
- The "uc80 wins on size" claim is false. The expectation in section 8 that uc80 loses on cycles is false for the mean. It is true for small integer loops, for code with many small functions and for the curses games.

What is left:

- Freestanding and ROM target (6.3, phase 3): nothing done. The answer to issue #1 about the Neo Geo sound driver stays "not today".
- One program fails on uc80: `empire-trefonas` (the image of 69,888 bytes is more than 64 KB). Two size-only programs, `battlestar-size` and `rogue36-size`, stop with BUILD-TIMEOUT at the whole-program link (bug C8, the dead-code pass).
- Games tier gaps: mojozork runs only as a size-only row (no scripted Zork I run), and Rogue 3.6 is size-only. Larn, TSCP and the Scott Adams games are not in, because of their licenses. We did not run `battlestar-size`, `rogue36-size` and `mojozork-size` again for sdcc, sdcc-4.4 and zsdcc, because each build takes more than an hour. These rows come from the earlier run with the same tools and sources.
- Open compiler bugs from 6.5. A recheck on 2026-10-24 with the single-file repros: C1 and C6 compile now. C3, C10 and C15 still give no diagnostic, and C13 still gives "internal error: unexpected token". C2, C4, C5, C7, C9 and C14 are not rechecked.
- cpmemu T-states (6.2) are not needed for the numbers. We used `z88dk-ticks`.
- A CI gate for size (5.5) and a rerun on a fresh machine.
- Historic CP/M compilers and a check in an emulator with real timing.

The original summary follows. Some of its statements are out of date; the status above wins.

- **What we add:** speed numbers in Z80 T-states, size metrics that leave out the C runtime, and more rival compilers.
  The rivals are standalone SDCC (4.4.0 and 4.6.0), z88dk (sccz80, zsdcc, 80cc), HI-TECH C 3.09, dcc and llvm-z80.
  We also add three program tiers: classic benchmarks, embedded kernels that include a Neo Geo sound-driver set, and early games (early Unix games, Rogue, and famous small 8-bit games).
- **Why:** the current page compares only `.com` size, against one compiler. The z88dk C runtime of about 5 KB dominates that comparison.
- **Direct answer for the issue reporter:** do not switch the Neo Geo sound driver to uc80 today.
  - uc80 has no interrupt-function attribute, no `__sfr`/`__at`, no port I/O intrinsic and no banking.
  - By default ul80 puts globals, BSS, runtime temporaries (`__tmp32`) and the `??AUTO` block of locals directly after the code. In a ROM image, all of that storage is in ROM. ul80 has no command-line option to put data at F800h, and no startup code copies initialized data from ROM to RAM.
  - uc80 silently erases a bare SDCC `__interrupt`, so an SDCC ISR compiles to a plain function with a normal `ret`. The other SDCC forms (`__interrupt(1)`, `__critical __interrupt`, `__naked`, `__critical { }`, `__asm ... __endasm;`) stop with "internal error: unexpected token".
  - uc80 writes M80 `.REL` files (bit-packed). `sdldz80` reads sdas `.rel` files (text), so `sdldz80` cannot link uc80 output with nullsound. uc80 also passes arguments on the stack, not in registers as SDCC `__sdcccall(1)` does, so calls into nullsound need shims.
- **What uc80 can do today:** link an image at address 0 with a custom startup file, with port I/O through inline `asm`.
  The `--no-shared-storage` option puts locals in an IX stack frame, so locals work from ROM and are reentrant. Globals still need manual placement.
- **Size and speed expectation (superseded by the status above):** the first like-for-like test does not support "uc80 wins on size against SDCC".
  On the z88dk sieve, the bytes beyond the 8,000-byte flags array are about 279 for SDCC 4.6.0 and about 658 for uc80 (the uc80 figure includes the CP/M crt0).
  An estimate puts the uc80 sieve at 7-10 M T-states, against 4.22 M for SDCC (estimate, not a measurement). The benchmarks in this proposal measure both.
- **First fix (done in 8ffbdbf):** the toolchain linked about 9 KB of unused libc into every program (section 6.1).
  No new number was valid until uc80 fixed that regression.
- **Reply now:** post a first answer to issue #1 in Phase 0, not after the benchmarks, and ask the reporter for one hot function from the real driver. A draft is at the end of this document.

## 2. Why the current benchmark is not enough

| Problem | Detail |
|---|---|
| Size only | There is no speed measurement. uc80 code keeps no value in a register across a statement ([inline_assembly.md](inline_assembly.md)). The sieve comparison `i <= SIZE` takes about 15 instructions with push/pop (confirmed in uc80 HEAD output). A size win can hide a speed loss. |
| One rival | The table has only z88dk with zsdcc (`-SO3 --max-allocs-per-node10000`). The table has no row for standalone SDCC, which ngdevkit ships. |
| CRT-dominated | The z88dk CP/M CRT is about 5 KB (minimal binary 5,172 B, against 128 B for uc80). Most of the 46 % size ratio comes from the CRT, not from code generation. See [size_analysis_0053_0456.md](size_analysis_0053_0456.md). |
| Not reproducible | No script for `SIZE_COMPARISON.txt` exists in the repo or in its git history. The file came in with commit 0109bd1 (2026-03-12). |
| Numbers out of date | With um80 0.3.50 and later, `puts("hello")` links to 9,088 B, not 256 B (section 6.1). |
| Test programs are not benchmarks | The 47 Fujitsu programs are compiler conformance tests, not workloads. |
| No freestanding case | Every number is for a CP/M `.com`. Nothing measures a ROM sound driver, which is the case in issue #1. |
| Two different sieves | The repo research used Gilbreath's BYTE sieve (8190 odd-only flags, 1899 primes). z88dk uses a plain Sieve of Eratosthenes (8000 flags, 1007 primes). The two programs must stay separate rows. |
| Claim not checked | [benchmarks.md](benchmarks.md) says "smallest known binaries for Z80/CP/M among current compilers". The [davidly/cpm_compilers](https://github.com/davidly/cpm_compilers) README (line 57) makes the same kind of claim for dcc ("the smallest, fastest binaries of all the compilers"), and the table has no dcc row. The 9 KB regression also makes the claim false for the current release. |
| Docs disagree | `docs/benchmarks.md` says SDCC tests 514/523 and c-testsuite 220/220. `docs/ANSI_C_COMPLIANCE.md:11` says 218/220 and 488/523. |

## 3. Compilers and platforms to compare

### 3.1 First wave

Ranked by value to uc80 users and to the issue reporter.

| Rank | Compiler | Version | License | Headless use | Flags (size row / speed row) | Setup effort |
|---|---|---|---|---|---|---|
| 1 | SDCC standalone, z80 port | 4.6.0 (2026-06-22) and 4.4.0 (current ngdevkit pin) | Compiler GPL-2.0+/GPL-3.0+. Run-time libraries GPL with linking exception ("GPL+LE") | Native on macOS and Linux. `brew install sdcc` gives 4.6.0. ngdevkit installs `z80-neogeo-ihx-sdcc` | `sdcc -mz80 --opt-code-size --max-allocs-per-node 200000` / `--opt-code-speed --max-allocs-per-node 200000`. Third row with the z88dk wiki method: default optimization plus `--max-allocs-per-node200000`. Freestanding: `--no-std-crt0 --code-loc --data-loc --nostdlib` | Low for ROM. CP/M needs a small crt0, a BDOS `putchar` and an `.ihx` to `.com` step |
| 2 | z88dk zsdcc | nightly, zsdcc = SDCC 4.6.0 #16639. Last tag v2.4 (2025-10-02) | Clarified Artistic License | `zcc`. macOS nightly zip. Not in Homebrew core | `zcc +cpm -compiler=sdcc -clib=sdcc_iy -SO3 --max-allocs-per-node200000 --opt-code-size -create-app` / same with `--opt-code-speed` | Low |
| 3 | z88dk sccz80 | nightly | as above | `zcc` | `zcc +cpm -compiler=sccz80 -O3 -create-app` (classic lib) and `-clib=new` | Low |
| 4 | HI-TECH C for CP/M | v3.09-21 | Freeware, "free of charge for any use, private or commercial". HI-TECH keeps the copyright | Native CP/M binary. Runs under cpmemu or zxcc 0.5.7 (installed on the dev machine) | `c -O -N x.c`. `-N` links the minimal NRTCPM startup | Low to medium: upper-case 8.3 names, CR/LF |
| 5 | dcc | v3.6.0 (2026-09-05). Earlier tags v2.0.0, v3.0.0, v3.5.0 | MIT | Cross compiler for macOS, Linux and Windows. Writes M80 `.MAC`, ships `m80c` (a native M80 clone) and links with L80 | Build with `pwsh ./scripts/build-dcc.ps1`, or see `m.sh` and `mmacos.sh`. Build with and without `dccpeep` | Low to medium. Unverified whether um80/ul80 can assemble and link dcc output |
| 6 | z88dk 80cc | `25461-415806f08c-20260813` | as z88dk | `zcc -compiler=80cc` | `-O3`, `--opt-code-size`. Optional `-fframe-pointer` | Low, experimental |
| 7 | llvm-z80 | `llvmz80-23.1.3-r1` (2026-10-20) | Apache-2.0 with LLVM Exceptions (LICENSE.TXT; GitHub shows NOASSERTION) | Prebuilt macOS ARM64 tarball | `--target=z80-unknown-none-sdcc` emits sdasz80 output for `sdldz80`. CP/M libc support unverified | Low to install, medium to use |

Sources:
[SDCC home](https://sdcc.sourceforge.net/),
[SDCC news](https://sourceforge.net/p/sdcc/news/),
[Homebrew sdcc](https://formulae.brew.sh/formula/sdcc),
[ngdevkit-toolchain](https://github.com/dciabrin/ngdevkit-toolchain),
[z88dk releases](https://github.com/z88dk/z88dk/releases),
[z88dk installation](https://github.com/z88dk/z88dk/wiki/installation),
[z88dk Compilers](https://github.com/z88dk/z88dk/wiki/Compilers),
[z88dk Tool---80cc](https://github.com/z88dk/z88dk/wiki/Tool---80cc),
[agn453/HI-TECH-Z80-C](https://github.com/agn453/HI-TECH-Z80-C),
[davidly/dcc](https://github.com/davidly/dcc), [dcc releases](https://github.com/davidly/dcc/releases),
[llvm-z80](https://github.com/llvm-z80/llvm-z80).

Notes:
- The SDCC flag spellings come from SDCC 4.x. Nobody has checked the flags against the 4.6 manual.
- SDCC 4.4.0 is probably the version the issue reporter uses. The issue names only "the tool suite that comes with ngdevkit". ngdevkit-toolchain pins `SRC_SDCC=sdcc-src-4.4.0` and adds 3 small build patches (cpp-install-name, sdccattr-missing-include, aslink-prototype).
- The SDCC 4.6.0 release notes claim "Substantially improved code generation for z80-related ports". Run both SDCC versions to show the difference.
- z88dk targets for ROM work: `+z80` (newlib, sdcc_iy) and `+embedded` (classic). See [Platform---Z80-Embedded](https://github.com/z88dk/z88dk/wiki/Platform---Z80-Embedded).
- dcc is the closest peer to uc80: M80 output, L80 link, CP/M `.COM`, no `double`, 16-bit int. The dcc README already tests dcc output under cpmemu.
- The llvm-z80 README documents sdasz80/sdldz80 output, so llvm-z80 objects can in principle link with nullsound (not tested by us). Work to add llvm-z80 to z88dk is in progress ([z88dk issue #3033](https://github.com/z88dk/z88dk/issues/3033), opened 2026-07-28).

### 3.2 Later waves

| Compiler | Status | Why include | Effort |
|---|---|---|---|
| SDCC 4.2.0 | 2022-03-08, the first release with `__sdcccall(1)` as default | Baseline after the calling-convention change. Optional, because the [SDCC historygraphs](https://sourceforge.net/p/sdcc/code/HEAD/tree/trunk/sdcc-extra/historygraphs/) already chart z80 code size by version (whether z80 score data also exist is unverified) | Medium |
| HI-TECH Z80 cross v4.11 (MS-DOS) | Released with Microchip permission ([repo](https://github.com/agn453/HI-TECH-Z80-C-Cross-Compiler)) | Makes CP/M and ROM-able programs | Medium to high. Needs DOSBox (unverified) |
| Fuzix Compiler Kit (fcc) | Archived, compiler GPLv3 ([repo](https://github.com/EtchedPixels/Fuzix-Compiler-Kit)). Last push 2025-09-07; archive date unverified | Another modern ANSI-subset Z80 compiler | Medium. No K&R, no bitfields, no struct passing or return |
| Historic CP/M compilers: Aztec C, BDS C 1.60, Software Toolworks C/80, Whitesmiths, Small-C, MESCC | Collected in [davidly/cpm_compilers](https://github.com/davidly/cpm_compilers) (CC0), with E.C, TTT.C, TM.C, MM.C, TPI.C and SIEVE.C adapted for each compiler | "1983 against 2026" rows. Good publicity, low switching value | Medium each. Sources need K&R or dialect variants |
| Non-C rows: Turbo Pascal 1-3, Microsoft BASCOM v5, DR PL/I-80, Cowgol 0.6.1, hand-written asm | Also in cpm_compilers, with the same sieve, e and ttt programs | Answers "what would a 1982 programmer use" | Low to medium |
| uplm80 (sister PL/M project) | Same um80/ul80/cpmemu pipeline | A PL/M row costs very little | Low |
| ACK `cpm` platform | 8080 code only ([davidgiven/ack](https://github.com/davidgiven/ack)). The ACK README says "Only some of it builds" | Historic data point | High |

### 3.3 Published numbers we quote but do not run

The [z88dk Benchmarks page](https://github.com/z88dk/z88dk/wiki/Benchmarks) (wiki commit 2026-10-15) has exact cycle counts and sizes.
The page covers HI-TECH C 3.09, HI-TECH C 7.80pl2, IAR 4.06A, SDCC 4.6.0 #16608 and the z88dk compilers.
IAR 4.06A and HI-TECH 7.80pl2 are commercial and we cannot get them, so we quote their rows only.
Appendix A has a copy of the main rows.
The wiki rows do not use identical flags: each compiler uses its own documented `-D` switches (section 5.3).
A uc80 row fits directly into the z88dk tables only if uc80 uses the same sources, the same `+test`-style build (no stdio), the same switches and the same timing region (`TIMER_START` to `TIMER_STOP`).

### 3.4 Out of scope

- eZ80 toolchains (CE toolchain, Zilog ZDS II): 24-bit ADL mode, not plain Z80.
- vbcc: no Z80 back end ("vbccz" targets the Z-machine).
- GCC: no maintained Z80 port found.
- chibicc, lcc: no maintained Z80 port found.
- cc65: 6502, context only.
- gbdk-2020-sdcc: SDCC plus patches. One stock SDCC row covers gbdk-2020-sdcc.

## 4. Benchmark programs

The survey downloaded all candidate sources to a scratch directory. Where noted, the survey built the sources with uc80 HEAD and ran them under cpmemu.
Sizes marked "linked today" include the unused-libc regression from section 6.1, so the real figures are smaller.

### 4.1 Tier 1: classic small benchmarks (direct comparison numbers)

The main source is [z88dk support/benchmarks](https://github.com/z88dk/z88dk/tree/master/support/benchmarks).
That tree keeps the original source of each program plus one copy for each compiler, with only the edits that compiler needs.

| Program | Measures | Source / license | uc80 result today |
|---|---|---|---|
| Sieve of Eratosthenes, z88dk (primes in 2..7999) | loops, array index | z88dk `sieve/sieve.c`, 89 lines ([readme](https://github.com/z88dk/z88dk/blob/master/support/benchmarks/sieve/readme.txt)) | Correct, 1007 primes. `main()` needs `int main(void)`. CODE+DATA+BSS 8,658 B with unused EXTRNs removed |
| BYTE Sieve (Gilbreath 1981, 8190 odd-only flags) | loops, array index | [Byte Sieve](https://en.wikipedia.org/wiki/Byte_Sieve). Also in the davidly set | Correct, 1899 primes. 10 iterations: 10,442,638 instructions. Never compare with the z88dk sieve |
| Dhrystone 2.1 | integer, struct copy, strings, calls | z88dk `dhrystone21`, 3 files, 1,013 lines (dhry.h 472, mostly comments; dhry_1.c 361; dhry_2.c 180). [netlib](https://netlib.org/benchmark/). No license statement exists (unverified) | Correct after `-Ddouble_t=double` and a fixed run count. z88dk marks Dhrystone deprecated |
| fannkuch(7) | permutations, array shuffle | Benchmarks Game, revised BSD | Correct, `Pfannkuchen(7) = 16` |
| pi, 800 digits (with and without `ldiv`) | 32-bit multiply, divide, modulo | z88dk `pi` | **Wrong**: `%.4d` ignores the precision. `pi_ldiv.c` is wrong because of the `ldiv` bug |
| binary-trees (N=8) | malloc/free, recursion with `long` | Benchmarks Game | **Wrong**: hangs in whole-program mode. `--no-whole-program` prints `check: -57736351` (should be `-1`), then "Out of Memory" |
| mandelbrot 60x60 | float inner loop, golden bitmap | z88dk | Runs. Add a fixed-point (8.8 or 16.16) variant, the form that games and drivers use |
| sorting | library `qsort` | z88dk `sorting/sort.c` | **FAIL**: uc80 `qsort` sorts in descending order |
| sprintf | printf formats | z88dk (sources in the compiler subdirectories) | Not run |
| Whetstone 1.2 | float library, transcendental functions | z88dk `whetstone.c`, 512 lines | Runs with `-Ddouble_t=double`. Output not checked |
| n-body | float `sqrt` | Benchmarks Game | Runs, prints `-0.169066343`. Expected value not checked |
| gamer_benchmark | "time sensitive code commonly found in games written in C for z80 machines" | z88dk `gamer_benchmark` | Not run. Fits the game theme of the request |
| davidly set: E, TTT, TM, MM, TPI, SIEVE | mixed | [davidly/cpm_compilers](https://github.com/davidly/cpm_compilers), CC0 | Not run. Needed to compare with the dcc and cpm_compilers tables |
| `micro.c` (new) | 8/16/32-bit arithmetic, shifts, compares, `char` against `int` loop counters, struct copy 4/16/64 B, dense and sparse `switch`, call cost with 0-4 args, bitfields, `volatile` | Write in the uc80 repo | Not written |
| CRC-16/CCITT and CRC-32 (bitwise and table) | byte loops, 32-bit shifts | Write, about 40 lines. Check values: CRC-16/CCITT-FALSE("123456789") = 0x29B1, CRC-32 = 0xCBF43926 | Not written |
| ZX0 decoder | Z80-style decompression | [einar-saukas/ZX0](https://github.com/einar-saukas/ZX0), BSD-3-Clause, `src/dzx0.c`. The hand-written Z80 decoder sets the speed ceiling | Not run |

Whetstone and n-body measure the float library more than the code generator.
uc80 `double` is a 4-byte IEEE single (`uc_core/type_config.py`). SDCC also makes `double` a 32-bit float ([SDCC manual](https://sdcc.sourceforge.net/doc/sdccman.pdf)). The HI-TECH 3.09 float size is unverified.
So the float rows compare library implementations (C or assembly, IEEE, MBF32, math48) more than formats. Each float row must state the library of each compiler.

### 4.2 Tier 2: real-world kernels

#### 4.2.1 General kernels

| Group | Programs | Source / license | Notes |
|---|---|---|---|
| Stanford set | Perm, Towers, Queens, Intmm, Puzzle, Quicksort, Bubble, Tree | [LLVM test-suite Stanford](https://github.com/llvm/llvm-test-suite/tree/main/SingleSource/Benchmarks/Stanford). Not listed as third-party in the suite LICENSE.TXT, so the suite license applies (Apache-2.0 with LLVM exception plus the legacy NCSA license) | The sources assume 32-bit `int`. With 16-bit `int`, all of them hang or print nothing under uc80 (cause not found). With `--int=32` and 1 iteration, all of them finish. Port to `long` and make new reference outputs |
| LLVM small programs | Richards (408 lines), Ackermann, fib2, McGill queens, salsa20, evalloop | [LLVM test-suite](https://github.com/llvm/llvm-test-suite/tree/main/SingleSource/Benchmarks) | Reference outputs included |
| Tak | `tak(18,12,6)` makes 63,609 calls | Write | Call and return cost. Matters for uc80's shared static locals |
| Embench-IoT | crc32, matmult-int, edn, md5sum, nettle-sha256, slre, huffbench, tarfind, ud | [embench-iot](https://github.com/embench/embench-iot), GPL-3.0 with some per-file licenses | Each program has `verify_benchmark()`. Lower `LOCAL_SCALE_FACTOR`. Some files assume 32-bit `int` (unverified) |
| MiBench | sha, CRC32, bitcount, adpcm, dijkstra_small | [embecosm/mibench](https://github.com/embecosm/mibench), per-program license unverified | Embed small inputs as C arrays. adpcm is close to sound work |
| CoreMark 1.0 | list, matrix, state machine, CRC | [eembc/coremark](https://github.com/eembc/coremark), Apache-2.0 plus the CoreMark Acceptable Use Agreement | Only `core_portme.*` may change. Clause 1.2 forbids the trademark on modified software. Follow the reporting rules before we publish a "CoreMark" score |
| stdcbench | freestanding C90 and library parts | stdcbench.org (returns 403, license unverified) | The SDCC maintainer designed stdcbench for about 4 KB RAM and 32 KB flash. No published Z80 scores |
| Decompressors | LZ4 block decoder, heatshrink | [lz4](https://github.com/lz4/lz4) `lib/` BSD-2-Clause, [heatshrink](https://github.com/atomicobject/heatshrink) ISC | Decode a fixed blob, check the output with a CRC |
| fasta, spectral-norm | float | Benchmarks Game, z88dk | Run. Outputs not checked against references |

#### 4.2.2 Neo Geo sound-driver kernel set (for issue #1)

Context:
- ngdevkit's driver, nullsound, is hand-written sdasz80 assembly (`nullsound/*.s`, 19 `.s` and 11 `.inc` files, no C, LGPL) in [dciabrin/ngdevkit](https://github.com/dciabrin/ngdevkit).
- The survey found no open-source C YM2610 driver.
- The closest C example is [superctr/SGDK_Z80_C](https://github.com/superctr/SGDK_Z80_C) (public domain, SDCC, YM2612). Its README says SDCC code is too slow for sample playback and gives a rule of about 50 Z80 cycles between YM register writes.
- The YM2610 has 6 ADPCM-A channels and 1 ADPCM-B channel ([neogeodev YM2610](https://wiki.neogeodev.org/index.php?title=YM2610)). The chip fetches samples over its own ADPCM bus, so the Z80 does not stream samples (bus detail unverified). If the bus detail is true, the SGDK_Z80_C warning applies to the Mega Drive YM2612 DAC, not to the Neo Geo. The Neo Geo CPU budget is then register writes per tick: at 4 MHz and a 60 Hz tick, about 66,667 cycles per tick.
- Neo Geo Z80 memory map, from [neogeodev Z80](https://wiki.neogeodev.org/index.php?title=Z80) and [Z80 bankswitching](https://wiki.neogeodev.org/index.php/Z80_bankswitching):

| Address | Use |
|---|---|
| 0000-7FFF | fixed ROM |
| 8000-F7FF | four bank windows. The program selects a bank with a port **read** (`IN`) of ports 08h-0Bh, with the bank number in the upper address byte: 08h selects F000-F7FF, 09h E000-EFFF, 0Ah C000-DFFF, 0Bh 8000-BFFF |
| F800-FFFF | 2 KB RAM |
| 0038h | INT from the YM2610 timers |
| 0066h | NMI from a 68k command |

Each kernel is a freestanding ROM build: origin 0, RAM at F800, no stdio, no malloc.
An ordered log of port reads and port writes replaces stdout as the correctness check (section 6.2 adds the log to cpmemu). The read log is necessary because bank selection is a port read.

| # | Kernel | What the kernel does | Tier |
|---|---|---|---|
| 0 | reporter's code | One hot function from the issue reporter's driver, if the reporter shares one. Real code is better than the synthetic kernels | 2a |
| 1 | `nmi_cmd` | NMI reads a command byte from a port, puts the byte in a 16-entry ring buffer (`volatile` head and tail), writes an ack. Measure cycles from entry to `RETN` | 2a |
| 2 | `ym_write` | Write 64 register/value pairs from a table, with a busy-wait on the status bit. Report cycles per write and the minimum spacing | 2a |
| 3 | `sequencer_tick` | 4 FM + 3 SSG channels. Fetch pattern bytes through `const uint8_t *`, decode with a dense `switch`, look up the envelope, apply vibrato from an 8-bit sine table with a 16-bit fixed-point add, write only changed registers (shadow compare) | 2a |
| 4 | `note_to_fnum` | Note to F-number and block: table plus shifts and masks | 2a |
| 5 | `adpcm_trigger` | Split 24-bit start and end addresses into register bytes | 2b |
| 6 | `bank_select` | Compute bank and offset from a 24-bit ROM address, then select the bank with `IN A,(C)`, B = bank number, C = port 08h-0Bh | 2b |
| 7 | `bitbang` | Shift a byte out MSB first with a clock line, plus bitfield extraction | 2b |
| 8 | `cmd_fsm` | Command parser state machine, as a function-pointer table and as a `switch` | 2b |

Report for each kernel: ROM bytes, RAM bytes (data + BSS + peak stack) against the 2 KB budget, cycles per call, and the port log.

Compilers for this set: SDCC 4.4.0 and 4.6.0 (with `__interrupt` and `__critical` where needed), z88dk `+z80`, llvm-z80 (sdcc output mode) and uc80.
uc80 workarounds today, which the kernel sources must show openly, because the workarounds are part of the answer to issue #1:
- Port I/O goes through globals plus `asm("out (4),a")`. The globals are not ISR-safe: if `main` and the ISR both call `ym_write`, the ISR can corrupt the globals. Use `di`/`ei` around each call from `main`, or separate globals for the ISR.
- Build with `--no-shared-storage`, so locals live in an IX frame and not in the `??AUTO` block (which would be in ROM).
- Each ISR needs an assembly wrapper. A grep finds no IY, `EXX` or `EX AF,AF'` in the uc80 code generator, `lib/lc` or `lib/rt`, so a wrapper can use `EX AF,AF'` plus `EXX` plus `PUSH IX` instead of six pushes. The wrapper is safe only if nullsound leaves the alternate registers alone (unverified).

Hand-written assembly baselines for speed (not C tests): [joffb/banjo](https://github.com/joffb/banjo) (MIT) and [sverx/PSGlib](https://github.com/sverx/PSGlib) (BSD-3-Clause according to GitHub).

### 4.3 Tier 3: real programs (early games)

Games test code size, many small functions, string data and libc coverage. This section answers two questions:
- Which famous early games fit in a Z80 address space?
- Which games run under cpmemu today?

#### 4.3.1 Which games shipped with early Unix

The release lists come from section-6 man pages and source trees on [TUHS](https://www.tuhs.org/cgi-bin/utree.pl) and in [unix-history-repo](https://github.com/dspinellis/unix-history-repo).

| Release | Games | Surviving source |
|---|---|---|
| V1 (1971) | bj, chess, moo, ttt ([V1 man6](https://www.tuhs.org/cgi-bin/utree.pl?file=V1/man/man6)) | None from V1. Later PDP-11 assembly versions (bj.s, moo.s, ttt.s) survive in System III and in [2.11BSD games/pdp](https://www.tuhs.org/cgi-bin/utree.pl?file=2.11BSD/src/games/pdp) |
| V6 (1975) | adds cubic, quiz, wump | `quiz.c` only |
| V7 (1979) | man6 (16 pages): arithmetic, backgammon, banner, bcd, bj, checkers, chess, ching, cubic, maze, moo, quiz, reversi, ttt, words, wump ([V7 man6](https://github.com/dspinellis/unix-history-repo/tree/Research-V7/usr/man/man6)) | C: arithmetic.c 193, backgammon.c 584, fish.c 498, fortune.c 31, hangman.c 146, quiz.c 473, wump.c 375 lines (2,300 in total). fish, fortune and hangman have source but no man6 page. chess is C mixed with PDP-11 `.s` ([V7 games](https://www.tuhs.org/cgi-bin/utree.pl?file=V7/usr/src/games)). ANSI ports exist in FUZIX (below) |
| 1BSD (1978) | trek (Eric Allman wrote trek in 1976, "5/76" in main.c), 50 K&R `.c` files, 5,817 lines | yes |
| 3BSD | arithmetic, banner, bcd, fortune, number, quiz | yes. trek is absent from 3BSD, 4BSD and 4.1cBSD |
| 4BSD | adds aard, chess.c, zork ([4BSD games](https://www.tuhs.org/cgi-bin/utree.pl?file=4BSD/usr/src/games)) | yes |
| 4.1BSD | adds adventure, backgammon, boggle, canfield, cribbage, fish, hangman, mille, monop, rain, snake, worm, worms, wump | yes |
| 4.2BSD | adds factor, primes, sail, rogue; trek returns. btlgammon is already in 4.1cBSD | rogue directory: Makefile, READ_ME, extern.h, mach_dep.c, rogue.doc. No game source |
| 4.3BSD | adds battlestar, hunt, robots | yes. rogue directory: Makefile, README, machdep.c, machdep.h only |
| 4.3BSD-Reno (1990) | adds atc, larn, hack, phantasia, pom, ppt, morse, caesar, dm ([4.3BSD-Reno games](https://www.tuhs.org/cgi-bin/utree.pl?file=4.3BSD-Reno/src/games)) | yes |
| 4.4BSD | adds pig, tetris ([4.4BSD games](https://www.tuhs.org/cgi-bin/utree.pl?file=4.4BSD/usr/src/games)). gomoku is not in the TUHS 4.4BSD tree; the 1994 header points to 4.4BSD-Lite (unverified) | yes |

The 2.11BSD Makefiles show how much memory each game needed on a 16-bit PDP-11:
- canfield links without `-i`, so its code plus data fit in 64 KB.
- adventure, trek, monop and robots use `SEPFLAG= -i` (separate 64 KB code and data spaces, confirmed). The survey also listed battlestar, mille, atc, sail, snake, cribbage and backgammon with `-i` (unverified).
- hack, phantasia and warp use the `-Z` PDP-11 overlay flag.

On a Z80, code and data share one 64 KB space, so a game that needed `-i` is a warning sign.

Licenses:
- Research V1-V7 and 32V: the Caldera license of 2002-01-23 ([Caldera license](https://www.tuhs.org/Archive/Caldera-license.pdf)). The Caldera license is a 4-clause BSD-style license with an advertising clause, and every copy must keep the notice.
- The Caldera license excludes System III and System V ([TUHS thread](https://www.tuhs.org/pipermail/tuhs/2003-March/003581.html)). Do not use the System III versions of bj, moo, ttt, back, craps or trk.
- NetBSD games: mostly 3-clause BSD (Regents of the University of California). robots and sail are also Regents 3-clause BSD. Only a few later files (robots/auto.c, and sail/array.c, array.h, display.h, restart.h) carry a NetBSD Foundation notice. warp/warp.c carries a 2020 NetBSD Foundation notice. hack has a CWI header (3-clause BSD per [Wikipedia](https://en.wikipedia.org/wiki/Hack_(video_game))). phantasia is "distributed without notice of copyright".
- Larn: "Morgan's original 1986 license prohibits commercial redistribution" ([Larn](https://en.wikipedia.org/wiki/Larn_(video_game))). The NetBSD `larn/COPYRIGHT` gives no grant. Treat Larn as non-commercial with unclear redistribution terms. Do not vendor Larn.

Language and port base:
- The V7 originals use implicit `int` and K&R definitions. C23 removed both features, and uc80 rejects both by project rule.
- [FUZIX](https://github.com/EtchedPixels/FUZIX) `Applications/V7/games/` has ANSI ports of the V7 games with prototypes (for example `int main(int argc, char *argv[])` in wump.c), under the Caldera notice in `Applications/V7/COPYRIGHT`. FUZIX already builds these games for the Z80 (`Makefile.z80`). Source sizes: arithmetic.c 3,082 B, backgammon.c 14,526 B, fish.c 9,111 B, hangman.c 2,814 B, quiz.c 7,147 B, wump.c 7,340 B.
- Use the FUZIX V7 ports as the primary "early Unix games" set. Use [NetBSD src/games](https://github.com/NetBSD/src/tree/trunk/games) as the larger 4.xBSD set.
- The NetBSD wump, fish, arithmetic, hangman and backgammon are later Berkeley rewrites, not the V7 programs: wump 1989 (Dave Taylor), fish 1990 (Muffy Barkocy), arithmetic 1989 (Eamonn McManus), hangman 1983 (UC Regents), backgammon 1980 (UC, 5,458 lines against 584 in V7).

#### 4.3.2 Early Rogue

| Version | Source | License | Lines | Notes |
|---|---|---|---|---|
| Rogue 3.6 (1980-81) | [RoguelikeRestorationProject/rogue3.6](https://github.com/RoguelikeRestorationProject/rogue3.6), also in [Rogue-Collection](https://github.com/mikeyk730/Rogue-Collection) | BSD 3-clause, "Copyright (C) 1980, 1981 Michael Toy, Ken Arnold and Glenn Wichman". The same LICENSE.TXT covers state.c, mdport.c, mdport.h (Kisseberth, BSD) and xcrypt.c (David Burren FreeSec, BSD) | 13,413 in 31 `.c` files, about 10.4K of game code | Best early-Rogue candidate |
| Rogue 5.4 | [RoguelikeRestorationProject/rogue5.4](https://github.com/RoguelikeRestorationProject/rogue5.4) | BSD 3-clause | 16,876 | Per-module code about 141 KB. Larger than 3.6 |
| BSD "rogue" (Tim Stoehr clone, 1987-88) | NetBSD `games/rogue` | BSD | 11,933 | Not the original game |

Rogue 3.6 facts from the test build:
- The Restoration Project already converted the source to ANSI C. Two K&R definitions remain: `prname(who, upper)` in fight.c and `main(argc, argv, envp)` in main.c.
- A missing `;` in `md_ucount()` in `mdport.c` breaks non-Unix builds.
- About 40 curses calls and 4 windows (stdscr, cw, mw, hw) plus curscr: about 9.6 KB of screen buffers. The windows also hold the level map.
- Unix calls: `signal` (23), `alarm` (3), `getenv` (3), `time`/`localtime`, `crypt`, `kill`, `malloc`/`free`. `mdport.c` adds getpwuid, fork/exec, load average and score-file locks.
- With stub headers and 3 edits, all 31 files compile.
- Code size without `xcrypt.c`, `state.c` and `mdport.c` (a CP/M port replaces those files): 104,998 B of code and 22,408 B of data with separate compilation. Whole-program mode gives 134,541 B, or 107,951 B with `--no-inlining`.
- Data trap: `int e_levels[]` (fight.c line 19) is `10, ..., 20480, 40920, 81920, 163840, 327680, 655360, 1310720, 2621440, 0`. With 16-bit `int` the values go wrong from 40920 on (40920 becomes negative, 81920 becomes 16384, 327680 becomes 0), with no warning. Use `long` or `--int=32`. 40920 is an original typo for 40960. Keep the typo, because the typo is the original data.
- Result: Rogue 3.6 does not fit. The code must shrink by about 2.3x, or the game needs overlays. ul80 has no overlay manager: the only related option is `--allow-overlap`.
- Use Rogue 3.6 as a size-only benchmark against SDCC and z88dk, with the per-object metric of section 5.3. Rogue does not need to run for that use.

8-bit history:
- Wikipedia lists CP/M, ZX Spectrum and Amstrad CPC among Rogue's platforms ([Rogue](https://en.wikipedia.org/wiki/Rogue_(video_game))) and gives no details of a CP/M version.
- "RogueZ80" is XmikeX's repackaging of "the long-running C128 CP/M version of Rogue" ([theoasisbbs](https://theoasisbbs.com/roguez80-brings-classic-rogue-to-native-boot-on-commodore-128/)). The origin and size of the C128 version are unverified.
- The 1988 Spectrum release (Mastertronic, Icon Design) ([spectrumcomputing](https://spectrumcomputing.co.uk/entry/4224/ZX-Spectrum/Rogue)) probably came from a rewrite, not from the C source (unverified).
- No C Rogue that fits a Z80 was found.

#### 4.3.3 Game candidates

The "Fit" column comes from uc80 HEAD. All COM sizes are too large and must be measured again in Phase 0:
- The survey's link script put `crt0.rel` in front of a whole-program `.rel` that already embeds crt0 and the runtime, and the unused-libc regression (section 6.1) also applies.
- A relink with both causes removed gave wump 31,488 B (was 34,304), monop 59,776 B (was 63,104) and robots 48,640 B (was 51,456).
- Even after the relink, ul80 warns `%Mult. Def. Global` (21, 11 and 33 warnings) and links duplicate runtime modules, which can add up to 362 + 821 + 1,907 B (rt_arith16, rt_arith32, rt_float). Cause not found (bug C16).

cpmemu gives a 64,512-byte TPA (`BDOS_BASE 0xFD00`). A real 64 KB CP/M 2.2 system gives about 58 KB.
"tinycurses" is a 130-line VT100 curses subset written for this survey: 3,887 B of code and 2,140 B of data.

| Game | First release | Source | License | Lines | Needs | Port work | Fit with uc80 today |
|---|---|---|---|---|---|---|---|
| V7 games, ANSI ports: arithmetic, backgammon, fish, hangman, quiz, wump | V7 (1979) | FUZIX `Applications/V7/games` | Caldera | 2.8-14.5 KB of source each | stdio. hangman reads a word file (`/usr/dict/words`; FUZIX ships a 196,513 B `words`) | POSIX shim | Not built. z88dk already builds the V7 backgammon for `+cpm` (`examples/console/backgammon.c`) |
| wump | V7 game; NetBSD file is a 1989 Berkeley rewrite | NetBSD `games/wump` | BSD-3 | 905 | stdio | POSIX shim | **Runs**, plays correctly. COM 31,488 B after relink |
| fish | V7 game; NetBSD file is a 1990 rewrite | NetBSD | BSD-3 | 518 | stdio | CR to LF input shim | **Runs**. COM 26,752 B (inflated) |
| arithmetic | V7 game; NetBSD file is a 1989 rewrite | NetBSD | BSD-3 | 385 | stdio, argv | argv shim | **Runs**. COM 23,040 B (inflated) |
| morse, number, pig, caesar, random, banner | 3BSD-4.4BSD | NetBSD | BSD-3 | 135-1,178 | stdio | shim | Link, 18.7-30.8 KB COM (inflated) |
| bcd, pom | 3BSD, 4.3BSD-Reno | NetBSD | BSD-3 | 220 / 278 | `isascii`, `localtime`, `mktime`, `strftime` | libc additions | Link fails today |
| ppt | 4.3BSD-Reno | NetBSD | BSD-3 | 175 | stdio | none | Compiler crash (bug C1) |
| monop | 4.1BSD | NetBSD | BSD-3 | 3,973 | stdio | workaround for bug C4 | **Runs** on cpmemu. COM 59,776 B after relink. Still too big for a 58 KB TPA once BSS and stack are added |
| robots | 4.3BSD | NetBSD | BSD-3 (Regents); auto.c NetBSD Foundation | 2,104 | curses | tinycurses | **Runs**, plays. COM 48,640 B after relink |
| snake | 4.1BSD | NetBSD | BSD-3 | 1,168 | curses, libm | tinycurses | **Runs**. COM 45,440 B (inflated) |
| worm | 4.1BSD | NetBSD | BSD-3 | 369 | curses, SIGALRM | tinycurses, timer from a BDOS 11 poll | Draws. COM 25,728 B (inflated) |
| mille | 4.1BSD | NetBSD | BSD-3 | 3,003 | curses, malloc | tinycurses, fixed malloc | **Runs**. COM 53,888 B (inflated) |
| canfield | 4.1BSD | NetBSD | BSD-3 | 2,010 | curses | tinycurses | Starts and prompts. COM 49,408 B (inflated) |
| hangman (NetBSD) | V7 game; NetBSD file is a 1983 Berkeley curses rewrite | NetBSD | BSD-3 | 895 | curses `deleteln` | more curses | Not run. The V7 hangman needs only stdio (row 1) |
| backgammon (NetBSD) | 1980 Berkeley version (V7 original 584 lines) | NetBSD | BSD-3 | 5,458 | termcap | termcap stub | Not run |
| Star Trek (David Ahl's BASIC game, C by Chris Nystrom) | 1970s BASIC | z88dk `examples/console/startrek.c` (47,722 B), FUZIX `games/startrek.c` (38,535 B) | unverified | not measured | stdio | small | Not built. The famous Star Trek that fits a Z80, where BSD trek does not |
| Empire (Peter Trefonas, "(C) CLOAD 1981", TRS-80 BASIC; C by aralbrec 2016) | 1981 | z88dk `examples/console/empire.c` (38,008 B) | unverified | not measured | stdio | small | Not built. The file header gives the `zcc +cpm ... -SO3 -clib=sdcc_iy --max-allocs-per-node200000` line |
| micro-Max 1.6 chess (H.G. Muller) | modern | z88dk `examples/console/umchess.c` (5,243 B), [chessprogramming](https://www.chessprogramming.org/Micro-Max) | not stated (unverified) | about 133 | stdio | small | Not built. z88dk has a build line. Sargon (1978, Z80 assembly, [retro-sargon](https://github.com/billforsternz/retro-sargon)) is the native reference |
| Scott Adams adventures (TRS-80, a Z80 machine, 1978) | 1978 | FUZIX `games/adv01.c` to `adv14b.c` (about 62 KB of source each), generated by [ScottAdams6800](https://github.com/EtchedPixels/ScottAdams6800) | game data copyrighted by Scott Adams, freely downloadable only: fetch, do not vendor | not measured | stdio | small | Not built. The most period-true Z80 text games |
| Fweeplet (cut-down Z-machine, versions 1-5 and 8) | modern | FUZIX `games/fweep.c` (47,005 B) | GPL-3.0+ | not measured | file I/O | page cache | Not built. A second Z-machine, already trimmed for 8-bit machines |
| invaders | modern | FUZIX `cursesgames/invaders.c` (23,752 B) | unverified | not measured | real-time curses | tinycurses | Not built. Good tinycurses test |
| hamurabi | 1970s BASIC | FUZIX `games/hamurabi.c` (11,582 B) | unverified | not measured | stdio | small | Not built |
| trek | written 1976, shipped 1BSD 1978 (5,817 K&R lines) | NetBSD `games/trek` | BSD | 7,894 | float, stdio | POSIX stubs | Compiles. About 70 KB of code, COM 75,648 B. **Does not fit** |
| adventure (Crowther/Woods 350-point, Gillogly C port) | 4.1BSD (2,623 K&R lines, reads the text from disk) | NetBSD `games/adventure` | BSD-3 | 4,695 | 56 KB `glorkz` text embedded as a C array | port the 4.1BSD text-on-disk design, rewrite save | 57,011 B of code even with the text removed. **Does not fit** |
| battlestar | 4.3BSD | NetBSD | BSD-3 | 8,584 | 120.6 KB of room data | text to disk plus overlays | 70.5 KB of code. **Does not fit**. Whole-program build takes over 20 minutes (bug C8) |
| Rogue 3.6 | 1980-81 | section 4.3.2 | BSD-3 | 13,413 | curses, signals | curses, mdport | About 105-108 KB of code. **Does not fit** |
| mojozork (Z-machine V3, plays Zork I) | modern interpreter for the Infocom story format | [icculus/mojozork](https://github.com/icculus/mojozork) | zlib | `mojozork.c`, 2,156 lines | file I/O, `fseek` | page cache for the story file (about 100-150 lines), static opcode table, `fseek` and argv fixes | Compiles untouched: 36,616 B of code, 3,419 B of data. Fails at story load on the `fseek` bugs. Fit is borderline (below) |
| Open Adventure | forward port of Adventure 2.5 (1995) | [esr/open-adventure](https://gitlab.com/esr/open-adventure) | BSD-2 | 4,697 + generated `dungeon.c` | editline, about 90 KB of text in a 157 KB YAML file | text to disk | Not built. 225 test files (`.log`/`.chk`) give a ready correctness suite |
| Dungeon (mainframe Zork, f2c C) | 1980s FORTRAN | [devshane/zork](https://github.com/devshane/zork) | public domain | 17,094 | text in a 133 KB disk file | convert 82+ K&R definitions (prototypes exist in `funcs.h`) | 2 of 33 files compile. Probably too big (unverified) |
| Hack 1.0.3 | 1984-85 | NetBSD `games/hack` | CWI, BSD-3 | 26,307 | `makedefs` host tool, termcap | large | Skip. Hack needed PDP-11 overlays |
| Larn 12 | 1986 | NetBSD `games/larn` | non-commercial (section 4.3.1); do not vendor | 13,367 | curses-like terminal | stubs | 140,638 B of code. Skip, or size only |

mojozork fit: about 36.6 KB of code, 3.4 KB of data, libc, 11.8 KB of Zork I dynamic memory, a page cache and the stack.
That total fits the 64.5 KB cpmemu TPA only after the regression fix and after the 4,567 B `inititialOpcodeTableSetup` becomes a static table. The total does not fit a real 58 KB system (estimate).
The 16-bit `fseek` offset blocks the 92,160 B story file directly.
For the story file, use `COMPILED/zork1.z3` (86,838 B) from [historicalsource/zork1](https://github.com/historicalsource/zork1) (MIT, Microsoft, released 2025-11-20), not mojozork's `zork1.dat`. Check that `zork1-script.txt` still plays on that release (the release serial may differ, unverified).

Excluded, with reasons:
- Ladder and Catchum (Yahoo Software, CP/M, 1982): the originals are binaries, and the only rewrite found is in Turbo Pascal ([mecparts/Ladder](https://github.com/mecparts/Ladder)). No C source.
- Sargon: Z80 assembly. Use as a native reference only.
- Space Travel (PDP-7): assembly, source lost (unverified).

Two surveys measured wump with different methods and got different splits (12,872 B code / 4,192 B data against 7,145 B code / 17,761 B data). Phase 1 must define one method (section 5.1).

Recommended game set:
- **Runs today, measure cycles and size:** wump, fish, arithmetic, robots, mille, snake. Script the input so that each run gives the same result.
- **Has a z88dk baseline at once:** FUZIX V7 games, Ahl Star Trek, Empire, umchess. The z88dk file headers give the `zcc +cpm` command lines.
- **Runs after libc work:** mojozork with Zork I and its 366-command `zork1-script.txt` (the first line, `#random -2`, makes runs repeatable). Infocom sold Zork for CP/M ([Z-machine](https://en.wikipedia.org/wiki/Z-machine)), so the benchmark is true to the period. Fweeplet is the second interpreter.
- **Size only ("does it fit"):** Rogue 3.6, trek, adventure, monop. Use the per-object code metric of section 5.3 for uc80, SDCC and z88dk on the same sources.

## 5. Measurement method

### 5.1 Metrics

| Metric | How | Notes |
|---|---|---|
| Total `.com` size | file size | Note the 128-byte rounding |
| Program delta | `.com` minus the same compiler's minimal `int main(void){return 0;}` | Removes most of the CRT and libc difference |
| CODE + DATA + BSS | from the ul80 `.sym`/map (`__BSS_START`, `__END__`) and each rival's map file, stack excluded | Same metric as the z88dk wiki |
| Code only | CODE section alone | Required. Data arrays dominate CODE+DATA+BSS: the z88dk sieve is 8,279 B for SDCC, of which 8,008 B is data |
| User code only | sum of the sizes of the program's own modules, CRT and libc excluded, from map files | For the game and kernel tiers |
| RAM | DATA + BSS + `??AUTO` + measured peak stack | Required for every freestanding kernel (2 KB budget) |
| Peak stack | SP low-water mark in cpmemu (about 3 lines in the instruction loop) | Not measurable today |
| T-states | qkz80 with a real T-state table, between `TIMER_START` and `TIMER_STOP` labels or marker port writes (section 6.2). Cross-check with `z88dk-ticks` | The qkz80 counter today adds a flat 5 per instruction (`qkz80.cc:411-413`) and is not usable |
| Instructions executed | patched cpmemu STATS line | Secondary only. BYTE sieve, 10 iterations: 10,442,638 instructions. z88dk sieve, whole program: 897,583 instructions, including the BSS clear and `memset` outside the z88dk timing region |
| Wall time at 4 MHz | T-states / 4,000,000 | For readers who think in seconds |
| Compile time | wall clock | uc80 whole-program mode takes over 15 minutes on Rogue and battlestar (bug C8) |

Both cpmemu and `z88dk-ticks` handle BDOS calls inside the emulator. Whole-program cycle counts therefore leave out all BDOS and console time. The results page must say so.

### 5.2 Correctness first

A run counts only if its output is correct. Check in this order:
1. The program's own self-check (Dhrystone "should be" lines, Embench `verify_benchmark()`).
2. Golden files (z88dk mandelbrot and fasta output, LLVM `.reference_output` regenerated for the 16-bit `int` ports, Open Adventure `.chk` files).
3. Published test vectors (CRC, SHA, MD5).
4. For new programs, a final checksum compared across uc80, SDCC and host gcc.
5. For freestanding kernels, the ordered (port, direction, value) log.
6. Under cpmemu, "Reached instruction limit" (9e9 instructions, `cpmemu.cc:4695`) is a failure.

### 5.3 Fairness rules

- Use the same source for every compiler. Keep each compiler's edits as a patch in the repo, as z88dk does.
- Allow the per-compiler `-D` switches that the z88dk readmes document, and record the switches: `-DSTATIC` for SDCC sieve, pi, n-body and spectral-norm, `-DINLINE` for fannkuch, `-DNOSTRUCTASSIGN` for Dhrystone (`support/benchmarks/*/sdcc/readme.txt`).
- For a row next to Appendix A, copy the z88dk method exactly. For SDCC: default optimization plus `--max-allocs-per-node200000`, size summed from `.map` sections, cycles counted only between `TIMER_START` and `TIMER_STOP` with `z88dk-ticks` on a flat `makebin` binary. uc80 needs an equivalent label, for example `asm("TIMER_START::")`. Whether the label appears in the ul80 `.sym` file is unverified.
- Use the same `int` size (16-bit) and the same problem size N for every compiler.
- Read N from a `volatile` variable or from argv, so that whole-program mode, inlining and constant propagation cannot fold the work away. If cycles drop by more than 2x between two uc80 versions, check the generated loop before publishing.
- Give each compiler two rows: best size flags and best speed flags. SDCC size and speed rows use `--max-allocs-per-node 200000`.
- Pin and record each compiler's version, revision and exact command line. Use the flag lines from the z88dk example headers and wiki without change.
- State the math library for each float row (uc80 IEEE single in C or assembly, z88dk math32/mbf32/math48, the SDCC library), and whether 32-bit math is in C or assembly.
- uc80 printf has no `%.4d` and no `%*d`, so part of a uc80 size lead can come from missing features. Use no-printf builds as the main rows, or match formats with z88dk `#pragma printf`.
- Report uc80 whole-program mode and `--no-whole-program` separately. Dhrystone: 20,864 B against 29,184 B (linked today).
- Size-only rows (programs that do not fit): sum the code sizes of separately compiled objects. uc80 `--no-whole-program`, SDCC `-c` with `_CODE` summed, z88dk per object. Use the same curses and POSIX stub headers for every compiler. Never compare uc80 whole-program numbers with SDCC per-file numbers.
- Use `-mz80` timing in `z88dk-ticks`, not `-mz80_strict`, to match the published z88dk numbers ([Tool---ticks](https://github.com/z88dk/z88dk/wiki/Tool---ticks)).

### 5.4 How to present results

- Add `docs/benchmarks_speed.md` with one table per tier: rows are programs, columns are compilers, cells are "bytes / T-states". Show FAIL cells, never empty cells.
- Generate the page with `report.py` from the CSV. Never edit the tables by hand. Start the page with a date, a block of tool versions and commit SHAs, and the one command that reproduces every number.
- Publish no composite score. If a summary is necessary, give a geometric mean per tier, with the program count.
- Link the new page from [benchmarks.md](benchmarks.md). In the README, change the existing line 66 text ("comparison with z88dk and external test suite pass rates") to "size and speed against SDCC, z88dk, HI-TECH C and dcc; test-suite pass rates". No new README section.
- Run `python3 ~/src/claude-skills/tools/ste_check.py --tier B docs/benchmarks_speed.md` before each commit.
- Remove the "smallest known binaries" sentence from [benchmarks.md](benchmarks.md) in Phase 0, not later.
- Answer issue #1 in Phase 0 (draft at the end), then again with the SDCC columns and the freestanding kernel table.
- Offer the uc80 rows as a PR to z88dk `support/benchmarks`, so that z88dk maintainers check the method.

### 5.5 Regression gate

- Add a job `bench-size` to `.github/workflows/pytest.yml` that runs on every PR.
- The job builds Tier 1 plus `hello.c` with uc80 only and compares the sizes against `bench/baseline.csv`.
- The job fails if `hello` is not 256 B, or if any program grows by more than 1 %.
- The job pins the benchmark tools in `bench/constraints.txt` (for example `um80==0.3.52`, `uc_core==0.4.1`, `upeepz80==0.2.7`) and pins cpmemu to a commit SHA.
- Cycle runs and rival compilers run in a separate weekly job (`workflow_dispatch` plus cron), because the cycle runs are slow (pi takes 1.7-7.2 G cycles on the z88dk wiki).

### 5.6 Harness layout

```
bench/                      top level, not under src/uc80, so it never ships to PyPI
  manifest.toml             per program: url, commit SHA, sha256, SPDX license,
                            redistribute = "vendor" | "fetch"
  compilers.toml            name, exact version/revision, install source + sha256,
                            size flags, speed flags, -D switches
  fetch.py                  downloads pinned sources into bench/_cache/ (gitignored),
                            checks sha256, applies patches/
  patches/<program>/<compiler>.patch
  third_party/<name>/       vendored trees, each with its own LICENSE
  src/                      our own code: micro.c, crc.c, tak.c, sound kernels,
                            tinycurses, POSIX shim
  crt/                      SDCC CP/M crt0 + BDOS putchar/getchar, ROM crt0 per compiler
  golden/                   expected stdout and port logs
  run.py                    build + run + check matrix -> results/<date>.csv,
                            raw logs and every exact command line
  report.py                 CSV -> docs/benchmarks_speed.md
```

Rival compiler pins:
- SDCC 4.4.0: build from `sdcc-src-4.4.0` with the 3 ngdevkit-toolchain patches, or use the ngdevkit Homebrew tap. Record the sha256 of the source tarball.
- SDCC 4.6.0: the release tarball, or brew. A brew bottle cannot be pinned, so record `sdcc -v`.
- z88dk: a dated nightly zip with its sha256. The repo has a `z88dk.Dockerfile`. A published image tag is unverified.

Commit `manifest.toml` before the first rival run. Never drop a program after seeing its results.

## 6. Toolchain work needed

### 6.1 Blocker: unused EXTRNs pull in libc

Status: fixed in 8ffbdbf. uc80 drops every `EXTRN` that nothing uses.

- uc80 emits an `EXTRN` for every prototype in every included header (`src/uc80/codegen.py:3797-3800`, `:5028-5030`).
- `asm_dce.py:575-582` keeps an `EXTRN` that nothing references.
- Since um80 0.3.50 (2026-09-25), um80 writes an unused `EXTRN` as an empty chain, the way MACRO-80 does. ul80 then searches the libraries for the symbol (`um80_and_friends/CHANGELOG.md:821-827`).
- `um80>=0.3.46` in `pyproject.toml:5` is only a build-system requirement (used to build the libraries into the wheel). um80 is not a runtime dependency, and the README says `pip install um80` with no version, so every fresh install gets a current um80 and the bad behavior. Pinning um80 is not the fix.
- Effect: `puts("hello")` is 9,088 B, not 256 B (reproduced with um80/ul80 0.3.52).
- A script that removes the unused EXTRNs restores the sizes: hello 256 B, sieve with `printf %d` 5,248 B, sieve with `puts` 640 B.
- Fix: drop unreferenced `EXTRN` lines in `codegen.py`. Also define or remove `quick_exit`/`at_quick_exit`, which the header declares but libc does not define.

### 6.2 cpmemu / qkz80

Status: not done, and not needed. The harness uses the alternative in the last row: `z88dk-ticks` with `-start` and `-end` on the `TIMER_START` and `TIMER_STOP` labels. The SP low-water mark, the port log and the ROM mode are not done.

| Work | Detail | Effort (estimate) |
|---|---|---|
| T-state table | Per-opcode times for base, CB, ED, DD/FD and DDCB/FDCB opcodes, plus the extra time for taken jumps, calls, returns, DJNZ and repeated block instructions. Today `qkz80.cc:411-413` adds a flat 5. `qkz80.cc` (2,227 lines) decodes with prefix flags and one large switch, so every case needs an edit. Real T-states change what `--int-cycles` means for its current users. The `--8080` mode needs 8080 timings, or STATS must refuse to report cycles in that mode | 3-5 days |
| Region timer | Override `port_out` so that a marker port (for example `OUT (0FEh),A`) starts and stops a counter. Or add `--start=SYM --end=SYM` that reads the `.sym` file (for `TIMER_START`/`TIMER_STOP`) | about 20 lines |
| STATS at exit | Print instructions, T-states and the SP low-water mark in `program_exit` (`cpmemu.cc:142-146`). A 5-line version works in scratch | small |
| Port log | Log (port, direction, value) for writes and reads, for the freestanding kernels. `port_out`/`port_in` do nothing today (`qkz80.cc:2216-2226`) | small |
| ROM mode | cpmemu already has `--int-cycles=N` and `--int-rst=N` (`cpmemu.cc:4397-4444`, `:4708`), and qkz80 already has NMI, IM 0/1/2 and the EI delay. Still needed: an Intel HEX loader at address 0, a switch to turn off the page-zero and BDOS traps, NMI scheduling, and the port log | about 1 day |
| Alternative | Run the same binaries in `z88dk-ticks` with `-start`/`-end` ([Tool---ticks](https://github.com/z88dk/z88dk/wiki/Tool---ticks)). The ticks source has `hook_cpm.c` (FCB file I/O and console) and traps at pc 7, 10 and 13 ([src/ticks](https://github.com/z88dk/z88dk/tree/master/src/ticks)). How a plain `.com` gets a page zero is unverified | install only |
| Hardware check | Run the sound kernels in a Neo Geo emulator (MAME or gngeo). A port log alone does not prove YM2610 timing. Headless mode unverified | later wave |

### 6.3 Freestanding target (needed before uc80 is a real answer to issue #1)

Status: not started. Every gap in the table is still open.

| Gap | Today |
|---|---|
| Data and BSS origin | ul80 has `data_base`/`common_base` in the `Linker` class (`ul80.py:137-138`) but no command-line option for them (LINK-80 had `/D:`). By default globals, BSS, runtime temporaries and `??AUTO` follow the code, so in a ROM image all of that storage is in ROM. Example: in a test `rom.sym`, `_COUNTER` is at 0173h and BSS at 0175-0179h. cpmemu hides the fault because all cpmemu memory is RAM |
| Locals in ROM | `--no-shared-storage` (`src/uc80/main.py`) puts locals in an IX frame (`(IX-1)`, `(IX-2)` in the test output). Locals then work from ROM and are reentrant. Globals still need absolute pointers or an ASEG assembly module |
| Const data in ROM | All initialized data, `static const` tables and string literals go in DSEG (`codegen.py:3627-3712`) |
| ROM to RAM copy | No startup code copies initialized data. A ROM crt0 needs the copy plus a BSS clear. Set the stack with `LD SP,0`, not `LD SP,0FFFFH` (odd SP) |
| Interrupt functions | No ISR attribute. `[[gnu::interrupt]]` is ignored. A bare SDCC `__interrupt` is silently erased (`uc_core/frontend.py:62-79`): a silent miscompile. `__interrupt(1)`, `__critical __interrupt`, `__naked`, `__critical { }`, `__asm ... __endasm;` and `interrupt 1` stop with "internal error: unexpected token" (loud, but not a proper diagnostic). SDCC `__asm`/`__endasm` blocks must be rewritten |
| Port I/O | No `__sfr`, no `__at`, no in/out intrinsic, no extended asm operands. `__sfr __at(0x04) X;` is a parse error. The globals-plus-`asm` workaround is not ISR-safe |
| Startup references | Dead-function elimination deletes functions that only the startup file names. Workaround: a file-scope `asm("; _isr\n")` |
| ISR safety | Runtime helpers use fixed memory (`__tmp32`, `__acc64`, `__sret_buf`). A function called from `main` and from an ISR is not reentrant, and nothing detects the case |
| RAM cost | `__sret_buf DS 256` is 12.5 % of Neo Geo RAM |
| Banking | No `__banked`, no far pointers, no trampolines |
| Calling convention | Arguments on the stack right to left, caller cleans up, results in A, HL or DEHL (`docs/implementation_plan.md:35-38`); parameters read from `(IX+4)`, `(IX+6)`. Close to SDCC `__sdcccall(0)` (the 8-bit return register may differ, unverified). Calls into nullsound's register-argument routines need shims |
| Interoperation | M80 `.REL` is bit-packed; sdas `.rel` is text, so `sdldz80` cannot read uc80 objects. Options: an M80-to-sdasz80 syntax translator (the reverse of `docs/z88dk_um80_syntax_spec.md`), or a binary-level link of ul80 `-x` output into the ROM. Not tested |
| Startup files | `--no-embed-startup` and `--startup-lib FILE` exist (`src/uc80/main.py:418-427`). A ROM test with a custom crt0 and `ul80 -p 0 -x` linked one module and no libc |

Good news for small RAM: uc80 overlays the locals of non-recursive functions in one static `??AUTO` block (`codegen.py:1132`, `:1647-1690`). After ul80 gets a data origin option, that block can sit in the F800 RAM.

### 6.4 libc work for the game tier

Status: `malloc` and `free` (first-fit list with merging, heap from `__END__`), `qsort` (ascending quicksort) and `printf` precision, `-` and `*` are fixed. The other rows are not rechecked or not started.

| Gap | Today |
|---|---|
| argc/argv | crt0 calls `main()` with no arguments. The CP/M command tail at 0x80 is ignored |
| Console input | `getchar` uses BDOS 1 (echo), returns 13 for Enter and 26 at end of input, never `'\n'` or `EOF` (`lc/lc_getchar.mac:17-22`). No raw or non-blocking input |
| curses | None. Add a VT100 subset with a shadow screen (tinycurses: 3,887 B of code, 2,140 B of data) |
| malloc | Bump allocator, and `free` is a no-op. **Bug:** the heap starts at `_heap_start` in the malloc module's own DSEG (`lc_malloc.mac:31`, `:229-231`) and overwrites later data and BSS. Start the heap at `__END__` |
| fseek/ftell | `SEEK_END` returns -1. `SEEK_SET` goes to the start of the 128-byte record. Offsets are cut to 16 bits |
| time | `time()` and `clock()` return 0. `localtime`, `mktime`, `strftime` and `ctime` are declared but do not link |
| rand | 15-bit LCG, repeats after at most 32,768 values |
| Missing | `vsnprintf`, `strcasecmp`, `random`, `getopt`, `getenv` (returns NULL), `isascii`, `sig_atomic_t`, `float_t`/`double_t` |
| Wrong prototypes | `vprintf`/`vfprintf`/`vsprintf` take `...` instead of `va_list`. `div` returns `div_t *`. `memset` takes `unsigned char` |
| printf | `%.4d` and `%5.3d` precision ignored. `%*d` not supported |
| qsort | Sorts in descending order with an ascending comparator (test output `32766 32753 7 5 0 -3`) |

### 6.5 Compiler bugs found by the survey

Status: C11 (`ldiv`), C12 (binary-trees) and C16 (duplicate runtime modules) are fixed; `binary-trees` is OK in the tables. A recheck on 2026-10-24 shows that C1 and C6 are fixed, C3, C10 and C13 and C15 are not, and C8 still blocks the whole-program link of battlestar and Rogue 3.6. The other bugs are not rechecked.

The repro files are in the survey's scratch directories. Each bug needs a regression test in the uc80 test suite.

| ID | Bug | Minimal repro |
|---|---|---|
| C1 | Crash on a cast of a character literal: `TypeError ... 'Token' and 'int'` in `gen_cast` (`codegen.py:10665`) | `void f(int); int main(void){ f((int)' '); return 0; }` |
| C2 | Block-scope `extern` gets no `EXTRN`, so um80 reports "Undefined symbol" | `void f(char **e){ extern char **environ; environ = e; }` |
| C3 | Undeclared identifiers and implicit function calls get no diagnostic and become `EXTRN` symbols (C23 requires a diagnostic) | `int g(void){ return UNDECLARED_X / 2; }` |
| C4 | Whole-program mode merges file-scope `static` names across files. Same type: silent miscompile. Different array sizes: false "conflicting types" | two files, each with `static int cnt;` |
| C5 | Whole-program mode accepts two definitions of `main`, and the last one wins | three files a.c, b.c, c.c |
| C6 | Internal error on a local array sized with a pointer type | `void f(void){ int u[sizeof (char *)]; u[0]=0; }` |
| C7 | `"ab\\\n"` loses the backslash and emits a raw newline into `db` | Rogue 5.4 rip.c |
| C8 | Assembly dead-code pass is far too slow on large programs (over 15-20 minutes on Rogue and battlestar). `--no-asm-dce` takes 22 s | Rogue 3.6 whole-program build |
| C9 | Inlining makes Rogue 25 % bigger (134.5 KB against 108 KB) | Rogue 3.6 |
| C10 | No warning when an integer constant does not fit in `int` | `int e_levels[] = { 40920, 327680 };` |
| C11 | `ldiv` is wrong in whole-program mode (quot=-1), right with `--no-whole-program` | `res = ldiv(314159265UL, 10000L)` |
| C12 | binary-trees hangs or gives a wrong check value | z88dk binary-trees N=8 |
| C13 | An unknown type name gives "internal error: unexpected token" | `sig_atomic_t x;` with no definition |
| C14 | `scanf("%d")` reads nothing from redirected stdin under cpmemu. Cause (uc80 or cpmemu) not found | Dhrystone run count |
| C15 | Bare SDCC `__interrupt` silently erased; other SDCC keywords give "internal error" instead of a diagnostic | `void h(void) __interrupt { }` |
| C16 | Linked game programs warn `%Mult. Def. Global` (`__START`, `__MUL16`, `__ADD32`, `__TMP32`) and carry duplicate runtime modules. Cause (survey link script, uc80 or ul80) not found | wump, monop, robots relinks |

## 7. Phased plan

Effort figures are rough estimates for one developer.

Status on 2026-10-24: phase 0 is done in part. The reply to issue #1, the `bench-size` CI gate, `float_t` and `double_t`, and the sync of the compliance documents are not rechecked. Phase 1 is done through `z88dk-ticks` instead of a new cpmemu timer. Phase 2 is done with 9 compilers (80cc is in). Phases 4 and 5 (the games tier) are done with the exceptions in section 1: argv, `fseek` and time functions were not needed, because the games use scripted stdin and the shim in `bench/support/posix_shim.c`; mojozork has a size-only row and no Zork I run. Phase 6 is started: Stanford, Embench, CoreMark, decompressors and 4 sound kernels are in. Phase 3 is not started.

| Phase | Work | Output | Effort |
|---|---|---|---|
| 0 | Post the first reply to issue #1 (draft below). Remove the "smallest known binaries" sentence. Fix the unused-EXTRN regression (6.1). Fix the heap start, `qsort`, `%.4d` and `ldiv`, and add `float_t`/`double_t`. Make `benchmarks.md` and `ANSI_C_COMPLIANCE.md` agree. Add the `bench-size` CI gate (5.5). Measure the game COM sizes again (C16) | Valid uc80 sizes, reply posted | 3-4 days |
| 1 | cpmemu T-state table, STATS line, SP low-water mark and region timer (6.2). The `bench/` harness (5.6), reusing the `run_fujitsu.py` pipeline | Reproducible size and cycle numbers | 1-2 weeks |
| 2 | Tier 1 on uc80, SDCC 4.6.0, SDCC 4.4.0, z88dk (zsdcc, sccz80, 80cc), HI-TECH 3.09 and dcc, including the davidly set and gamer_benchmark. Cross-check two programs with `z88dk-ticks`. Publish `docs/benchmarks_speed.md`. Second reply to issue #1 with the SDCC columns | Speed and size table | 1 week |
| 3 | Freestanding work: ul80 data/BSS origin option, ROM crt0 with data copy and BSS clear, interrupt attribute, keep startup-file references, stop erasing `__interrupt` and give a real diagnostic for other SDCC keywords. Then sound-driver kernels 0-4 on uc80 (`--no-shared-storage`), SDCC 4.4/4.6, z88dk `+z80` and llvm-z80 | Freestanding table, full answer to issue #1 | 2-3 weeks |
| 4 | Game tier, part 1: argv, console input, malloc/free, fseek and time functions in libc. tinycurses in the repo (or in a test directory). Run wump, fish, arithmetic, robots, mille, snake and the FUZIX V7 games with scripted input; add Ahl Star Trek, Empire and umchess with z88dk baselines | Game cycle and size table | 1-2 weeks |
| 5 | Game tier, part 2: mojozork with Zork I (historicalsource story file) and a paged story file. Rogue 3.6, trek and adventure as size-only rows against SDCC and z88dk. Fix C1-C13 along the way | "Does it fit" table | 1-2 weeks |
| 6 | Tier 2 kernels (Stanford, Embench, MiBench, CoreMark, decompressors), sound kernels 5-8, emulator check, later-wave compilers, historic CP/M compilers and non-C rows | Full tables | open-ended |

## 8. Risks and open questions

- **Speed results may look bad.** The code shape in section 2 and the sieve estimate (7-10 M against 4.22 M T-states) predict that uc80 loses on cycles against SDCC. Publish the numbers anyway, and use them to choose optimizer work.
- **The size claim may not hold.** On the first like-for-like test, SDCC 4.6.0 uses fewer bytes than uc80 beyond the sieve array (about 279 against 658). dcc may also be smaller.
- **Conflict of interest.** The uc80 developer picks the rivals' flags. Mitigation: use z88dk header and wiki flag lines without change, and send the uc80 rows to z88dk for review (5.4).
- **Tuning to the suite.** Commit the manifest before the first rival run, never drop a program after seeing results, and keep the game tier as a set the optimizer was not tuned on.
- **Licensing policy.** uc80 is GPL-3.0 (`LICENSE`), so BSD, MIT, zlib, ISC, Apache-2.0 (CoreMark) and GPL-3.0 (Embench) sources can be vendored under `bench/third_party/<name>/` with their own LICENSE files. V7 code needs the Caldera notice in every copy. Fetch only, never vendor: Scott Adams game data, the Creative Computing `eliza.c`, Larn, Zork data other than the MIT historicalsource release, and anything with an unverified license (Dhrystone, stdcbench, MiBench, micro-Max, Ahl Star Trek, Empire, ScottFree 1.14). Do not commit generated files that hold third-party text (for example `glorkz` as a C array). Do not use TSCP: the TSCP README forbids distribution without the author's permission ([TSCP](https://www.chessprogramming.org/TSCP)). CoreMark has reporting rules. The System III games are not usable. Download historic CP/M compilers from official or museum sources.
- **16-bit `int` ports.** Many sources (Stanford, Embench, Rogue `e_levels`) assume 32-bit `int`. Every compiler must use the same ported source.
- **SDCC on CP/M.** SDCC ships no CP/M crt0 or BDOS stdio. Our glue code affects the SDCC numbers, so keep the glue small and publish the glue.
- **z88dk changes fast.** Nightly builds change the numbers. Pin a nightly date and record the zsdcc revision.
- **qkz80 accuracy.** A new T-state table can contain errors. Cross-check against `z88dk-ticks` on two or more programs. BDOS time is not in any cycle count.
- **Overlays.** No famous large game fits without overlays or a much smaller code generator. Do we add an overlay manager to ul80, or keep large games as size-only rows?
- **Freestanding scope.** Do we want uc80 to target ROM code at all? If yes, phase 3 is the largest work item. If no, the reply to issue #1 must say so.
- **Interoperation with ngdevkit.** Should uc80 emit sdas-compatible output or use the SDCC calling convention, so that C code can link with nullsound? llvm-z80 already has an sdas output mode.
- **Open fact checks:** SDCC 4.6 flag spellings, dcc output with um80/ul80, llvm-z80 CP/M support, `z88dk-ticks` page-zero setup for a `.com`, whether `TIMER_START::` reaches the ul80 `.sym`, the YM2610 ADPCM bus detail, whether nullsound uses the alternate registers, the stdcbench site and license, the Dhrystone and MiBench licenses, the cause of the `scanf` failure (C14) and of the duplicate runtime modules (C16), the HI-TECH 3.09 float size, the CP/M Rogue origin.

## Appendix A: z88dk wiki reference numbers

Source: [z88dk Benchmarks](https://github.com/z88dk/z88dk/wiki/Benchmarks) (wiki commit 2026-10-15), `+test` target, no stdio. Each cell is Z80 cycles / size in bytes (CODE + DATA + BSS).
Each compiler uses its own documented `-D` switches. SDCC uses default optimization plus `--max-allocs-per-node200000` (section 5.3).

| Benchmark | SDCC 4.6.0 | HI-TECH 3.09 CP/M | HI-TECH 7.80 | IAR 4.06A | Best z88dk (Z80) |
|---|---|---|---|---|---|
| sieve | 4,218,689 / 8279 | 7,916,099 / 10297 | 3,885,436 / 8472 | 3,714,152 / 8772 | 3,445,306 / 9177 (80cc) |
| dhrystone 2.1 | 274,542,684 / 6774 | 356,235,065 / 8988 | 280,100,135 / 7002 | 306,860,580 / 7371 | 242,442,688 / 6982 (zsdcc new) |
| fannkuch | 55,733,097 / 946 | 56,614,856 / 2771 | 51,982,515 / 868 | 56,708,022 / 1347 | 52,705,234 / 1109 (zsdcc new) |
| binary-trees | 193,466,493 / 7196 | 298,416,076 / 5821 | 240,336,355 / 4247 | 7,358,336,547 / 4525 | 137,486,796 / 3858 (80cc classic) |
| pi (no ldiv) | 7,218,092,827 / 6598 | 5,532,347,800 / 8342 | 5,528,979,464 / 6593 | 8,762,223,085 / 6789 | 1,696,878,309 / 8999 (sccz80 new fast) |
| fasta | 414,019,207 / 6027 | 189,901,647 / 5638 | DISQ | 223,805,149 / 6041 | 123,339,059 / 4479 (80cc math32) |
| n-body | 4,361,393,645 / 7283 | 1,594,771,948 / 5633 | 1,600,543,903 / 3736 | 2,331,516,019 / 4084 | 800,805,511 / 6859 (80cc math32) |
| whetstone 1.2 | 1,850,296,726 / 10364 | 646,520,995 / 9076 | 614,748,605 / 6919 | 732,360,277 / 6524 | 352,030,380 / 10782 (80cc math32) |

The wiki also lists a faster binary-trees row, 125,194,602 / 3621, but that row is 80cc 8085 code timed with `z88dk-ticks -m8085`, so the row does not belong in a Z80 table.
The wiki reports that HI-TECH 7.80pl2 miscompiles fasta (DISQ) and hangs in Dhrystone above global optimizer level 2.

## Appendix B: other sources

- [ngdevkit](https://github.com/dciabrin/ngdevkit), [ngdevkit-examples](https://github.com/dciabrin/ngdevkit-examples), [FOSDEM 2026 ngdevkit talk](https://fosdem.org/2026/schedule/event/SUPPYH-ngdevkit_free_and_open_source_cc_development_on_the_neo_geo_in_2026/)
- [neogeodev YM2610](https://wiki.neogeodev.org/index.php?title=YM2610), [neogeodev Z80 bankswitching](https://wiki.neogeodev.org/index.php/Z80_bankswitching)
- [z88dk Tool---ticks](https://github.com/z88dk/z88dk/wiki/Tool---ticks), [z88dk Benchmarks](https://github.com/z88dk/z88dk/wiki/Benchmarks), [z88dk examples/console](https://github.com/z88dk/z88dk/tree/master/examples/console)
- [FUZIX](https://github.com/EtchedPixels/FUZIX), [ScottAdams6800](https://github.com/EtchedPixels/ScottAdams6800), [ScottFree on IFWiki](https://www.ifwiki.org/ScottFree), [ScottFree on IF Archive](https://ifarchive.org/indexes/if-archive/scott-adams/interpreters/scottfree/)
- [historicalsource/zork1](https://github.com/historicalsource/zork1), [TechSpot on the Zork release](https://www.techspot.com/news/110337-microsoft-open-sources-original-zork-trilogy-unlocking-huge.html)
- [davidly/cpm_compilers](https://github.com/davidly/cpm_compilers)
- [msx.org C Benchmarks thread (2017)](https://msx.org/ja/node/52107), the origin of the z88dk method
- [Z80Babel](https://github.com/MartinezTorres/z80_babel)
- [comp.compilers 21-10-034](https://compilers.iecc.com/comparch/article/21-10-034) (SDCC register allocator papers)
- [SDCC FrOSCon 2025 slides](https://programm.froscon.org/2025/system/event_attachments/attachments/000/000/922/original/SDCC-2025.pdf)
- [embeddedrelated stdcbench thread](https://www.embeddedrelated.com/showthread/comp.arch.embedded/225684-1.php)
- [BEEBS](https://github.com/mageec/beebs), [zForth](https://github.com/zevv/zForth)
- [1981 BYTE sieve timings](https://txbobsc.com/aal/1981/aal8110.html)
- [BDS C](https://www.bdsoft.com/resources/bdsc.html), [Aztec C notes (Kevin Boone)](https://kevinboone.me/cpm-c.html), [MESCC](https://github.com/MiguelVis/mescc)
- [Hack](https://en.wikipedia.org/wiki/Hack_(video_game)), [Ladder](https://en.wikipedia.org/wiki/Ladder_(video_game))

## Draft reply to issue #1

Thanks for the question, and for the pointer to ngdevkit.
Short answer today: no, uc80 is not ready for a Neo Geo sound driver.
uc80 has no interrupt-function attribute, no `__sfr`/`__at` port access and no banking.
By default uc80 puts globals, BSS and locals right after the code, so in a ROM build those variables land in ROM. ul80 has no option yet to put data at F800h, and no startup code copies data from ROM to RAM.
uc80 silently drops a bare SDCC `__interrupt`. Other SDCC forms such as `__critical __interrupt` and `__asm`/`__endasm` stop with an error, so SDCC driver code needs edits.
uc80 writes M80 `.REL` objects and passes arguments on the stack, so `sdldz80` cannot link uc80 output with nullsound.
On size, our first like-for-like test (the z88dk sieve) shows SDCC 4.6.0 smaller than uc80, and we expect SDCC to be faster too.
We plan to add Z80 T-state counts, standalone SDCC 4.4.0 (the ngdevkit pin) and 4.6.0 columns, and a freestanding sound-driver set: NMI command ring, YM2610 register writes, sequencer tick, note to F-number.
If you need C next to nullsound now, llvm-z80 has an sdas output mode that may suit you better. We have not tested the llvm-z80 path ourselves.
It would help a lot if you could share one hot function from your driver, or the driver's per-tick cycle and RAM budget, so we benchmark real code.
We will post the numbers in this issue when the numbers exist.
