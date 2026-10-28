# Big games: does it fit?

Back to [bench_harness.md](bench_harness.md). This group has six real
programs from early Unix and Rogue. The runner builds each program for
size only (`kind = "size-only"`, tier 3). The programs are in
`bench/programs.d/games_big.toml`. The plan is in section 4.3 of
[benchmark_proposal.md](benchmark_proposal.md).

The question is: what is the summed per-object size of the program for
each compiler, and does the linked program fit in the 58,112-byte TPA
of a 64 KB CP/M system? A program that does not link, or does not
compile, gets a status with its cause (see [Results](#results)).

## Programs

| Program | Upstream | License | Lines | Pin |
|---|---|---|---|---|
| `monop-size` | NetBSD `games/monop` (4.1BSD) | BSD 3-clause | 4.0k | NetBSD/src 5b0e0d5 |
| `trek-size` | NetBSD `games/trek` (1BSD) | BSD 3-clause | 7.9k | NetBSD/src 5b0e0d5 |
| `adventure-size` | NetBSD `games/adventure` (Crowther and Woods, 350 points) | BSD 3-clause | 4.7k | NetBSD/src 5b0e0d5 |
| `battlestar-size` | NetBSD `games/battlestar` (4.3BSD) | BSD 3-clause | 8.6k | NetBSD/src 5b0e0d5 |
| `rogue36-size` | RoguelikeRestorationProject/rogue3.6 | BSD 3-clause | 13.4k | f36912c |
| `mojozork-size` | icculus/mojozork (Z-machine interpreter) | zlib | 2.2k | ff7e007 |

The runner fetches each file from `raw.githubusercontent.com` at the
pinned commit and checks its sha256. The repository holds no upstream
text. `bench/programs/games_big/gen_toml.py` writes the manifest from
local clones at the pinned commits. Run it again when you change an edit:

```bash
python3 bench/programs/games_big/gen_toml.py NETBSD_SRC ROGUE36 MOJOZORK
```

## What every compiler gets

Every compiler gets the same source text, the same edits and the same
defines. The glue is our own code (GPL-3.0-or-later):

- `bgstub.h` and `bgstub.c`. A manifest edit replaces `#include
  <sys/cdefs.h>` of each NetBSD file with `#include "bgstub.h"`, and
  deletes the includes of the Unix-only headers. The stub gives small
  stand-ins for `FILE` calls, `open`/`read`/`write`, `scanf`, `stat`,
  `ctime`, `localtime`, `setjmp`, `getopt`, `htonl`, signals and the
  math names. The SDCC library has no `FILE`, `scanf` or `snprintf`, so
  every compiler uses the stand-ins. A stand-in returns a failure or a
  constant. The programs never run.
- `bench/support/posix_shim.[ch]` (shared with the other games) and, for
  battlestar and Rogue, `bench/support/tinycurses.[ch]`.
- `rgstub.h`, `rgmd.c`, `curses.h` for Rogue. They replace `mdport.c`,
  `state.c` (save and restore) and `xcrypt.c`. `rgmd.c` has the `md_`
  calls that the game makes.
- `advdata.c` for adventure. NetBSD builds a 55,716-byte array
  `data_file[]` from the text file `glorkz` with a host tool. The
  benchmark does not store or generate the text. Every compiler gets a
  zero-filled array of the same size in the BSS. The program has the same
  RAM size with every compiler, and the text does not change the
  comparison.

Each glue file adds the same code to every compiler. The sums in the
table include `posix_shim` and `bgstub` (and `tinycurses`, `rgmd`).

The manifest edits fix what a C23 compiler or a small compiler rejects:
the two K&R definitions of Rogue (`prname`, `main`), `void (*)()` calls
(SDCC reads the empty parentheses as `(void)`), `sizeof` forms that
sccz80 cannot read, names that HI-TECH C 3.09 cannot use (a label with
the name of a function, one macro that its cpp does not expand), and
two pairs of file-scope statics with the same name (see [uc80
bugs](#uc80-bugs-found)). Every edit is in `gen_toml.py`.

## How to read the numbers

The per-object build is the one in [bench_harness.md](bench_harness.md#size-only-programs).
The row holds the sum of the code bytes and the data bytes of all
objects. Compare the sum of both: SDCC and llvm-z80 put strings and
`const` tables in the code, uc80 puts them in the data. The status is
The `data` field of uc80 does not count the BSS blocks (zero-start data of each module) from stage B on: that data is now in one COMMON block for each module, so `data` is smaller than in older result files for some programs (adventure, curses-demo, monop, trek, mojozork). Other compilers count zero-start data in their data field. The status is
FITS or TOO-BIG for the whole-program link. A program with a large `bss`
(adventure) does not fit because of the BSS.

The sizes of the whole-program uc80 link and of the sum of objects are
different numbers. A whole-program link drops unused code. Do not
compare a whole-program uc80 number with the per-object sums of the
others.

## Results

Mode `size`, run on 2026-10-24. The first number of a cell is the sum of
the code and data bytes of all objects. The text in brackets is the
status. The numbers include `posix_shim`, `bgstub` and the other glue.

| Compiler | monop | mojozork | trek | adventure | battlestar | Rogue 3.6 |
|---|---|---|---|---|---|---|
| uc80 | 39687 (FITS) | 38021 (FITS) | 60619 (TOO-BIG) | 102848 (TOO-BIG) | 172370 (BUILD-TIMEOUT(link)) | 99973 (BUILD-TIMEOUT(link)) |
| sdcc | 35280 (FITS) | 29568 (FITS) | 52806 (TOO-BIG) | 99686 (TOO-BIG) | 167768 (TOO-BIG) | BUILD-FAIL(objects) |
| sdcc-4.4 | 35903 (FITS) | 31374 (FITS) | 53739 (TOO-BIG) | 100328 (TOO-BIG) | 168950 (TOO-BIG) | BUILD-FAIL(objects) |
| z88dk-zsdcc | 35578 (FITS) | 31481 (FITS) | 52521 (TOO-BIG) | 101415 (TOO-BIG) | 168329 (TOO-BIG) | BUILD-FAIL(objects) |
| z88dk-sccz80 | 37725 (FITS) | 35168 (FITS) | 58536 (TOO-BIG) | BUILD-FAIL(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) |
| hitech | 41833 (BUILD-FAIL(link)) | BUILD-TIMEOUT(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) |
| dcc | BUILD-FAIL(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) | BUILD-FAIL(objects) |
| llvm-z80 | 38679 (FITS) | 27448 (FITS) | 60867 (TOO-BIG) | 103427 (TOO-BIG) | 167285 (TOO-BIG) | 98789 (TOO-BIG) |

Fit for each program, in bytes of the linked image plus BSS (the TPA ends
at 58,112 bytes less 512 bytes of stack):

- monop fits with every compiler that links it: uc80 46,278, sdcc 42,636,
  sdcc-4.4 43,369, z88dk-zsdcc 44,566, z88dk-sccz80 46,222, llvm-z80
  46,044.
- mojozork fits: uc80 43,977, sdcc 34,063, sdcc-4.4 35,894, z88dk-zsdcc
  40,115, z88dk-sccz80 43,271, llvm-z80 31,959.
- trek does not fit with any compiler. The linked uc80 image is 70,485
  bytes, z88dk-sccz80 70,517 and z88dk-zsdcc 63,192 (the other
  compilers: the sum of the objects alone is above the TPA).
- adventure does not fit with any compiler. The 55,716-byte array is half
  of the reason; the code alone (34,000 to 39,500 bytes) leaves less than
  the array needs.
- battlestar does not fit. Its room data is 120 KB. Every compiler that
  linked it reports TOO-BIG. uc80 in whole-program mode did not finish in
  the 1800 s limit of the harness (BUILD-TIMEOUT(link), the known
  whole-program compile time). A manual link of the uc80 objects without
  whole-program mode (`ul80` of the `--no-whole-program` objects with
  `libc.lib` and `runtime.lib`) ends at 179,805 bytes.
- Rogue 3.6 does not fit. llvm-z80 gives 98,789 bytes in objects alone.
  The uc80 whole-program link timed out. The manual link of the uc80
  objects ends at 114,221 bytes (about 2x the TPA), trek at 73,474 bytes.

Compilers that fail, with the cause:

- **sdcc, sdcc-4.4, z88dk-zsdcc, Rogue.** SDCC cannot read the flat
  initializer of an array of structs that has `char *` members
  (`init.c` `helpstr`, `weapons.c` `init_dam`). SDCC 4.6.0 gives error 47
  and error 35, SDCC 4.4.0 gives error 69 ("initialization needs curly
  braces"). The same flat initializer in plain C:
  `static struct iw { char *a; char *b; int c; int d; } tab[3] = { "2d4",
  "1d3", -1, 0, ... };`.
- **z88dk-sccz80.** `adventure` and `Rogue 3.6`: "Unknown symbol: sizeof",
  because `sizeof` of an array in the initializer of a global variable
  is not supported (`save.c`, `init.c`). `battlestar`: sccz80 reads
  `sizeof(location[n].link)`, a `sizeof` of a subscripted member, wrongly
  ("Missing token, expecting ) got ."). `trek` links and is TOO-BIG.
- **hitech.** The compiler is C89 and has limits in its tools. `monop`
  compiles, but the library has no `strtol` and no `strcspn`, so the link
  stops (BUILD-FAIL(link), per-object sizes kept). `trek`: the cpp
  expands a function-like macro (`time` of `posix_shim.h`) when the name
  is a struct member ("argument mismatch"). `adventure`: P1 stops with
  "Expression too complicated" in `crc.c`, "Out of memory" in `main.c` and
  `stdbool.h` is missing. `battlestar` and `Rogue 3.6`: the cpp stops
  with "too many defines" (its macro table is full). `mojozork`: the
  same macro problem (`index`, `read`), then P1 runs past the 600 s
  limit (BUILD-TIMEOUT(objects)).
- **dcc.** The compiler rejects the programs: DCC-E1002 "unsupported
  expression statement, if, for, switch, while" (all six programs; for
  example `for (;;)` in `monop.c` and `battlestar.c`), DCC-E1201 "double is
  not supported" (`trek`), DCC-E1203 "64-bit integer types are not
  supported" (`mojozork`) and an undefined goto label in `adventure`.
- **uc80, battlestar and Rogue.** BUILD-TIMEOUT(link): the whole-program
  compile of 25 or 29 files is longer than the 1800 s limit of the
  harness. The per-object sizes are in the row.
- **llvm-z80.** No build failure after the glue. `adventure` first failed
  at the link with `abort` undefined (the harness start-up code has no
  `abort`); the glue maps `abort()` to `exit(134)`.

The harness fix for this group: `bench/cc_hitech.py` could not link a
program with more than a few modules, because the CP/M command tail holds
127 bytes (zxcc cut a tail of 117 bytes). `build()` now copies the
start-up module and the libraries to the work directory and joins the
modules in groups with `LINQ -R`; `objects()` reads the partial maps.

A note on the glue and `hitech`: its cpp does not expand a macro name
that is split by a 128-byte record boundary of the input file. `monop`
had three such names (`CC_D`). The manifest replaces the uses with
`deck[0]`. Another change of the glue can move a boundary.


## uc80 bugs found

Each repro is a complete file or file pair. The uc80 commands are
`uc80 FILES -o x.mac`, and `um80` where it is said.

1. **Whole-program mode shares or rejects file-scope statics of the same
   name.** (Fixed: the compiler now renames the statics of each file, see
   `multi_unit.py`. The text below says how the old compiler behaved.) `a1.c`: `static const char *names[10];` and a function that
   uses it. `a2.c`: `static const char *names[30];` and `main`. Then
   `uc80 a1.c a2.c` stops with "conflicting types for 'names': array of
   30 element(s) redeclares an array of 10 element(s)". With
   `static int count;` in one file and `static long count;` in the other
   file, uc80 compiles both and uses one `_count` for both files, and
   with `static int count = 5;` in both files the two files share one
   variable. NetBSD monop (`names` in houses.c and morg.c, `buf` in
   execute.c and getinp.c) failed at the link because of this. The
   manifest renames the statics (`#define names hs_names`), so the
   benchmark does not depend on the fix.
2. **A block-scope `extern` gets no `EXTRN`.**
   `void f(char **p) { extern char **environ; environ = p; }` gives
   `ld (_environ),HL` and `um80` stops with "Undefined symbol
   '_environ'". The same declaration at file scope works. Rogue
   `save.c` has this. The manifest moves the declaration.
3. **An undeclared identifier is accepted.**
   `int main(void) { return FOO + 1; }` compiles without a message and
   the `.mac` file has `EXTRN _FOO`. The error shows only as an undefined
   symbol at the link. A call of an undeclared function
   (`return foo(3);`) is accepted in the same way (C23 has no implicit
   function declarations).
4. **A struct type that is not defined is accepted as complete.**
   `struct timespec now; int main(void) { now.tv_sec = 1; return
   now.tv_sec; }` compiles and allocates `_now`.
   `int main(void) { struct nosuch *p = 0; return p->x; }` also compiles.
   clang and SDCC stop with "incomplete type". The size-only builds of
   adventure (`struct timespec`, `struct tm`) and battlestar (`struct
   passwd`) compiled with uc80 while the headers were missing.
5. **A call with arguments through `void (*f)()`** is accepted. In C23
   the empty parentheses mean `(void)`, so the call is a constraint
   violation. SDCC rejects it ("too many parameters"). Rogue
   `daemon.c` calls `(*dev->d_func)(dev->d_arg)` in this way.
6. **Whole-program compile time.** battlestar (25 files, 120 KB of
   data) and Rogue (29 files) do not finish in 1800 s (known, bug C8).


## mojozork run row

The plan asks for a run row with Zork I when CP/M file I/O works under
`z88dk-ticks` for every compiler. It does not:

- `z88dk-ticks` runs `fopen` and `fread` of a z88dk program that reads a
  file in the current directory.
- A uc80 program opens the file, but its `fread` uses BDOS function 20
  (sequential read), and `z88dk-ticks` prints `Unsupported BDOS call 20`.
- The SDCC start-up code of the harness has no file I/O: the library has
  no `FILE`.

`mojozork-size` is thus a size-only program. The story file (86,838 bytes
of `zork1.z3`) is also larger than the address space, so a run needs a
page cache that is not part of this group.
