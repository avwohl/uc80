# Changelog

All notable changes to uc80 are recorded here. Versions follow
`major.minor.patch`: the minor number is raised when a release changes the
observable behaviour of a program uc80 compiles.

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
