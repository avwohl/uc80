# Build a Z80 ROM with uc80

Back to the [README](../README.md). The plan and the open work are in [rom_target.md](rom_target.md). Interrupt functions, port input and output, and objects at a fixed address are in [rom_hardware.md](rom_hardware.md). The use of uc80 code with the SDCC tools and with nullsound is in [rom_interop.md](rom_interop.md).

uc80 can build a Z80 ROM image that has no operating system. The program has no CP/M and no BDOS. The reset vector starts the program at address 0. The ROM options of `ul80` place the code in ROM and the variables in RAM. The ROM test runner (see [romrun.md](romrun.md)) runs the image.

## Quick start

```
uc80 --target=rom main.c -o main.mac
um80 main.mac -o main.rel
ul80 --bin -p 0 -d F800 --rom-data --rom-size 8000 --ram-size 800 \
     --min-stack 100 -o main.bin -S main.sym main.rel \
     $(uc80 --print-lib-dir)/libc_rom.lib $(uc80 --print-lib-dir)/runtime.lib
```

`uc80 --target=rom -v` prints a `ul80` line like this one at the end of the compile. Change `-d` (the first RAM address), `--rom-size` and `--ram-size` for your hardware. The example is the Neo Geo sound CPU: ROM at 0000h to 7FFFh, RAM at F800h to FFFFh.

Run `uc80 --build-libs` once after a checkout. The command builds `libc_rom.lib` with the other libraries.

## Compiler options

- `--target=rom`: build for a ROM. `--target=cpm` is the default.
- `-ffreestanding`: the same as `--target=rom`.
- `--isr-safe`: keep the locals of each function that an interrupt function reaches on the stack (see [rom_hardware.md](rom_hardware.md)).
- `--stack-top EXPR`: the first value of SP, an assembler expression such as `0FF00H`. The default value is the symbol `__RAM_END`. The command `ul80` sets `__RAM_END` from `--ram-size` or `--ram-end`. Only the ROM target accepts `--stack-top`.

With the ROM target the compiler:

- embeds `lib/crt0_rom.mac` in place of the CP/M `crt0.mac` (`--startup-lib` gives your own file)
- does not translate `\n` to CR LF
- defines `__UC80_ROM__` and does not define `__CPM__`
- keeps whole-program mode as the default
- refuses a program that needs the BDOS (see Calls that the ROM target refuses)

## Start-up code and vectors

`crt0_rom.mac` puts the vectors in CSEG with `ORG`. Link with `-p 0`. The gaps between the vectors are zero bytes.

- 0000h: `DI`, `IM 1`, `JP __start`.
- 0008h, 0010h, 0018h, 0020h, 0028h, 0030h: `RET`.
- 0038h: `EI`, `RETI`. The default INT vector.
- 0066h: `RETN`. The default NMI vector.
- 0068h: `__start`. The code of the program follows.

`__start` does these steps:

1. Load SP from `__RAM_END` (or from `--stack-top`).
2. Copy `__DATA_LEN` bytes from `__DATA_ROM` to `__DATA_RAM`. No copy happens when `__DATA_LEN` is 0.
3. Clear the BSS from `__BSS_START` to `__BSS_END`. No clear happens when the BSS is empty.
4. Call `main`. If the program has no `main`, call the first function that `--entry` names (see Roots, entry points and vectors).
5. Loop on `HALT`. The interrupt state is the state that `main` left.

If the program has a watchdog hook, `__start` also calls the hook (see Watchdog hook).

The link must use `--rom-data` and `--data-origin` (`-d`), because `__DATA_ROM`, `__DATA_RAM` and `__DATA_LEN` come from them. The link must also give `--ram-size` or `--ram-end`, unless you use `--stack-top`.

### Your own interrupt handlers

Write an interrupt handler in C with `#pragma uc80 interrupt NAME` or `#pragma uc80 nmi NAME` (see [rom_hardware.md](rom_hardware.md)). The compiler saves the registers, ends the function with `RETI` or `RETN`, and sets the vector. You can also write the handler in assembly. Give the assembly file to the compiler as a second input file:

```
uc80 --target=rom main.c isr.mac -o main.mac
```

If `isr.mac` has `PUBLIC __int_handler`, the vector at 0038h becomes `JP __int_handler`. If `isr.mac` has `PUBLIC __nmi_handler`, the vector at 0066h becomes `JP __nmi_handler`. The handler must save the registers it uses and must end with `RETI` (INT) or `RETN` (NMI).

A handler can share a variable with the C program. Define the variable in the assembly file with `PUBLIC _name`, and declare the variable `extern` in the C program. Or define the variable in C and name the variable in `#pragma uc80 public` (see Roots, entry points and vectors). The assembly file then uses `EXTRN _name`.

### Watchdog hook

A program can give the start-up code a function that kicks a watchdog. The function is `__watchdog_kick`. Write the function in C or in assembly:

```
#include <z80.h>
void _watchdog_kick(void) { z80_out(0x7F, 0); }
#pragma uc80 public _watchdog_kick
```

For assembly, give a `.mac` file to the compiler as a second input file, and put `PUBLIC __watchdog_kick` in the file. The hook must end with `RET`. The hook can change all registers.

When the program defines `__watchdog_kick`, the start-up code calls the hook in three places:

1. At reset, as the first step after the load of SP. The data copy and the BSS clear have not started. A hook in C can use its local variables, but a static variable that the hook sets here is lost when the BSS clear runs.
2. After the BSS clear, before `main`.
3. In the idle loop, before each `HALT`. An interrupt that wakes the CPU runs the loop again, so the hook runs after each interrupt.

A program that has no `__watchdog_kick` gets no call and no extra byte. A hook costs 9 bytes of calls, and the bytes of the hook. The program can also call `_watchdog_kick()` from its own code.

The hook works only for a whole-program build, where the compiler embeds the start-up code. The hook must be in the same compilation as the start-up code (a C file or a `.mac` file that the compiler gets), as for `__int_handler`. With `--no-embed-startup`, call the hook from your own start-up code.

The test `tests/test_rom_target.py` (`TestWatchdogHook`) runs a hook in `bench/romrun` with a port that logs each kick.

## Roots, entry points and vectors

In whole-program mode the compiler removes each function that no code calls, and each object that no live code uses. Four pragmas and one option tell the compiler what hardware or assembly code uses. The pragmas go in any C source file. Several names can follow one pragma, separated by commas or spaces.

```
#pragma uc80 keep NAME ...      keep the function or object, and what the function calls
#pragma uc80 public NAME ...    keep NAME and give NAME a PUBLIC label, also if NAME is static
#pragma uc80 vector 38h NAME    the INT vector jumps to the C function NAME
#pragma uc80 vector 66h NAME    the NMI vector jumps to the C function NAME
```

- `--entry NAME` (repeatable) is the same as `#pragma uc80 keep NAME`. The option also has a second use: a ROM program may have no `main`. The start-up code then calls the first `--entry` function in place of `main`. A ROM program with no `main` and no `--entry` is an error.
- A kept function has the same storage rules as a function whose address the code takes. No other function shares the local storage of the kept function, because hardware can enter the function at any time.
- A function in a `vector` pragma is kept. The vector becomes `JP _NAME`. The compiler gives an error when a `vector` pragma and an assembly `__int_handler` (or `__nmi_handler`) both set the same vector.
- The compiler gives an error in three cases: an unknown pragma verb, a vector address that is not 38h or 66h, and a name that the program does not define.

A C function in a `vector` pragma has no interrupt prologue and no `RETI`. It ends with `RET`. The compiler prints a warning for each such `vector` pragma. Use a `vector` target only for code that handles this itself. For a real interrupt function, use `#pragma uc80 interrupt NAME` or `#pragma uc80 nmi NAME`: these set the vector for you and print no warning.

Objects in the BSS (uninitialised globals) now keep a `PUBLIC` label in the ROM target. The `.sym` file of `ul80` lists them, and `bench/romrun` can read them by name (`m.peek8("_ticks")`). The CP/M target does not change.

