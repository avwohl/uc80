# Build a Z80 ROM with uc80

Back to the [README](../README.md). The plan and the open work are in [rom_target.md](rom_target.md).

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
4. Call `main`.
5. Loop on `HALT`. The interrupt state is the state that `main` left.

The link must use `--rom-data` and `--data-origin` (`-d`), because `__DATA_ROM`, `__DATA_RAM` and `__DATA_LEN` come from them. The link must also give `--ram-size` or `--ram-end`, unless you use `--stack-top`.

### Your own interrupt handlers

C code cannot yet make an interrupt handler, because the compiler emits no `RETI` and no register save. Write the handler in assembly. Give the assembly file to the compiler as a second input file:

```
uc80 --target=rom main.c isr.mac -o main.mac
```

If `isr.mac` has `PUBLIC __int_handler`, the vector at 0038h becomes `JP __int_handler`. If `isr.mac` has `PUBLIC __nmi_handler`, the vector at 0066h becomes `JP __nmi_handler`. The handler must save the registers it uses and must end with `RETI` (INT) or `RETN` (NMI).

A handler can share a variable with the C program. Define the variable in the assembly file with `PUBLIC _name`. Declare the variable `extern` in the C program. A global variable that the C program defines has no `PUBLIC` label in whole-program mode.

## Calls that the ROM target refuses

The compiler stops with `uc80: error: --target=rom: ...` when the program:

- declares `main` with parameters, such as `main(int argc, char **argv)`. Write `main(void)`.
- calls a library function that `libc_rom.lib` does not have: console and file I/O (`printf`, `puts`, `putchar`, `getchar`, `fopen`, and the others), `exit`, `abort`, `atexit`, `system`, `getenv`, `time`, `clock`, and the heap (`malloc`, `calloc`, `realloc`, `free`).

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

- The const tables, string literals and initialised variables are all in DSEG. They use ROM and RAM both, because the start-up code copies the data to RAM. The next part of Phase 1 puts const data in CSEG (see [rom_target.md](rom_target.md)).
- No interrupt handler in C. The compiler refuses `__interrupt` and the other spellings (see Refused spellings). No port intrinsics. Use an assembly file for `IN` and `OUT`, as `bench/rom/z80io.mac` does.
- A program without `main` and the `--entry` option are not done.
- The heap and `sprintf` are not available.
- The helpers of the runtime keep their temporaries at fixed RAM addresses. A routine that an interrupt handler and the main program both call can fail. Do not call long division, 64-bit arithmetic or float in a handler.
- Float code uses the alternate registers (`EXX`).

## Refused spellings

uc80 refuses the interrupt and fastcall markers. Older versions dropped the markers without a message. The compiler prints `uc80: error: file:line: uc80 does not support '__interrupt' yet; see docs/rom_target.md` and exits with 1. uc80 refuses these spellings: `__interrupt`, `interrupt`, `__interrupt(1)`, `[[gnu::interrupt]]`, `__attribute__((interrupt))`, `__critical __interrupt` and a bare `__fastcall`. A name that only contains the word (`interrupt_count`, `struct interrupt`) is fine.

## Proof: the snd-* kernels as ROM images

`bench/rom/sndrom.py` builds the four sound-driver kernels of `bench/programs/snd-*` as ROM images and runs them in `bench/romrun`.

```
python3 bench/rom/sndrom.py                  # ROM 0000-7FFF, RAM 8000-FFFF
python3 bench/rom/sndrom.py --layout neogeo  # RAM F800-FFFF (2 KB)
```

`bench/rom/snd.h` replaces the CP/M header. The functions `out` and `in` are real `OUT` and `IN` instructions (`bench/rom/z80io.mac`). The text output goes to the debug port 0FEh. A host model catches the ports. The model keeps the same ring of the last 256 writes and the same busy count as the CP/M header. The decoded output is equal to the CP/M golden output (`bench/golden/snd-*.txt`) for all four kernels.

The run starts with random RAM and a poisoned SP. Figures from the last run (uc80 ROM build, `libc_rom.lib`):

```
kernel      ROM bytes  ROM no debug  RAM data  RAM bss  stack  T-states (timed region)
snd-cmdq        1811          1517        39       42     30      10 727 741
snd-ymreg       1337           885       240      528     28      18 620 847
snd-seq         2853          2420       309      634     26      13 567 802
snd-fnum        1329          1329        71       14     22      14 456 390
```

- ROM bytes: the end of the image (vectors, code and the initial data). "No debug" is the same kernel with `rom_printf` removed. The first 104 bytes are the vector area.
- RAM data and bss: from the `ul80` summary. The stack is the peak depth that romrun measured. The stack does not include an interrupt.
- All four kernels fit the real Neo Geo layout (RAM F800h to FFFFh, 2 KB). The largest one, snd-seq, uses 943 bytes of RAM data and BSS plus 26 bytes of stack. The const tables are in this RAM figure until the const-in-CSEG part lands.
- The T-states cover the code between `TIMER_START` and `TIMER_STOP` and include the real `OUT` and `IN` calls. The CP/M figures of [rom_target.md](rom_target.md) use a C model of the ports, so the two sets do not compare.

The test `tests/test_rom_target.py` builds and runs `snd-cmdq` end to end. The test skips when `um80`, a `ul80` with the ROM options, or `libc_rom.lib` is missing.
