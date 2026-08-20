# Changelog

All notable changes to uc80 are recorded here. Versions follow
`major.minor.patch`: the minor number is raised when a release changes the
observable behaviour of a program uc80 compiles.

## 0.7.0

An ABI change, and a correctness release around it. A function returning a
struct or union wider than HL now takes a hidden destination pointer from
its caller, so **every translation unit of a program must be recompiled
together**, and the libraries rebuilt -- in a git checkout, that is:

```bash
uc80 --build-libs
```

A wheel ships `libc.lib`, `runtime.lib` and `crt0.rel` already built, so
`pip install --upgrade uc80` is the whole of it there.

Two of the fixes are for regressions in 0.6.0, so 0.6.0 is superseded
rather than merely improved on.

Two passes are recorded here. The first was an independent verification of
the seven bugs the mbasic project reported, carried out by verifiers working
from the reports rather than from the fixes; it found more than it
confirmed. The second started from the 256-byte limit on returning an
aggregate and went looking for what else was wrong nearby.

### Changed

- **A function returning an aggregate writes it where the caller says.**
  The result used to go into `__sret_buf`, one static buffer the whole
  program shared, whose address came back in HL. That capped a returned
  aggregate at 256 bytes -- uc80 refused anything larger, which is the entry
  below about writing past the buffer -- and it meant only one result could
  be in flight: `arr[g(1).a] = f(90);` computes the destination address after `f`
  returns, so `g` overwrote `f`'s bytes on the way and the assignment stored
  `g`'s result, silently.

  The caller now passes the address to write to as a hidden first argument,
  pushed last so it lands at `IX+4`, with the declared parameters starting
  at `IX+6`. The callee copies its result there and still leaves that
  address in HL, so nothing that reads a result changed. The destination is
  a slot in the caller's own frame, one per call site, which is what removes
  the size limit and separates one call's result from the next.

  This is an ABI change: **recompile every translation unit of a program
  together**, and rebuild the libraries. An aggregate of two bytes or fewer
  still comes back in HL and is unaffected. `__sret_buf` remains only for a
  call whose return type could not be resolved when the slots were laid out,
  so an ordinary program no longer reserves its 256 bytes.

- **Separately compiled units no longer share one another's storage.**
  Uninitialized statics, and the `??AUTO` region holding a shared-storage
  function's locals, went into the blank COMMON block. That is what a common
  block is for -- L80 puts every module's blank COMMON at the same address --
  so with two uc80 modules in a link, one unit's globals sat on top of the
  other's and one unit's automatic storage sat on top of the other's live
  locals. `--no-whole-program` now puts that storage in DSEG, which the
  linker gives each module its own space for; the bytes cost their size in
  the image, where they are already zero. Whole-program output is unchanged.

- **`%F` prints `INF` and `NAN`** (C17 7.21.6.1p8), through its own alias
  entry point rather than sharing `%f`'s.

- **A frame that does not match its body stops the compile.** The prologue
  reserves automatic storage before a line of the body is generated, and
  the body then hands out the slots. When the two disagree the extra locals
  sit below SP, or on the return slots, or -- with nothing reserved at all
  -- at `(IX+0)`, on top of the frame pointer the prologue had just pushed,
  which hangs the program. Every symptom of that is silent, so
  `gen_function` now compares what the body took against what it reserved
  and refuses to emit the function. Both known causes are fixed below: a
  tag defined in the body, and a typedef that leaked out of the function
  that declared it.

- **Dependency floors move.** uc80 requires `uc_core >= 0.4.1` (was 0.4.0)
  and `uplox >= 3.3.0` (was 3.2.0); `pip install --upgrade uc80` pulls
  both. Building a wheel from source needs `um80 >= 0.3.46` (was 0.3.43,
  added in 0.6.0) and `setuptools >= 64.0`, because the wheel assembles the
  CP/M libraries with um80. 0.3.46 is also the ul80 that reports an error
  recorded during a link that still succeeded, which is what makes a
  duplicate definition such as the `__sret_buf` below visible at all.

### Fixed

- **A struct or union tag defined inside a function body was sized as
  zero.** Frame sizing runs before the body is generated and the tag was
  not registered yet, so `struct L { int p, q, r, s; }; struct L x;` gave
  `x` no storage and the next local was laid on top of it. A typedef or tag
  declared in one function also stayed registered for the next, which let
  frame sizing and the shared-storage plan -- they run at different times --
  size the same declaration two different ways.

- **A call in a declaration inside a statement expression was invisible to
  the call graph,** so `({ S q = f(4); q; })` as the only use of `f` had it
  removed as dead and the link failed on the call still emitted for it.

- **A name declared in a block stayed visible after the block closed.**
  C23 6.2.1p4 ends its scope at the brace; only the extern set was being
  restored. With a different type inside -- a block-local function pointer,
  say -- every later call through the outer name was emitted with the inner
  one's calling sequence.

