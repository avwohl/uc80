# Family K step 1: library and link size

Family K is the work that follows [family J](family_j.md). The aim is a small linked image. The image of each benchmark program must not be bigger than the image of SDCC 4.6.0 in its size mode. The column `image` in `bench/results/results.csv` is this image. It holds the library code and the start-up code.

The analysis of the 26 programs that were bigger than SDCC is in [codegen_gap.md](codegen_gap.md). About 40% of the gap was library code that SDCC does not link: the printf core, `qsort` and the 32-bit divide. Step 1 changes the choice of library code. It does not change the speed of any benchmark program for the worse.

Each change has a switch that turns the change off. Each change has a test that runs code and compares the output with the host compiler.

- [printf of one character](#printf-of-one-character): `--no-printf-to-putchar`.
- [No printf tables without printf](#no-printf-tables-without-printf): `--no-drop-printf-tables`.
- [The small start-up code](#the-small-start-up-code): `--fast-bss-clear` (the old default).
- [Formats that are not literals](#formats-that-are-not-literals): `--no-printf-literal-union`.
- [A 32-bit divide for a narrow divisor](#a-32-bit-divide-for-a-narrow-divisor): `--no-div32w`.
- [qsort for a small array](#qsort-for-a-small-array): `--no-qsort-small`.
- [A 16-bit divide by a small constant](#a-16-bit-divide-by-a-small-constant): `--no-div16b`.
- [A shorter 32-bit shift](#a-shorter-32-bit-shift): `--no-shr32-loop`.

## printf of one character

The call `printf("\n");` links the whole printf core. The core is 814 bytes of code and 60 bytes of data. The core pulls the console routine as well. A program that prints only a newline with `printf` pays all of this.

The compiler already changed `printf("text\n")` to `puts("text")` when the program has no other printf call. The rule now has one more case. A call `printf("c");` with a format of exactly one character is a call of `putchar(c)`. The character can be a newline. The call then uses the statement form of `putchar` (`ld E,c` and `call __putchar_v`, see [void_putchar.md](void_putchar.md)).

The rewrite is made only when all of these are true:

- The call is a whole expression statement. A used result keeps the call of `printf`.
- The format is a string literal of one character. The character is not `%` and not NUL.
- The rewrite removes every printf-family call of the program. A program that keeps one `printf` with a conversion keeps all its calls.
- The statement form of `putchar` is on. It is off for a ROM, for sdas text and with `--no-void-putchar`. The program does not define its own `putchar`.

`printf` and `putchar` write to the same console routine, so the bytes are the same. The change saves 884 bytes in the programs `pi`, `pi_ldiv` and `davidly-tpi`.

## No printf tables without printf

Each program had a block of dispatch tables for printf, also with no call of printf. The block had four bytes: three end marks and one `ret`. No code used these bytes.

The compiler now writes the tables only when a printf-family call is left in the program. The compiler keeps the tables in these cases:

- The program has a call.
- The build is not a whole program.
- The command line has an assembly file.
- The program has `#pragma printf` or the option `--printf`.

The change saves four bytes in each program that does not call `printf`.

## The small start-up code

The start-up code of a CP/M program sets the BSS to zero. [startup_fast.md](startup_fast.md) describes two files. The file `crt0_small.mac` (24 bytes) uses one `LDIR`. The file `crt0.mac` (98 bytes) uses a push fill for a BSS of 6000 bytes or more. The push fill is faster by 14 T-states for each byte.

The clear runs before `main`. The timed part of a benchmark program starts later, so the push fill gave no gain in a timed run. The 74 bytes were a loss in nine programs.

The small file is now the default for every size of BSS. The option `--fast-bss-clear` selects the push fill again, for a BSS of 6000 bytes or more. The option `--no-fast-bss-clear` still selects the old file `crt0_slow.mac`.

A program with a BSS of 6000 bytes or more needs about 83000 T-states more at the start (20 ms at 4 MHz) without the option. The program `stanford-bubble` needs 0.07% more T-states in total. The timed T-states do not change.

## Formats that are not literals

A format that is not a string literal (for example the format parameter of a `vfprintf` wrapper) made the compiler link every conversion, also the float printer and the soft-float code. The program `fuzix-startrek` has such a wrapper. It uses only `%d %s %u %6s %02d %-25s`, and it linked 4700 bytes of printf code.

A format that the program builds at run time comes from a string literal of the program in nearly all cases. So the compiler now takes the union of the conversions of all string literals of the program. It adds `int` to this set, always. The same union decides the optional parts that [printf_hook.py](libraries.md) links (`%n`, `*`, `%hhd`, `%a`).

The rule has a risk. A program that reads a format from a file or from the user can ask for a conversion that no literal has. The conversion then prints wrong text. Use `--printf all`, or `--no-printf-literal-union`, for such a program.

When no string literal of the program has a conversion, the format must come from outside, and the compiler links every conversion as before. The rule does not apply to a build with an assembly file or without whole-program mode. These builds keep every conversion.

The change saves 2594 bytes in `fuzix-startrek`.

## A 32-bit divide for a narrow divisor

The routine `__div32` divides in two ways. A divisor below 65536 uses a byte-wise loop. A divisor of 65536 or more uses the 32/32 loop `__div32_big` and `__div32_g` (144 bytes). The program `pi` divides by a 16-bit value only. It linked the second loop and never ran the loop.

The compiler now calls `__div32w` and `__mod32w` when the divisor of an unsigned 32-bit division is below 65536. The new modules `rt_div32w.mac` and `rt_mod32w.mac` have the same code as `__div32` without the second loop. The divisor is below 65536 in these cases:

- The divisor is a constant from 0 to 65535.
- The divisor has an unsigned type of 16 bits or less.
- The divisor is such a value with a cast to a 32-bit type.
- The divisor has the form `x & k`, with a constant `k` from 0 to 65535.

The routines divide by zero as `__div32` does: the quotient is 0xFFFFFFFF and the remainder is the dividend. Each call is faster by about 30 T-states, because the test of the high word is not needed. The signed routines (`__sdiv32`, `__smod32`) are not changed.

A program can need `__div32` too: a divisor that is not proved narrow, a signed division, or a libc routine that divides (`ldiv`, `cbrt`, `scanf`). Then the two routines would be two copies of the same code. So the compiler calls `__div32` and `__mod32` at every site of the program, and the image holds one copy. The change saves bytes in a program that has narrow divisors only. A program of the other kind has the old image.

## qsort for a small array

The routine `qsort` has 855 bytes. It has a quicksort, a run test, an insertion sort and a generic swap. SDCC has a `qsort` of 249 bytes, but the routine of SDCC is quadratic.

The compiler now calls `__qsort_s` when the element count is a constant of 32 or less and the element size is a constant from 1 to 8. The code is in `lc_qsort_s.mac`. The routine has the same arguments as `qsort`. A program cannot call the routine in another way, because an element of more than 8 bytes does not fit into the key buffer. The call of `qsort` stays when the program defines `qsort`, in a ROM build, with sdas text, with an assembly file, and with `--no-qsort-small`.

The algorithm has two parts:

1. The routine tests if each element is bigger than the next element. If this is true for the whole array, the routine reverses the array. The test uses the compare of the first pair that the sort needs, so an ordered array loses no compare.
2. The routine runs a binary insertion sort. The compare of an element with its left neighbour comes first, so an ordered array costs `n-1` compares. The routine copies an element that is out of order to a key buffer. A binary search finds the place after equal elements. One `LDDR` moves the larger elements, and the key goes into the gap. The sort is stable.

The routine saves its 23 bytes of static storage on the stack when it starts, and it copies the bytes back when it ends. So a comparator can call `qsort`. An interrupt function cannot call `qsort` while the routine runs, as for `qsort`.

The module has 411 bytes and the library routine `qsort` is not linked. The T-states of the four benchmark programs with 20 elements are not higher than before (see the results below).

## A 16-bit divide by a small constant

The routines `__div16`, `__dv8w`, `__dv8n` and `__mod16` have 176 bytes. They are fast, and they are 64 bytes bigger than the routine of SDCC. The programs `queens` and `tak` divide only by the constant 10.

The compiler now calls `__div16b` when an unsigned 16-bit division has a constant divisor from 1 to 255. The module `rt_div16b.mac` has 47 bytes. It returns the quotient in HL and the remainder in DE. The module `rt_mod16b.mac` (5 bytes) returns the remainder in HL. A dividend below the divisor returns at once. A dividend with a high byte below the divisor needs 8 steps, any other dividend needs 16 steps.

The signed routines and the other divisors keep `__div16`. The same rule as for `__div32w` applies: when the program needs `__div16` anyway (a signed division, a divisor above 255, `fread`, `fwrite`, `ldiv`), every site calls `__div16`. The change saves 133 bytes in each of `queens`, `tak` and `stanford-intmm`. The T-states of these programs do not change by more than 0.4%.

## A shorter 32-bit shift

The routine `__shr32` has 74 bytes with special paths for the counts 5 to 7. A shift by a constant count is made in line and does not call the routine. So each call has a count that is not a constant.

The compiler now calls `__shr32c` when the count has the form `x * 4`, `x << 2` or a multiple of 4. The module `rt_shr32c.mac` has 45 bytes. It moves words and bytes, and then it shifts at most 7 bits one by one.

A count of 5, 6 or 7 is slower than in `__shr32`. A count that is a multiple of 4 takes the same time or less. So the compiler uses `__shr32c` for these counts only, and every other count keeps `__shr32`. A program that has both kinds of count calls `__shr32` at every site. The program `crc` shifts by multiples of 4, and its T-states do not change. The change saves 29 bytes in `crc`.

## Not in the code

The plan had a second change for the 16-bit divide: a shorter `__div16` for a divisor of 256 or more. The change saves 42 bytes in programs without a 32-bit divide. It needs a second copy of `__mod16`, `__sdiv16` and `__smod16` for the switch. The change is not in the code.

## Tests

- `tests/test_k1_printf.py` checks the printf rewrite, the dispatch tables and the literal union. It compiles and runs programs, and it compares the output with the host compiler.
- `tests/test_k1_divide.py` checks `__div32w`, `__mod32w`, `__div16b` and `__shr32c` with edge values and random values, against the host compiler. The tests build each program again with the switch that turns the change off.
- `tests/test_k1_qsort_small.py` sorts 0 to 32 elements of the sizes 1, 2, 3, 4, 6 and 8 in five patterns, in comparison with the host `qsort`. It checks 9-byte elements and 33 elements (generic `qsort`), and a comparator that calls `qsort`.
- The tests of the push fill (`tests/test_integ12_bss_nmi.py`, `tests/test_famj_startup.py`) use `--fast-bss-clear`.

## Results

The table has the 26 programs that were bigger than SDCC 4.6.0 (size mode). The columns are the image in bytes and the ratio to the image of SDCC. T-states are the count of the timed region (or of the whole run for a program with `timing = total`).

| Program | SDCC image | Before | Ratio | After | Ratio | T-states before | T-states after |
|---|---|---|---|---|---|---|---|
| pi+static | 1,041 | 2,054 | 1.973 | 1,013 | 0.973 | 1,591,791,613 | 1,574,332,413 |
| pi | 1,076 | 2,049 | 1.904 | 1,008 | 0.937 | 1,590,103,213 | 1,572,644,013 |
| sort-ran-5000 | 735 | 1,396 | 1.899 | 1,318 | 1.793 | 36,025,466 | 36,025,466 |
| sort-ran-20 | 736 | 1,322 | 1.796 | 874 | 1.188 | 70,753 | 56,863 |
| sort-equ-5000 | 732 | 1,203 | 1.643 | 1,125 | 1.537 | 2,537,375 | 2,537,375 |
| sort-ord-5000 | 736 | 1,205 | 1.637 | 1,127 | 1.531 | 2,462,395 | 2,462,395 |
| sort-rev-5000 | 746 | 1,216 | 1.630 | 1,138 | 1.525 | 3,137,459 | 3,137,459 |
| sort-equ-20 | 731 | 1,129 | 1.544 | 681 | 0.932 | 12,515 | 11,675 |
| sort-ord-20 | 735 | 1,131 | 1.539 | 683 | 0.929 | 12,235 | 11,390 |
| davidly-tpi | 1,495 | 2,294 | 1.534 | 1,404 | 0.939 | 53,033,515 | 53,033,515 |
| sort-rev-20 | 747 | 1,142 | 1.529 | 694 | 0.929 | 14,999 | 14,934 |
| pi_ldiv+static | 1,464 | 2,216 | 1.514 | 1,326 | 0.906 | 1,813,135,213 | 1,813,135,213 |
| pi_ldiv | 1,603 | 2,213 | 1.381 | 1,323 | 0.825 | 1,808,632,813 | 1,808,632,813 |
| queens | 674 | 803 | 1.191 | 670 | 0.994 | 20,594,010 | 20,594,010 |
| stanford-puzzle | 2,024 | 2,363 | 1.167 | 2,285 | 1.129 | 281,853,340 | 281,853,340 |
| davidly-e | 489 | 566 | 1.157 | 562 | 1.149 | 33,333,355 | 33,333,355 |
| dhrystone21+nostructassign | 1,590 | 1,801 | 1.133 | 1,797 | 1.130 | 207,560,098 | 207,560,098 |
| dhrystone21 | 1,612 | 1,801 | 1.117 | 1,797 | 1.115 | 207,560,098 | 207,560,098 |
| dhrystone21+static | 1,656 | 1,838 | 1.110 | 1,834 | 1.107 | 214,520,098 | 214,520,098 |
| tak | 765 | 834 | 1.090 | 701 | 0.916 | 31,585,808 | 31,585,808 |
| crc | 1,457 | 1,565 | 1.074 | 1,532 | 1.051 | 4,451,869 | 4,451,869 |
| mojozork-size | 28,505 | 30,487 | 1.070 | 30,487 | 1.070 | - | - |
| stanford-bubble | 1,011 | 1,076 | 1.064 | 998 | 0.987 | 412,892,015 | 412,892,015 |
| stanford-intmm | 998 | 1,059 | 1.061 | 852 | 0.854 | 335,451,435 | 334,363,175 |
| fuzix-startrek | 21,610 | 22,519 | 1.042 | 19,925 | 0.922 | 35,469,315 | 35,286,469 |
| sieve | 217 | 218 | 1.005 | 140 | 0.645 | 1,223,848 | 1,223,848 |

14 of the 26 programs are at or below the image of SDCC now. The other 12 are above: the five `sort-*` programs and `davidly-e`, `dhrystone21` (three builds), `stanford-puzzle`, `crc` and `mojozork-size`.

The reasons, from the analysis in [codegen_gap.md](codegen_gap.md):

- The `sort-*-5000` programs need a `qsort` of 460 bytes or less. The `qsort` that the compiler uses is 855 bytes. The routine `__qsort_s` is only for 32 elements or less. `sort-ran-20` has user code that is 110 bytes bigger than the code of SDCC, and `__qsort_s` has 411 bytes.
- `davidly-e` and `dhrystone21` divide by a variable. The routines `__div16`, `__dv8w` and `__dv8n` are about 60 bytes bigger than the routine of SDCC. A shorter routine takes more T-states.
- `stanford-puzzle` has 13 loop nests that the code generator makes about 660 bytes bigger than SDCC does. `crc` and `mojozork-size` have bigger user code.

The means against SDCC 4.6.0, from `bench/report.py` on the merged table:

| Mean | Before | After |
|---|---|---|
| Size (bytes minus the empty program), size mode | 0.9124 | 0.8401 |
| Whole image, size mode | 0.8705 | 0.8021 |
| T-states, speed mode of SDCC | 0.5036 | 0.5011 |
| T-states without `sort-*`, speed mode of SDCC | 0.6047 | 0.6045 |
| Programs faster than SDCC (speed mode) | 75 of 78 | 75 of 78 |

No row has a bigger image than before. The rows that need more T-states than before:

- `bsd-wump` (+4.8%) and `v7-backgammon` (+0.08%): both count the whole run, and the start-up clear with `LDIR` is slower than the push fill. `--fast-bss-clear` gives the old numbers.
- `embench-md5sum` (+2 T-states) and `embench-huffbench` (+1 T-state): the heap address moves, and the count depends on the data.
- A program with a BSS of 6000 bytes or more has a bigger `total_cycles`. The start-up needs about 14 T-states more for each byte of BSS (`sieve` +6%, the `sort-*-5000` programs +2.7%). The timed part is not changed.

With all the new switches off and `--fast-bss-clear`, the rows of `v7-backgammon`, `embench-md5sum` and `embench-huffbench` are the same as before step 1.
