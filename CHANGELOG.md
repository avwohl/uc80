# Changelog

All notable changes to uc80 are recorded here. Versions follow
`major.minor.patch`: the minor number is raised when a release changes the
observable behaviour of a program uc80 compiles.

## Unreleased

### Added

- **RST vectors and IM 2 in the ROM target** ([docs/rom_hardware.md](docs/rom_hardware.md)). `#pragma uc80 rst ADDR NAME` binds a plain function (a software call target) to RST 08h to 30h, and `rst ADDR NAME interrupt [critical]` binds an interrupt function. `#pragma uc80 im2 SLOT NAME [critical]` and `#pragma uc80 im2table ADDR` select IM 2: the start-up code sets I and `IM 2`, and writes an aligned table of 128 vectors (an unbound slot points to a default `EI`, `RETI`). The compiler stops with an error for an address out of range, a vector or slot in use twice, and a misaligned table. A program with no such pragma has the same image as before. `bench/romrun` delivers an interrupt with a data bus byte (`add_irq_at(t, vec)`, `--irq-vec`), and no longer forces bit 0 of the IM 2 byte to 0. Tests: `tests/test_rom_vectors.py`, with `ul80` and with `--asm-syntax=sdas`.
- An `im2` or `rst ... interrupt` pragma can name a handler that an assembly input file defines (declare the function in C). `bench/romrun`: a level-triggered source (`set_irq_level`, `YM2610`) can give an IM 2 data bus byte. A test checks that the IM 2 start-up code stays below the table.

### Fixed in the ROM work

- **IM 2 table at an address that starts with A to F.** `#pragma uc80 im2table 0xA000` wrote `LD A,A0H` and `ORG A000H`, and `um80` stopped with "Undefined symbol A0H" (sdas: "ORG needs a constant address"). The start-up text now writes `0A0H` and `0A000H`. The accepted range is now 0100h to FE00h: a table at FF00h put the program code at 10000h, which `um80` wrote over the start of the image without a message. Tests at 4000h, A000h and FE00h run in `bench/romrun`, with `ul80` and with sdas; FF00h is an error.
- **`--asm-syntax=sdas` checks the ROM size of the table.** `sdldz80` built a 32802-byte image for a table at 7F00h, where `ul80` stops with "ROM overflow". The compiler now stops with "ROM overflow" when the program code starts at the end of the ROM or above. The new option `--rom-size HEX` (default 8000) sets the end.
- **A vector pragma names a function in the file that holds the pragma.** Two `static` functions `h` in two files made both IM 2 slots (or `interrupt` vectors) jump to the `h` of the last file. The compiler now stops with an error that names both files when two input files define the function of a vector pragma.
- **The vector pragmas warn in a CP/M program.** `im2`, `im2table`, `rst`, `vector`, `interrupt` and `nmi` had no effect with the CP/M target and no message. They now print the "not set" warning, as for `--no-embed-startup`.
- **`__attribute__((naked))` is a naked function.** It was dropped without a message, so a naked handler got a normal prologue. `__attribute__((interrupt))` stays an error.
- A naked function that returns a structure or union of more than 2 bytes, or takes `...`, is an error. A naked marker that differs between prototype and definition gives a warning. `typedef int F(int) __naked;` is an error. The "no asm statement" warning and the naked errors carry file and line.
- **A label with two colons in an `__asm` block is PUBLIC** (`_g_alt::`), as in sdas and MACRO-80. It was an ordinary label, so the link failed with "Undefined symbol".
- **An asm statement as the body of `if`, `else`, `for`, `while` and `do` without braces** (`for (;;) __asm__("halt");`) gave "unexpected token KW_ASM". The prepass in `sdcc_compat.py` adds the braces. The grammar of uc_core (`statement` in `data/c23.json`) has no asm as a statement, only as a declaration in a block; this is not changed in uc_core.

### Changed

- **Register calling convention merged with the libc rewrite in C, the float interpreter and the ROM work.** The generated library modules (`lc/*.mac`) are unchanged by the merge: the generators compile with `--no-whole-program`, so every library function keeps the stack convention. Against the tree before the merge (run again with the same harness): 82 of 103 OK programs smaller, none bigger (the FITS games `mojozork-size` +2,229 bytes of image and `monop-size` +325 bytes of image are bigger: the call graph change below moves their locals from `??AUTO` to stack frames), image total 638,662 to 619,188 bytes; 56 programs faster, 2 slower; no status change. Mean against SDCC 4.6.0: size 0.987 to 0.958, T-states (speed mode) 0.606 to 0.585, without `sort-*` 0.743 to 0.715. `snd-cmdq` 13.72M to 10.60M T-states, `snd-seq` 14.52M to 12.34M (faster than SDCC speed mode). `docs/benchmarks.md`, `docs/benchmarks_speed.md`, `docs/codegen_gap.md` and `docs/benchmark_proposal.md` have the new numbers.
- Fix: the push and pop of a register parameter across a call (register convention, step 2) is not used when the call is `__save_tmp64`, `__restore_tmp64` or `__push64_acc`. These routines change SP by 8 bytes, so the `pop` read the wrong word (wrong answers with long long values). `bench/ccfuzz.py --ll` finds this; `tests/test_register_cc.py` pins it. `docs/benchmarks.md` marks two old tables as not recomputed.
- The call graph of the shared-storage analysis now has an edge from every function that calls through a pointer (or into a library routine that takes a callback) to every function with a taken address. Recursion through a pointer is now found, and a function that an interrupt function reaches through a pointer keeps its locals and parameters on the stack. This also fixes locals in shared `??AUTO` of a function that recursed through a pointer, wrong before the register convention.
- `bench/ccfuzz.py` makes cycles that run through a pointer variable; `tests/test_register_cc.py` pins two such programs and an interrupt that calls a hook pointer.
- `peep_local` rule 3 (`call` then `ret` becomes `jp`) now applies to calls of register-convention functions that take no stack argument, because the caller has nothing to pop. In the stack convention the rule almost never applied.
- `generate()` has a `register_cc` argument (default True). The unit tests that pin the shape of stack-convention parameters pass `register_cc=False`.
- `bench/ccfuzz.py` also makes tail calls (`return g(...)` with a result of the same type) and returns of an expression of another scalar type. The second shape found an old wrong answer with `-O0` (seed 40035, also wrong with `--no-register-cc` and with the compiler before the register convention); the cause is not found.
- **Register calling convention, step 2** (family F of `docs/codegen_gap.md`; `docs/calling_convention.md`). A function of the register convention that returns `char`, `signed char` or `unsigned char` returns the byte in A when the calls of the function use the byte as a byte at least as often as they need an int. The callee no longer widens the byte, and a call that is a statement, a condition, a comparison, a store to a char or a `return` in a char function does not widen it either. `char f(void)` and `char f(long)` also qualify. A register parameter that is stored once and loaded once after a call, with only straight-line code between, uses `push` and `pop` instead of a slot in `??AUTO` (`regcc.push_pop_slots`). `return g(...);` in a function with no stack local becomes `pop IX` and `jp _g` (or `jp _g` when the function has no frame). Measured against step 1: the image of the 104 programs falls by 408 bytes (41 smaller, none bigger), 21 programs get faster and one slower (`embench-tarfind`, 0.04%), `stanford-perm` -1.5% T-states, `binary-trees` -1.05%, `snd-cmdq` -1.05%. Geometric mean against SDCC 4.6.0: size 0.9484 to 0.9476, T-states (speed mode) 0.6020 to 0.6015.

### Fixed

