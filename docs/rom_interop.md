# Use uc80 next to SDCC and nullsound

Back to [rom.md](rom.md). The plan is in [rom_target.md](rom_target.md) (Phase 5).

A sound ROM often has code from more than one tool. The SDCC tools (`sdasz80`, `sdldz80`) build the sound driver nullsound of ngdevkit. This page tells how to put C code from uc80 in such a ROM, and how to use SDCC `__asm` blocks in uc80 source.

Three parts are available:

- SDCC `__asm ... __endasm;` blocks in the C source of uc80.
- The option `--asm-syntax=sdas`. The compiler writes sdas text (a `.s` file) in place of MACRO-80 text.
- A library of the uc80 runtime and libc for `sdldz80`.

uc80 stays the compiler. Its own tools (`um80`, `ul80`) stay the default. The sdas output is opt-in. The default output does not change.

## SDCC `__asm` blocks

The compiler reads `__asm ... __endasm;` at statement level and at file level. The text in the block is sdas syntax, as in SDCC. The compiler turns the block into an `asm("...")` statement (see [inline_assembly.md](inline_assembly.md)) with MACRO-80 text. A block can be the body of `if`, `else`, `for`, `while` or `do` with no braces. The compiler then adds braces (`{ asm("..."); }`). The grammar of uc80 does not accept `asm` as a sub-statement.

```c
unsigned char r;

void f(void)
{
    __asm
        ld a,#0x5A
        ld (_r),a
        ld b,#3
    1$: djnz 1$
    __endasm;
}
```

What the translation does:

- `#value` becomes `value`. A memory operand `(_name)` stays.
- `2(ix)` and `(ix+2)` become `(IX+2)`.
- `0x1F` becomes `1FH`. `<x` becomes `LOW x` and `>x` becomes `HIGH x`.
- `.db`, `.byte` and `.fcb` become `DB`. `.dw` and `.word` become `DW`. `.ds`, `.blkb` and `.blkw` become `DS`. `.ascii` and `.asciz` become `DB` with the characters.
- `name = value` and `.equ name, value` become `EQU`.
- `.globl name` becomes `PUBLIC` when the block defines `name`. Otherwise it becomes `EXTRN`.
- A local label `1$` is local to the block and to the text between two global labels.

Both syntaxes use the same word for a C name. The global `x` has the name `_x`.

What the compiler refuses, with the line number of the block:

- Every directive that is not in the list above (`.area`, `.org`, `.module`, `.include`, `.macro`, `.if`, `.rept`, `.even`).
- An operand that needs `#` in sdas but has none. sdasz80 refuses it too.
- `.db "text"`. Use `.ascii "text"`.
- A block with no `__endasm`, and `__endasm` with no `__asm`.

In a normal function the compiler writes the frame code, and the frame of uc80 is not the frame of SDCC. For a function that is all assembly, use `__naked` (see "Naked functions" in [rom_hardware.md](rom_hardware.md)): the compiler writes no frame code, and the parameters are at `(SP+2)`, `(SP+4)` at entry. Do not use `N(ix)` for a C parameter in a block that you share with SDCC.

## The option --asm-syntax=sdas

```
uc80 --target=rom --asm-syntax=sdas main.c -o main.s
sdasz80 -g -l -o main.s
sdldz80 -n -b _CODE=0x68 -b _INITIALIZED=0xF800 -g __RAM_END=0x0 \
        -g __DATA_ROM=s__INITIALIZER -g __DATA_RAM=s__INITIALIZED \
        -g __DATA_LEN=l__INITIALIZED -g __BSS_START=s__DATA \
        -g __BSS_END=s__DATA+l__DATA -i -j -m main.ihx main.rel \
        -l uc80_lc.lib -l uc80_rt.lib
sdobjcopy -I ihex -O binary main.ihx main.bin
```

`uc80 -v` prints these lines for your program. The first run needs the two libraries:

```
uc80 --target=rom --build-sdas-libs DIR
```

The command translates every module of `runtime.lib` and `libc.lib`, assembles the modules with `sdasz80`, and packs them with `sdar` into `DIR/uc80_rt.lib` and `DIR/uc80_lc.lib`. Give each library to `sdldz80` with `-l`. With `-l` the linker takes only the modules that the program needs. A library on the command line without `-l` links every module.

The libraries come from a translation of the library modules. They are not a `ul80` blob. The translation has no fixed addresses.

The linker places the library code and data together with your code and data. The libraries are build output. They are not in the repository. Build them with the same `--sdas-style` and the same `--target` as your program.

### Area names

The translator maps the segments of MACRO-80 to sdas areas by role.

