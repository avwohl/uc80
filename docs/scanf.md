# scanf and strtol Functions

Back to the [README](../README.md).

The C library has `scanf`, `fscanf`, `sscanf`, `vscanf`, `vfscanf` and
`vsscanf`. All six use one engine (`lc_scan_core`, in assembly). The engine
takes its characters from a string, a `FILE` or the console. The front ends
are in `src/uc80/lib/csrc/`. See
[Library modules written in C](libc_c_sources.md). `sscanf` and `vsscanf`
have no BDOS call, so they are in the ROM library.

## Conversions

- `%d %i %u %o %x %X`: integers with an optional sign. `%i` reads the
  prefixes `0x`, `0b` and `0`. `%b` reads a binary number with an optional
  `0b` prefix.
- The size modifiers `hh`, `h`, `l`, `ll`, `j`, `z`, `t`. An `int` has 2
  bytes, a `long` 4 bytes and a `long long` 8 bytes. The engine stores the
  number of bytes that the modifier gives. A value that does not fit wraps.
- `%f %e %g %a` and the capital letters, with `l` and `L`. They read `inf`,
  `infinity`, `nan`, `nan(chars)` and hexadecimal floats. A float, a double
  and a long double have 4 bytes.
- `%c`, `%s` and `%[set]`. A set can have ranges (`%[a-z]`) and a leading
  `^`. A `]` as the first member is part of the set.
- `%p` reads hexadecimal digits with an optional `0x`.
- `%n` stores the number of characters that the call has read. It is not an
  assignment.
- `%%`, a width (`%3d`, `%3c`) and the assignment suppression `%*`.

A white-space character in the format skips all white space in the input.
All conversions except `%c`, `%[` and `%n` skip white space first.

## Return value

The functions return the number of assigned items. They return `EOF` when
the input ends before the first assignment. A matching failure stops the
call. The return value is then the number of assignments so far. The end of
the input after the first character of a number (for example `-`) is also a
matching failure.

## Limits

- `%i` and `%x` read the prefix `0x` only when a hexadecimal digit follows.
  `%i` and `%b` read the prefix `0b` only when a binary digit follows.
  If not, the number is `0` and the `x` stays in the input. A `FILE` keeps
  only one pushed-back character. After `0xg` the `g` is lost for a `FILE`.
  A string keeps all characters.
- The float conversion collects 126 characters and drops the rest of a longer
  number. A string source puts every unused character back. A `FILE` keeps
  only the last one.
- On the console, the Enter key gives CR (13) and the end of the input is
  Ctrl-Z (26), as in CP/M. `%c` can return 13.
- `scanf` and `fscanf(stdin, ...)` keep one pushed-back character between
  calls. `scanf("%d")` does not lose the character that ended the number.
  `getchar` does not see that character.
- The library does not support wide characters, the locale and `%L` for
  integers.
- With `--int=32`, the compiler changes `%d`, `%i`, `%u` and `%x` in a format
  string literal to the `long` forms. `%n` keeps the size of a 16-bit `int`.
- The engine does the integer conversions, the literal characters, `%%` and
  `%n`. A program that uses only these conversions links about 1.3 KB for
  `sscanf("%d")`. The compiler links the float conversions and the text
  conversions (`%c`, `%s`, `%[`) only when the program needs them. The
  program needs a part in three cases. A scanf call has a conversion of that
  part in a format literal. A scanf call has a format that is not a literal.
  The program uses a scanf function without a call. The float part is about
  6.4 KB, with `strtod`. The text part is about 0.3 KB. To link the float
  part by force, use `#pragma scanf float` or `--scanf float`. To link the
  text part by force, use `--scanf text`.

## Number strings

`strtol`, `strtoul`, `strtoll` and `strtoull` take the bases 0 and 2 to 36.
They skip white space and accept a sign. For base 0 and base 16 they accept
the prefix `0x`. On overflow they return `LONG_MAX`, `LONG_MIN`, `ULONG_MAX`
or the long long limits, and they set `errno` to `ERANGE`. A base that is not
valid sets `errno` to `EINVAL`. If the string has no number, the end pointer
is the start of the string. `atoll`, `strtoimax` and `strtoumax` call these
functions.

`strtod`, `strtof`, `strtold` and `atof` use the same code. They read decimal
numbers, `inf`, `infinity`, `nan` and hexadecimal floats. The result is the
nearest float for every string, and a tie goes to the even mantissa. A value
that is too large gives an infinity. A value that is too small gives zero or a
subnormal number. Both set `errno` to `ERANGE`.

The converter reads up to 116 digits into a big integer. It multiplies the top
64 bits of the integer by a power of ten with 64-bit mantissa arithmetic. The
error of the product is below 2^-54. About 1 string in 2^23 is within 2^-48
of a tie. For such a string, the converter compares the digits with the tie in
big integers, and the answer is exact.

The longest tie of a float has 113 digits, so the cut after 116 digits cannot
hide a tie. A hexadecimal string is exact in 64 bits. A number with more than
32000 digits does not work, because the digit counters have the type `int`.

The exact comparison adds about 700 bytes of code and 170 bytes of RAM. Each
program that links `strtod`, `atof` or the float part of `scanf` pays this
cost. The comparison runs only for a string that is near a tie.
