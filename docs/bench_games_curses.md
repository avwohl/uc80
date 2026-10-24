# Curses games (tier 3)

Back to [bench_harness.md](bench_harness.md). The manifest file
`bench/programs.d/games_curses.toml` has six curses games. Each game
runs on the shared `tinycurses` and `posix_shim` of `bench/support/`.
Each run plays one scripted game. The row of the report has the cycles
of the whole run (`timing = "total"`).

## The programs

| Program | Source | What the script does |
|---|---|---|
| `robots` | NetBSD `games/robots` | Plays one game to the death of the player. The keys come from the auto-player of the game. Then `n` to "Another game?". |
| `worm` | NetBSD `games/worm` | Steers the worm to 450 keys, with a path search, then Ctrl-C. |
| `snake` | NetBSD `games/snake/snake` | Takes 12 treasures, then goes to the exit. |
| `mille` | NetBSD `games/mille` | Plays three hands against the computer, then `nn`. |
| `canfield` | NetBSD `games/canfield/canfield` | Plays three games of solitaire, then quits. |
| `invaders` | FUZIX `Applications/cursesgames` | Plays 330 frames. One script character is one frame. |

The NetBSD files come from `NetBSD/src` at commit 5b0e0d5997. The
invaders files come from FUZIX at commit 776bfc942c. The runner checks
the sha256 of each file. The repository has none of these files. The
NetBSD files are 3-clause BSD. The file `invaders.c` is GPL-2.0 or later.

## What the edits change

Every compiler gets the same edited text. The edits are in the manifest.
They do these things:

- `<sys/cdefs.h>` and `<curses.h>` become `bsdcompat.h`. The other BSD
  headers go away. `bsdcompat.h` defines `bool`, `uint32_t`, the
  no-op calls (`signal`, `alarm`, `getuid`, `fflush`) and a few curses
  calls that `tinycurses` does not have (`unctrl`, `vwprintw`,
  `deleteln`). The functions are in `bsdcompat.c`.
- The games do not read or write a score file, a log file or a save
  file. `robots_score.c` and `mille_save.c` replace the file code. A
  game without a score file does what the original does when it cannot
  open the file.
- The command-line options and the signals are out. `worm` has a tick
  shim: `alarm()` arms a timer, and the script character `.` is one
  second that passes with no key.
- The seed of the random generator has 15 bits, and a few expressions
  have a fixed order (`worm` calls `rand()` two times in one
  expression). The order of evaluation is not defined in C, and
  compilers differ.
- A few edits keep the weak compilers working: shorter names for dcc
  and L80 (`invaders`), a split declaration (`worm`), temporary
  variables for HI-TECH C (`robots`, `snake`), and no `setjmp()` in
  `robots` (only the real-time mode uses `setjmp()`).

## How to make a script

The script ends with the quit command of the game. `z88dk-ticks`
reads the end of the input as the byte 0FFh, and some games loop on
that byte. HI-TECH C reads at most 2048 bytes of the input.

Each script came from a search on the host. The search runs the host
build with a prefix of the keys, reads the screen with a small VT100
reader, and picks the next key. `snake` has one more check. The chase
code compares floating-point values, and two values can be equal in
theory. The search rejects each state with a near tie, so that a
compiler with a short mantissa gives the same game.

To make the golden files again, run
`python3 bench/run.py --golden robots worm snake mille canfield invaders`.

## Results

The first full run is in `bench/results/results.csv` (status, image
bytes and whole-run T-states of each cell). Cycles of a game are not
comparable with region counts of other programs.

- All six games build and match the golden file with llvm-z80,
  standalone SDCC 4.6 (`snake` after the `sqrtf()` mapping), and
  z88dk-sccz80 (except `mille`).
- uc80 matches the golden file for all six games (after the two uc80
  fixes below: `robots` 21205 bytes and 884.8M cycles, `mille` 31514
  bytes and 1347.8M cycles).
- SDCC 4.4 and z88dk-zsdcc give wrong output for `canfield`
  (zsdcc also for `invaders`). zsdcc does not finish the size-mode
  build of `snake` and `mille` in 30 minutes.
- z88dk-sccz80 runs `mille` wrong: the game reads `U1` and then
  does not take the space as the end of the card number.
- HI-TECH C runs `worm` and `robots`. It stops with "Expression too
  complicated", "No room", "Operand error" or "Out of memory" on the
  other games.
- dcc runs `worm` and `invaders`. It has no floating point (`snake`,
  `canfield`), cannot link `robots`, and `mille` does not finish.

## uc80 bugs that these games found (both fixed)

- A function and a global variable with names that differ only in
  case (`Score` and `score`): uc80 names the definition `_score_1`, but the
  call goes to `_score`, and the call jumps into the variable.
  `robots` hung.  Fixed: the call uses the label of the definition.
- `(pp - arr) + 21` where `pp` and `arr` point to a structure of 110
  bytes: the result is 2310, not 21. The addition scales the integer
  as if the difference were a pointer. `mille` was wrong.  Fixed: a
  pointer difference has type int.
  Tests: `tests/test_ptrdiff_and_case_call.py`.
