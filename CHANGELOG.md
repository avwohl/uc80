# Changelog

All notable changes to uc80 are recorded here. Versions follow
`major.minor.patch`: the minor number is raised when a release changes the
observable behaviour of a program uc80 compiles.

## Unreleased

A correctness release. Two of the fixes are for regressions in 0.6.0, so
0.6.0 is superseded rather than merely improved on. Rebuild the libraries
after upgrading:

```bash
uc80 --build-libs
```

Everything here came out of an independent verification pass over the seven
bugs the mbasic project reported, carried out by verifiers working from the
reports rather than from the fixes. It found more than it confirmed.

### Fixed

- **`printf("%f")` was wrong for every `|value| >= 65536`,** and usually did
  not even emit digits: `1000000.0` printed as `3906.00///` and `-123456.0`
  emitted a raw backslash. The integer part was built in `H:L:E` and printed
  with the 16-bit `_prt_dec`, which drops `E`; above 2^24 a second path
  shifted a 16-bit `HL` and printed `0`. `sprintf` in the same libc had
  always been right, so one program could print one value two ways and get
  two answers. Values beyond 2^31 -- where an `int32` no longer holds the
  integer part -- now print exactly, by decimal doubling of the mantissa, so
  `3.4e38` gives all 39 digits. Not a regression; it predates the %e/%g work.

- **The whole printf family shares one formatter.** `sprintf`, `snprintf`,
  `fprintf`, `vprintf`, `vfprintf` and `vsprintf` each carried their own
  conversion chain, so 0.6.0's `%e` and `%g` reached `printf` and nothing
  else -- they printed an empty field and, because a table miss does not
  advance the vararg offset, desynced every later conversion in the call.
  `fprintf` and `snprintf` had no `%f` at all. A program calling both
  `printf` and `sprintf` shrinks from 7040 to 5248 bytes, the duplicate
  formatters having been linked in alongside the real one.

- **A comma expression yielding `long long` carried a stale value.**
  Regression in 0.6.0: once the comma operator had a result type,
  `(f(), 42LL)` reported itself as 64-bit, and three places took that to mean
  the value was in `__acc64`. A 64-bit literal has a 16-bit fast path, so it
  was not, and the expression evaluated to whatever the accumulator last
  held. Affected initialisers, every condition (`if`/`while`/`do`/`for`/
  `&&`/`||`/`?:`) and variadic arguments.

- **A struct-valued comma produced garbage** and, in argument position,
  dropped the left operand's side effect entirely. Not a regression -- an
  unfixed gap of the same kind.

- **Under `--no-whole-program`, link order decided which printf conversions
  worked.** Regression in 0.6.0: the per-specifier float filter made each
  unit emit a different `PUBLIC __printf_format_table`, and L80 keeps the
  first definition. A unit calling no `printf` emitted an *empty* table, so
  linking it first disabled every conversion in the program. The table is now
  emitted by the unit defining `main()` -- exactly one per link -- and a unit
  that prints conversions that unit does not is told to pass an explicit
  `--printf`. Whole-program mode is byte-identical.

- **An array bound spelled with an enum constant was ignored** when
  compositing `extern int a[N];` with `int a[];`, giving a zero-byte object
  that the next global was written through, and skipping the C 6.7.6.2p6
  conflict check. Enum constants are now registered before declarations are
  composited.

- **Adjacent string literals initialised nothing.** `char s[] = "ab" "cd";`
  emitted correctly sized, entirely zero storage; inside a struct initialiser
  the pieces were spread across the following members, so a `char *` member
  got a string's bytes where its pointer belonged.

- **An appended `.mac` is fenced from the optimizers,** as inline `asm()`
  already was. The peephole fused `LD A,(HL)` + `LD C,A` with `A` still live,
  deleted a label caught between the fused pair (turning a working helper
  into an `Undefined symbol` from um80), and discarded `ORG` and `ASEG`.

- **`printf("%f", -0.0)` printed `0.000000`** -- the zero test masked the sign
  bit off before anything checked it -- and **`printf("%.0f", 2.0)` printed
  `2.`**, a trailing point with no digits after it. `%e` and `%g` already got
  both right.

- The printf family returns the number of bytes it actually wrote.
  `_prt_dec32` wrote straight to `__conout`, so `printf("[%ld]\n", 1234567L)`
  returned 3 for the ten bytes it produced.

### Known limitations

Recorded so they are not rediscovered; `todo.txt` has the full list.

- Field width and the `-`, `+` and ` ` flags do nothing on a float
  conversion. The format parser discards those three flags for every
  conversion, so they have never been implemented.
