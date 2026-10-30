# printf and the other printf functions

Back to the [README](../README.md). The limits are also in
[Known limits of the C library](libraries.md).

`printf`, `fprintf`, `sprintf`, `snprintf`, `vprintf`, `vfprintf`, `vsprintf` and
`vsnprintf` use one engine (`lc_printf_core.mac`). The engine reads the format string and
finds each conversion in a dispatch table. The compiler writes the table next
to `main()`. The table has only the conversions that the format strings of the
program use, so a program links only the handlers that it needs
([compiler options](compiler_options.md)).

## snprintf and vsnprintf

`snprintf(buf, n, fmt, ...)` and `vsnprintf(buf, n, fmt, ap)` have the C99
rules:

- They write at most `n - 1` characters and then a NUL. When `n` is greater
  than 0, the buffer always ends with a NUL.
- When `n` is 0, they write nothing, and `buf` can be a null pointer.
- They return the number of characters that the full output has, without the
  NUL, also when the output was cut. A return value of `n` or more means that
  the output was cut.

The two functions share `lc_snprintf_run.mac`, which has the sink, the NUL and
the count. `vsnprintf` takes the argument `ap` as a `va_list`, but `<stdio.h>`
declares the last parameter as `...`, as it does for `vsprintf`.

The code generator does not scan the format string of a `vsnprintf` call. So a
program that calls `vsnprintf` gets every conversion in the dispatch table, as
for a format that is not a literal. A `--printf` option or a `#pragma printf`
in the program overrides this (`main.py`, `printf_hook.uses_unscanned`).

The count has 16 bits. An output of 65,536 characters or more gives a wrong
return value. Test: `tests/test_stdio_library.py`.

## Conversions

Supported:

- `d i u o x X c s p %`
- `f F e E g G a A`
- `n`
- flags `-` `+` space `#` `0`, a field width, a precision, and `*` for each
- length modifiers `hh h l ll z t L`

Not supported (the engine prints the specification as it is and does not read
the argument): `j` (`%jd`), `q`, `%ls`, `%lc`, `%S`, `%C`.

The rules that are not obvious:

- `double`, `float` and `long double` are the same 32-bit type, so `L` and `l`
  before a float conversion change nothing.
- `ptrdiff_t` and `size_t` are 16 bits, as `int`, so `t` and `z` change nothing.
- `hh` converts the `int` argument to `signed char` (`d i`) or `unsigned char`
  (`u o x X`). It works for the 16-bit `int` only. `h` changes nothing, because
  `short` and `int` are the same size.
- `%p` prints `0x` and the address in lower case hex. A null pointer prints
  `0x0`. C leaves the form of `%p` to the implementation. The flags, the width
  and the precision work as for `%x`.
- `%n` stores the number of characters written so far in the `int` that the
  argument points to.
- `%s` with a null pointer reads the bytes at address 0. C does not define
  this case.

## Float conversions

The float conversions (`lc_prt_float.mac`) do no float arithmetic. A float is
`M * 2^E` with a 24-bit `M`, so its decimal value is exact. The code makes the
exact digits with integer arithmetic. It doubles a decimal digit string `E`
times, or halves the string `-E` times. Then it rounds the digits to the
precision. The rounding is to nearest, and a tie goes to the even digit.

The output is the same as the output of the host `printf` for the same value.
This holds for every precision, for subnormal numbers, for `inf`, `nan` and
`-0.0`. The test `tests/test_printf_float_exact.py` compares 200 random
floats with Python. The audit that made this code compared more than 20,000
generated combinations of format and value with the host `printf`.

Cost:

- A `printf("%f")` program is 1,170 bytes smaller than before, and a program
  that uses `%f`, `%e` and `%g` is 3,285 bytes smaller. The engine does not link
  `__fadd`, `__fmul`, `__fdiv`, `__ftoi` or `__itof`.
- The engine uses 214 bytes of RAM.
- A typical value is faster than before (`printf("%f", 3.14159f)` takes
  50,500 T-states, it took 117,700). The slowest value is the smallest
  subnormal number, `1.4e-45`: it needs 149 halvings of a string of up to 149
  digits, about 664,000 T-states (0.17 s at 4 MHz).

`printf` prints the sign of a NaN (`-nan`). The `+` and space flags apply to a
NaN, as in glibc. Some other C libraries print no sign for a NaN.

## Parts that link only when a format needs them

Some conversions have large code, and few programs use them. The library holds
each of them in its own module. `printf_hook.py` reads the format strings in the
program. After the code generator writes the table, the hook adds a table entry.
The linker then takes the module. The float part of `scanf` uses the same
method.

- `%td`, `%lf`: an entry that skips the length modifier (`lc_printf_skip`)
- `%n`: `lc_printf_n`
- `%a`, `%A`: `lc_printf_a`
- `%llo`, `%llX`: `lc_printf_llo`, `lc_printf_llxu`
- `%hhd`, `%hhu`, `%hhx` and so on: a short routine (`lc_printf_hh`) that
  narrows the argument before the normal handler

A format that is not a string literal, for example `printf(fmt)`, links all of
these parts except `%a`. `--printf all` links all of them, `%a` included.

With `--no-whole-program`, only the unit that defines `main()` has a table.
Compile that unit with `--printf all`, or the table does not have the parts that
the other units need.

The compiler (`codegen.py`) does not know these entries, so its warning
`has no handler in this libc` would be wrong for them. `main.py` removes that
warning for each conversion that the hook handles.

## Limits

These limits are deliberate.

- A field width or a precision is a 16-bit number and has a limit of 32767.
  `printf("%1000d", 1)` prints a field of 1000 characters. A precision of a
  float conversion (`%f`, `%e`, `%g`, `%a`) above 255 is correct too. The
  engine makes at most 255 digits, and the rest of the precision is zeros.
  The exact decimal form of a float has at most 149 digits after the point.
  So every digit after the 255th is 0, and the rounding does not change.
  `%.300f` of `1.5f` prints `1.5` and 299 zeros, as glibc does. The test
  `tests/test_printf_big_precision.py` compares 18 such conversions with the
  exact text from Python.
- A `*` width or precision reads an `int` argument. The code is in the module
  `lc_printf_star`. The printf hook adds a `*` entry to the dispatch table when
  a format has a `*`. A program without `*` does not link the code. With
  `--int 32` the entry is for an `int` of 4 bytes. The runtime uses 32767 for
  a value above 32767. It uses a left-justified field of 32767 for a value
  below -32768.
- The `hh` modifier does not narrow the argument when `int` has 32 bits
  (`--int 32`). `%n` stores a 16-bit value.
- `%jd`, `%ju`, `%jx` (`intmax_t`) are not supported. Cast the argument to
  `long long` and use `%lld`.
- `%a` with a precision under 6 rounds to nearest, a tie to even, and a carry
  into the first digit gives `0x2p+0` (glibc). Some C libraries cut the digits
  and do not round.

## Tests

- `tests/test_printf_float_exact.py`: floats against Python
- `tests/test_printf_audit.py`: `hh`, `t`, `z`, `l`, `%p`, `%n`, `%a`, `%llo`,
  `%llX`, the printf family, the width limit, the hook
- `tests/test_printf_flags.py`, `tests/test_printf_features.py`,
  `tests/test_printf_family.py`: flags, width and precision of the integer
  conversions
