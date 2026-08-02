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
uc80 hello.c -o hello.mac
um80 hello.mac -o hello.rel
ul80 hello.rel src/uc80/lib/libc.lib src/uc80/lib/runtime.lib -o hello.com
```

## Best Optimization (Whole-Program)

For smallest binaries, compile all `.c` files in a single invocation.
This enables whole-program optimizations that are not possible when
compiling files separately:

```bash
# Single-file (best optimization - all optimizations enabled by default)
uc80 main.c utils.c -o program.mac
um80 program.mac -o program.rel
ul80 program.rel src/uc80/lib/libc.lib src/uc80/lib/runtime.lib -o program.com
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
and links only the needed handlers. You can also control this explicitly:

```bash
# Command line
uc80 program.c --printf int           # %d %u %x %o %s %c %p only
uc80 program.c --printf int --printf long  # add %ld %lu %lx
uc80 program.c --printf float         # add %f

# In source code
#pragma printf int
#pragma printf long
```

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

## Binary Size

uc80 produces the smallest known binaries for Z80/CP/M among current compilers.

Tested against [z88dk](https://z88dk.org/) (SDCC backend, `-SO3 --max-allocs-per-node10000`)
on the [Fujitsu compiler-test-suite](https://github.com/AcademySoftwareFoundation/CompilerTestSuite):

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
| [c-testsuite](https://github.com/nicklockwood/c-testsuite) | 220/220 | full pass |
| c-testsuite `--int=32` | 219/220 | 00200 (long-long shift) overflows 64K TPA |
| c-testsuite `--int=32 --long=64` | 218/220 | same as above + marginal timeout |
| [Fujitsu compiler-test-suite](https://github.com/AcademySoftwareFoundation/CompilerTestSuite) 0003 | 371/374 | |
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
- CP/M target with embedded crt0

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