## Const data in the ROM

With `--target=rom` the compiler puts these objects in CSEG, so they stay in the ROM and the start-up code does not copy them to RAM:

- an object whose type is const all the way down: `const int tab[3]`, `static const unsigned char k[]`, `const struct S s`, `const char *const names[]`
- string literals
- compound literals that are const-qualified, such as `(const int[]){1, 2}`, when no code writes the literal at run time

These objects stay in DSEG, and so in RAM:

- a pointer to const data: `const char *p = "x"` (the pointer can change)
- an array of such pointers: `const char *tbl[]` (the array is not const). Write `const char *const tbl[]` to put the array in the ROM.
- a volatile const object
- a struct that is not const but has a const member
- a compound literal that is not const, or that the code initialises at run time

A write to a const object through a cast has undefined behavior. In a ROM the write goes nowhere. Run the ROM in `bench/romrun` with `rom_write="fault"` (or `--rom-write fault`) to stop at the first such write (`rom-write` is the stop reason).

The CP/M target does not change. Many CP/M programs write to string literals or to const objects through casts. The CP/M image gets no gain, because CSEG and DSEG are both in the `.com` file.

## Calls that the ROM target refuses

The compiler stops with `uc80: error: --target=rom: ...` when the program:

- declares `main` with parameters, such as `main(int argc, char **argv)`. Write `main(void)`.
- calls a library function that `libc_rom.lib` does not have: console and file I/O (`printf`, `puts`, `putchar`, `getchar`, `fopen`, and the others), `exit`, `_Exit`, `quick_exit`, `abort`, `atexit`, `at_quick_exit`, `system`, `getenv`, `time`, `clock`, and the heap (`malloc`, `calloc`, `realloc`, `free`).

A call of `printf("text\n")` becomes a call of `puts` in the generated code, so the error names `puts`. Send output to a port with your own function. The kernels in `bench/rom/` show one way.

## The ROM library

`libc_rom.lib` has the modules of `libc.lib` that do not use the BDOS and that make sense in a ROM. `build_libs.py` chooses the modules with `src/uc80/rom.py`. A module is in the library when three things are true. The module does not call the BDOS. The module is not on the exclusion list. The module needs only symbols of other accepted modules.

The library has:

- `string.h`: `strlen`, `strcpy`, `strcmp`, `strchr`, `strstr`, `strtok` and the others, and `memcpy`, `memmove`, `memset`, `memcmp`, `memchr`
- `ctype.h`: `isalpha`, `toupper` and the others
- `stdlib.h`: `abs`, `labs`, `div`, `ldiv`, `atoi`, `atol`, `atof`, `strtol`, `strtoul`, `qsort`, `bsearch`, `rand`, `srand`
- `math.h` and `complex.h`
- `setjmp.h` (the code is in `runtime.lib`)

The integer, 64-bit and float runtime come from `runtime.lib`. `runtime.lib` has no BDOS use. Link both libraries.

`sprintf` and `snprintf` are not in `libc_rom.lib`, because they use the printf core, which writes through the BDOS.

## Memory use of the runtime

The fixed temporaries of the runtime and of the library are in the BSS, in a named COMMON block for each module (`COMMON /rt_arith32/` and so on). A variable that starts with zero is not copied from ROM. This holds for `__tmp32`, `__divrem`, `__div32_dvd`, `__rem32`, the 64-bit areas, the float and complex temporaries, and the static variables of libc. A ROM image keeps only the data that has a non-zero start value.

This change also makes the CP/M images that use these modules smaller, because the zero bytes are no longer in the `.com` file.

## Limits today

