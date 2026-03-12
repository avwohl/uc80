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
ul80 hello.rel lib/libc.lib lib/runtime.lib -o hello.com
```

## Best Optimization (Whole-Program)

For smallest binaries, compile all `.c` files in a single invocation.
This enables whole-program optimizations that are not possible when
compiling files separately:

```bash
# Single-file (best optimization - all optimizations enabled by default)
uc80 main.c utils.c -o program.mac
um80 program.mac -o program.rel
ul80 program.rel lib/libc.lib lib/runtime.lib -o program.com
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
| [c-testsuite](https://github.com/nicklockwood/c-testsuite) | 218/220 | 1 timeout, 1 `_Generic` |
| [Fujitsu compiler-test-suite](https://github.com/AcademySoftwareFoundation/CompilerTestSuite) 0003 | 371/374 | |
| Fujitsu 0010 | 59/75 | 9 int16, 4 bitfield, 1 float, 2 timeout |
| Fujitsu 0011 | 291/334 | 14 int16, 16 bitfield, 5 large struct |
| Fujitsu 0012 | 3/9 | all bitfield |
| [SDCC regression tests](https://sourceforge.net/projects/sdcc/) | 488/523 | 6 sdcc ext, 8 float math, 5 libc |

Most non-passing tests are due to platform differences, not bugs:
- **int16**: Z80 has 16-bit int, tests assume 32-bit
- **bitfield**: Bitfield layout/packing not yet implemented
- **large struct**: Struct-by-value in complex expressions
- **sdcc ext**: SDCC-specific language extensions
- **float math**: Edge cases in transcendental functions

## Features

- ANSI C (C11/C23) with most standard features
- Z80 code generation with peephole optimization
- IEEE 754 single-precision float
- 16-bit int, 32-bit long, 64-bit long long
- Full preprocessor (#include, #define, #if, #pragma, etc.)
- Modular library with selective linking
- Whole-program optimization
- CP/M target with embedded crt0

## Related Projects

| Project | Description | Link |
|---------|-------------|------|
| um80 | MACRO-80 compatible assembler/linker | [github](https://github.com/avwohl/um80_and_friends) / [pypi](https://pypi.org/project/um80/) |
| uplm80 | PL/M-80 compiler for Z80 | [github](https://github.com/avwohl/uplm80) / [pypi](https://pypi.org/project/uplm80/) |
| cpmemu | CP/M emulator for testing | [github](https://github.com/avwohl/cpmemu) |
| upeepz80 | Z80 peephole optimizer | [github](https://github.com/avwohl/upeepz80) / [pypi](https://pypi.org/project/upeepz80/) |
| uada80 | Ada compiler for Z80 | [github](https://github.com/avwohl/uada80) / [pypi](https://pypi.org/project/uada80/) |

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
