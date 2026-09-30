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

`uc80 --print-lib-dir` prints the library directory. In a git checkout, run
`uc80 --build-libs` once first. Details are in [docs/libraries.md](https://github.com/avwohl/uc80/blob/main/docs/libraries.md).

For the smallest binaries, compile all `.c` files in one invocation, so uc80
can optimize the whole program. Separate compilation needs `--no-whole-program`
and `crt0.rel`; see [docs/compiler_options.md](https://github.com/avwohl/uc80/blob/main/docs/compiler_options.md).

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

## Binary Size and Tests

uc80 produces the smallest known binaries for Z80/CP/M among current compilers.
On the Fujitsu compiler-test-suite, uc80 output is less than half the size of
z88dk output. uc80 passes all 220 c-testsuite tests. Numbers are in
[docs/benchmarks.md](https://github.com/avwohl/uc80/blob/main/docs/benchmarks.md).

## Documentation

- [Finding the libraries](https://github.com/avwohl/uc80/blob/main/docs/libraries.md) - `--print-lib-dir`, `--build-libs`, `UC80_LIB_DIR`
- [Compiler options](https://github.com/avwohl/uc80/blob/main/docs/compiler_options.md) - whole-program optimization, `--printf`, CR LF line endings, `--int`/`--long`, separate compilation
- [Inline assembly](https://github.com/avwohl/uc80/blob/main/docs/inline_assembly.md) - `asm("...")` rules and limits
- [Binary size and test results](https://github.com/avwohl/uc80/blob/main/docs/benchmarks.md) - comparison with z88dk and external test suite pass rates
- [C standard compliance](https://github.com/avwohl/uc80/blob/main/docs/ANSI_C_COMPLIANCE.md) - implemented and missing C features
- [Float status](https://github.com/avwohl/uc80/blob/main/docs/FLOAT_STATUS.md) - IEEE 754 single-precision support
- [CHANGELOG](https://github.com/avwohl/uc80/blob/main/CHANGELOG.md) - release history

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

GPL-3.0-or-later. See [LICENSE](https://github.com/avwohl/uc80/blob/main/LICENSE).
