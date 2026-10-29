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
| `bench/support/` | Shared support code for games: `posix_shim.[ch]` and `tinycurses.[ch]` (see [Games](#games-scripted-interactive-programs)) |
| `bench/report.py` | Makes `docs/benchmarks_speed.md` from `results.csv` |
| `bench/difftest.py` | The differential tester: random C programs, uc80 against SDCC (see [The differential tester](#the-differential-tester)) |

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
| `files` | A list of files. `{repo, path, sha256}` is an upstream file. `{path}` is relative to `bench/programs/<name>/`. `{shared}` is relative to `bench/programs/`, or else to `bench/` (a file that more than one program uses, for example `support/posix_shim.c`). `main = true` marks the source that gets `#include "bench.h"`. |
| `edit` | A list of `{file, old, new}` text replacements. The text in `old` must occur one time. Every compiler gets the same edited text. |
| `check_defines` | The defines of the check build, for example `["PRINTF"]`. |
| `time_defines` | The defines of the time build, for example `["TIMER"]`. |
| `variants` | A list of `{name, defines}`. Each variant is one more program, `<name>+<variant>`, with the defines added for every compiler. A variant can also have `about`, `label` and `time_only = true`. |
| `kind` | Optional. `"size-only"` makes a program that the runner builds but does not run (see [Size-only programs](#size-only-programs)). The default is a program that runs. |
| `stdin` | Text for the standard input of the program. |
| `stdin_file` | A file in `bench/programs/<name>/` that holds the standard input. A variant uses the file of its base program. Use it for a script that is longer than a few lines. |
| `timing` | Optional. `"total"` makes the runner skip the region run. The row then has the cycles of the whole run and `timing=total`, also when the program has TIMER labels. Games use it. |
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

## Size-only programs

The runner only builds a program with `kind = "size-only"`. The
program has no check build, no run and no golden file. Use this kind for a program that does
not fit in memory, or that needs input that the harness cannot give.
The report shows these programs in their own tables, and not in the
speed tables.

For each compiler and mode the runner makes two builds in
`bench/_work/<program>/<compiler>/<mode>/objects/` and `.../link/`:

1. **The per-object build.** The compiler compiles each source file
   alone, with no link. The runner reads the code size and the data size of each
   object. `Compiler.objects()` does this, and it returns a
   `compilers.Objects`. The row holds the sums in `code` and `data`, and
   the size of each object in `objects` (`name=code/data;...`).
2. **The whole-program link.** The runner calls `build()` as for any
   other program. The row holds `image`, `bss` and `com`.

The status of a size-only row is one of these:

| Status | Meaning |
|---|---|
| FITS | The program links, and `0100h + image + bss + 512` is not above `E400h`. |
| TOO-BIG | The program links and is larger, or the linker stops and its message says that the program is too large (`compilers.TOO_BIG_RE`). A program that runs also gets TOO-BIG when its .com is more than 65,280 bytes: ticks cannot load that file (it prints "Incorrect length" and no output), and the program does not fit the 64 KB address space of a CP/M machine. This rule applies to the check build and to the time build. The row of a TOO-BIG program holds `com`, `bss` and `image`. When `ul80` wrapped the symbols, `image` is the size of the image in the .com file. |
| BUILD-FAIL(objects) | A compiler or assembler stopped in the per-object build. |
| BUILD-FAIL(link) | The link stopped, and the message does not say that the program is too large. The row keeps the per-object sizes. |
| BUILD-TIMEOUT(objects), BUILD-TIMEOUT(link) | A tool stopped at its time limit. |

`E400h` is the BDOS base of a 64 KB CP/M 2.2 system. The TPA is thus
0100h to E3FFh, which is 58,112 bytes. The 512 bytes are for the stack.
`TPA_LIMIT` and `TPA_STACK` in `bench/compilers.py` hold the numbers.
The heap of the program has no reserve. Under `z88dk-ticks` the stack
top is 0C9FEh, so a program that fits here can still be too large to
run there.

### How each compiler measures the size of an object

Code is the instructions. Data is the variables, and for some compilers
also the strings and the constant tables. Compare the sum of code and
data (the second number in the report), because the compilers put
strings in different places. Never compare the numbers of the
per-object build with the size of a whole-program uc80 link. A
whole-program link removes code that no function calls.

| Compiler | Compile step | Code | Data |
|---|---|---|---|
| uc80 | `uc80 --no-whole-program --no-embed-runtime --no-embed-startup`, then `um80` | The "Code segment" bytes that `um80` prints | The "Data segment" bytes. This includes strings and the BSS. |
| sdcc, sdcc-4.4 | `sdcc -c` | The areas `_CODE`, `_HOME`, `_GSINIT` and `_GSFINAL` of the `.rel` file. Strings and `const` tables are in `_CODE`. | `_DATA` and `_INITIALIZED`. `_INITIALIZER` (the ROM copy of `_INITIALIZED`) does not count. |
| llvm-z80 | `clang -S`, then `sdasz80` | The same areas as for SDCC | The same areas as for SDCC |
| z88dk-zsdcc, z88dk-sccz80 | `zcc +cpm -c`, then `z88dk-z80nm -a` | The sections that start with `code` | The sections `rodata`, `data` and `bss` |
| hitech | The full build. The map of the link lists each module. | The `text` length of each user module | The `data` and `bss` lengths of the module |
| dcc | `dcc` and `dccpeep`, then `um80 -t -e .Z80` | The "Code segment" bytes of the `.MAC` file | The "Data segment" bytes |

Notes:

- dcc puts all variables in its code segment. Its code number thus
  includes the data, and its data number is 0. Compare the sum.
- The hitech numbers include a 2048-byte input buffer of `posix_shim.c`
  that no other compiler has.
- The hitech and dcc numbers are weaker than the others. HI-TECH C has
  no tool that prints the size of one `.obj`. The runner thus reads the
  map of the link. A program that does not link has no per-object sizes.
  For dcc, the per-object build does not run `dccrtlstrip`, which removes
  unused code between modules.
- The per-object uc80 build links against `libc.lib` in the whole link
  only. Its object holds a table of the printf handlers (for example,
  195 bytes of code for a three-line program). Thus uc80 has a fixed
  offset for each object that calls `printf`.

## Games: scripted interactive programs

A game reads commands from the standard input and draws on a terminal.
The harness runs a game under `z88dk-ticks` with its input from a file,
and compares the output with a golden file from the host compiler.
These rules make the run the same on every compiler:

- **The input.** Put the script in `bench/programs/<name>/input.txt` and
  name it with `stdin_file`. `z88dk-ticks` reads end of file as the byte
  0FFh, so the script must end with the quit command of the program.
- **The clock and the random numbers.** The program must not read the
  clock. `posix_shim.h` maps `time()` to a function that returns the
  constant 1000000000, and `getpid()` to 1. It maps `rand()` and
  `srand()` to one generator for all compilers (the ANSI C sample
  generator, `RAND_MAX` 32767). `srand()` takes an `unsigned`. A seed
  that is larger than 16 bits gives different values on a 16-bit
  compiler and on the host. Mask the seed to 15 bits, as `guess.c` does.
- **The timing.** A game has no TIMER labels. Set `timing = "total"` in
  the manifest. The cycles of the row are the count of the whole run,
  and the report marks them with a dagger. Do not compare them with the
  region counts of other programs.
- **The golden file.** `python3 bench/run.py --golden <name>` builds the
  program with the host `cc`, with the same shared files, runs it with
  the same input and writes the output. Read the file. Make the golden
  file again each time that you change the script, a seed, or a shared
  file.
- **The end of the output.** Make the program end its output with a
  newline. `tinycurses` writes a newline in `endwin()`.

The six big real games (Rogue 3.6, trek, adventure, battlestar, monop,
mojozork) are size-only programs: see [bench_big_games.md](bench_big_games.md).

The proof programs are our own. `game-guess` is a stdio game with a
scripted input. `curses-demo` is a tinycurses program with a status
window. `curses-demo-size` has the sources of `curses-demo` as a
size-only program. Their manifest file is `bench/programs.d/games.toml`.

The six curses games (robots, worm, snake, mille, canfield, invaders)
are in [bench_games_curses.md](bench_games_curses.md).

### Shared support code

The files in `bench/support/` are our own code (GPL-3.0-or-later). A
program lists them as `{ shared = "support/<file>" }`. The same C text
goes to every compiler, with a few `#ifdef` branches for the library
differences below. The code is C89, because HI-TECH C 3.09 and dcc
accept only C89. It has no `//` comments, no variadic macros, and no
`#` or `##`.

Its declarations are at the start of a block.

**Names.** dcc and L80 keep only six characters of an external name:
the `_` and five more. Two names with the same first five characters
become one symbol. The link then calls the wrong function. For
example, `mvaddch` and `mvaddstr` are the same name for dcc.

- Each external name in `bench/support/` has a different five-character
  prefix (`tc_xx` for tinycurses, `px_xx` for the shim).
- The calls that a program makes are function-like macros that map to
  these names. A function-like macro does not change a variable with
  the same name, so `int index;` and `int move;` still work.
- The printw family are object-like macros, because they take a
  variable number of arguments.
- A program of our own must follow the same rule for its public names.

**posix_shim.h and posix_shim.c.** A game includes `posix_shim.h` after
all system headers, and a manifest `edit` replaces the includes of
`<unistd.h>`, `<time.h>`, `<signal.h>`, `<err.h>` and `<strings.h>`.

| Call | What the shim does |
|---|---|
| `time(p)`, `getpid()` | Return the constants 1000000000 and 1. |
| `sleep(n)`, `usleep(n)` | Return 0 at once. |
| `getenv(s)` | Returns NULL. |
| `signal(s, h)` | Returns NULL. A signal never comes. The handler type is `void *`. `SIGINT`, `SIG_IGN` and other macros are defined when the system has none. |
| `rand()`, `srand(s)`, `random()`, `srandom(s)` | One generator for all compilers. |
| `index`, `rindex`, `bzero`, `bcopy` | Macros for `strchr`, `strrchr`, `memset` and `memmove`. |
| `strcasecmp`, `strncasecmp` | ASCII comparison without case. |
| `bench_args(&argc, &argv)` | When argc is less than 1, sets argc to 1 and argv to `{"game", NULL}`. |
| `err`, `errx`, `warn`, `warnx` | Print `game: message` (and `: error` for `err` and `warn`) to the standard OUTPUT, because the golden file holds only standard output. `err` and `errx` then call `exit()`. |
| `px_vf(buf, cap, fmt, ap)` | A small formatter: `%% %c %s %d %i %u %x %X`, the `l` modifier, the flags `-` and `0`, and a width. No floating point. `err()` and `printw()` use it, so these functions print the same on every compiler. |

Library differences that the shim hides:

- **z88dk.** The classic console driver echoes each character that
  `getchar()` reads. The shim maps `getchar()` to `fgetc_cons()`, which
  makes the same BDOS call without the echo.
- **HI-TECH C.** `getchar()` uses BDOS function 10, which
  `z88dk-ticks` does not implement. The console output of HI-TECH C also
  takes characters out of the input file while the program prints.
  `bench_args()` and `initscr()` thus read the whole input (at most 2048
  bytes) into a buffer, and the shim maps `getchar()` to `px_gc()`,
  which reads from that buffer. The buffer is in the BSS of every hitech
  game.
- **SDCC and llvm-z80.** `stdlib.h` does not declare `exit()`. The shim
  declares it. The CP/M start-up code of the harness defines it.
- **z88dk sccz80.** A function with `...` cannot find its named
  parameters with the default calling convention. `PX_VA` (`__stdc`) in
  each prototype and definition of such a function selects the standard
  convention, and the shim defines the `va_` macros for it. A program
  of our own that defines a variadic function needs the same. `unsigned
  long % 10` also gives wrong digits (for example 100000 % 10 is 4),
  so `px_vf()` computes `v - (v / 10) * 10`. The second `long` argument
  of one variadic call is read wrongly, so do not pass two `%ld` or
  `%lu` arguments in one call under sccz80.
- **uc80.** The AST optimizer runs the assignment of one `if` branch
  in the case that the program takes the other branch. This happens
  with a temporary for a long quotient. One branch has
  `q = v / 10; d = v - q * 10; v = q;` and the other branch has
  `d = v & 15; v = v >> 4;`. `px_vf()` has no such temporary.
- **dcc.** The compiler refuses `sv = va_arg(ap, long)` in one arm of an
  `if` and `sv = va_arg(ap, int)` in the other arm. `px_vf()` reads the
  `int` into a temporary variable first.

A game can read its input with `fgets()`, `gets()` or `scanf()`. Such
a game needs a similar mapping for each compiler that echoes or does
not support the call. Add the mapping to `posix_shim.h` in the same
way.

**tinycurses.h and tinycurses.c.** The library is a VT100 and ANSI subset
of curses with a shadow screen. The screen has 24 lines (`LINES`) of 80
columns (`COLS`). The library writes only the cells that changed, and
only at `refresh()`, with `putchar()`. `getch()` refreshes, and then
reads with `getchar()`.

The header lists the functions. These are the main ones:

```
initscr endwin move addch addstr mvaddch mvaddstr printw mvprintw
clear erase refresh clrtoeol getch cbreak noecho nl nonl standout
standend newwin wmove waddch wrefresh
```

The library has no color, no attribute other than standout, no
function key and no scroll. A window from `newwin()` is a rectangle of
the one screen array. The window has no text of its own. Windows do
not overlap in a managed way. There are at most two such windows. A
write past the last cell returns `ERR`.

`printw()` uses `px_vf()`. A program that uses tinycurses thus links
`posix_shim.c` too. The host build gives byte-identical output, because
the same C makes the output.

## The differential tester

`bench/difftest.py` makes random C programs. It compiles each program
with uc80 and with SDCC 4.6.0, runs the two `.com` files under
`z88dk-ticks`, and compares the output.

### What a program contains

Each program has globals and locals of the types `char`,
`signed char`, `unsigned char`, `int`, `unsigned int`, `long` and
`unsigned long`. It also has arrays, structs with bit-fields, pointers,
and small helper functions. Some calls go through tables of function
pointers. The statements use all C integer operators, casts, compound
assignment, `++` and `--`, `if`, loops and `switch`. After each
assignment, a checksum gets the new value. After each top-level
statement of `main`, the program prints the checksum.

The programs have no undefined behavior. The generator puts a guard
on each operation that can overflow or divide by zero. A Python model
of the C language with a 16-bit `int` also runs each program. The
model stops at undefined behavior: a signed overflow, a shift by a bad
count, a division by zero, `INT_MIN / -1`, or an index out of range.
The generator then makes the program again with the next attempt
number.

uc80 has a signed plain `char`. Thus SDCC gets `--fsigned-char`.
SDCC also gets `--nogenconstprop`, because the generalized constant
propagation of SDCC 4.6.0 has bugs. Use `--sdcc-flags` to change
these flags.

### Results

The model gives a third output. Thus each result tells which compiler
is wrong:

| Status | Meaning |
|---|---|
| PASS | uc80, SDCC and the model give the same output. |
| UC80-WRONG | SDCC and the model agree. uc80 is wrong. |
| SDCC-WRONG | uc80 and the model agree. SDCC is wrong. |
| MODEL-DIFF | uc80 and SDCC agree, and the model does not. The generator has a bug, or the two compilers have the same bug. Examine each one. |
| ALL-DIFF | The three outputs are all different. |
| UC80-BUILD-FAIL, SDCC-BUILD-FAIL | The compiler, assembler or linker failed. |
| UC80-HANG, SDCC-HANG | The program did not stop before `--timeout`. |
| SDCC-BUILD-TIMEOUT | SDCC did not compile the program before `--build-timeout`. |

The `line` value of a result is the first output line that is
different. Line *n* is the checksum after statement *n* of `main`.

### Running the tester

Run the commands from the uc80 checkout.

1. To test 500 programs, run `python3 bench/difftest.py --count 500`.
   Each program takes the next focus area.
2. To test some focus areas only, give `--focus` and a comma list.
3. To keep each failed program, give `--keep-failures DIR`. The tester
   writes `prog.c`, the three outputs and the build logs to
   `DIR/s<seed>-<focus>/`.
4. To reduce each kept failure after the run, also give
   `--reduce-failures`. The tester reduces only the failures that
   involve uc80, because a reduction takes some minutes.

```bash
python3 bench/difftest.py --count 500 -j 8 --keep-failures /tmp/df --reduce-failures
python3 bench/difftest.py --count 300 --focus shifts,mul-div-const --seed 5000
python3 bench/difftest.py --print 17 --focus calls
```

The focus areas are `all`, `char-arith`, `const-ops`, `conditions`,
`assign`, `bitfields`, `shifts`, `mul-div-const`, `long`, `llong`, `switch`,
`calls`, `incdec` and `memory`. A focus area changes the weights of
the types, operators and statements. Program *i* of a run has the seed
`--seed` + *i*. The same seed, focus area and tester version always
give the same program.

The focus area `llong` gives much weight to `long long` and
`unsigned long long`. SDCC cannot build or run every such program
correctly. When SDCC does not agree with the model, only the model
decides: the status is `UC80-WRONG` if uc80 differs from the model.
A checksum takes both halves of a 64-bit value.

| Option | Meaning |
|---|---|
| `--seed N` | The first seed. The default is 1. |
| `--count N` | The number of programs. |
| `--focus LIST` | A comma list of focus areas, or `rotate` (the default). |
| `-j N` | The number of programs that build at the same time. |
| `--keep-failures DIR` | Keep each failed program in `DIR`. |
| `--reduce-failures` | After the run, reduce each kept failure that involves uc80. |
| `--reduce FILE...` | Reduce these files, then stop. |
| `--reduce-strict` | While the reducer works on a uc80 failure, also compile each candidate with SDCC. |
| `--reduce-budget N` | The maximum number of test builds for one reduction. The default is 800. |
| `--print SEED` | Print the program of `SEED` and `--focus`, then stop. Add `--model` to print the model output. |
| `--work DIR` | The work directory. The default is `bench/_work/difftest`. |
| `--timeout S`, `--build-timeout S` | The time limits for one run and for one SDCC compile. |
| `--reduce-timeout S` | The time limit for one run while the reducer works. The default is 10. |
| `--sdcc-flags`, `--sdcc-allocs`, `--sdcc-mode` | The SDCC options. |

The exit status is 1 when a result names uc80 as wrong.

### Reducing a failure

`--reduce DIR/prog.c` writes the reduced program to
`DIR/prog.reduced.c`, and the three outputs to `DIR/prog.reduced.txt`.

The reducer keeps the failure. For a uc80 failure, the reducer
compares uc80 with the model only, because the model is the
reference. SDCC then compiles the reduced program one time, for the
`.reduced.txt` file. With `--reduce-strict`, SDCC also compiles each
candidate, and the status must stay the same. For example, a
UC80-WRONG program then stays UC80-WRONG.

The first line of a generated file holds its seed, focus area and
attempt number. With this line, the reducer makes the syntax tree
again. It then deletes statements, replaces compound statements with
their bodies, replaces expressions with smaller expressions, and
removes unused declarations. The model checks each candidate, so a
reduced program also has no undefined behavior.

A file without this line, or a file that you changed, gets the line
reducer. The line reducer deletes lines and blocks while the uc80 and
SDCC outputs stay different. The line reducer has no model. Thus,
make sure that the reduced program has no undefined behavior.

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
