# Benchmark Harness

Back to [benchmarks.md](benchmarks.md). The harness in `bench/` compiles
each benchmark program with each compiler, runs the program under
`z88dk-ticks`, and checks the output. It then writes one row per program,
compiler and mode to `bench/results/results.csv`.
`bench/report.py` makes [benchmarks_speed.md](benchmarks_speed.md) from
that file.

## Files

| File | Contents |
|---|---|
| `bench/run.py` | The runner: build, run, check, write `results.csv` |
| `bench/compilers.py` | Built-in compiler adapters: uc80, sdcc, z88dk-zsdcc, z88dk-sccz80 |
| `bench/cc_*.py` | Compiler plugins: dcc, hitech, llvm-z80, sdcc-4.4 |
| `bench/bench.h` | The `intrinsic_label()` macro for each compiler (the TIMER labels) |
| `bench/programs.toml` | The program manifest, with the `[repo.z88dk]` pin |
| `bench/programs.d/*.toml` | More manifest fragments, one for each group of programs |
| `bench/programs/<name>/` | The sources of our own programs |
| `bench/golden/<name>.txt` | The correct output of each program |
| `bench/crt/sdcc/` | CP/M start-up code and console I/O for sdcc, sdcc-4.4 and llvm-z80 |
| `bench/crt/llvm-z80/include/` | Header wrappers that let clang read the SDCC headers |
| `bench/report.py` | Makes `docs/benchmarks_speed.md` from `results.csv` |

The runner writes temporary files to `bench/_work/` and keeps the
downloaded upstream files in `bench/_cache/`. Git ignores both
directories.

## Installing the tools

The harness finds the tools in `$UC80_BENCH_TOOLS`. The default is
`~/src/external/tools` (the `external/tools` directory next to the uc80
checkout). The module comment of each adapter or plugin gives the full
provenance of its tool.

| Compiler | What to install | Override |
|---|---|---|
| uc80 | uc80, um80 and ul80, as the [README](../README.md) tells. The runner uses the `uc80`, `um80` and `ul80` commands on `PATH`. An editable uc80 checkout (`pip install -e .`) measures the checkout. | none |
| sdcc | SDCC 4.6.0: `brew install sdcc`. The runner uses `sdcc` on `PATH`. | none |
| sdcc-4.4 | SDCC 4.4.0, built from `sdcc-src-4.4.0.tar.bz2` with the z80 port only, into `$UC80_BENCH_TOOLS/sdcc-4.4.0`. | none |
| z88dk-zsdcc, z88dk-sccz80 | The z88dk 2.4 release, so that `$UC80_BENCH_TOOLS/z88dk-2.4/z88dk/bin/zcc` exists. | `Z88DK` |
| dcc | `git archive v3.6.0` of davidly/dcc into `$UC80_BENCH_TOOLS/dcc-v3.6.0`, then `sh m-posix.sh` in that directory. | `DCC_HOME`, `DCC_ASM=um80` |
| hitech | `dist/` of agn453/HI-TECH-Z80-C v3.09-21 with lower-case names in `$UC80_BENCH_TOOLS/hitech-3.09`, plus `brew install zxcc`. | `HITECH`, `ZXCC` |
| llvm-z80 | The `llvmz80-23.1.3-r1` macOS ARM64 tarball, unpacked with `--strip-components=1` into `$UC80_BENCH_TOOLS/llvm-z80-llvmz80-23.1.3-r1`. It also needs SDCC 4.6.0 (sdasz80, sdldz80 and `z80.lib`). | `LLVMZ80` |

All compilers need `z88dk-ticks` from the z88dk installation. Without
`z88dk-ticks`, the runner stops. A compiler that is not installed gets
a "skip" message, and the runner continues with the other compilers.

To see which programs and compilers the harness finds, run this command:

```bash
python3 bench/run.py --list
```

## Running the benchmarks

Run the commands from the uc80 checkout.

1. To run all programs with all compilers, run `python3 bench/run.py`.
   A full run takes many hours.
2. To run some programs, give their names. To select compilers, give
   `-c` one time for each compiler.
3. To select a mode, give `-m size` or `-m speed`. To select tiers, give
   `--tier N`.
4. To make the report again, run `python3 bench/report.py`.

```bash
python3 bench/run.py sieve sieve+static -c uc80 -c sdcc
python3 bench/run.py --tier 2 -c z88dk-zsdcc -m size
python3 bench/report.py
```

Each finished cell replaces its row in `results.csv` at once. Rows of
other cells stay. Thus you can run different compilers in parallel
processes:

```bash
for c in uc80 sdcc dcc; do python3 bench/run.py sieve -c $c & done; wait
```

