# Benchmark: stdio games (tier 3)

Back to [bench_harness.md](bench_harness.md). This group has early Unix
games and classic BASIC-era games that talk to the terminal through
standard input and standard output only. The manifest is
`bench/programs.d/games_stdio.toml`. The plan is in section 4.3 of
[benchmark_proposal.md](benchmark_proposal.md).

## How a game runs

Each game has a script in `bench/programs/<name>/input.txt` that plays a
real game until the program stops. The golden file in
`bench/golden/<name>.txt` is the output of a host build with the same
shared glue. The runner uses `timing = "total"`, so the cycles are the
count of the whole run.

The scripts come from a bot that reads the output of the host build and
decides the next input. Each script is only a text file. The bot is not
part of the repository.

Some games have no quit command. Such a game stops at the end of the
script. The glue turns the byte 1Ah into end of file, and each script
of that kind ends with this byte. The table lists the end of each game.

## Programs

| Program | Original | License | Script ends with |
|---|---|---|---|
| v7-arithmetic | V7 arithmetic, Unix Version 7, 1979 (FUZIX ANSI port) | Caldera | end of file, 50 answers |
| v7-fish | V7 fish, Go Fish, Unix Version 7, 1979 (FUZIX port) | Caldera | one side wins |
| v7-wump | V7 wump, from PCC Vol 2 No 1, Unix Version 7, 1979 (FUZIX port) | Caldera | answer n to "Another game?" |
| v7-backgammon | V7 backgammon, Unix Version 7, 1979 (FUZIX port) | Caldera | White wins |
| bsd-wump | NetBSD wump, Dave Taylor 1989 | 3-clause BSD | answer n to "play another game?" |
| bsd-arithmetic | NetBSD arithmetic, Eamonn McManus 1989 | 3-clause BSD | end of file |
| bsd-fish | NetBSD fish, Muffy Barkocy 1990 | 3-clause BSD | one side wins |
| bsd-pig, bsd-number, bsd-morse, bsd-caesar, bsd-bcd, bsd-ppt | NetBSD filters, 3BSD to 4.4BSD | 3-clause BSD | end of file |
| ahl-startrek | Super Star Trek, Mayfield and Ahl 1971-78, C port by Chris Nystrom 1996 (z88dk copy) | free to use, no license text | resign, then answer no |
| fuzix-startrek | The same game without floating point (Alan Cox 2018, FUZIX) | free to use, no license text | resign, then answer no |
| empire-trefonas | Empire, Peter Trefonas, CLOAD 1981, C conversion 2016 (z88dk copy) | no license text in the file | end of file in year 17 |
| umchess | micro-Max 1.6 chess, H. G. Muller (z88dk copy) | no license text in the file | end of file after 16 moves |
| hamurabi | Hamurabi, Rick Merrill and David Ahl, C version by Brian Troutwine 2010 (FUZIX copy) | MIT | the game rejects the ruler |

The V7 games come from the FUZIX tree `Applications/V7/games`. The
Caldera license of Unix Version 7 has an advertising clause. The
harness fetches each file at a pinned commit and checks its sha256. The
repository does not store third-party source.

## Edits and glue

Every compiler gets the same text. The manifest `edit` entries do these
things:

- Remove the Unix headers and the K and R style code that uc80 rejects.
- Replace `getopt` loops, `fork` and `exec` code, and `setgid`.
- Mask a seed to 15 bits, so that a 16-bit compiler and the host start
  the random generator in the same state.
- Call `bench_args()` or `px_ti()` first in `main()`. HI-TECH C loses
  script characters when the program prints before it reads.
- Fix three faults of the upstream source. They make the output depend
  on the compiler, so the golden file would not match.

The three faults are in the upstream source:

- v7-arithmetic: `random(range) + random(range)` has no fixed order of
  evaluation. The edit makes two statements.
- fuzix-startrek: `int d1;` in `repair_damage()` has no initial value,
  and `quadname[12]` is too short for "Sagittarius IV".

The shared glue in `bench/programs/stdio-glue/` has two parts.
`sg_getc()` returns end of file for 0FFh and 1Ah. It skips CR.
`sg_fgets()` is `fgets()` on top of `sg_getc()`. For SDCC and llvm-z80
the header maps the `FILE` functions (`fflush`, `fprintf`, `fputs`,
`ferror`, `feof`, `perror`) and the names `stdin`, `stdout` and `stderr`
to the console. The SDCC library has no `FILE`.

## Not in the group

- primes and factor: the prime table is 6542 values of 8 bytes, and the
  sieve table is 256 KB. They do not fit in a Z80 address space.
- hangman and quiz: they need a word list of 196 KB or a question
  directory. No fair copy of that data exists in the harness.
- Larn, TSCP and the Scott Adams data: the project rules do not allow
  them.

## Results (size mode, first run)

The numbers are in `bench/results/results.csv`. For sdcc, sdcc-4.4 and
z88dk-zsdcc only the size mode ran for most programs of this group.

- uc80 runs 13 of 18 programs. The five that fail are v7-backgammon
  (sscanf), bsd-bcd (no `isascii`), ahl-startrek and empire-trefonas
  (atof, and a double backslash in a string) and hamurabi (rand
  returns 0).
- v7-wump, bsd-ppt and fuzix-startrek run on seven compilers each.
- dcc has no `double`, so the four floating-point programs fail to build.
- SDCC and llvm-z80 have no `%f`, `%.*s`, `EXIT_FAILURE`, `isascii`,
  `snprintf` or `sscanf`. A program that needs one of them fails.
- umchess needs a signed `char`. It gives a wrong game with sdcc-4.4 and
  both z88dk compilers.
