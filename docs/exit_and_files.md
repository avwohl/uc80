# Program exit and files

Back to the [README](../README.md). The limits are also in
[Known limits of the C library](libraries.md).

## Program exit

A program ends in one of three ways:

- `main` returns
- the program calls `exit()`
- the program calls `abort()`

The return from `main` and `exit()` do the same work, in this order:

1. Call the functions that `atexit()` registered. The last function registered
   runs first. A function that calls `atexit()` adds a function that runs next.
2. Close the open files. A file that the program wrote but did not close
   has its last record written.
3. Return to CP/M (warm boot, `JP 0`).

`abort()` prints `Aborted` and returns to CP/M. It runs no function and closes no
file.

`crt0` calls `main`. Then `crt0` jumps through the word `__exit_hook`
(`lc_exit_hook.mac`, in BSS). The word is 0 at the start, and a jump to address
0 is the warm boot. So a program that links no exit code only returns to CP/M.
`atexit()` and `fopen()` put the address of `__exit_run` (in `lc_exit.mac`) in
the word.

A program that uses no `atexit` and no files is 1 byte bigger than
before. A startup file of your own that does not read the word still works:
`exit()` runs the functions and closes the files, but the return from `main`
does not.

`atexit()` takes 32 functions (the minimum that C requires). The 33rd call
returns 1.

### Exit status

CP/M 2.2 has no exit status, so the argument of `exit()` and the value that
`main` returns are not used. The emulator `cpmemu` always exits with 0 as well.
A test must print its result, as the tests in `tests/` do.

## Files

The `FILE` functions (`fopen`, `fgetc`, `fputc` and the others) read and write
a file in records of 128 bytes. They use the random access calls of the BDOS
(33 and 34). The number of the next record is in the fields `r0` and `r1` of
the FCB of the `FILE`. The position is exact because of that:

- `ftell(f)` is the number of bytes before the next byte that `fgetc` reads or
  `fputc` writes. It is correct after reads, writes, `fflush`, `ungetc`,
  `rewind` and at the end of the file. It is -1 for `stdin`, `stdout`, `stderr`.
- `fseek(f, offset, whence)` works for `SEEK_SET`, `SEEK_CUR` and `SEEK_END`,
  for a stream that reads and for a stream that writes. It returns -1 when the
  position is below 0 or above 8,388,607 (8 MB), or when `whence` is wrong.
  `fgetpos`, `fsetpos` and `rewind` use `fseek` and `ftell`.
- `fflush` writes the record that is in the buffer, with Ctrl-Z after the
  data. More output goes in the same record, so no Ctrl-Z stays in the middle
  of the file.

The limits:

- The files are text files in the CP/M sense. `fgetc` skips a carriage return
  and reads the Ctrl-Z byte as the end of the file. `ftell` counts the carriage
  returns, because the file holds them.
- The emulator `cpmemu` changes each line end of a host text file to CR LF.
  After a line end, the position is one more than the number of bytes that
  the program reads.
- `SEEK_END` is the end of the data: the bytes before the first Ctrl-Z of the
  last record. A file of binary data with a byte 0x1A has a wrong end.
- `fopen` has the modes `r` and `w` only. The mode `a` creates a new file (it
  does not append). `r+`, `w+` and `a+` are not supported.
- A program can have 4 open files at the same time.
- `fseek` on a stream that writes, to a position inside a record, reads that
  record first. When the file has no such record, the bytes before the position
  are 0.
- `cpmemu` keeps the Ctrl-Z padding of the last record on the host when the
  program writes a file with LF line ends. The data before the padding is
  correct. A real CP/M disk has the same padding.

Tests: `tests/test_atexit_exit.py`, `tests/test_file_position.py`.