```
role         MACRO-80          sdcc style     ngdevkit style
code         CSEG              _CODE          CODE
rom_init     DSEG, the bytes   _INITIALIZER   INITIALIZER
ram_init     DSEG, the space   _INITIALIZED   INITIALIZED
bss          COMMON            _DATA          DATA
absolute     ASEG, ORG         _ABS (ABS)     START (ABS)
```

- `--sdas-style=sdcc` (default) uses the names of the SDCC start-up code.
- `--sdas-style=ngdevkit` uses the names that nullsound uses for its code and its data. nullsound links `CODE` and `DATA`, and it links `DATA` at F800h with `sdldz80 -b DATA=0xf800`.
- `--sdas-areas=role=NAME,...` changes single names.

The start-up code of the ROM target copies the initial data from the `rom_init` area to the `ram_init` area. Each module adds the same number of bytes to both areas. So the two areas have the same layout. The translator writes the data bytes in the first area and the labels, with `.ds`, in the second area. A CP/M program (`--target=cpm`) has its data in the image. The translator then writes the bytes in the `ram_init` area, and the first area stays empty.

The ROM library and the CP/M library differ in this point. Build the one that matches your target.

### Link flags

`uc80.sdas.link_flags()` returns the flags above. The start-up code `crt0_rom` needs six symbols. The linker makes them from the area symbols (`s_AREA` is the start and `l_AREA` is the length of an area):

```
__RAM_END    the first value of SP, 0 for 10000h
__DATA_ROM   s_<rom_init>        __DATA_RAM   s_<ram_init>
__DATA_LEN   l_<ram_init>        __BSS_START  s_<bss>
__BSS_END    s_<bss>+l_<bss>
```

`sdldz80` accepts `-g` only for a symbol that a module uses. A program that links `malloc` needs `link_flags(..., heap=True)`. The flags then also hold `-g __END__=s__DATA+l__DATA` (the start of the heap). Without the flag, the linker says `No definition of symbol __END__` or `Undefined Global __END__`. The use of `malloc` can be indirect, for example in a libc function that calls it.

The vectors are in an absolute area. The area stops at the marker `;@RELOC` in `crt0_rom.mac`. The marker gives the origin of the code that follows (68h, or 69h when a C function is the NMI vector). The translator writes the origin in the first line of the `.s` file as `code-origin`. `-b _CODE=0x68` uses it. An `--startup-lib` file has no marker, and an `ORG` in it makes absolute code up to the next segment directive.

### Differences between um80 and sdasz80 that the translator handles

- um80 changes a `JR` or a `DJNZ` that is out of range to `JP` (or `DEC B` and `JP NZ`). sdasz80 gives an error. The translator knows the size of each instruction and makes the same change. The test suite compares the sizes with `sdasz80` for all modules of the libraries.
- Labels such as `@DO1` and `??AUTO` become `L$DO1` and `Q$Q$AUTO`.
- um80 does not distinguish upper case and lower case in a symbol. sdasz80 does. The translator uses the first form of a symbol in a module for all forms of the symbol.
- Operands that are values have `#`. Operands in parentheses are memory.
- `SET n,r` is an instruction. `name SET value` is a directive.

The translator stops with the line number for a statement it does not know: `IF`, `MACRO`, `INCLUDE`, `DS` with a fill value, data in `COMMON`, an `ORG` in `DSEG`. uc80 never writes these.

### Proof: the same ROM images

`bench/rom/sndrom.py --sdas` builds the four snd-* kernels through `sdasz80` and `sdldz80`. `bench/rom/isrproof.py --sdas` does the same for the three interrupt proofs. The results equal the results of the `um80` and `ul80` build.

```
kernel      ROM bytes  RAM data+bss  stack  T-states (timed region)
snd-cmdq        1777      6 + 42       30      10 236 778
snd-ymreg       1312      2 + 528      28      17 676 627
snd-seq         2797      2 + 634      26      12 749 126
snd-fnum        1121      2 + 12       18       9 478 190
```

The image of each kernel is byte for byte the image of `ul80` (`ul80` pads the file to the ROM size with FFh). All 105 programs of the benchmark set (the sources that are in `bench/_cache`) translate and assemble with no error. A manual run of all 105 in `cpmemu`, linked by `sdldz80`, gave the same output as the `um80` build for each program. The test suite runs five of them.

## C code from uc80 next to SDCC C code

A module from uc80 and a module from SDCC can share one ROM. The SDCC crt0 does the start-up. The area names of the `sdcc` style are the names of SDCC, so the linker merges them.

```
uc80 --target=rom --asm-syntax=sdas --no-embed-startup --entry uc_work uc.c -o uc.s
sdasz80 -g -l -o uc.s
sdcc -mz80 -c main.c
sdcc -mz80 --code-loc 0x200 --data-loc 0xF800 main.rel uc.rel -o prog.ihx
```

