# Console output

Back to the [README](../README.md). The limits are also in
[Known limits of the C library](libraries.md).

`putchar`, `puts`, `printf`, `fputs`, `fputc` and `fwrite` on `stdout` or
`stderr` write each character with one routine, `__conout`
(`lc_conout.mac`). It is the only code in the library that calls BDOS function
2. The routine does these things:

- LF (0AH) becomes CR LF, unless the character before was CR, or the program
  sets `__crlf_mode` to 0 (`--no-crlf`).
- TAB (09H) becomes spaces up to the next multiple of 8 columns. The routine
  counts the columns: a visible character adds 1, backspace (08H) takes 1
  away, and CR sets the count to 0.
- Every other character goes to the BDOS as it is, with no change.

## Why TAB becomes spaces

A real CP/M BDOS does the same for function 2: it writes spaces up to the next
tab stop. The emulator `cpmemu` does not. It acts as an ADM-3A terminal and
drops the byte 09H, so each TAB was missing from the output. The library
sends spaces only. The output is the same on a real CP/M system and under
`cpmemu`. A program that needs the byte 09H on the console cannot write it
with the C library.

The column count follows only the characters that go through `__conout`. A
cursor movement that the terminal makes by itself (an escape sequence) does not
change the count.

## The other control characters

`putchar`, `puts`, `printf` and `fputs` send the characters 01H to 1FH and 7FH
without change, and all four send the same bytes. What the console does with
them is the decision of the console. `cpmemu` passes BEL (07H), backspace,
CR, LF and 7FH, and it drops 01H to 06H, 0EH to 19H, 1CH, 1DH and 1FH. It
acts on ESC (1BH, an escape sequence), Ctrl-Z (1AH, clear screen), Ctrl-K
(0BH, cursor up), Ctrl-L (0CH, cursor right) and Ctrl-^ (1EH, home cursor). The
file functions (`fputc` and `fwrite` on a `FILE` that `fopen` returned) write
all bytes as they are, TAB included.

Test: `tests/test_stdio_library.py` (`test_tab_makes_spaces_to_the_next_tab_stop`,
`test_every_console_route_sends_the_same_bytes`), `tests/test_crlf.py`.
