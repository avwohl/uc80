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

The design notes for this feature are in [configurable_int_sizes.md](configurable_int_sizes.md).

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