Two runs can share `results.csv`, because `save_rows()` locks the file.
Two runs of the same cell (program, compiler and mode) also work. The
second run waits for the lock on the work directory of that cell, and
then does its own builds.

To end a run, press Ctrl-C or send SIGTERM. The runner then stops the
compiler or `z88dk-ticks` process that runs at that time.

## Adding a program

1. Put the program in a manifest fragment, for example
   `bench/programs.d/mygroup.toml`. Use one `[[program]]` table for each
   program.
2. For an upstream file, add a `[repo.<name>]` table with `commit` and
   `raw`, and give the file a `sha256`. For our own program, put the
   files in `bench/programs/<name>/`.
3. Make the golden file with the host C compiler:
   `python3 bench/run.py --golden <name>`. Read the golden file and make
   sure that the output is correct.
4. Run the program with all compilers. Then run `bench/report.py`.

Each program must include the TIMER labels through
`intrinsic_label(TIMER_START)` and `intrinsic_label(TIMER_STOP)`. The
runner puts `#include "bench.h"` at the top of the main source.

### Manifest keys

| Key | Meaning |
|---|---|
| `name` | The program name. No spaces (zcc splits the work path on spaces). |
| `label` | Optional. The name that the report shows, for example `coremark (unofficial)`. |
| `tier` | 0 baseline, 1 classic benchmarks, 2 kernels, 3 games and real programs. |
| `about` | One line for the Programs table of the report. |
| `files` | A list of files. `{repo, path, sha256}` is an upstream file. `{path}` is relative to `bench/programs/<name>/`. `{shared}` is relative to `bench/programs/` (a file that more than one program uses). `main = true` marks the source that gets `#include "bench.h"`. |
| `edit` | A list of `{file, old, new}` text replacements. The text in `old` must occur one time. Every compiler gets the same edited text. |
| `check_defines` | The defines of the check build, for example `["PRINTF"]`. |
| `time_defines` | The defines of the time build, for example `["TIMER"]`. |
| `variants` | A list of `{name, defines}`. Each variant is one more program, `<name>+<variant>`, with the defines added for every compiler. A variant can also have `about`, `label` and `time_only = true`. |
| `stdin` | Text for the standard input of the program. |
| `defines.host` | Optional. More defines for the host build that makes the golden file (`run.py --golden`) only. No compiler gets a define that the other compilers do not get. |
| `timeout` | Seconds for one run under `z88dk-ticks`. The default is 600. |

A variant with `time_only = true` has defines that only move the timed
region. The check build of the variant then has no variant defines. When
the same check build passed earlier in the same run, the runner does not
build the variant check again.

A variant uses the golden file of its base program. When the output of
a variant is different, put the correct output in
`bench/golden/<name>+<variant>.txt`. `run.py --golden` writes this file
only when the output is different from the golden file of the base
program.

## Adding a compiler

1. Make a file `bench/cc_<name>.py`.
2. In that file, write a subclass of `compilers.Compiler` with `name`,
   `modes`, `available()`, `version()` and `build()`.
3. At the end of the file, set `COMPILERS = [MyCompiler()]`.
   `all_compilers()` loads every `bench/cc_*.py` file.
4. If the compiler needs a TIMER label macro, add a branch to
   `bench/bench.h`. Do not give the plugin a private copy of `bench.h`.
5. Add the name to `ORDER` in `bench/report.py`, so that the column has
   a fixed position.

`build()` gets the source paths, the defines, the include directories,
the work directory and the mode. `build()` returns a `compilers.Build`:

| Field | Meaning |
|---|---|
| `ok` | True when the compiler, assembler and linker succeeded. |
| `com` | The path of the CP/M `.com` file. |
| `symbols` | Label name (upper case) to address. The runner reads `TIMER_START` and `TIMER_STOP` from this table. |
| `log` | The text of all tool output. The runner writes this text to `build.log`. |
| `command` | The compiler command line, for the report. |
| `image` | Bytes of code and initialized data (all bytes below the BSS). |
| `bss` | Bytes of zero-filled RAM after the image. |

An adapter must not change the sources. Some compilers cannot read a
source as it is. For such a compiler, the adapter can change a private
copy of the source, but the meaning of the program must not change.
`bench/cc_hitech.py` is an example.

When a tool stops at its time limit, put a line that starts with
`timeout after <n>s` in the log. The runner then gives the status
BUILD-TIMEOUT, not BUILD-FAIL. `compilers._run()` does this for you.

## How the harness measures

For each cell the runner makes two builds in
`bench/_work/<program>/<compiler>/<mode>/`.