- The compiler sets the vectors only in whole-program mode with the embedded start-up code. With `--no-whole-program` or `--startup-lib`, the compiler prints a warning and your own start-up code must jump to the interrupt function.
- A call of an interrupt function from C is an error. A handler cannot take arguments or return a value.
- The heap and `sprintf` are not available.
- The helpers of the runtime keep their temporaries at fixed RAM addresses. A routine that an interrupt handler and the main program both call can fail. The compiler prints a warning with the call chain when a handler reaches such a routine. Do not call long division, 64-bit arithmetic or float in a handler that the main program shares them with.
- The float runtime of uc80 does not use the alternate registers. Assembly code can. The prologue of a handler saves the alternate registers when the handler reaches code that uses them.
- Each C object that the program defines is one object of one module. A second C file does not get its own BSS block (the plan lists this as optional).

## Refused spellings

uc80 refuses the interrupt and fastcall markers that it does not support. Older versions dropped the markers without a message. The compiler prints `uc80: error: file:line: uc80 does not support '__interrupt' yet; see docs/rom_target.md` and exits with 1. uc80 refuses these spellings: `interrupt`, `__interrupt` before the declarator, `[[gnu::interrupt]]`, `__attribute__((interrupt))` and a bare `__fastcall`. A name that only contains the word (`interrupt_count`, `struct interrupt`) is fine.

uc80 accepts the SDCC forms `void f(void) __interrupt`, `__interrupt(n)`, `__critical __interrupt` and `__critical { }`, because it has interrupt functions now. [rom_hardware.md](rom_hardware.md) lists them with the SDCC forms that stay errors.

## Proof: the snd-* kernels as ROM images

`bench/rom/sndrom.py` builds the four sound-driver kernels of `bench/programs/snd-*` as ROM images and runs them in `bench/romrun`.

```
python3 bench/rom/sndrom.py                  # ROM 0000-7FFF, RAM 8000-FFFF
python3 bench/rom/sndrom.py --layout neogeo  # RAM F800-FFFF (2 KB)
```

`bench/rom/snd.h` replaces the CP/M header. The functions `out` and `in` are real `OUT` and `IN` instructions (`bench/rom/z80io.mac`). The text output goes to the debug port 0FEh. A host model catches the ports. The model keeps the same ring of the last 256 writes and the same busy count as the CP/M header. The decoded output is equal to the CP/M golden output (`bench/golden/snd-*.txt`) for all four kernels.

The run starts with random RAM and a poisoned SP. Figures from the last run (Neo Geo layout, uc80 ROM build, `libc_rom.lib`). Before the const data moved to CSEG, the RAM figure included the const tables.

```
kernel      ROM bytes  RAM before  RAM now  RAM data  RAM bss  stack  T-states (timed region)
snd-cmdq        1777          81       48         6       42     30      10 236 778
snd-ymreg       1312         768      530         2      528     28      17 676 627
snd-seq         2797         943      636         2      634     26      12 749 126
snd-fnum        1121          83       14         2       12     18       9 478 190
```

- ROM bytes: the end of the image (vectors, code and the initial data). The ROM size and the T-states did not change when the const data moved to CSEG. The same bytes are in the image, and the code reads the bytes at the same cost.
- RAM before and now: data plus BSS, from the `ul80` summary. The stack is the peak depth that romrun measured. The stack does not include an interrupt.
- All four kernels fit the real Neo Geo layout (RAM F800h to FFFFh, 2 KB). The largest one, snd-seq, uses 636 bytes of RAM data and BSS plus 26 bytes of stack.
- The T-states cover the code between `TIMER_START` and `TIMER_STOP` and include the real `OUT` and `IN` calls. The CP/M figures of [rom_target.md](rom_target.md) use a C model of the ports, so the two sets do not compare.

The test `tests/test_rom_target.py` builds and runs `snd-cmdq` end to end. The test skips when `um80`, a `ul80` with the ROM options, or `libc_rom.lib` is missing.

## Proof: interrupts and ports

`bench/rom/isrproof.py` runs three more ROMs in `bench/romrun`: the snd-cmdq kernel with a real NMI, the snd-seq kernel driven by a YM2610 timer interrupt, and a bank select with `z80_in16`. Each port log equals the expected sequence. [rom_hardware.md](rom_hardware.md) has the figures.
