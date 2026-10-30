# Compiler Options

Back to the [README](../README.md). Library lookup is in [libraries.md](libraries.md); inline assembly is in [inline_assembly.md](inline_assembly.md).

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
  rewrites `printf("...\n")` and `printf("%s\n", s)` to `puts` when the
  result is unused (whole-program mode only; see the Printf Control section)
- **Embedded runtime**: Runtime functions included as source, DCE removes unused ones

### Register calling convention

In whole-program mode, a function that only direct calls reach takes its first two scalar parameters in registers (A or HL, then E or DE). The caller pushes no word for these parameters, and a small function needs no IX frame. `--no-register-cc` passes every argument on the stack. [calling_convention.md](calling_convention.md) lists the rules.

In the same mode, up to two scalar locals of a function live in BC or DE instead of memory. One pointer local lives in IY, and a field access through the pointer is `(IY+d)`. `--no-register-locals` keeps every local in memory. [register_locals.md](register_locals.md) lists the rules.

The pass computes a repeated element address (`a[i]` read and written, or `a[i]` and `a[i + 1]`) one time. The pass keeps the address in a new pointer local. `--no-addr-cse` computes each address again. The statement `a[i] = b[j] = 1;` makes one constant store for each target. `--no-chain-store` keeps the chain. [addr_cse.md](addr_cse.md) lists the rules and the debug switches.

The dead-local pass removes a local that no expression reads, with all its stores, and a store that a later store of the same list overwrites. A right side with an effect (a call, `++`, a volatile access) stays as a statement. `--no-dead-store` keeps every store. [dead_store.md](dead_store.md) lists the rules and the debug switches.

The inline block-operation pass changes a call of `memset` or `memcpy` with a constant size (1 to 65535) into `LDIR` or `LDI` code, when the program does not use the result and no argument is volatile. `--no-blockops` keeps the library calls. [blockops.md](blockops.md) lists the rules and the switches.

Three switches belong to family I step 1. `--no-discard-comma` computes the right operand of a comma expression for its value, also when no code uses the value. `--no-peep-pairs` keeps the copies between BC, DE and HL that the pair rules remove. [peephole_pairs.md](peephole_pairs.md) lists the rules. `--no-slot-fold` keeps a local in memory when the code reaches the local with `ld HL,slot` and an operation on `(HL)`, and treats each call as a call that returns. [register_locals.md](register_locals.md) describes both changes.

Three switches belong to family I step 2. `--no-licm` computes a loop-invariant integer expression in each pass of the loop ([licm.md](licm.md)). `--no-range-cmp` uses the signed 16-bit compare for a counter that is never negative ([range_compare.md](range_compare.md)). `--no-narrow-index` computes a 32-bit array index in 32 bits, although only the low 16 bits reach the address ([narrow_index.md](narrow_index.md)).

### Printf Control

In whole-program mode, an expression statement `printf("text\n");` or
`printf("%s\n", s);` becomes `puts("text");` or `puts(s);`. A program with no
other `printf` call then does not link the printf core (a hello program drops
from about 3.9 KB to 256 B). The compiler rewrites only when no other `printf`-family call is left in the
program. The compiler does not rewrite a call when the
result is used, when the literal holds `%` (other than the exact `%s\n`
form), `\0` or a non-ASCII character, when the program defines its own
`printf`, `puts` or `cputs`, or with `--no-whole-program`. A non-empty literal
without a newline, such as `printf("Hello");`, becomes `cputs("Hello");`.
The libc function `cputs` writes a string without a newline.

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

`%n`, `%a`, `%td`, `%lf`, `%llo`, `%llX` and `%hhd` need no `--printf` value: the
compiler links their code when a format string uses them
([printf](printf.md)). `--printf all` links all of them.

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

The design notes for this feature are in [configurable_int_sizes.md](configurable_int_sizes.md).

### ROM target

`--target=rom` (or `-ffreestanding`) builds a Z80 ROM image with no CP/M. The option
`--stack-top EXPR` sets the first value of SP. See [rom.md](rom.md).

### Declarations

`--allow-implicit-functions` changes the error for a call of an undeclared
function to a warning. See [ANSI_C_COMPLIANCE.md](ANSI_C_COMPLIANCE.md).

### Separate Compilation

When compiling files separately for separate linking, use `--no-whole-program`:

```bash
uc80 --no-whole-program module.c -o module.mac
```

Link those with `crt0.rel` first — the compiler only embeds crt0 in
whole-program mode. A module that defines `main` gives a warning that says
this. If you link without `crt0.rel`, the program starts in the first
function of the first module. Then the program can print no output.
`--no-embed-startup` silences the warning when you supply the start-up
code:

```bash
LIB=$(uc80 --print-lib-dir)
ul80 $LIB/crt0.rel module.rel main.rel $LIB/libc.lib $LIB/runtime.lib -o prog.com
```

Each module keeps its zero-start data in one BSS block of its own, and the start-up code clears the block. `--no-module-bss` puts the data in the image (DSEG) as before. See [rom.md](rom.md).

`--no-direct-pointers` keeps every access through a pointer that holds a constant address as an access through the pointer. The default turns the access into a direct access. See [rom_hardware.md](rom_hardware.md).

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