- **check build**: the program with its check defines. The runner runs
  the program under `z88dk-ticks`, and the output must be the same as the
  golden file. The runner ignores spaces at the end of a line, CR
  characters, and empty lines at the end.
- **time build**: the program with its time defines. This build gives
  the size and the cycles.

### Size

The size is the `image` of the time build: code and initialized data,
without the BSS. The file size alone does not compare well, because a
z88dk `+cpm` `.com` file contains its BSS. The report shows the image
minus the image of the `empty` program of the same compiler and mode.
Thus the start-up code and the library code that every program links do
not count.

### Cycles

`z88dk-ticks` counts Z80 T-states. The runner runs the time build two
times:

1. The full run, from 0100h to the jump to 0000h. This run gives
   `total_cycles`.
2. The region run, with `-start` at `TIMER_START` and `-end` at
   `TIMER_STOP`. This run gives `cycles`, and the row gets
   `timing=region`.

When the build has no TIMER labels, `cycles` is the full count and the
row gets `timing=total`. The report marks these counts with †. Do not
compare a † count with a region count.

The dcc labels are the entry points of two empty functions. Thus the
dcc region includes one RET and one CALL (27 T-states) that the other
compilers do not have.

When the check defines and the time defines are the same, the runner
also compares the output of the time build with the golden file.

### Statuses

| Status | Meaning |
|---|---|
| OK | The output is correct. The row has a size and cycles. |
| WRONG | The output of the check build is not correct. |
| HANG | The check build did not stop before the timeout. |
| BUILD-FAIL(check), BUILD-FAIL(time) | The compiler, assembler or linker failed. |
| BUILD-TIMEOUT(check), BUILD-TIMEOUT(time) | A build tool stopped at its time limit: 1800 s in `compilers._run()`, 600 s for the hitech and dcc tools. |
| WRONG(time) | The time build has the check defines, and its output is not correct. |
| HANG(time) | The full run of the time build did not stop before the timeout. |
| HANG(region) | The region run did not get to `TIMER_STOP`. |

The region run has a shorter time limit: five times the time of the full
run, and not less than 60 seconds. A program that does not get to
`TIMER_STOP` thus fails fast.

## Known problems

- **Output without a final newline.** `z88dk-ticks` writes the cycle
  count directly after the last character of the program output. When
  the output ends in a digit, the digits and the count run together. The
  runner can split them again only when it knows the correct text (the
  golden file). Make each program end its output with a newline.
- **Dots in paths.** `z88dk-ticks` selects CP/M mode from the first dot
  in its file argument. Thus the runner starts `z88dk-ticks` in the
  directory of the `.com` file and gives only the name `prog.com`. Do not
  give a path to `z88dk-ticks` in a new tool.
- **Spaces in names.** zcc splits the work path on spaces. Use a program
  name without spaces, and put the display name in `label`.
- **8.3 file names.** The CP/M compilers (hitech, dcc) get all files
  flat in one directory. Give each header an 8.3-safe name, for example
  `embmain.h`.
- **Memory under z88dk-ticks.** `z88dk-ticks` puts the bytes ED FE C9
  at address 5. A start-up that loads SP from address 6 thus gets a stack
  top of 0C9FEh. A program has about 51 KB, not the 58 to 62 KB of a real
  CP/M system.
- **SDCC heap.** The SDCC `z80.lib` reserves a 1023-byte heap. The link
  for sdcc, sdcc-4.4 and llvm-z80 moves the end of the heap to C000h
  (`SDCC_HEAP_TOP` in `bench/compilers.py`). Thus `malloc()` gets the
  memory from the end of the program to C000h, and 2.5 KB stay for the
  stack. When a program that uses `malloc()` ends above C000h, the build
  fails with a message.
- **z88dk float library.** zcc links no float library unless the command
  asks for one. The adapter gives `-lm` (genmath) to sccz80 and
  `-lmath48` to zsdcc, as the z88dk readmes do. A program without
  floats links nothing from these libraries.
- **argc and argv.** The SDCC start-up code calls `main()` with argc 0
  and an argv that holds only a null pointer.
- **Inlined TIMER labels.** clang (llvm-z80) can inline a function that
  holds a TIMER label and also keep the out-of-line copy. The label then
  occurs two times, and the assembler stops. Make such a function
  `static` with a manifest edit, as in the `sort-*` programs.
- **zsh word splitting.** In zsh, `P="a b"; python3 bench/run.py $P`
  gives one argument, `a b`. The runner then prints a warning that no
  program has that name. Use `${=P}` in zsh.
- **Upstream file names.** The runner changes an upper-case `.C` suffix
  to `.c`, and quotes the path in the download URL. Thus a path with
  spaces can use `{path}` in the `raw` URL.
