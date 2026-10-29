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
- [Calling convention](https://github.com/avwohl/uc80/blob/main/docs/calling_convention.md) - register and stack convention, which functions qualify, `--no-register-cc`
- [Register locals](https://github.com/avwohl/uc80/blob/main/docs/register_locals.md) - up to two scalar locals of a function live in BC or DE, and one pointer lives in IY, `--no-register-locals`
- [Address CSE](https://github.com/avwohl/uc80/blob/main/docs/addr_cse.md) - the compiler computes a repeated array element address once, `a = b = K` stores K to each target, `--no-addr-cse`, `--no-chain-store`
- [Dead locals](https://github.com/avwohl/uc80/blob/main/docs/dead_store.md) - the compiler removes a local that nothing reads, and a store that a later store overwrites, `--no-dead-store`
- [Inline block operations](https://github.com/avwohl/uc80/blob/main/docs/blockops.md) - `memset` and `memcpy` with a constant size become `LDIR` or `LDI` code, `--no-blockops`
- [Pair peephole rules](https://github.com/avwohl/uc80/blob/main/docs/peephole_pairs.md) - a discarded comma computes no value, copies between BC, DE and HL go away, `--no-discard-comma`, `--no-peep-pairs`
- [Loop-invariant code motion](https://github.com/avwohl/uc80/blob/main/docs/licm.md) - an integer expression that a loop does not change is computed before the loop, `--no-licm`
- [Value ranges](https://github.com/avwohl/uc80/blob/main/docs/range_compare.md) - a counter in 0 to 32767 uses the short unsigned compare, `--no-range-cmp`
- [Index narrowing](https://github.com/avwohl/uc80/blob/main/docs/narrow_index.md) - a 32-bit array index is computed in 16 bits, `--no-narrow-index`
- [Inline assembly](https://github.com/avwohl/uc80/blob/main/docs/inline_assembly.md) - `asm("...")` rules and limits
- [Volatile objects](https://github.com/avwohl/uc80/blob/main/docs/volatile.md) - every volatile access happens as written, and how each pass keeps that
- [Binary size and test results](https://github.com/avwohl/uc80/blob/main/docs/benchmarks.md) - speed and size against 8 compilers, and external test suite pass rates
- [C standard compliance](https://github.com/avwohl/uc80/blob/main/docs/ANSI_C_COMPLIANCE.md) - implemented and missing C features
- [Build a Z80 ROM](https://github.com/avwohl/uc80/blob/main/docs/rom.md) - `--target=rom`, crt0, vectors, `ul80` ROM link, `libc_rom.lib`, limits
- [Interrupt functions and hardware access](https://github.com/avwohl/uc80/blob/main/docs/rom_hardware.md) - `#pragma uc80 interrupt`, `<z80.h>` port I/O, critical sections, `__at`, SDCC `__interrupt` and `__sfr`
- [Neo Geo sound driver example](https://github.com/avwohl/uc80/blob/main/docs/neogeo_driver.md) - NMI command, timer IRQ, YM busy wait and bank select in one C file
- [ROM test runner](https://github.com/avwohl/uc80/blob/main/docs/romrun.md) - run a Z80 ROM, log port I/O and T-states, inject IRQ and NMI
- [Float status](https://github.com/avwohl/uc80/blob/main/docs/FLOAT_STATUS.md) - IEEE 754 single-precision support
- [printf](https://github.com/avwohl/uc80/blob/main/docs/printf.md), [program exit and files](https://github.com/avwohl/uc80/blob/main/docs/exit_and_files.md), [console output](https://github.com/avwohl/uc80/blob/main/docs/console_output.md) - conversions and limits, `atexit`, `fopen` modes, `ftell` and `fseek`, TAB
- [Math library](https://github.com/avwohl/uc80/blob/main/docs/libm.md), [scanf and strtol](https://github.com/avwohl/uc80/blob/main/docs/scanf.md), [library modules in C](https://github.com/avwohl/uc80/blob/main/docs/libc_c_sources.md), [float interpreter](https://github.com/avwohl/uc80/blob/main/docs/libm_vm.md) - accuracy, limits, how the modules are made
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