- **A function reached only through a pointer shared storage with its
  caller.** Which functions can be on the stack at once was answered from
  the direct call graph alone, so an address-taken function looked
  unrelated to whoever called it indirectly and was packed at overlapping
  offsets in ??AUTO: two calls through the same pointer returned the second
  result for both.

- **A local further than 128 bytes from the frame pointer was addressed
  with the low byte of its offset.** `(IX+d)` holds a signed byte and um80
  assembles a larger operand without complaint, so the access landed in the
  caller's frame -- and once a frame passed 256 bytes, two locals aliased
  each other. Only functions with a real stack frame were affected
  (recursive, variadic, or built with `--no-shared-storage`); shared
  automatic storage is addressed absolutely. Scalar loads and stores now
  walk IX to the slot when the displacement cannot reach, and anywhere that
  still cannot, the compiler says so instead of emitting the wrong access.

- **An expression whose value is a struct was stored, not copied.** A call
  returning a struct, a member read, a cast, a conditional: each designates
  bytes, and evaluating one yields their address. Wherever an initializer or
  an assignment expected a scalar it stored that address into the first
  field and left the rest zero, so `struct S a[] = { mk(7), mk(9) };` filled
  the array with two pointers. The same shape reached a struct member
  (`struct O o = { mk(3), 5 };`), a union's first member, a designated
  element, and the right-hand side of an assignment. A conditional was
  worse: taking an address had no case for one and no fallback, so nothing
  at all was emitted and `(c ? q : r).b` read through whatever HL held. A
  chained assignment (`a = b = c;`) and a statement expression
  (`s = ({ ...; obj; });`) emitted nothing for the same reason, which lost
  the inner assignment and the statements' side effects outright. A shape
  that still has no case is now a compile error rather than silence.

- **A local declared anywhere the frame sizer did not look got no storage.**
  Sizing walked a hand-written list of statement kinds, so `label: { int
  a[8]; }` and `default: { int b[8]; }` reserved nothing and the array sat
  below SP, where the next `push` wrote over it. It also sized each
  declarator from its declared type alone, so `char s[] = "hello world"`
  counted as zero bytes. Both were silent, and a function eligible for
  shared storage escaped the second half, so it showed only in recursive and
  variadic functions.

- **An unnamed parameter was skipped rather than stepped over,** so in
  `int f(int, int x)` every parameter after the unnamed one read the
  argument before it -- `x` returned the first argument. Abstract
  declarators (`char *`) did the same.

- **A local array ignored index designators:** `int a[] = {[9]=91, [0]=2}`
  filled positionally, putting 91 in `a[0]`. The same declaration at file
  scope was always right, so the two storage durations disagreed about one
  initializer.

- **`--no-shared-storage` produced unlinkable output.** The flag gated the
  allocation pass but not the decision to use it, so functions addressed
  `??AUTO+n` and the symbol was never defined.

- **`INFINITY` was not an infinity and `NAN` was not a NaN.** math.h defined
  them as `((double)0x7FFFFFFF)` and `((double)0)`, so `isinf(INFINITY)` was
  false and `NAN` compared equal to zero. `HUGE_VAL` -- what the math
  functions return on overflow -- was that same `0x7FFFFFFF`, a finite
  value of about 2.1e9 that an ordinary result can exceed. All three are
  now folded from the GCC builtins, which keeps them constant expressions
  usable in a static initializer, and `HUGE_VALF` and `HUGE_VALL` are
  defined alongside them. Reaching them needed two more fixes: a literal
  too large for binary32 stopped the compiler with "internal error: float
  too large to pack with f format", and C23 6.4.4p2 makes a constant's
  representability a constraint, so `1e39f` warns and becomes an infinity
  now rather than crashing; and `printf("%f")` decided "infinity" from the
  exponent alone, after a rounding pass that does not preserve the mantissa
  telling an infinity from a NaN -- so every NaN printed as `inf`.

- **A printf under a `default:` label or inside an initializer list was
  invisible to auto-detection,** so the unit reported that it called no
  printf, its dispatch table collapsed to the sentinel, and the conversion
  came out verbatim at run time. No warning either: the compile-time check
  reads the same scan. The scan now walks the AST rather than a list of
  statement kinds it knows.

- **`%*d` and `%.*f` are diagnosed.** The runtime parser implements
  neither: it echoes the specification and never consumes the int argument,
  so every later conversion in the call reads the wrong one. The
  compile-time scan skipped the star without recording it.

- **A relative `UC80_LIB_DIR` came back as written,** so a captured
  `LIB=$(uc80 --print-lib-dir)` named a different directory -- or nothing --
  when used from anywhere else, and the link went to the packaged tree with
  nothing said.

- **Inline assembly kept a colon-less label named like a mnemonic.**
  `_format_asm_block` indented any column-0 word that is a mnemonic, but
  `SET`, `AND`, `OUT`, `PAGE` and `NAME` are legal symbol names and a
  MACRO-80 label written without a colon must be in column 0, so `SET equ 5`
  became a `SET` instruction. What follows the word decides now.

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
  `--printf`. Auto-detection cannot be trusted across a separate build
  either, since a unit sees only its own format strings: a `main()`
  compiled under `--no-whole-program` without an explicit `--printf` now
  registers every handler and says so, so such a build links a larger
  binary than it did until it passes the flag. Whole-program mode is
  byte-identical.

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