- **`vsnprintf` is new, and `snprintf` and `vsnprintf` share one routine** ([docs/printf.md](docs/printf.md)). Both write at most `n - 1` characters and a NUL, write nothing when `n` is 0 (`buf` can then be a null pointer), and return the length that the full output has. A program that calls `vsnprintf` gets every printf conversion, as for a format that is not a literal (the format scan of the code generator does not know the function; `main.py` sets `--printf all`). Tests: `tests/test_stdio_library.py`.
- **`_Exit`, `quick_exit` and `at_quick_exit` are new** ([docs/exit_and_files.md](docs/exit_and_files.md)). The header had macros that turned `quick_exit` into `exit` and `at_quick_exit` into `atexit`, which is wrong: `quick_exit` runs the `at_quick_exit` functions (32) and not the `atexit` functions. `_Exit` and `quick_exit` close no file and write no buffer. A program that does not use them links none of the code.
- **A TAB reaches the console.** The byte 09H never reached the console: the emulator `cpmemu` dropped it. The fix is in `cpmemu` (BDOS 2 and 9 expand a TAB, as the CP/M 2.2 BDOS does), not in the library, so it costs no bytes. `bench/run.py` compares text with the TABs expanded, because `z88dk-ticks` passes the byte through and the golden files hold it ([docs/console_output.md](docs/console_output.md)).
- **`fopen` has the modes `a`, `r+`, `w+` and `a+`** ([docs/exit_and_files.md](docs/exit_and_files.md)). Mode `a` made a new file, and the `+` modes were not accepted. Now each write to an `a` stream goes to the end of the data, `r+` changes only the bytes that it writes, and a stream that can read and write changes between the two by itself (`fflush`, `fseek` or `rewind` between a read and a write are not required, as the C standard requires them). `fputc` on a stream that cannot write and `fgetc` on a stream that cannot read return EOF. Also fixed in this code: `fseek` on a stream that writes lost the bytes of the record after the position when it wrote at the start of a record, and a write that crossed the end of a record in the middle of a file replaced the bytes of the next record with Ctrl-Z (`w` mode after `fseek`). `tmpfile` returned NULL always (it passed the arguments of `fopen` in the wrong order, and `tmpnam` wrote the name to a wrong place); it opens the file with `w+` now. Tests: `tests/test_stdio_library.py` compares a stream of each mode with a model of the C rules for random series of operations.
- **8 files can be open at the same time** (4 before). Each file costs 170 bytes of RAM in the BSS segment, so the program on disk is not bigger, and a program that links `fopen` has 680 bytes of RAM less than before. `UC80_MAX_FILES=n uc80 --build-libs` builds the libraries for another number (1 to 64).
- Fix: `freopen` always returned NULL, because it passed the arguments of `fopen` in the wrong order. It now opens the new file in the same `FILE` and returns the stream. `freopen` on `stdin`, `stdout` and `stderr` returns NULL and changes nothing (documented). `fseek` past the end of the data on a stream that can write lost a write in the same record, and left Ctrl-Z fill (and not zeros) before the position; the fill is now zeros. Tests in `tests/test_stdio_library.py`.
- **The code for the update and append modes links only when the program can need it** (`file_hook.py`). When the whole program has no `fopen` mode with `a` or `+`, no `tmpfile`, and no mode that is not a literal, the compiler adds a reference to `__fio_lite`, and the linker takes 8 bytes of stubs and not `lc_ftowrite` and `lc_ftoread` (about 300 bytes). Not with `--no-whole-program`, for the ROM target, or with assembly files. A program that has `fputs(s, stdout)` or `fprintf(stderr, ...)` only is 89 bytes bigger than before with this hook (it was 251 without).
- **Size effect of the vsnprintf, exit, TAB and file changes** (`bench/run.py -c uc80 -m size`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`, against the tree before): no program changes status. The image total goes from 794,239 to 798,701 bytes (+4,462, +0.56%). 62 programs are 60 bytes bigger (the TAB code of `__conout`), 3 are 73 bigger, 6 are 149 bigger (the 60 bytes and 89 bytes for `fputc` and `fprintf` on a stream, with files that are never opened), 7 are 53 smaller (they link the record writer of `fclose` or `fflush`, which was inside the module of `fputc`, and is a module of its own now), and 31 are the same. No program links `fopen`, so the BSS is the same for all. Programs that print a lot are 0.1% to 0.3% slower (`invaders` 994,106,550 to 996,610,003 T-states).
- **`printf` of floats is exact and keeps the field width.** `printf("%5.2f", x)` and `%10.3f` lost the width, the `+`, space and `0` flags did nothing for floats, a tie rounded away from zero, and the last digit could be one off. All six float conversions (`%f %F %e %E %g %G`) are now in `lc_prt_float.mac`. The code makes the exact decimal digits of the float with integer arithmetic (it doubles or halves a digit string) and rounds to nearest, a tie to even, so the text is the text of the host `printf` for the same float value, for every precision, for subnormal numbers, `inf`, `nan` and `-0.0`. `lc_printf_e`, `lc_printf_f` and `lc_printf_g` are gone. The float runtime is no longer linked for `printf`: `printf("%f")` goes from 3,979 to 2,807 bytes, and a program with `%f %e %g` from 6,128 to 2,841. The code uses 214 bytes of RAM. The c-testsuite expectations of 00174 and 00195 (`tests/c-testsuite-z80/*.expected`) held the old inexact digits (`-44.439998` and `56.779998`); the exact values are `-44.439999` and `56.779999`. Tests: `tests/test_printf_float_exact.py`; the audit compared more than 20,000 generated combinations of format and value with the host `printf`.
- **Size effect of the printf, exit and file changes** (`bench/run.py -c uc80 -m size`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`, against the tree before): no program changes status; the image total goes from 802,812 to 794,242 bytes. Nine programs that print floats are smaller: `v7-fish`, `v7-wump` and `fuzix-startrek` by 2,866 bytes each, `bsd-arithmetic` by 782, `pi` and `davidly-tpi` by 4. 78 programs are 1 byte bigger (the crt0 jump through `__exit_hook`). 12 programs that call `exit()` are 8 or 9 bytes bigger, 9 programs that use `fputc` or `fputs` on a stream are 35 bytes bigger (the record logic of `fputc` and the exit code), and `trek-size` is 340 bytes bigger (`fseek` and `ftell` now do the work of all three whence values).
- **printf audit** ([docs/printf.md](docs/printf.md)). `%hhd %hhu %hhx %hho` narrow the argument. `%td` works. `%lf` works without any long conversion in the program (the table had no `l` entry, so `%lf` was printed as text). `%n` stores the count. `%a` and `%A` print hexadecimal floats. `%llo` and `%llX` work. `%p` prints `0x` and the hex digits (`0x0` for a null pointer, it was `0`). `ptrdiff_t` was not defined when `<stddef.h>` came after `<stdio.h>`. A field width or a precision above 255 is 255 (deliberate, documented). `%jd`, `%ls` and `%lc` are not supported. The code generator writes the dispatch table and does not know the new entries, so the rarely used parts are modules of their own and the new `src/uc80/printf_hook.py` adds a table entry when a format string of the program needs one (the method of the float part of scanf): a program that does not use them links none of them, and `printf("%d")` is 1 byte bigger (crt0). The compiler warning "no handler in this libc" is removed for the conversions that the hook handles, and the warning that `%*d` is not implemented is removed, because `*` works.
- **`main` returning runs the `atexit` handlers and closes the open files** ([docs/exit_and_files.md](docs/exit_and_files.md)). Before, crt0 jumped to the warm boot, so the output of an `atexit` handler was missing and a file that the program wrote but did not close was empty. crt0 now jumps through the word `__exit_hook`, which `atexit()` and `fopen()` set; a program that uses neither is 1 byte bigger. `atexit()` takes 32 functions (4 before). `exit()` closes the open files. CP/M 2.2 has no exit status: the status of `exit()` and `main` is not used.
- **`ftell` and `fseek` are exact** ([docs/exit_and_files.md](docs/exit_and_files.md)). `ftell` was wrong after any read (it read a field of the FCB that sequential reads do not set) and never added the position in the buffer. `fseek` took only `SEEK_SET`, only 16 bits of the offset, and it lost the buffer of a stream that writes. Files are now read and written with the random access BDOS calls (33 and 34), so `SEEK_SET`, `SEEK_CUR` and `SEEK_END` work up to 8 MB, for reading and for writing, and `fflush` no longer leaves a Ctrl-Z in the middle of the file. `fgetpos`, `fsetpos` and `rewind` use them. `fopen` mode `a` still makes a new file. Tests: `tests/test_file_position.py`, `tests/test_atexit_exit.py`.
- scanf `%f` is correctly rounded for a mantissa of any length. Before, the scanner dropped all mantissa characters past 119 without a correction, so a long number could round wrong or get the wrong magnitude. `tests/test_strtod_exact.py` now tests `atof` and `sscanf` too.
- `strtod`, `strtof`, `strtold`, `atof` and scanf `%f` round every decimal string correctly, also a string with more than 19 digits that is close to the middle of two floats (before, such a string could give the neighbour: `2.52540519340982427820563316345214843751e-6` was one unit too low, and a string just above 2^-150 gave 0 and not the smallest subnormal number). The converter uses 64-bit arithmetic and, for a string within 2^-48 of a tie, compares the digits with the tie in big integers (`lc_strtod.c`). Cost for a program that links `strtod`: +708 bytes of code and +170 bytes of RAM (`atof` program 6059 to 6767 bytes, `sscanf("%f")` 8422 to 9130). Test: `tests/test_strtod_exact.py`.
- `__fdiv` gives the IEEE result for an infinity or NaN operand and keeps the sign of a zero numerator (`inf/2` was a huge finite number, `inf/inf` was 1.0, `0/-1` was `+0`). Float comparison with a NaN stays non-IEEE; recorded in `docs/FLOAT_STATUS.md`.
- `strtol`, `strtoul`, `strtoll`, `strtoull` and scanf `%i` accept the C23 prefix `0b`; scanf has the `%b` conversion.
- The tests use the `src` directory of their own checkout (`PYTHONPATH` set in `tests/conftest.py`).
- `?:` after a side effect in the condition: `int a=v; return (a++ > 0) ? a : 99;` read `a` as `v` (uc80's own `?:` walk skipped the sequence point after the condition). uc80 now uses uc_core's walk and keeps only its typed constant fold (`_fold_ternary`). Needs the uc_core copy-propagation fix.

- **scanf family rewritten in C** (`src/uc80/lib/csrc/`, generated into `lc/` by `gen_c_modules.py`). `sscanf`, `fscanf` and `scanf` now do `%f %e %g %a` (and `%lf`, `%Lf`), `%n`, widths (`%3c%d`), `%*` suppression, `%[...]` scan sets, `%%`, `%i` with `0x` and `0` prefixes, `%o %u %x` with signs, `%hhd %hd %ld %lld %zd %jd %td`, `%p`, and return EOF when the input ends before the first item. New `vscanf`, `vfscanf` and `vsscanf`. `sscanf` and `vsscanf` no longer link BDOS code.
- `strtod`, `strtof`, `strtold` and `atof` rewritten: the result is correctly rounded for every decimal string (the old code was up to 20 units in the last place off, and `1e-38` or `99999999999` were far wrong), and `inf`, `infinity`, `nan`, `nan(chars)` and hex floats (`0x1p3`) work. The end pointer is the input pointer when no number is read. `errno` is `ERANGE` on overflow and underflow.
- `strtol` and `strtoul` rewritten: bases 0 and 2 to 36, `0x` prefix only before a hex digit, overflow gives `LONG_MAX`, `LONG_MIN` or `ULONG_MAX` with `errno == ERANGE`, a bad base gives `EINVAL`.
- `errno` is its own module (`lc_errno`), so `strtol` stays in the ROM library.
- **Float `+ - / ` and int-to-float round to nearest, ties to even.** `__fadd` and `__fsub` dropped the bit they shifted out (so `x - y` was inexact when the exponents differed by one), `__fdiv` made only 23 bits when the dividend was smaller than the divisor (a result could be 2 units in the last place off), and `__itof`, `__ltof`, `__uitof` truncated (`(float)50066279` was one unit low). `__fadd` also handles infinity and NaN operands, and `x/0` is `+-inf` with the sign of `x`, `0/0` is NaN.
- **libm rewritten** (programs for a float interpreter in `src/uc80/lib/vmsrc/`, C in `src/uc80/lib/csrc/`). Every `math.h` function is now within 2 units in the last place of the exact result (the old library was off by 5 to 10^9 for many arguments), handles infinity, NaN, signed zero and the C99 special cases, and reduces the argument of `sin`, `cos` and `tan` with Payne-Hanek, so `sin(1e30)` is right. `sqrt`, `cbrt`, `fmod`, `remainder`, `floor`, `ceil`, `trunc`, `round`, `hypot` are correctly rounded or exact; `pow` is exact when the result is a float (`pow(3,2)` is 9) and within 1 ulp otherwise. Fixed: `floor`, `ceil`, `trunc`, `round` clamped to +-2^31; `cbrt(27)` was 3.00007; `sinh(0)` and `tanh(0)` were `-inf`; `atan2` was wrong in the second and third quadrant; `acos(-1)` was 0; `exp(100)` was 2e-34; `modf` of a negative number and of a large number; `frexp(0)`; `ldexp(0, n)`; `remainder`; `log` near 1.
- **The float part of `scanf` links only when the program needs it.** The compiler adds a reference when a scanf call has a float conversion in its format, a format that is not a literal, or when a scanf function is used without a call (`scan_hook.py`). `sscanf("%d")` is 1.3 KB (1.5 KB with the old library), and 7 KB with the float part.
- `printf("%f")`, `%e` and `%g` print the same digits as before with the rounding float runtime. The `%f` printer adds its pre-round term with rounding toward zero (`printf("%f", 12.34)` had become `12.340001`), and the `%e` printer no longer adds an ulp to make up for a truncating runtime. c-testsuite 00174 expects `sin(2)` as `0.909297`, the correct value.
- `__fadd` is 511 bytes (was 439 bytes) and `__fdiv` 385 bytes (was 432), so float programs that only add, subtract and divide change by +72 to -47 bytes. `sscanf` and `vsscanf` are in the ROM library.
- `sqrt` is correctly rounded and 259 bytes: an integer digit-by-digit root in assembly. The old one was off by one ulp in 25% of the cases.
- `fma(1e20, 1e20, -inf)` is `-inf` (it was NaN: the product overflowed before the sum). An infinite third argument is returned when the first two are finite.
- **The math functions that do float arithmetic are programs for a float interpreter** (`src/uc80/lib/vmsrc/*.vc`, made into `lc/*.mac` by `gen_vm_modules.py`, `docs/libm_vm.md`). `sin cos tan asin acos atan atan2 sinh cosh tanh asinh acosh atanh exp exp2 expm1 log log2 log10 log1p pow hypot fma` and their kernels were C code, 25 to 40 bytes for each float operation; a float operation is now one byte. The results are bit for bit the same as the results of the C versions, except `pow`, which has a table of 16 values instead of 32 (still within 1 ulp). A program that calls `sin` is 3307 bytes (5550 before, 2748 with the library before the C rewrite), `pow` 4211 (8075), `atan2` 3639 (5604), `exp` 2869 (3291). `whetstone` is 10135 bytes (14252 before, 9649 with the old library), `binary-trees` 5454 (9318, 4422), `trek-size` 66928 (76111, 65540), `bsd-caesar` 9623 (10116, 8724). The interpreter makes `whetstone` 21% slower than the C versions (788 M T-states, 654 M before; the library before the C rewrite needed 1226 M).
- The scanf engine (`lc_scan_core`) and the integer reader of `strtol` (`lc_strtoint`) are assembly; the conversions `%c`, `%s` and `%[` are a module of their own that the compiler links when a format has them (`--scanf text`). `sscanf("%d")` is 1312 bytes, `sscanf("%s")` 1605. `_m_pack`, `_m_unpack` and the Payne-Hanek reduction are assembly (`_m_reduce` was 1614 bytes in C, it is 111 bytes and 402 bytes of `lc_m_phred`). `scalbn` and `ldexp` are assembly and do not link `_m_pack` and `_m_unpack`.
- `rint`, `nearbyint`, `lrint`, `llrint`, `lround`, `llround`, `remquo`, `nexttoward` and their `f` and `l` forms are new (`math.h` declared several of them but the library had none).

### Added

- **Naked functions: SDCC `__naked` and `[[uc80::naked]]`** ([docs/rom_hardware.md](docs/rom_hardware.md)). A naked function has no prologue, no epilogue, no frame and no `RET`: the body is the asm of the programmer. The body may hold only `asm(...)` statements, `__asm` blocks and empty statements; any other statement is an error with the line number. Parameters use the stack convention (first parameter at `(SP+2)` at entry). A naked function can be a vector target of `#pragma uc80 interrupt` or `nmi`; the compiler then writes nothing around the body. Never inlined, never given a constant parameter, never in the register convention; the call graph follows each `_name` in the asm text. Tests: `tests/test_naked.py`, `tests/test_sdcc_compat.py`, `tests/test_rom_interop.py` (sdas flow).


- **Watchdog hook for the ROM target.** A program that defines `__watchdog_kick` (a C function with `#pragma uc80 public _watchdog_kick`, or an assembly file) gets a call of the hook at reset, before `main` and before each `HALT` of the idle loop. A program without the hook has no call and no extra byte. Tested in `bench/romrun` with a port that logs the kicks (`tests/test_rom_target.py`, `TestWatchdogHook`). See `docs/rom.md`.
- **Interop with SDCC and nullsound** (Phase 5 of `docs/rom_target.md`; see `docs/rom_interop.md`):
  - SDCC `__asm ... __endasm;` blocks, at statement level and at file level. The prepass in `sdcc_compat.py` turns the sdas text of a block into an `asm("...")` statement with MACRO-80 text (`#imm`, `N(ix)`, `.db`, `.dw`, `.ds`, `.ascii`, `.globl`, `1$` labels, `0x` numbers). A directive or an operand it cannot translate is an error with the line number.
  - `--asm-syntax=sdas` (with `--sdas-style=sdcc|ngdevkit` and `--sdas-areas`): the compiler writes sdas text for `sdasz80` and `sdldz80`. New module `src/uc80/sdas.py` (with `asmexpr.py` and `sdas_inline.py`). The default output does not change. The translator keeps the `JR` range rule of um80 (a far `JR` becomes `JP`), splits DSEG into an image area and a RAM area, and maps the vector area to an absolute area (marker `;@RELOC` in `crt0_rom.mac`).
  - An `__asm` block as the unbraced body of `if`, `else`, `for`, `while` or `do` gets braces. `link_flags(heap=True)` defines `__END__` for a program that links malloc. In the ngdevkit style, uc80 warns when an `--entry` function reaches `EXX` code and when the module has initialised globals. `bench/rom/nullsound.py --irq-period T` injects timer interrupts into the nullsound proof (command 06h starts timer B).
  - `uc80 --build-sdas-libs DIR` translates and assembles the runtime and libc into `uc80_rt.lib` and `uc80_lc.lib` for `sdldz80 -l`.
  - `bench/rom/sndrom.py --sdas` and `bench/rom/isrproof.py --sdas`: the snd-* kernels and the interrupt proofs link with `sdldz80` to the same images, sizes and T-states as the `ul80` build.
  - `bench/rom/nullsound.py`: builds nullsound (ngdevkit commit pinned, sha256 checked, fetched into `bench/_cache`, not vendored) and links it with C commands from uc80 (`ns_cmds.c`, `ns_user.s`). The ROM runs in romrun and the YM2610 writes equal the expected sequence.
  - Tests: `tests/test_sdas.py` (every library module and every benchmark program translate and assemble, the instruction-size table equals `sdasz80`), `tests/test_sdcc_compat.py`, `tests/test_rom_interop.py`. They skip without the SDCC tools.
- **Register calling convention, step 1** (family F of `docs/codegen_gap.md`; rules in `docs/calling_convention.md`). In whole-program mode, a function that only direct calls reach takes its first two scalar parameters in registers: parameter 0 in A (byte) or HL (word), parameter 1 in E (byte) or DE (word). Other parameters are pushed as before, and the caller does not pop the register parameters. A function qualifies when its name occurs only as the callee of direct calls, no assembly (inline, file scope, appended `.mac`, library sources) mentions the name, and it is not `main`, variadic, an entry point (`--entry`, `#pragma uc80 keep`, `public`, `vector`), an interrupt function or reachable from one, or a function that returns a wide struct. Every other function keeps the stack convention. The callee stores each parameter that the body names into `??AUTO` or, in a stack frame, pushes it in the prologue (recursion saves the parameter per activation). The pass `regcc.forward_slots` replaces the loads that a register still serves and deletes stores that nothing reads, so a small leaf like `out(port, v)` is `add A,E / ld (a),A / ret` with no IX frame. `--no-register-cc` turns the convention off. Measured against `--no-register-cc`: snd-cmdq 13.72M to 10.72M T-states, snd-ymreg 21.21M to 17.87M, snd-seq 14.52M to 12.34M, snd-fnum 13.69M to 11.06M, stanford-perm -19%, stanford-towers -19%, dhrystone -13.5%, tak -6.6%; 82 of 104 programs change, none gets bigger (total image -19,114 bytes). Geometric mean against SDCC 4.6.0: size 0.9757 to 0.9484, T-states (speed mode) 0.6228 to 0.6020.
- `bench/ccfuzz.py`: a calling-convention fuzzer. Random programs with many small functions (0 to 4 parameters of char, short, int, long, unsigned, pointer and struct pointer type; recursion and mutual recursion; calls in arguments, conditions, loops and `?:`; function pointer tables; variadic helpers; static and extern definitions) run on the host and on uc80, and the checksums must agree. `tests/test_register_cc.py`.
- `bench/run.py` honours `UC80_BENCH_RESULTS` (write the rows to another file), and the uc80 adapter honours `UC80_BENCH_FLAGS` (extra uc80 options), for A/B runs.
- docs/neogeo_driver.md and examples/neogeo_driver.c: a Neo Geo sound driver example (NMI command, timer IRQ, YM busy wait, bank select) with the port table and a test that runs the example in romrun.
- **Interrupt functions and hardware access** (Phase 2 of `docs/rom_target.md`; see `docs/rom_hardware.md`):
  - `#pragma uc80 interrupt NAME [critical]` and `#pragma uc80 nmi NAME` make a C function an interrupt function: `EI` first (for `interrupt`), `PUSH AF, BC, DE, HL, IY`, the matching pops, then `RETI` or `RETN`. The compiler sets the INT (38h) or NMI (66h) vector, keeps the function, and refuses parameters, a return value and a direct call. The prologue saves the alternate registers only when the call graph reaches code that uses them. `asm_dce` knows that `RETI` and `RETN` end a block.
  - SDCC spellings `void f(void) __interrupt`, `__interrupt(n)`, `__critical __interrupt[(n)]`, `__sfr __at(n) NAME;`, `__sfr __banked __at(n) NAME;`, `T name __at(addr);` and `__critical { }` (prepass `sdcc_compat.py`). Every other SDCC spelling is still an error.
  - `<z80.h>`: `z80_out`, `z80_in`, `z80_out16`, `z80_in16` (BC is the whole port), `z80_di`, `z80_ei`, `z80_irq_save`, `z80_irq_restore`, `z80_critical_enter`, `z80_critical_leave` and `z80_load16` (a 16-bit read that an interrupt cannot tear). The compiler writes each in line, and no optimizer pass moves, merges or removes one.
  - `T name __at(addr)` and `[[uc80::at(addr)]]` make an object at a fixed address with no storage. A volatile integer object at a constant address (`*(volatile unsigned char *) 0xF900`) becomes the same kind of name, so it uses `LD A,(nn)` and `LD (nn),A`. This also changes CP/M code that has such an expression.
  - `--isr-safe` keeps the locals of functions that an interrupt function reaches on the stack. Without it, the compiler warns when a function that an interrupt function and other code both call uses the shared `??AUTO` area. The compiler also warns, with the call chain, when an interrupt function reaches a routine with work areas at fixed RAM addresses.
  - `bench/rom/isrproof.py`: an NMI command queue, a YM2610 timer interrupt that drives the sequencer, and a bank select with `z80_in16`, each compared with the expected port log. Tests: `tests/test_rom_isr.py`, `tests/test_rom_hw.py`, `tests/test_rom_isr_proof.py`.
- ROM target: a warning for each `#pragma uc80 vector`, because a C function has no RETI. Tests for const objects in two C modules and for an assembly module that reads a const array.
- **Const data in the ROM** (`--target=rom` only): const objects, string literals and const compound literals go to CSEG, so the start-up code does not copy them to RAM. RAM of the four snd-* kernels drops by 33 to 232 bytes; ROM size and T-states stay the same. The CP/M output is byte-identical. See `docs/rom.md`.
- **Roots and vectors for the ROM target**: `--entry NAME` (repeatable) and `#pragma uc80 keep NAME` keep a function the code never calls; a ROM program with no `main` starts at the first `--entry`; `#pragma uc80 vector 38h NAME` and `66h NAME` make the INT and NMI vectors jump to a C function; `#pragma uc80 public NAME` gives a static name or an unused BSS object a PUBLIC label. BSS objects of the ROM target keep their PUBLIC label. uc_core records `#pragma uc80 ...` lines in `Preprocessor.target_pragmas`.
- `strtoll`, `strtoull`, `atoll`, `strtoimax` and `strtoumax`.

- `tests/conftest.py` builds `libc_rom.lib` and `crt0_rom.rel` when a checkout lacks them. The ROM tests fail, not skip, if a library is missing.
- **ROM target (Phase 1 of `docs/rom_target.md`): `uc80 --target=rom`** (alias
  `-ffreestanding`). The compiler embeds `lib/crt0_rom.mac` (reset vector,
  RST stubs, INT and NMI vectors, SP from `__RAM_END` or `--stack-top`, copy
  of the initial data from ROM, BSS clear, `HALT` loop after `main`) and
  refuses `exit`, `abort`, console and file I/O, the heap, `main(argc, argv)`
  with a clear error. A `__int_handler` or `__nmi_handler` label in an
  assembly input file becomes the INT or NMI vector. Link with the ROM options
  of `ul80`. New `libc_rom.lib` (93 libc modules with no BDOS use) is built by
  `uc80 --build-libs`. See `docs/rom.md`.

- **The four snd-* kernels as ROM images** (`bench/rom/sndrom.py`): real `OUT`
  and `IN`, debug port output, run in `bench/romrun`; the decoded output is the
  CP/M golden output. All four fit the 2 KB RAM of the Neo Geo sound CPU.
  `tests/test_rom_target.py` runs one kernel end to end.

- **Strict mode for the interrupt and fastcall markers.** uc80 asks the uc_core
  frontend to refuse `__interrupt`, `interrupt`, `__interrupt(1)`,
  `[[gnu::interrupt]]`, `__attribute__((interrupt))`, `__critical __interrupt`
  and bare `__fastcall`, with `uc80: error: file:line: uc80 does not support
  '...' yet` and exit code 1. They were erased without a message before.

- **`bench/romrun`, a Z80 ROM test runner** (Phase 1 of
  `docs/rom_target.md`). The runner starts a raw ROM or Intel HEX image at
  address 0 and logs every port IN and OUT with the full 16-bit address and
  the T-state. The runner injects IM 1 interrupts and NMIs (EI delay, RETN),
  models banked windows, ROM write faults, a YM2610 timer model, scripted
  port reads and cycle spans between symbols. The core counts T-states
  exactly; the count matches z88dk-ticks on the benchmark programs. See
  `docs/romrun.md`.

- **romrun repair pass**: `ram_fill` and `sp` (start state of RAM and SP,
  so a broken crt0 fails), `peek8`/`peek16`/`peek` and `--dump`,
  `--dump-ram`, `track=True` (lowest SP and RAM written), `nmi_gate=True`
  (Neo Geo NMI enable port 08h and disable port 18h) and a golden file of
  the per-opcode timing sweep against z88dk-ticks
  (`bench/romrun/golden/ticks_sweep.txt`). The random-stream test no longer
  produces an indexed instruction without the displacement byte.

### Changed

- The runtime library (`lib/rt/`) and the math, `strtod` and `scanf` parts of libc (`lib/lc/`) hold one module for each entry point, or for each group of entry points that share a body. `rt_arith16`, `rt_arith32`, `rt_arith64`, `rt_float`, `rt_complex`, `rt_mul`, `rt_div8`, `rt_setjmp`, `lc_atof`, `lc_scanf`, `lc_floor`, `lc_fabs`, `lc_fmax`, `lc_fmod`, `lc_fpclassify`, `lc_frexp`, `lc_logb`, `lc_math_helper`, `lc_atomic` and `lc_thread` are split. `runtime.lib` has 104 modules (was 11) and `libc.lib` has 250 (was 177). The image of no bench program grows; 58 of 109 get smaller (817,781 to 814,805 bytes in total). See `docs/libraries.md`.
- `runtime.py` no longer reads a comment line as initialized data, so a `DS` block at the end of a module stays in the BSS of the embedded runtime.

- **Zero-start runtime and libc variables are in the BSS.** `__tmp32`,
  `__divrem`, `__div32_dvd`, `__rem32`, the 64-bit, float and complex
  temporaries, `__sret_buf` and the static variables of libc are in a named
  COMMON block for each module instead of DSEG. A ROM image does not copy them,
  and a CP/M `.com` file no longer holds their zero bytes.

- **Unsigned 8-bit divide and modulo by a constant 1 to 255 (speed family D).** When the left operand is known to be 0..255 (an unsigned char, a masked value, another such quotient), `x / K` and `x % K` no longer use the signed 16-bit chain `__sdiv16 / __div16 / __dv8w / __dv8n`. A power of two is a shift or a mask. For 192 of the other 247 divisors the quotient is an exact reciprocal multiply `(x * M) >> S` (inline shift-and-add), and the remainder is `x - q * K`. The rest call `__dv8n` once. Two adjacent statements (or declarations) `q = x / K; r = x % K;` make one division. A signed operand stays on the signed path. The helpers that lose their last caller are not linked. `snd-fnum` runs 18.83M to 13.98M T-states and shrinks by 196 bytes; `snd-cmdq` runs 14.32M to 14.05M and shrinks by 196 bytes. See `docs/u8_divmod.md`.

- **Byte paths and peephole rules (speed family E).** Seven rules in the code generator and three in the new `src/uc80/peep_local.py`; `docs/byte_paths.md` has the list. The arguments for a `char` parameter of a function in the program go in L without extension (`ld L,K`, or a value computed in A). A `switch` on an int value known to be 0..255 compares one byte. A second operand `*p` or `p->m` is `cp (HL)`, not `push AF / ld E,(HL) / pop AF / cp E`. A byte comparison puts the variable on the `cp` side. A cast to a char evaluates the operand once. `(int8_t)x >= 0` and `< 0` are `bit 7,A`. A local `int` that only receives byte values is a byte value for compares and switch keys. The peephole pass deletes a dead `ld H,0`, turns `ld L,S / ld H,L / ld L,0` into `ld H,S / ld L,0`, and turns `call X` + `ret` into `jp X`. Bench (uc80 size mode, T-states and image bytes): `snd-cmdq` 14.32M to 13.72M and 3075 to 2798 B (with D), `snd-ymreg` 22.22M to 21.21M and 2470 to 2443 B, `snd-seq` 15.38M to 14.52M and 3939 to 3867 B, `snd-fnum` 18.83M to 13.69M and 2830 to 2598 B (with D), `davidly-ttt` 14.38M to 13.12M and 4210 to 3816 B. The mean size against SDCC 4.6.0 falls from 0.992 to 0.982 (together with the float fixes). The mean T-state ratio (speed mode, 78 programs) falls from 0.629 to 0.623. The tests that count `call X` in a function that ends in a call now also count `jp X`.

- **`bsearch`, `vprintf` and `vsprintf` are their own modules, and `rand` links no runtime multiply.** A `qsort`-only program is 192 bytes smaller, an `fprintf` program about 220 bytes smaller, and a `rand` program about 550 bytes smaller.
- **Smaller programs: libc is split into one module per function.** The
  float printer was part of `lc_printf_core`, so every program that called
  `printf` linked the soft-float code (about 2.8 KB). It is now
  `lc_prt_float.mac` and only `%f` links it: a `printf("%d")` program drops
  from 4343 to 1498 bytes. The modules that held several public functions
  (`lc_string`, `lc_mem`, `lc_stdlib`, `lc_atoi`, `lc_ctype`, `lc_wchar`,
  `lc_malloc`, `lc_file`, `lc_misc`, `lc_time`, `lc_puts`) are split so a
  program links only the functions it calls: `strlen` no longer pulls in
  `malloc`, and `exit` no longer pulls in `abort`. `uc80 --build-libs`
  removes the stale `.rel` files of removed modules.

- **`printf("text\n")` becomes `puts("text")` again.** The rewrite in
  whole-program mode had stopped matching after the AST change and never
  fired. It now handles adjacent literals and escapes, `printf("%s\n", s)`,
  only an unused result, and only when no other `printf`-family call is left.
  A hello program drops from 3,968 B to 256 B.
  `printf("text")` without a newline becomes `cputs("text")`.
- **Measured result of the library work.** In the size-mode benchmark run
  the mean size ratio against SDCC 4.6.0 fell from 1.53 to 0.99 (96
  programs OK on both; uc80 is smaller on 52 and bigger on 44). The T-state
  ratio is the same (0.63). The four `snd-*` kernels are now smaller than
  SDCC. See `docs/benchmarks_speed.md` and `docs/codegen_gap.md`.
- **Faster `qsort`.** `qsort` was a selection sort (n² comparisons, 37
  billion T-states for 5,000 equal ints) and sorted in descending order.
  It is now a non-recursive quicksort with a median-of-three pivot, a
  partition that stops on equal keys, insertion sort for parts of 8 or
  fewer elements, and a check for sorted or reversed input. 5,000 equal
  ints now take 2.6 million T-states. A comparator can call `qsort()`.
- **Faster runtime helpers.** 16-bit multiply is 3 to 5 times faster,
  16-bit divide and modulo about 3 times, 32-bit multiply and divide
  about 10 times, and long shifts up to 8 times. `strlen`, `strcpy`,
  `strcmp`, `memcpy` and `memset` are faster too. The new modules
  `rt_mul`, `rt_div8`, `rt_neg32` and `rt_tmp32` hold shared pieces, so
  a program links only the pieces it uses.

- **Code generator speed work (eight families).** Over the 61 programs that
  were OK on uc80 in the first benchmark run, the geometric mean of the
  size is now 0.83 of the first run. The mean of the T-states is 0.67 of
  the first run. The numbers below are the effect of each step when we
  made the step, measured with `bench/run.py`. Programs that the text
  does not name stayed the same or improved.
  - *Constants in arithmetic.* A multiply, divide, modulo or shift by a
    constant no longer calls a runtime helper in the common cases. 16-bit
    multiply uses a shift-and-add chain, unsigned divide and modulo by a
    power of 2 use shifts and masks, and 32-bit shifts use byte and word
    moves. `stanford-puzzle` 1,162 M to 861 M T-states (-26%),
    `stanford-intmm` -19%, `crc` -13%, `fannkuch` -7%. Total of 41
    programs -6.0%.
  - *Operands and byte values.* A constant or simple variable on the right
    of `+ - & | ^` goes straight into DE, and a value that is only used as
    a `char` is computed in A. `sieve` -19%, `micro+a8` -22%,
    `micro+loop` -22%, `snd-cmdq` -28%, `davidly-sieve` -19%.
  - *In-place update.* `x++`, `x--`, `op=` and stores to `char`, array
    element, member and `*p` work in place, and an unused value is not
    kept in HL. `sieve` 4.69 M to 4.41 M, `snd-cmdq` 20.1 M to 17.1 M,
    `fannkuch` 58.2 M to 51.5 M, `stanford-puzzle` 758 M to 720 M. The
    image of `micro` is 326 bytes smaller.
  - *Bit-fields.* A bit-field that lies in one byte is read and written
    as that byte, with shifts, masks and `bit n,(hl)`. `micro+bits`
    5.49 M to 1.39 M T-states (-75%).
  - *Frames and switch.* A function that never uses IX has no frame
    (68 T-states and 9 bytes less per call). A `switch` on a `char`, or on
    an `int` whose case values share a high byte, compares bytes, and
    dense cases use an 8-bit table. Every one of 41 images is 12 to 399
    bytes smaller. `embench-statemate` 5.64 M to 5.24 M, `zx0` 10.2 M to
    9.3 M, `snd-cmdq` 17.1 M to 16.1 M.
  - *32-bit values.* `+ - & | ^`, compares, casts, shifts and `switch` on
    a `long` read their operands straight into BC and A, with no call
    through `__tmp32`. `embench-md5sum` 7.07 M to 3.64 M (-49%), `crc`
    -41%, `embench-huffbench` -33%, `embench-crc32` -34%,
    `micro+a32` -25%.
  - *Addressing.* `x`, `s.m` and `a[K]` of a named object use `ld (nn)`
    and `(IX+d)` directly, `p[K]` and `p->m` use `inc HL`, a 16-bit truth
    test uses `ld A,(HL)` and `or (HL)`, and a word that is already in HL
    is not loaded again. `davidly-ttt` 16.4 M to 14.2 M, `snd-cmdq`
    15.7 M to 14.2 M, `queens` 369 M to 336 M, `statemate` 4.79 M to
    4.32 M, 40 of 41 programs faster.
  - *Conditions.* The code compares and tests a 16-bit variable in the IX
    frame without a register load (`ld A,(IX+d)` and `sub (IX+e)`). `tak` 48,229,623
    to 47,816,155, `embench-statemate` 4,324,681 to 4,315,073,
    `davidly-ttt` 14,229,879 to 14,197,127.

### Fixed

- **`typeof(x)` where `x` was declared with `typeof(type-name)`** was an
  int (`typeof(unsigned char) a; typeof(a) b;` gave `b` two bytes).  The
  operator tokens are now normalised before the typeof pass reads them.
- **Ordering of two negative floats was reversed** (`-44.0 < -1.0` was 0).
  `__fcmp` now compares the magnitudes the other way round when both signs
  are negative.
- **`**qf` of a `float **` read 2 bytes.**  The dereference size of `*p`
  was the size of `p`'s pointee type, a pointer; it is now the size of
  what `*p` points to.
- **An inlined function with a typedef'd parameter or return type**
  (`typedef signed char I8; I8 f(I8 c) { return c * 8; }`) was not
  narrowed; the result was 168 and not -88.
- **A float or double times a power of two was not multiplied.** `float nv =
  1.5f; float r = nv * 4;` gave 1.5 and `double d = 2.5; d = d * 8;` gave
  2.5. The AST optimizer turned `x * 2^n` into `x << n`, a shift of the
  integer value of the float. Also `x / 8u` (shift), `(x * 3) * 5` and
  `(x + 1) + 2` (an integer constant, wrapped), `x - x`, `x == x`, `x * 0`
  and `x + 0` were integer rewrites of a float. uc_core now skips them for any
  operand that may be floating (see the uc_core changelog), and
  `Uc80ASTOptimizer` leaves the power-of-two multiply to the code generator,
  which knows the type. Its integer code is the same or better: a multiply
  by 128 uses the 7-bit shift sequence, a multiply by a power of two that is
  stored in a char is computed in A, and `x * 2` no longer reads `x` twice
  (`(a % 2) * 2` computed `a % 2` twice). Over the benchmark programs 16 of
  them get shorter and none gets longer. New tests run float, double and long
  double, negative values, 2 to 2^14, multiply, divide and compound
  assignment (`tests/test_float_strength.py`).
- **`typeof(char_object)` was two bytes, and `typeof(volatile unsigned
  char)` did not parse.** Code generation took every `typeof` for `int`.
  uc80 now resolves the type of the operand (an object, a member, an
  expression, at file scope or in a function, before the call-graph analyzer
  sizes the declarations). The qualified type-name form `typeof(type-name)`
  parses (uc_core change).
- **A block-scope `extern` object had no `EXTRN`.** `void f(void) { extern
  int g; g = 1; }` with `g` defined in another unit emitted no `extrn _g`,
  so the link failed with an undefined symbol (or bound to a wrong one). The
  name is now an `EXTRN` when this unit does not define the object.

- **Three miscompiles in the inliner and the copy propagation.** (1) A
  small function that changed its by-value parameter (`v++`, `*p++`,
  `v += 3`) changed the variable of the caller when the call was inlined;
  the function is now not inlined. (2) An inlined call did not narrow the
  argument when the parameter type, or the return type, was a typedef name
  such as `u8` or `s8`. (3) Copy propagation ignored `++` and calls in the
  initialiser of a declaration, so `int a = v; int b = a++; return a;`
  returned `v`. `tests/test_repair_miscompiles.py` runs all three.

- **Every access to a volatile object now happens as written (C23
  6.7.3).** `volatile unsigned char ymaddr; ymaddr = 0x28; ymaddr = 0x28;
  ymaddr = 1;` compiled to one store, and `g = 1; x = g;` to one store and
  no load. Four passes caused it, and each is fixed:
  - the AST optimizer (uc_core) deleted adjacent stores to a volatile
    global, static, extern, local or typedef object, and a store whose
    right side read a volatile object. It folded `v == v`, `v | v`,
    `v % 1` and `v * 2`, and it unrolled a loop over a volatile variable;
  - the inliner dropped a volatile argument that the body did not use, and
    repeated one that the body used twice;
  - the peephole pass merged `ld (_g),A / ld A,(_g)`, dropped the second of
    two equal reads, and removed the store of `g = g`. The code generator
    now brackets the code that can access a volatile object, and `main.py`
    fences the bracketed code with labels that the peephole matcher does
    not match across (`src/uc80/volatile_fence.py`);
  - `++` and `+=` on a 32-bit volatile object read and wrote the high word
    only after a carry. They now access all four bytes;
  - `x++`, `--x` and the like on a volatile `float` or `double` read the
    object twice, and on a volatile `char` reached through a pointer, an
    array or a member (value used) they read it twice. Each is now one read
    and one write;
  - `v * 2` on a volatile object is no longer rewritten to `v << 1`, which
    gave wrong code for a `float`.
  Code that uses no volatile object compiles to the same assembly as
  before. Details are in [docs/volatile.md](docs/volatile.md).

- **Integer promotion and `char` handling.** Each operand now gets its own
  promotion (`unsigned char`, `short`, `_Bool`, enum and narrow bit-fields
  become `int`). Thus with `unsigned char c = 10`, `(c - 20) < 0` is 1.
  `long` with `unsigned int` divides as signed. `++` and `--` on a
  one-byte local read two bytes, so the value of `c++` had the next
  local in H and an index `i++` read the wrong element. The value of
  `++c`, `(c = 300)` and `(s.f = 9)` is the narrowed stored value.
  `unsigned int` with `long` is `long`. A switch on a `long` compared
  only the low word. `++` and `--` on a `long`, `float` or `long long`
  element, member or `*p` changed only the low 16 bits.
- **The inliner and constant folder dropped types.** The inliner now
  converts arguments and the return value to the declared types and does
  not inline a call whose arguments have side effects (`sq(k++)` ran
  `k++` twice). The folder of `ast_opt.py` kept the type of a literal
  (`x * 8UL`, `-2L`, `-256L >= 65280u`).
- **`!x` of a `long long` tested only the low 32 bits,** and `-0.0` was
  true in `if`, `!`, `?:` and `&&`. `x && y` with a `float` operand gave 0
  or -1. The runtime BSS had its own `COMMON` block that overlaid the
  program's block, so `__acc64` shared bytes with the first global.
- **`div()` returned `denom / numer`.** `_div` passed its operands to
  `__sdiv16` and `__smod16` in swapped registers (they compute `DE op HL`),
  so `div(4425, 3)` gave quotient 0, remainder 3. `ldiv()` was right.
- **`calloc()` wrapped `nmemb * size`.** `calloc(300, 300)` returned a
  24,464 byte block. It now returns NULL when the product does not fit in
  16 bits, and counts with the smaller factor.
- **`%#.0o` and `%#.0lo` of 0 printed nothing;** the C standard says `0`.
- **A literal width or precision over 255 wrapped** (`%300d` made a 44
  column field). It now stays at 255, as `%*d` already did.
- **`__fmul` mishandled infinity, NaN and the sign of zero.** `x * inf`
  returned garbage, `inf * 0` and any NaN operand were not NaN, and a zero or
  an underflowed product was always `+0`. An infinity times a finite value
  is now a signed infinity, `inf * 0` and a NaN operand give a NaN, and a
  zero result keeps the sign. A subnormal operand is read as zero (a
  subnormal result was already flushed to zero).

- **A runtime function was linked twice.** When libc needs a runtime
  module, the linker takes the whole module, and a function that the
  compiler had embedded from the same module was then in the image twice
  (ul80 warned `%Mult. Def. Global`; `ldiv()` could return `quot = -1`
  next to 32-bit arithmetic because there were two copies of `__tmp32`).
  The compiler now predicts the runtime modules the link will pull in
  and leaves their functions and data to the library. A program with
  floating point and `printf` is about 1.7 KB smaller.
- **`free()` frees.** `malloc`, `calloc`, `realloc` and `free` use a
  first-fit free list kept in address order, and `free()` merges the
  block with free neighbours. `realloc` keeps a block that is already
  big enough and frees the old block after a copy. A program that
  allocates and frees in a loop (binary-trees) no longer runs out of
  memory.
- **`printf` honours precision, `-`, width on long forms, `%s` width and
  `*`.** `%.4d`, `%5.3d` and `%.3x` now print the minimum number of
  digits, `%-5d`, `%-5u` and `%-5x` left-justify, `%8ld`, `%-8lu` and
  `%08lx` (and the `ll` forms) pad to the width, `%5s` and `%-5s` pad,
  and `%*d`, `%.*d` and `%*s` read the width or precision from the
  arguments. The flags `+`, space and `#` work for every integer
  conversion. All integer handlers now end in one routine,
  `_printf_numout`. `%llx`, `%lld` and `%llu` also write through
  `sprintf` and `fprintf` now (they wrote to the console before).
  `tests/test_printf_flags.py` compares about 1,200 conversions with the
  host `printf`.
- **Float multiply is correctly rounded.** `__fmul` lost one of two
  carries when it added the partial products, so a product could be
  wrong by up to 513 units in the last place, and it truncated instead
  of rounding. It now rounds to nearest even. 300,000 random products
  match Python's `float32` rounding. A product that would be subnormal
  is still flushed to zero. The c-testsuite output of test 00174 changed
  to the correctly rounded digits.
- **Every program linked about 9 KB of libc it never called.** uc80
  declares each prototype in each included header as `EXTRN`. Since um80
  0.3.50, um80 keeps an unreferenced `EXTRN`, as MACRO-80 does, and ul80
  then links the library module that defines the name. `puts("hello")`
  linked to 9,088 bytes instead of 256, and c-testsuite as a whole to
  752,000 bytes instead of 450,176. The compiler now drops every `EXTRN`
  that nothing in the module uses, as its last step, so a name used only
  by an appended `.mac` file keeps its `EXTRN`. When hand-written assembly
  uses `include`, a macro or `&`, nothing is dropped, because the names it
  uses cannot be seen.
- **`malloc` memory overwrote globals.** The heap started at a word in
  `lc_malloc`'s own data segment, below the program's later data and BSS,
  so `malloc(1000)` followed by `memset` cleared global and static
  variables. The heap now starts at `__END__`, the first free byte after
  the whole program. The smaller links above made the old bug hit with
  much smaller blocks. Rebuild the libraries (`uc80 --build-libs`).
- **Fixes that the benchmark programs found.** The benchmark programs
  (Embench, CoreMark, whetstone and the games) exposed these bugs:
  - `sizeof (void *)` or the `sizeof` of another pointer or array type
    name in a call argument stopped the compiler with an internal error
    (`embench-tarfind`).
  - `a + n * n` with an `unsigned long` index took the 32-bit path, which
    does not scale by `sizeof(*a)`. Pointer plus or minus a `long` index
    is now pointer arithmetic with scaling (`coremark`).
  - A pointer difference had the pointer type, so `(p - q) + n` scaled `n`
    by the size of the pointee. A pointer difference is now an `int`
    (`mille`).
  - A function whose name differs only in case from a global got a
    suffixed label at its definition, but its call sites used the plain
    name (`robots`).
  - A label may stand in front of an `asm` statement (`whetstone`). This
    needs `uc_core` 0.4.2.
  - `"a\\b"` lost the backslash, because the code generator read the
    decoded backslash as the start of a second escape.
  - The assembly dead-code pass kept a `PUBLIC` in the data segment
    together with the data block before it. A dead string literal in front
    of `__tmp32` then took the `PUBLIC` along, so the library and the
    program had two copies of `__tmp32`. `rand()` returned 0 next to
    `exit()`, and floating-point code called `__fadd` with the right
    operand in the wrong copy.
  - `atof` pushed the `strtod` arguments in the wrong order, lost
    fractions, had the words of the factor 10.0 swapped and unbalanced the
    stack for a string without digits. State of an earlier call leaked in.
  - `scanf` called the white-space loop as a subroutine, `ungetc` did
    nothing, the integer readers destroyed the argument offset, `%ld` read
    zero, and a conversion without digits counted as a match.
  - `pow` with a whole-number exponent up to 64 now uses repeated
    squaring, so `(int)pow(3, 2)` is 9 and `pow(-2, 3)` works.

## 0.7.0

An ABI change, and a correctness release around it. A function returning a
struct or union wider than HL now takes a hidden destination pointer from
its caller, so **every translation unit of a program must be recompiled
together**, and the libraries rebuilt -- in a git checkout, that is:

```bash
uc80 --build-libs
```

A wheel ships `libc.lib`, `runtime.lib` and `crt0.rel` already built, so
`pip install --upgrade uc80` is the whole of it there.

Two of the fixes are for regressions in 0.6.0, so 0.6.0 is superseded
rather than merely improved on.

Two passes are recorded here. The first was an independent verification of
the seven bugs the mbasic project reported, carried out by verifiers working
from the reports rather than from the fixes; it found more than it
confirmed. The second started from the 256-byte limit on returning an
aggregate and went looking for what else was wrong nearby.

### Changed

- **A function returning an aggregate writes it where the caller says.**
  The result used to go into `__sret_buf`, one static buffer the whole
  program shared, whose address came back in HL. That capped a returned
  aggregate at 256 bytes -- uc80 refused anything larger, which is the entry
  below about writing past the buffer -- and it meant only one result could
  be in flight: `arr[g(1).a] = f(90);` computes the destination address after `f`
  returns, so `g` overwrote `f`'s bytes on the way and the assignment stored
  `g`'s result, silently.

  The caller now passes the address to write to as a hidden first argument,
  pushed last so it lands at `IX+4`, with the declared parameters starting
  at `IX+6`. The callee copies its result there and still leaves that
  address in HL, so nothing that reads a result changed. The destination is
  a slot in the caller's own frame, one per call site, which is what removes
  the size limit and separates one call's result from the next.

  This is an ABI change: **recompile every translation unit of a program
  together**, and rebuild the libraries. An aggregate of two bytes or fewer
  still comes back in HL and is unaffected. `__sret_buf` remains only for a
  call whose return type could not be resolved when the slots were laid out,
  so an ordinary program no longer reserves its 256 bytes.

- **Separately compiled units no longer share one another's storage.**
  Uninitialized statics, and the `??AUTO` region holding a shared-storage
  function's locals, went into the blank COMMON block. That is what a common
  block is for -- L80 puts every module's blank COMMON at the same address --
  so with two uc80 modules in a link, one unit's globals sat on top of the
  other's and one unit's automatic storage sat on top of the other's live
  locals. `--no-whole-program` now puts that storage in DSEG, which the
  linker gives each module its own space for; the bytes cost their size in
  the image, where they are already zero. Whole-program output is unchanged.

- **`%F` prints `INF` and `NAN`** (C17 7.21.6.1p8), through its own alias
  entry point rather than sharing `%f`'s.

- **A frame that does not match its body stops the compile.** The prologue
  reserves automatic storage before a line of the body is generated, and
  the body then hands out the slots. When the two disagree the extra locals
  sit below SP, or on the return slots, or -- with nothing reserved at all
  -- at `(IX+0)`, on top of the frame pointer the prologue had just pushed,
  which hangs the program. Every symptom of that is silent, so
  `gen_function` now compares what the body took against what it reserved
  and refuses to emit the function. Both known causes are fixed below: a
  tag defined in the body, and a typedef that leaked out of the function
  that declared it.

- **Dependency floors move.** uc80 requires `uc_core >= 0.4.1` (was 0.4.0)
  and `uplox >= 3.3.0` (was 3.2.0); `pip install --upgrade uc80` pulls
  both. Building a wheel from source needs `um80 >= 0.3.46` (was 0.3.43,
  added in 0.6.0) and `setuptools >= 64.0`, because the wheel assembles the
  CP/M libraries with um80. 0.3.46 is also the ul80 that reports an error
  recorded during a link that still succeeded, which is what makes a
  duplicate definition such as the `__sret_buf` below visible at all.

### Fixed

- **A struct or union tag defined inside a function body was sized as
  zero.** Frame sizing runs before the body is generated and the tag was
  not registered yet, so `struct L { int p, q, r, s; }; struct L x;` gave
  `x` no storage and the next local was laid on top of it. A typedef or tag
  declared in one function also stayed registered for the next, which let
  frame sizing and the shared-storage plan -- they run at different times --
  size the same declaration two different ways.

- **A call in a declaration inside a statement expression was invisible to
  the call graph,** so `({ S q = f(4); q; })` as the only use of `f` had it
  removed as dead and the link failed on the call still emitted for it.

- **A name declared in a block stayed visible after the block closed.**
  C23 6.2.1p4 ends its scope at the brace; only the extern set was being
  restored. With a different type inside -- a block-local function pointer,
  say -- every later call through the outer name was emitted with the inner
  one's calling sequence.

- **A function reached only through a pointer shared storage with its
  caller.** Which functions can be on the stack at once was answered from
  the direct call graph alone, so an address-taken function looked
  unrelated to whoever called it indirectly and was packed at overlapping
  offsets in ??AUTO: two calls through the same pointer returned the second
  result for both.

- **A local further than 128 bytes from the frame pointer was addressed
  with the low byte of its offset.** `(IX+d)` holds a signed byte and um80
  assembles a larger operand without complaint, so the access landed in the
  caller's frame -- and once a frame passed 256 bytes, two locals aliased
  each other. Only functions with a real stack frame were affected
  (recursive, variadic, or built with `--no-shared-storage`); shared
  automatic storage is addressed absolutely. Scalar loads and stores now
  walk IX to the slot when the displacement cannot reach, and anywhere that
  still cannot, the compiler says so instead of emitting the wrong access.

- **An expression whose value is a struct was stored, not copied.** A call
  returning a struct, a member read, a cast, a conditional: each designates
  bytes, and evaluating one yields their address. Wherever an initializer or
  an assignment expected a scalar it stored that address into the first
  field and left the rest zero, so `struct S a[] = { mk(7), mk(9) };` filled
  the array with two pointers. The same shape reached a struct member
  (`struct O o = { mk(3), 5 };`), a union's first member, a designated
  element, and the right-hand side of an assignment. A conditional was
  worse: taking an address had no case for one and no fallback, so nothing
  at all was emitted and `(c ? q : r).b` read through whatever HL held. A
  chained assignment (`a = b = c;`) and a statement expression
  (`s = ({ ...; obj; });`) emitted nothing for the same reason, which lost
  the inner assignment and the statements' side effects outright. A shape
  that still has no case is now a compile error rather than silence.

- **A local declared anywhere the frame sizer did not look got no storage.**
  Sizing walked a hand-written list of statement kinds, so `label: { int
  a[8]; }` and `default: { int b[8]; }` reserved nothing and the array sat
  below SP, where the next `push` wrote over it. It also sized each
  declarator from its declared type alone, so `char s[] = "hello world"`
  counted as zero bytes. Both were silent, and a function eligible for
  shared storage escaped the second half, so it showed only in recursive and
  variadic functions.

- **An unnamed parameter was skipped rather than stepped over,** so in
  `int f(int, int x)` every parameter after the unnamed one read the
  argument before it -- `x` returned the first argument. Abstract
  declarators (`char *`) did the same.

- **A local array ignored index designators:** `int a[] = {[9]=91, [0]=2}`
  filled positionally, putting 91 in `a[0]`. The same declaration at file
  scope was always right, so the two storage durations disagreed about one
  initializer.

- **`--no-shared-storage` produced unlinkable output.** The flag gated the
  allocation pass but not the decision to use it, so functions addressed
  `??AUTO+n` and the symbol was never defined.

- **`INFINITY` was not an infinity and `NAN` was not a NaN.** math.h defined
  them as `((double)0x7FFFFFFF)` and `((double)0)`, so `isinf(INFINITY)` was
  false and `NAN` compared equal to zero. `HUGE_VAL` -- what the math
  functions return on overflow -- was that same `0x7FFFFFFF`, a finite
  value of about 2.1e9 that an ordinary result can exceed. All three are
  now folded from the GCC builtins, which keeps them constant expressions
  usable in a static initializer, and `HUGE_VALF` and `HUGE_VALL` are
  defined alongside them. Reaching them needed two more fixes: a literal
  too large for binary32 stopped the compiler with "internal error: float
  too large to pack with f format", and C23 6.4.4p2 makes a constant's
  representability a constraint, so `1e39f` warns and becomes an infinity
  now rather than crashing; and `printf("%f")` decided "infinity" from the
  exponent alone, after a rounding pass that does not preserve the mantissa
  telling an infinity from a NaN -- so every NaN printed as `inf`.

- **A printf under a `default:` label or inside an initializer list was
  invisible to auto-detection,** so the unit reported that it called no
  printf, its dispatch table collapsed to the sentinel, and the conversion
  came out verbatim at run time. No warning either: the compile-time check
  reads the same scan. The scan now walks the AST rather than a list of
  statement kinds it knows.

- **`%*d` and `%.*f` are diagnosed.** The runtime parser implements
  neither: it echoes the specification and never consumes the int argument,
  so every later conversion in the call reads the wrong one. The
  compile-time scan skipped the star without recording it.

- **A relative `UC80_LIB_DIR` came back as written,** so a captured
  `LIB=$(uc80 --print-lib-dir)` named a different directory -- or nothing --
  when used from anywhere else, and the link went to the packaged tree with
  nothing said.

- **Inline assembly kept a colon-less label named like a mnemonic.**
  `_format_asm_block` indented any column-0 word that is a mnemonic, but
  `SET`, `AND`, `OUT`, `PAGE` and `NAME` are legal symbol names and a
  MACRO-80 label written without a colon must be in column 0, so `SET equ 5`
  became a `SET` instruction. What follows the word decides now.

- **`printf("%f")` was wrong for every `|value| >= 65536`,** and usually did
  not even emit digits: `1000000.0` printed as `3906.00///` and `-123456.0`
  emitted a raw backslash. The integer part was built in `H:L:E` and printed
  with the 16-bit `_prt_dec`, which drops `E`; above 2^24 a second path
  shifted a 16-bit `HL` and printed `0`. `sprintf` in the same libc had
  always been right, so one program could print one value two ways and get
  two answers. Values beyond 2^31 -- where an `int32` no longer holds the
  integer part -- now print exactly, by decimal doubling of the mantissa, so
  `3.4e38` gives all 39 digits. Not a regression; it predates the %e/%g work.

- **The whole printf family shares one formatter.** `sprintf`, `snprintf`,
  `fprintf`, `vprintf`, `vfprintf` and `vsprintf` each carried their own
  conversion chain, so 0.6.0's `%e` and `%g` reached `printf` and nothing
  else -- they printed an empty field and, because a table miss does not
  advance the vararg offset, desynced every later conversion in the call.
  `fprintf` and `snprintf` had no `%f` at all. A program calling both
  `printf` and `sprintf` shrinks from 7040 to 5248 bytes, the duplicate
  formatters having been linked in alongside the real one.

- **A comma expression yielding `long long` carried a stale value.**
  Regression in 0.6.0: once the comma operator had a result type,
  `(f(), 42LL)` reported itself as 64-bit, and three places took that to mean
  the value was in `__acc64`. A 64-bit literal has a 16-bit fast path, so it
  was not, and the expression evaluated to whatever the accumulator last
  held. Affected initialisers, every condition (`if`/`while`/`do`/`for`/
  `&&`/`||`/`?:`) and variadic arguments.

- **A struct-valued comma produced garbage** and, in argument position,
  dropped the left operand's side effect entirely. Not a regression -- an
  unfixed gap of the same kind.

- **Under `--no-whole-program`, link order decided which printf conversions
  worked.** Regression in 0.6.0: the per-specifier float filter made each
  unit emit a different `PUBLIC __printf_format_table`, and L80 keeps the
  first definition. A unit calling no `printf` emitted an *empty* table, so
  linking it first disabled every conversion in the program. The table is now
  emitted by the unit defining `main()` -- exactly one per link -- and a unit
  that prints conversions that unit does not is told to pass an explicit
  `--printf`. Auto-detection cannot be trusted across a separate build
  either, since a unit sees only its own format strings: a `main()`
  compiled under `--no-whole-program` without an explicit `--printf` now
  registers every handler and says so, so such a build links a larger
  binary than it did until it passes the flag. Whole-program mode is
  byte-identical.

- **An array bound spelled with an enum constant was ignored** when
  compositing `extern int a[N];` with `int a[];`, giving a zero-byte object
  that the next global was written through, and skipping the C 6.7.6.2p6
  conflict check. Enum constants are now registered before declarations are
  composited.

- **Adjacent string literals initialised nothing.** `char s[] = "ab" "cd";`
  emitted correctly sized, entirely zero storage; inside a struct initialiser
  the pieces were spread across the following members, so a `char *` member
  got a string's bytes where its pointer belonged.

- **An appended `.mac` is fenced from the optimizers,** as inline `asm()`
  already was. The peephole fused `LD A,(HL)` + `LD C,A` with `A` still live,
  deleted a label caught between the fused pair (turning a working helper
  into an `Undefined symbol` from um80), and discarded `ORG` and `ASEG`.

- **`printf("%f", -0.0)` printed `0.000000`** -- the zero test masked the sign
  bit off before anything checked it -- and **`printf("%.0f", 2.0)` printed
  `2.`**, a trailing point with no digits after it. `%e` and `%g` already got
  both right.

- `%ld` and `%lu` count the bytes they write. `_prt_dec32` wrote straight to
  `__conout`, so `printf("[%ld]\n", 1234567L)` returned 3 for the ten bytes
  it produced; it goes through `_printf_putc` now, the same choke point as
  the rest of printf land, so the count is right and CR LF translation is
  unchanged. `_prt_dec64` and `_prt_hex` still write to `__conout`, so
  `%lld`, `%llu` and `%llx` remain uncounted -- recorded under Known
  limitations below.

- **The documented link line defined `__sret_buf` twice.** The struct-return
  buffer lived in the same runtime module as the 32-bit arithmetic helpers,
  so it had two homes: the runtime the compiler embeds, and `rt_arith32` in
  `runtime.lib`. A program that returned a struct embedded the buffer, and
  any later pull of `rt_arith32` -- printing a `long`, say, which reaches the
  32-bit helpers through libc rather than through the compiler -- brought a
  second definition with it. L80 keeps the first and links on, so this was
  silent until ul80 0.3.46 started reporting a recorded error; the second
  buffer was still allocated, costing 256 bytes of every affected binary.
  `__sret_buf` now has its own module, `rt_sret.mac`, which the linker pulls
  only when the symbol is genuinely unresolved.

- **Returning a struct larger than 256 bytes wrote past `__sret_buf`.** The
  return copy was an `ldir` of the struct's full size into a `DS 256` buffer
  with nothing bounding it, so a 402-byte struct put 146 bytes over whatever
  storage followed. It stayed quiet because the value still reads back
  correctly -- the clobbered bytes are not the ones the caller looks at --
  and how far it reached scaled with the struct. The first fix was to refuse
  the return and name both sizes; the ABI change above then removed the
  limit itself, so ordinary code is no longer refused. The refusal survives
  only on the `__sret_buf` fallback path -- a call whose return type could
  not be resolved when the slots were laid out -- where the 256 bytes still
  bound the copy.

- **Compiling the same source twice produced different assembly.** The
  embedded runtime functions came out in set-iteration order, which Python
  varies per process. Same functions, same binary, different file.

### Known limitations

Recorded so they are not rediscovered; `todo.txt` has the full list.

- Field width and the `-`, `+` and ` ` flags do nothing on a float
  conversion. The format parser discards those three flags for every
  conversion, so they have never been implemented.
- `%.*f` / `%*f`, and `%lf` / `%le` / `%lg` under auto-detection or
  `--printf float`, are not registered: the conversion is echoed verbatim and
  every later one in that call reads the wrong argument. The `*` forms are
  now diagnosed at compile time where the format string is a literal.
- `%lld`, `%llu` and `%llx` write their digits through `__conout` rather
  than through the sink the shared formatter hands each entry point, so
  `sprintf`, `snprintf` and `vsprintf` print those digits on the console and
  leave the buffer holding the surrounding text alone -- `sprintf(b,
  "<%lld>", 42LL)` puts `<>` in `b` and `42` on the screen -- and `printf`
  does not count them in its return value. Against 0.5.0 this is a
  regression for the buffer-writing calls, whose separate formatter wrote
  the digits into the buffer; `%ld` is the way round it until `_prt_dec64`
  and `_prt_hex` go through `_printf_putc`.
- Float comparison does not implement NaN's unordered result, so `x == x` is
  true for a NaN. No arithmetic in the runtime produces one; only the `NAN`
  macro and a bit pattern read back as a float can.

## 0.6.0

A behaviour-changing release. Rebuild the libraries after upgrading:

```bash
uc80 --build-libs
```

### Changed

- **Console output now ends a line with CR LF.** A bare LF is "cursor down"
  only on a real CP/M terminal, so output written with a bare LF stair-steps
  down the screen. CP/M translates nothing below the program, z88dk builds
  its CP/M library the same way, and C23 7.23.2 allows a text stream to alter
  characters on output to match the host convention.

  Compile with `--no-crlf`, or assign to `__crlf_mode` from `<stdio.h>`, to
  get the old raw LF. `--no-crlf` only takes effect on the translation unit
  that defines `main()`.

  This also removes an inconsistency that predates the change: `puts()`,
  `assert()`, `perror()` and `abort()` already emitted CR LF while
  `printf`, `putchar`, `fputs` and `fprintf` emitted a bare LF, so one
  program printed two different line terminators depending on which stdio
  call it used. All of them now follow one policy. Under `--no-crlf` that
  means `puts()`, `assert()` and `perror()` emit a bare LF, which they did
  not before; `abort()`'s message is still CR LF.

  Anything that compares a uc80 program's console output byte for byte will
  now see the CR. Python harnesses that read the output with `text=True`,
  including all four test drivers in this repository, are unaffected.

  File streams are not translated, with or without the flag: `fwrite` and
  `fputc` to a `FILE *` write the bytes they are given, in both `"w"` and
  `"wb"` mode.

- `--printf` and `--scanf` feature sets are closed over the features they
  imply, so `--printf float` no longer silently drops `%d`. An existing
  build script that passes a narrow `--printf` will link more handlers and
  produce a larger binary than it did in 0.5.0.

- An unknown printf conversion is echoed verbatim instead of vanishing. It
  still cannot resync the varargs, so the rest of that call is still
  misaligned, but the failure is now visible and is diagnosed at compile
  time when the format string is a literal.

- The package no longer declares `py.typed`. It never shipped the file, so
  the PEP 561 claim was false either way; uc80 carries inline annotations
  but has never been type checked, so the claim is dropped rather than
  made true.

### Added

- Basic inline assembly: `asm("...")`, spelled `asm` or `__asm__`, with or
  without `volatile`, at file scope and in a function body. The block is a
  barrier that no optimizer crosses. Extended asm and `asm goto` are
  rejected with an error instead of being silently dropped.
- The `%e`, `%E`, `%g` and `%G` printf conversions, and `%F`, which
  0.5.0 dropped along with every other conversion it had no handler for.
- `--no-crlf`, and `extern char __crlf_mode` in `<stdio.h>`.
- **Wheels now ship `libc.lib`, `runtime.lib` and `crt0.rel`.** `pip install
  uc80` previously installed a compiler that could not link anything: those
  three are assembler output, are gitignored, and were produced only by a
  manual step, so a wheel built from a clean checkout never contained them.
  They are assembled at wheel-build time, which adds `um80` to
  `[build-system].requires` and about 45 KB and 40 s to a release build. They
  are still not committed to git — `libc.lib` is not byte reproducible, so a
  committed copy would churn on every rebuild and could drift out of step
  with `lc/*.mac` unnoticed.
- `uc80 --print-lib-dir` prints the library directory on one line and exits
  0 without needing an input file, for `LIB=$(uc80 --print-lib-dir)`.
- `uc80 --build-libs` assembles the three link artifacts on demand.
- `uc80.lib_dir()` and `uc80.lib_file(name)` — the same answer from Python,
  and the supported way for another tool to locate the libraries. Consumers
  that guessed the path could land on a stale tree and link against it with
  no diagnostic from anything.
- `UC80_LIB_DIR` overrides the library directory, applied per file so a
  partial override cannot shadow the compiler's own version-locked inputs.

### Fixed

- The comma operator's result type is inferred from its right operand. A
  `(a, b)` yielding `double`, `float`, `long` or `long long` was compiled as
  a 16-bit signed `int`. Both operands are also evaluated exactly once.
- Merging an `extern` array declaration with its definition no longer
  crashes the compiler, and two different known sizes are now reported as
  conflicting types instead of being silently reconciled.
- Assembly appended from a `.mac` input, and the embedded runtime, are
  spliced in ahead of the BSS/COMMON block instead of after it, where crt0
  zeroed them.
- Several literal values that were compared without being decoded, which
  affected designated initializers and `_Bool b = 0.0`.

## 0.5.0 and earlier

Only the 2026-04-30 sweep is recorded, from notes kept in `todo.txt` at the
time; for anything else see `git log`.

That sweep added `--int` / `--long` / `--long-long` switches so a test
making an assumption about a type's width could be compiled under it, and
then fixed what running the suites under every width uncovered:

- 64-bit `++` / `--` updated only the low 16 bits.
- A condition test on a `long long` expression ORed only the low 32 bits.
- An implicitly declared external function got no `EXTRN`, which is what had
  stuck many gcc-torture tests that call `abort()` / `exit()` without a
  prototype.
- `__builtin_memcpy` / `memset` / `abort` / `trap` / `unreachable` /
  `malloc` and the rest now rewrite to their libc equivalents.
- Source bytes that are not UTF-8 fall back to latin-1.
- `__SIZE_TYPE__` / `__PTRDIFF_TYPE__` / `__WCHAR_TYPE__` are predefined
  (they live in uc_core's `type_config` now).
- A struct-array element initialized from a compound literal --
  `struct Wrap arr[] = { (struct Wrap){fn}, fn };` -- stored the address of
  the materialized literal in the first member instead of copying the bytes,
  which showed as an infinite loop in c-testsuite 00216 under `--long=64`.
