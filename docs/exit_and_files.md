# Program exit and files

Back to the [README](../README.md). The limits are also in
[Known limits of the C library](libraries.md).

## Program exit

A program ends in one of these ways:

- `main` returns
- the program calls `exit()`
- the program calls `abort()`
- the program calls `_Exit()` or `quick_exit()`

The return from `main` and `exit()` do the same work, in this order:

1. Call the functions that `atexit()` registered. The last function registered
   runs first. A function that calls `atexit()` adds a function that runs next.
2. Close the open files. A file that the program wrote but did not close
   has its last record written.
3. Return to CP/M (warm boot, `JP 0`).

`abort()` prints `Aborted` and returns to CP/M. It runs no function and closes no
file.

`_Exit(status)` returns to CP/M at once (`JP 0`). It runs no `atexit()`
function, closes no file and writes no buffer, so the last record of a file that
the program wrote is lost.

`quick_exit(status)` calls the functions that `at_quick_exit()` registered, last
registered first, and then does the work of `_Exit()`. It runs no `atexit()`
function and closes no file. `at_quick_exit()` takes 32 functions. The 33rd call
returns 1. A program that does not call `quick_exit` or `at_quick_exit` links
none of this code. The 65 bytes of RAM for the table are in the BSS segment.

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
- `fseek` on a stream that writes reads the record at the new position, so a
  write changes only the bytes that it writes. When the file has no such
  record, the bytes before the position are 0. A write that goes into the next
  record of such a stream also keeps the bytes of that record.
- When the position is after the end of the data, `fseek` on a stream that can
  write changes the Ctrl-Z fill of the last record to zeros up to the position
  (or to the end of the record, when the position is in a later record). Then
  a write after the gap is not lost, and the gap holds zeros as in C. A file of
  binary data that ends in 0x1A bytes has these bytes changed to zeros when
  the position is after them.
- `cpmemu` keeps the Ctrl-Z padding of the last record on the host when the
  program writes a file with LF line ends. The data before the padding is
  correct. A real CP/M disk has the same padding.

### Modes of fopen

`fopen` has the modes `r`, `w`, `a`, `r+`, `w+` and `a+`. `fopen` accepts the
letters `b` and `t` and does nothing with them: a file is read and written as it
is.

- `r`: read an existing file. `fopen` returns NULL when the file does not exist.
- `w`: make a new file. `fopen` deletes an old file of the same name.
- `a`: write at the end of the file. `fopen` makes the file when it does not
  exist. The stream cannot read.
- `r+`: read and write an existing file. A write changes only the bytes that it
  writes.
- `w+`: make a new file, then read and write it.
- `a+`: as `a`, and the stream can also read.

Each write to a stream in mode `a` or `a+` goes to the end of the data (as for
`SEEK_END`), also after `fseek` or `rewind`. `fseek` and `rewind` change the
position for the next read only. After `fopen`, the position of the stream is
the start of the file: `ftell` gives 0, and the first read of an `a+` stream
gives the first byte. After a write, `ftell` gives the end of the file.

`freopen(path, mode, f)` closes `f` and opens `path` in the same `FILE`, so the
result is `f`. It works for a stream from `fopen` or `tmpfile`. For `NULL`,
`stdin`, `stdout` and `stderr` it returns NULL and does nothing, because these
streams have no room for a file. When the open fails, `f` is closed and the
result is NULL.

The C standard requires `fflush`, `fseek` or `rewind` between a write and a
read on one stream. It also requires `fseek` or `rewind` between a read and a
write. The library changes between the two by itself, so a program that does
not call them also works. `fputc` on a stream that cannot write (mode `r`)
returns EOF. `fgetc` on a stream that cannot read (mode `w` or `a`) returns
EOF. `tmpfile` opens its file with `w+`.

A change from the writing state to the reading state writes the record that is
in the buffer. The code for the two changes is in `lc_ftowrite.mac` and
`lc_ftoread.mac`.

The code for the change is about 300 bytes. The compiler (`file_hook.py`) reads
the whole program and decides if the program needs it. The program does not need
it when all of these are true:

- No `fopen` or `freopen` has a mode with `a` or `+`, or a mode that is not a
  string literal.
- The program has no `tmpfile`.
- The program does not use the address of `fopen`, `freopen` or `tmpfile`.

Then the compiler adds a reference to `__fio_lite`. The linker takes
`lc_f_lite.mac`, which has short versions of the two entry points that fail.
It does not take the two full modules. The compiler does not add the reference
in these cases, and the library has the full code:

- The option is `--no-whole-program`.
- The target is ROM.
- The program has assembly files.
- The option is `--embed-lib`, `--runtime-lib` or `--startup-lib`.

If you link a unit that opens a file in mode `a` or `+` with a unit that does
not know about the first unit, compile the unit that has `main` with
`--no-whole-program`.

### Number of open files

A program can have 8 files open at the same time. The 9th `fopen` returns NULL
until the program closes a file. `FOPEN_MAX` in `<stdio.h>` is 8.

Each open file uses 170 bytes of RAM: 6 bytes of state, the 36-byte FCB and the
128-byte buffer. All the slots are in the BSS segment (`lc_fslots.mac`), so the
program on disk is not bigger. A program that links `fopen` has 1,360 bytes of
RAM less for the heap and the stack. The 8 slots use 680 bytes more than the 4
slots of the earlier library.

To use another number, build the libraries with the variable `UC80_MAX_FILES`
(from 1 to 64):

```bash
UC80_MAX_FILES=3 uc80 --build-libs
```

The number of slots equals the number that you set, and each slot is 170 bytes
of RAM. The value is in `lc_fslots.mac` and nowhere else.

Tests: `tests/test_atexit_exit.py`, `tests/test_file_position.py`,
`tests/test_stdio_library.py`. The last one compares a stream of each mode with
a model of the C rules for random series of operations.