- `--no-embed-startup` leaves out the crt0 of uc80. `--entry` keeps the functions that the SDCC code calls.
- The initial data of the uc80 module is in `_INITIALIZER` and `_INITIALIZED`. The SDCC crt0 copies both. The test `test_sdcc_main_with_a_uc80_module` checks this with initial data in both modules.
- The `_DATA` of the uc80 module (BSS and the shared automatic area) joins the `_DATA` of SDCC. The SDCC crt0 clears `_DATA` (checked with random RAM), so uc80 globals start at zero.
- Call functions without parameters, and share data with global variables. The convention for calls differs. SDCC 4.6 passes the first parameters in registers. uc80 passes all on the stack. SDCC keeps IX in a callee, and uc80 keeps IX, so a call in each direction is safe.
- With `sdcc --code-loc 0x100` the code overlaps the crt0 of SDCC. Use 0x200.
- Link the runtime of uc80 with `-l uc80_rt.lib` when the uc80 module calls it and does not embed it. In whole-program mode the compiler embeds the runtime functions that it needs.

## C code next to nullsound

nullsound is the sound driver of ngdevkit. Its Z80 code owns the vectors, the start-up, the stack and the NMI handler. The user part of the ROM is a table of 128 jumps, `cmd_jmptable`. A command byte from the 68k selects an entry. The goal is to keep nullsound and to write the commands in C.

The files:

- `bench/rom/ns_user.s`: the table, and the shim `_ns_ym_a` that lets C call `ym2610_write_port_a`.
- `bench/rom/ns_cmds.c`: three C commands.
- `bench/rom/nullsound.py`: fetches the sources of nullsound, builds, links and runs the ROM in romrun.

nullsound is not in this repository. The script fetches the files at the commit `5106cbd1414a86ba02810ee93ac977be3360de4f` of `github.com/dciabrin/ngdevkit` into `bench/_cache/ngdevkit`. It checks the sha256 of each file. The hashes are in `bench/rom/nullsound.py`. nullsound has the licence LGPL-3.0-or-later.

### Build commands

nullsound is built as its `Makefile.in` does. The C part is new.

```
sdasz80 -a -g -l -p -s -u -Imvs -o entrypoint-mvs.rel entrypoint.s
sdasz80 -a -g -l -p -s -u -o memory.rel memory.s          # and the other objects
cat entrypoint-mvs.rel memory.rel ... > nullsound.lib

uc80 --target=rom --asm-syntax=sdas --sdas-style=ngdevkit --no-embed-startup \
     --entry cmd_init --entry cmd_key_on --entry cmd_key_off ns_cmds.c -o ns_cmds.s
sdasz80 -g -l -o ns_cmds.s
sdasz80 -g -l -I<nullsound> -o ns_user.s

sdldz80 -n -b DATA=0xf800 -i -j -m -w ns.ihx nullsound.lib ns_user.rel ns_cmds.rel
sdobjcopy -I ihex -O binary ns.ihx ns.bin
```

The options `--no-embed-startup` and `--entry` leave out the start-up code of uc80 and keep the functions that the table calls. Link nullsound first, as ngdevkit does. Then the user area `DATA` of the C code follows the nullsound data, and the RAM ranges of nullsound do not move. `nullsound/check-bounds.py` confirms it.

### Register contract

Verified in the nullsound sources:

- The main loop calls a command with `push bc; push hl; ret`. The return address is `_ret_from_cmd`. The loop has pushed HL before the call. The loop saves no other register. A command can change A, BC, DE and HL.
- IX and IY are not live in the main loop. A uc80 function saves and restores IX (its frame pointer) and does not use IY.
- The NMI handler uses AF', BC', DE' and HL' at any time. C code must not use them. uc80 code does not use them, except the code of the float runtime. Do not call float, `double` or 64-bit arithmetic from a command.
- The INT handler at 38h keeps all registers. It calls `ym2610_restore_port_context`. That routine repairs the YM2610 address latch only when the interrupted code used `ym2610_write_port_a` or `ym2610_write_port_b`. So C code must use these nullsound routines (with a shim) to write the YM2610. A raw `OUT` or a port intrinsic in C can race with the timer interrupt.
- A command runs in the main loop, never in the NMI. The NMI can interrupt the command.
- The stack top is FFFDh. The nullsound data in the MVS build uses 1942 bytes from F800h. The data of the C module (5 bytes in the example) and the stack use the rest, about 100 bytes. Keep both small.
- The post-init hook is the word at FFFEh. Entry 2 of the table is the hook that nullsound calls after the driver start (command 02h, "eye catcher"). Use it to set the variables of the C code. No code clears `DATA` and no code copies initial data. So the C module has no initialised global. In the ngdevkit style, `uc80` prints a warning when the module has one.

The entry of a C command without a parameter is a plain `jp _cmd_key_on`. A command has no parameter: the 68k sends one byte.

### Call from C to nullsound

