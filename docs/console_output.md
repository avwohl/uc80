# Console output

Back to the [README](../README.md). The limits are also in
[Known limits of the C library](libraries.md).

`putchar`, `puts`, `printf`, `fputs`, `fputc` and `fwrite` on `stdout` or
`stderr` write each character with one routine, `__conout`
(`lc_conout.mac`). It is the only code in the library that calls BDOS function
2. The routine does these things:

- LF (0AH) becomes CR LF, unless the character before was CR, or the program
  sets `__crlf_mode` to 0 (`--no-crlf`).
- Every other character goes to the BDOS as it is, with no change. TAB (09H)
  is one of these characters.

## TAB

The CP/M 2.2 BDOS expands a TAB for function 2: it writes spaces up to the
next multiple of 8 columns. The library does not expand it, so it costs no
bytes. `cpmemu` 4.10 and older drop the byte 09H. The next version expands
it as the BDOS does. `z88dk-ticks` passes the byte through, so
`bench/run.py` compares text with the TABs expanded.

## The other control characters

`putchar`, `puts`, `printf` and `fputs` send the characters 01H to 1FH and 7FH
without change, and all four send the same bytes. What the console does with
them is the decision of the console. `cpmemu` passes BEL (07H), backspace,
CR, LF and 7FH, and it drops 01H to 06H, 0EH to 19H, 1CH, 1DH and 1FH. It
acts on ESC (1BH, an escape sequence), Ctrl-Z (1AH, clear screen), Ctrl-K
(0BH, cursor up), Ctrl-L (0CH, cursor right) and Ctrl-^ (1EH, home cursor). The
file functions (`fputc` and `fwrite` on a `FILE` that `fopen` returned) write
all bytes as they are, TAB included.

Test: `tests/test_stdio_library.py` (`test_every_console_route_sends_the_same_bytes`), `tests/test_crlf.py`.