- `%ld` and `%lu` count the bytes they write. `_prt_dec32` wrote straight to
  `__conout`, so `printf("[%ld]\n", 1234567L)` returned 3 for the ten bytes
  it produced; it goes through `_printf_putc` now, the same choke point as
  the rest of printf land, so the count is right and CR LF translation is
  unchanged. `_prt_dec64` and `_prt_hex` still write to `__conout`, so
  `%lld`, `%llu` and `%llx` remain uncounted -- recorded under Known
  limitations below.

- **The documented link line defined `__sret_buf` twice.** The struct-return
  buffer lived in the same runtime module as the 32-bit arithmetic helpers,
  so it had two homes: the runtime the compiler embeds, and `rt_arith32` in
  `runtime.lib`. A program that returned a struct embedded the buffer, and
  any later pull of `rt_arith32` -- printing a `long`, say, which reaches the
  32-bit helpers through libc rather than through the compiler -- brought a
  second definition with it. L80 keeps the first and links on, so this was
  silent until ul80 0.3.46 started reporting a recorded error; the second
  buffer was still allocated, costing 256 bytes of every affected binary.
  `__sret_buf` now has its own module, `rt_sret.mac`, which the linker pulls
  only when the symbol is genuinely unresolved.

- **Returning a struct larger than 256 bytes wrote past `__sret_buf`.** The
  return copy was an `ldir` of the struct's full size into a `DS 256` buffer
  with nothing bounding it, so a 402-byte struct put 146 bytes over whatever
  storage followed. It stayed quiet because the value still reads back
  correctly -- the clobbered bytes are not the ones the caller looks at --
  and how far it reached scaled with the struct. The first fix was to refuse
  the return and name both sizes; the ABI change above then removed the
  limit itself, so ordinary code is no longer refused. The refusal survives
  only on the `__sret_buf` fallback path -- a call whose return type could
  not be resolved when the slots were laid out -- where the 256 bytes still
  bound the copy.

- **Compiling the same source twice produced different assembly.** The
  embedded runtime functions came out in set-iteration order, which Python
  varies per process. Same functions, same binary, different file.

### Known limitations

Recorded so they are not rediscovered; `todo.txt` has the full list.

- Field width and the `-`, `+` and ` ` flags do nothing on a float
  conversion. The format parser discards those three flags for every
  conversion, so they have never been implemented.
- `%.*f` / `%*f`, and `%lf` / `%le` / `%lg` under auto-detection or
  `--printf float`, are not registered: the conversion is echoed verbatim and
  every later one in that call reads the wrong argument. The `*` forms are
  now diagnosed at compile time where the format string is a literal.
- `%lld`, `%llu` and `%llx` write their digits through `__conout` rather
  than through the sink the shared formatter hands each entry point, so
  `sprintf`, `snprintf` and `vsprintf` print those digits on the console and
  leave the buffer holding the surrounding text alone -- `sprintf(b,
  "<%lld>", 42LL)` puts `<>` in `b` and `42` on the screen -- and `printf`
  does not count them in its return value. Against 0.5.0 this is a
  regression for the buffer-writing calls, whose separate formatter wrote
  the digits into the buffer; `%ld` is the way round it until `_prt_dec64`
  and `_prt_hex` go through `_printf_putc`.
- Float comparison does not implement NaN's unordered result, so `x == x` is
  true for a NaN. No arithmetic in the runtime produces one; only the `NAN`
  macro and a bit pattern read back as a float can.

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
- The `%e`, `%E`, `%g` and `%G` printf conversions, and `%F`, which
  0.5.0 dropped along with every other conversion it had no handler for.
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

Only the 2026-04-30 sweep is recorded, from notes kept in `todo.txt` at the
time; for anything else see `git log`.

That sweep added `--int` / `--long` / `--long-long` switches so a test
making an assumption about a type's width could be compiled under it, and
then fixed what running the suites under every width uncovered:

- 64-bit `++` / `--` updated only the low 16 bits.
- A condition test on a `long long` expression ORed only the low 32 bits.
- An implicitly declared external function got no `EXTRN`, which is what had
  stuck many gcc-torture tests that call `abort()` / `exit()` without a
  prototype.
- `__builtin_memcpy` / `memset` / `abort` / `trap` / `unreachable` /
  `malloc` and the rest now rewrite to their libc equivalents.
- Source bytes that are not UTF-8 fall back to latin-1.
- `__SIZE_TYPE__` / `__PTRDIFF_TYPE__` / `__WCHAR_TYPE__` are predefined
  (they live in uc_core's `type_config` now).
- A struct-array element initialized from a compound literal --
  `struct Wrap arr[] = { (struct Wrap){fn}, fn };` -- stored the address of
  the materialized literal in the first member instead of copying the bytes,
  which showed as an infinite loop in c-testsuite 00216 under `--long=64`.