A function of nullsound takes its parameters in registers. A shim reads the stack of the C call. The convention of uc80 today is:

- The caller pushes the arguments from right to left. Each argument uses at least two bytes. A `char` is a 16-bit value. A `long` is two words, the low word at the lower address.
- The caller removes the arguments after the call.
- The return value is in HL (`char` in L), or in DEHL for a `long`.
- The callee keeps IX and SP. It can change every other register.

The shim in `ns_user.s` reads `reg` at SP+2 and `val` at SP+4, loads B and C, and jumps to `ym2610_write_port_a`. That function keeps all registers and returns to the C caller. If uc80 changes its convention, change each shim. Avoid `char` arguments and `char` return values at the boundary when you can.

### Proof

`python3 bench/rom/nullsound.py` builds the ROM (10 938 bytes for the MVS tables) and runs it in `bench/romrun` with the NMI gate of the Neo Geo and random RAM:

1. An NMI with command 02h starts the driver. The hook runs the C function `cmd_init`. It answers 20h on port 0Ch.
2. Three NMIs with command 04h. nullsound acknowledges each command with 84h on port 0Ch. The main loop calls `cmd_key_on` in C. The function takes a note from a const table with a division by 12. It writes the YM2610 registers A4h, A0h and 28h through `ym2610_write_port_a`. Then it answers 41h, 42h and 43h.
3. An NMI with command 05h. The C function `cmd_key_off` writes register 28h and answers 82h.

The script compares the port log with the expected sequence. It rebuilds the register writes from the address latch of the YM2610. It ignores the writes to register 1Ch (nullsound writes it all the time in its main loop), the writes to register 27h (the INT handler), and the acknowledgements 84h and 85h of nullsound. Only the traffic of the C commands remains, so the check is on the C commands and not on nullsound.

Command 04h before command 02h would see undefined `DATA`, because no code clears `DATA`. The proof always sends 02h first. The test `tests/test_rom_interop.py` runs this with zero RAM and with two random RAM patterns. It also checks the RAM variables and the stack.

`python3 bench/rom/nullsound.py --irq-period 997` also tests the INT handler. Command 06h (in `ns_user.s`) starts YM2610 timer B, and `timer_tempo` executes `EI`. Then romrun injects an interrupt every 997 T-states. About 4900 interrupts come.

Some of them (13 in the run) land inside a C command. The expected YM2610 sequence stays the same. With a period of 311 T-states the main loop of nullsound gets no time to run the command, because the INT handler is too slow. The test uses the periods 997 and 1213.

This proves the build, the link, the register contract and the two call directions. It does not run nullsound music. The ADPCM, SSG and FM sections of nullsound are not tested, and the YM2610 model of romrun makes no sound.

### Alternate registers next to nullsound

The NMI handler of nullsound uses AF', BC', DE' and HL' at any time. C code that executes `EXX` or `EX AF,AF'` can change the state of nullsound. The float division of uc80 (`__fdiv`) does this. The math functions that the ROM libraries link (`sin`, `sqrt` and others) do not.

uc80 checks the call graph of the final assembly when one of these is true:

- You give the option `--with-nullsound`.
- You use `--asm-syntax=sdas --sdas-style=ngdevkit` with `--entry`. The build of this page does this.

The roots of the check are the `--entry` functions, `main`, and the interrupt functions. For each root that reaches such code, the compiler prints a warning with the call chain. It prints one more warning for each function and each runtime helper that has the `EXX` or `EX AF,AF'` instruction:

```
uc80: warning: runtime helper '__fdiv_man' uses EXX or EX AF,AF' (the alternate
registers); the NMI handler of nullsound uses them at any time, so the code can
corrupt the state of nullsound (reached from entry function 'cmd_key_on':
_cmd_key_on -> __fdiv -> __fdiv_man)
```

The compiler prints each function once. A call through a function pointer reaches every function whose address the program takes (see [rom_hardware.md](rom_hardware.md)). `tests/test_nullsound_alt.py` runs programs with float division and with `EXX` in C, in both flows.

## Limits and open work

- The output is for ROMs. A CP/M program in sdas syntax links and runs (the tests do this), but the CP/M target is not a goal.
- Another track changes the convention for calls in uc80. Check each shim after that change.
- `uc80` warns about code with `EXX` or `EX AF,AF'` next to nullsound (see "Alternate registers next to nullsound" above). The warning does not see code that only the linker adds, for example a library that you link later.
- A C module with initialised globals (uc80 warns) needs `-b INITIALIZER=...` and `-b INITIALIZED=...` and a copy loop that you write. nullsound has no such code.
- Tests: `tests/test_sdas.py`, `tests/test_sdcc_compat.py` (the `__asm` block), `tests/test_rom_interop.py`. They skip when the SDCC tools are missing.