- `%.*f` / `%*f`, and `%lf` / `%le` / `%lg` under auto-detection or
  `--printf float`, are not registered: the conversion is echoed verbatim and
  every later one in that call reads the wrong argument.
- `__sret_buf` is defined by both the embedded runtime and `runtime.lib`, so
  the documented link line can define it twice; L80 keeps the first.

## 0.6.0

A behaviour-changing release. Rebuild the libraries after upgrading:

```bash
uc80 --build-libs
```

### Changed

- **Console output now ends a line with CR LF.** A bare LF is "cursor down"
  only on a real CP/M terminal, so output written with a bare LF stair-steps
  down the screen. CP/M translates nothing below the program, z88dk builds
  its CP/M library the same way, and C23 7.23.2 allows a text stream to alter
  characters on output to match the host convention.

  Compile with `--no-crlf`, or assign to `__crlf_mode` from `<stdio.h>`, to
  get the old raw LF. `--no-crlf` only takes effect on the translation unit
  that defines `main()`.

  This also removes an inconsistency that predates the change: `puts()`,
  `assert()`, `perror()` and `abort()` already emitted CR LF while
  `printf`, `putchar`, `fputs` and `fprintf` emitted a bare LF, so one
  program printed two different line terminators depending on which stdio
  call it used. All of them now follow one policy. Under `--no-crlf` that
  means `puts()`, `assert()` and `perror()` emit a bare LF, which they did
  not before; `abort()`'s message is still CR LF.

  Anything that compares a uc80 program's console output byte for byte will
  now see the CR. Python harnesses that read the output with `text=True`,
  including all four test drivers in this repository, are unaffected.

  File streams are not translated, with or without the flag: `fwrite` and
  `fputc` to a `FILE *` write the bytes they are given, in both `"w"` and
  `"wb"` mode.

- `--printf` and `--scanf` feature sets are closed over the features they
  imply, so `--printf float` no longer silently drops `%d`. An existing
  build script that passes a narrow `--printf` will link more handlers and
  produce a larger binary than it did in 0.5.0.

- An unknown printf conversion is echoed verbatim instead of vanishing. It
  still cannot resync the varargs, so the rest of that call is still
  misaligned, but the failure is now visible and is diagnosed at compile
  time when the format string is a literal.

- The package no longer declares `py.typed`. It never shipped the file, so
  the PEP 561 claim was false either way; uc80 carries inline annotations
  but has never been type checked, so the claim is dropped rather than
  made true.

### Added

- Basic inline assembly: `asm("...")`, spelled `asm` or `__asm__`, with or
  without `volatile`, at file scope and in a function body. The block is a
  barrier that no optimizer crosses. Extended asm and `asm goto` are
  rejected with an error instead of being silently dropped.
- The `%e`, `%E`, `%g` and `%G` printf conversions.
- `--no-crlf`, and `extern char __crlf_mode` in `<stdio.h>`.
- **Wheels now ship `libc.lib`, `runtime.lib` and `crt0.rel`.** `pip install
  uc80` previously installed a compiler that could not link anything: those
  three are assembler output, are gitignored, and were produced only by a
  manual step, so a wheel built from a clean checkout never contained them.
  They are assembled at wheel-build time, which adds `um80` to
  `[build-system].requires` and about 45 KB and 40 s to a release build. They
  are still not committed to git — `libc.lib` is not byte reproducible, so a
  committed copy would churn on every rebuild and could drift out of step
  with `lc/*.mac` unnoticed.
- `uc80 --print-lib-dir` prints the library directory on one line and exits
  0 without needing an input file, for `LIB=$(uc80 --print-lib-dir)`.
- `uc80 --build-libs` assembles the three link artifacts on demand.
- `uc80.lib_dir()` and `uc80.lib_file(name)` — the same answer from Python,
  and the supported way for another tool to locate the libraries. Consumers
  that guessed the path could land on a stale tree and link against it with
  no diagnostic from anything.
- `UC80_LIB_DIR` overrides the library directory, applied per file so a
  partial override cannot shadow the compiler's own version-locked inputs.

### Fixed

- The comma operator's result type is inferred from its right operand. A
  `(a, b)` yielding `double`, `float`, `long` or `long long` was compiled as
  a 16-bit signed `int`. Both operands are also evaluated exactly once.
- Merging an `extern` array declaration with its definition no longer
  crashes the compiler, and two different known sizes are now reported as
  conflicting types instead of being silently reconciled.
- Assembly appended from a `.mac` input, and the embedded runtime, are
  spliced in ahead of the BSS/COMMON block instead of after it, where crt0
  zeroed them.
- Several literal values that were compared without being decoded, which
  affected designated initializers and `_Bool b = 0.0`.

## 0.5.0 and earlier

Not recorded here; see `git log`.
