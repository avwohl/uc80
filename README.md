# uc80 - ANSI C Compiler for Z80

A C compiler targeting the Z80 processor and CP/M operating system.
Produces assembly compatible with the [um80](https://github.com/avwohl/um80_and_friends) assembler and linker toolchain.

## Installation

```
pip install uc80
```

Or from source:
```
pip install -e .
```

Requires the [um80](https://pypi.org/project/um80/) assembler/linker toolchain:
```
pip install um80
```

## Quick Start

```bash
# Compile, assemble, and link a C program
LIB=$(uc80 --print-lib-dir)
uc80 hello.c -o hello.mac
um80 hello.mac -o hello.rel
ul80 hello.rel $LIB/libc.lib $LIB/runtime.lib -o hello.com
```

## Finding the Libraries

Linking needs `libc.lib`, `runtime.lib` and — for separate compilation —
`crt0.rel`. Ask uc80 where they are instead of guessing:

```bash
LIB=$(uc80 --print-lib-dir)
```

It prints one line and exits 0, with no input file required. From Python:

```python
import uc80
uc80.lib_dir()             # -> Path to the library directory
uc80.lib_file("libc.lib")  # -> Path to one asset
```

Wheels ship the three link artifacts, so `pip install uc80` is enough. In a
git checkout they are build output (`.gitignore` covers `*.lib` and `*.rel`),
so build them once:

```bash
uc80 --build-libs          # assembles libc.lib, runtime.lib and crt0.rel (~40 s)
```

Re-run that after editing anything in `src/uc80/lib/lc/` or `src/uc80/lib/rt/`.

Set `UC80_LIB_DIR` to use libraries built somewhere else, which is what makes
uc80 usable when it is installed into a read-only `site-packages`. The
override applies per file, so a directory holding nothing but the two rebuilt
`.lib` files works and cannot shadow `crt0.mac`, `runtime.mac` or `include/` —
those are version-locked to the compiler and always come from the package.
Point it at a *complete* older library tree, though, and you get exactly the
silently-stale-libc problem this flag exists to prevent.

`src/uc80/lib/` is the only library directory. Nothing in uc80 looks anywhere
else; do not create a top-level `lib/`.

## Best Optimization (Whole-Program)

For smallest binaries, compile all `.c` files in a single invocation.
This enables whole-program optimizations that are not possible when
compiling files separately:

```bash
# Single-file (best optimization - all optimizations enabled by default)
uc80 main.c utils.c -o program.mac
um80 program.mac -o program.rel
ul80 program.rel $LIB/libc.lib $LIB/runtime.lib -o program.com
```

Default optimizations (all enabled unless disabled):
- **Whole-program mode**: Dead function elimination across all files
- **Shared storage**: Non-recursive functions use static allocation instead of stack frames
- **Function inlining**: Small functions expanded at call sites
- **Constant propagation**: Interprocedural constant folding
- **AST optimization**: Expression simplification, strength reduction
- **Assembly DCE**: Dead code elimination at assembly level
- **Peephole optimization**: Pattern-based instruction replacement
- **Printf auto-detection**: Scans format strings to link only needed handlers;
  rewrites `printf("...\n")` to `puts("...")` when no format specifiers are used
- **Embedded runtime**: Runtime functions included as source, DCE removes unused ones

### Printf Control

The compiler auto-detects which printf format specifiers your program uses
and links only the needed handlers. That inference needs the whole program, so
under `--no-whole-program` it is not used — see [Separate
Compilation](#separate-compilation). You can also control this explicitly:

```bash
# Command line
uc80 program.c --printf int           # %d %u %x %o %s %c %p only
uc80 program.c --printf int --printf long  # add %ld %lu %lx
uc80 program.c --printf float         # add %f

# In source code
#pragma printf int
#pragma printf long
```

### Console Line Endings

Console output ends a line with CR LF, because that is what a real CP/M
terminal needs. An ADM-3A, a Kaypro or a Televideo treats a bare LF as
"cursor down" only, so output written with a bare LF stair-steps down the
screen. CP/M itself translates nothing, so the program must emit both bytes.
z88dk builds its CP/M library the same way, and C23 7.23.2 allows a text
stream to alter characters on output to match the host convention.

libc does the translation in one place, `lib/lc/lc_conout.mac`, which every
console writer calls. A CR is only inserted before an LF that does not
already follow a CR, so a program that prints `"\r\n"` does not get
`"\r\r\n"`, and a `"\r"` progress-bar redraw still works.

To get raw LF instead:

```bash
uc80 --no-crlf program.c -o program.mac
```

```c
#include <stdio.h>       /* declares __crlf_mode */
__crlf_mode = 0;         /* raw LF from here on; 1 turns it back on */
```

Two things to know about `--no-crlf`. It only takes effect on the translation
unit that defines `main()`, because the flag is a runtime byte in libc and
the compiler zeroes it at the top of `main()`; libc ships prebuilt, so the
compiler cannot select different library source. And it links that byte's
module, about 44 bytes, into a program that would otherwise do no I/O.

File streams are never translated, with or without the flag. `fwrite` and
`fputc` to a `FILE *` write the exact bytes you hand them, in both `"w"` and
`"wb"` mode.

### Configurable Integer Sizes

By default `int` is 16 bits (natural Z80 word width).  Code that assumes
32-bit `int` can be compiled with a CLI override — no source changes:

```bash
uc80 program.c --int=32 -o program.mac     # 32-bit int
uc80 program.c --long=64 -o program.mac    # 64-bit long
```

The bundled headers (`<limits.h>`, `<stdint.h>`, `<stddef.h>`, `<inttypes.h>`)
derive their typedefs and limit macros from compiler-supplied `__SIZEOF_*__`
and `__*_MAX__` macros, so the same header files work under every config.
Codegen routes arithmetic, `printf`/`scanf` format dispatch, and `sizeof`
through the selected widths automatically.

### Separate Compilation

When compiling files separately for separate linking, use `--no-whole-program`:

```bash
uc80 --no-whole-program module.c -o module.mac
```

Link those with `crt0.rel` first — the compiler only embeds crt0 in
whole-program mode:

```bash
LIB=$(uc80 --print-lib-dir)
ul80 $LIB/crt0.rel module.rel main.rel $LIB/libc.lib $LIB/runtime.lib -o prog.com
```

Pass the **same `--printf`/`--scanf` set to every unit** of the program. Each
unit that calls `printf` emits the dispatch table, because the compiler's table
has to beat the 16-bit-int default in libc; L80 keeps the first definition of a
multiply-defined global and links on without complaint, so two units that
disagree about the table leave the link order deciding which conversions work.

Auto-detection cannot help here — a unit only ever sees its own format strings
— so with no explicit flag every handler is registered and uc80 says so:

```
uc80: warning: separate compilation (--no-whole-program) cannot see the format
strings in the other translation units, so every printf handler is registered;
pass an explicit --printf to select a smaller set
```

Passing a matching `--printf` to each unit silences it and shrinks the binary.

### Inline Assembly

uc80 supports basic `asm("...")`, spelled `asm` or `__asm__`, with or without
`volatile`. The text of the template goes into the output assembly unchanged,
at the point where it is written. Write it in MACRO-80 syntax, because um80
assembles it.

```c
int marker;

/* File scope: hand-written data and code. */
asm("\tPUBLIC\t_table\n"
    "_table:\tdw\t11,22,33\n");

void store(void) {
    /* Inside a function. */
    asm("ld hl,1234\n\tld (_marker),hl");
}
```

Rules for the assembly text:

- A C object is reached through its assembler symbol. A global `x` is `_x`.
- IX is the frame pointer. Preserve it. SP is free if you balance it. Every
  other register is free, because uc80 holds no value in a register across a
  statement.
- An inline assembly block is a barrier. The peephole optimizer and the
  assembly dead-code eliminator do not change, move or delete it, and no
  optimization crosses it.
- uc80 emits the block in CSEG. A block that changes the segment should change
  it back, because the compiler-generated code after it expects CSEG.
- The C grammar makes `asm` a block item, not a statement, so an unbraced
  `if (c) asm("nop");` is a syntax error. Write `if (c) { asm("nop"); }`.

Extended asm, which has an operand or clobber list, for example
`asm("ld hl,%0" : : "r"(x))`, is not supported. `asm goto` is not supported.
Both stop the compilation with an error message. Pass values through global
variables instead. `#asm`/`#endasm` and `__naked` are also not supported.

## Binary Size

uc80 produces the smallest known binaries for Z80/CP/M among current compilers.

Tested against [z88dk](https://z88dk.org/) (SDCC backend, `-SO3 --max-allocs-per-node10000`)
on the [Fujitsu compiler-test-suite](https://github.com/fujitsu/compiler-test-suite):

| Metric | Result |
|--------|--------|
| uc80 smaller | 47/47 tests (100%) |
| Aggregate size ratio | 46% (uc80 is less than half the size) |
| Total uc80 | 170,496 bytes |
| Total z88dk | 369,644 bytes |
| Minimal binary | 128 bytes (vs 5,172 for z88dk) |

Sample sizes (bytes):

| Program | uc80 | z88dk | Ratio |
|---------|------|-------|-------|
| hello world (puts) | 256 | 5,172 | 5% |
| printf %d | 4,608 | 7,696 | 60% |
| integer math | 5,248 | 7,948 | 66% |
| long arithmetic | 5,632 | 7,793 | 72% |

## Test Results

Tested against multiple external test suites:

| Suite | Pass Rate | Notes |
|-------|-----------|-------|
| [c-testsuite](https://github.com/c-testsuite/c-testsuite) | 220/220 | full pass |
| c-testsuite `--int=32` | 219/220 | 00200 (long-long shift) overflows 64K TPA |
| c-testsuite `--int=32 --long=64` | 218/220 | same as above + marginal timeout |
| [Fujitsu compiler-test-suite](https://github.com/fujitsu/compiler-test-suite) 0003 | 371/374 | |
| Fujitsu 0010 | 58/75 | 9 int16, 1 float, 2 timeout |
| Fujitsu 0011 | 287/335 | 14 int16, 5 large struct |
| Fujitsu 0012 | 4/9 | 4 int16/long long, 1 static DCE |
| [SDCC regression tests](https://sourceforge.net/projects/sdcc/) | 514/523 | 3 fail, 4 sdcc ext, 2 multi-file link |

Remaining non-passing tests are environmental, not codegen bugs:
- **sdcc ext**: SDCC-specific extensions (`__asm`, `#pragma save/restore`)
- **multi-file**: tests that require separate compilation units
- **float precision**: ACOSF/TANF near asymptotes (single-precision IEEE 754 limit)
- **malloc OOM**: SDCC test asserts `malloc(2000) == NULL`; we have plenty of TPA
- **00200**: 67KB binary exceeds 64KB CP/M TPA

## Features

- ANSI C (C11/C23) with most standard features
- Z80 code generation with peephole optimization
- IEEE 754 single-precision float
- Configurable integer sizes (`--int=16|32`, `--long=32|64`); default is 16-bit int, 32-bit long, 64-bit long long
- Structs, unions, bitfields, enums
- Full preprocessor (#include, #define, #if, #pragma, etc.)
- Modular library with selective linking
- Whole-program optimization
- Basic inline assembly (`asm("...")`), emitted verbatim and never optimized
- CP/M console line endings (CR LF by default, `--no-crlf` for raw LF)
- CP/M target with embedded crt0
- Libraries ship in the wheel and are discoverable (`uc80 --print-lib-dir`, `uc80.lib_dir()`)

## Related Projects

- [80un](https://github.com/avwohl/80un) - Unpacker for the CP/M archive and compression formats LBR, ARC, squeeze, crunch, and CrLZH.
- [cpmdroid](https://github.com/avwohl/cpmdroid) - Z80/CP/M emulator for Android phones and tablets. It emulates the RomWBW HBIOS interface and a VT100 terminal.
- [cpmemu](https://github.com/avwohl/cpmemu) - Z80/CP/M emulator for Linux and Windows, with Z80 and 8080 CPU cores. It translates the BDOS and BIOS calls of CP/M 2.2 programs to the host file system.
- [ioscpm](https://github.com/avwohl/ioscpm) - Z80/CP/M emulator for iOS and macOS. It emulates the RomWBW HBIOS interface and runs CP/M 2.2 and CP/M 3.
- [learn-ada-z80](https://github.com/avwohl/learn-ada-z80) - Collection of more than 90 Ada example programs for uada80, the Ada compiler for the Z80 processor and CP/M.
- [mbasic](https://github.com/avwohl/mbasic) - Python interpreter for MBASIC 5.21, the Microsoft BASIC-80 for CP/M. Two compiler backends compile the programs to CP/M .COM files or to JavaScript.
- [mbasic2025](https://github.com/avwohl/mbasic2025) - Reconstruction of the lost source code of MBASIC 5.21, the Microsoft BASIC-80 for CP/M. The MACRO-80 source code assembles to a binary that matches mbasic.com byte for byte.
- [mbasicc](https://github.com/avwohl/mbasicc) - C++17 interpreter for MBASIC 5.21, the Microsoft BASIC-80 for CP/M. It runs on Linux and macOS.
- [mbasicc_web](https://github.com/avwohl/mbasicc_web) - Web browser interpreter for MBASIC 5.21, the Microsoft BASIC-80 for CP/M. Emscripten compiles the mbasicc interpreter to WebAssembly.
- [mpm2](https://github.com/avwohl/mpm2) - Z80 emulator for MP/M II, the multi-user CP/M operating system. Users connect over SSH, and SFTP clients transfer files.
- [romwbw_emu](https://github.com/avwohl/romwbw_emu) - Hardware-level Z80/CP/M emulator for Linux and macOS. It emulates the RomWBW HBIOS interface and switches banks in 512 KB of ROM and 512 KB of RAM.
- [scelbal](https://github.com/avwohl/scelbal) - Floating-point BASIC interpreter for the 8080 processor and CP/M. A translator converts the original 8008 source code to 8080 source code.
- [uada80](https://github.com/avwohl/uada80) - Ada compiler for the Z80 processor and CP/M 2.2. It compiles a subset of Ada 2012 to CP/M .COM files.
- [uc386](https://github.com/avwohl/uc386) - C23 compiler for the i386 processor and MS-DOS. This sibling backend shares the uc_core frontend.
- [uc_core](https://github.com/avwohl/uc_core) - Shared C23 frontend and AST optimizer for the uc80 and uc386 compilers.
- [ucow](https://github.com/avwohl/ucow) - Cowgol compiler for the Z80 processor and CP/M. It runs on Linux in Python.
- [um80_and_friends](https://github.com/avwohl/um80_and_friends) - Linux toolchain that is compatible with Microsoft MACRO-80. It has an assembler, a linker, a librarian, and a disassembler.
- [upeepz80](https://github.com/avwohl/upeepz80) - Peephole optimizer for Z80 compilers. It shortens jumps to jr, builds djnz loops, and removes dead stores.
- [uplm80](https://github.com/avwohl/uplm80) - PL/M-80 compiler for the Z80 processor and CP/M. It writes Intel 8080 and Zilog Z80 assembly language.
- [uplox](https://github.com/avwohl/uplox) - LR(1) and GLR parser generator. It writes the lexer and parser tables for the C23 frontend of uc_core from `examples/c23.uplox`.
- [z80cpmw](https://github.com/avwohl/z80cpmw) - Z80/CP/M emulator for Windows. It emulates the RomWBW HBIOS interface and boots CP/M from disk images.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
