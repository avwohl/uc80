# Finding the Libraries

Back to the [README](../README.md).

Linking needs `libc.lib`, `runtime.lib` and — for separate compilation —
`crt0.rel`. Ask uc80 where they are instead of guessing:

```bash
LIB=$(uc80 --print-lib-dir)
```

It prints one line and exits 0, with no input file required. From Python:

```python
import uc80
uc80.lib_dir()             # -> Path to the library directory
uc80.lib_file("libc.lib")  # -> Path to one asset
```

Wheels ship the three link artifacts, so `pip install uc80` is enough. In a
git checkout they are build output (`.gitignore` covers `*.lib` and `*.rel`),
so build them once:

```bash
uc80 --build-libs          # assembles libc.lib, runtime.lib and crt0.rel (~40 s)
```

Re-run that after editing anything in `src/uc80/lib/lc/` or `src/uc80/lib/rt/`.

Set `UC80_LIB_DIR` to use libraries built somewhere else, which is what makes
uc80 usable when it is installed into a read-only `site-packages`. The
override applies per file, so a directory holding nothing but the two rebuilt
`.lib` files works and cannot shadow `crt0.mac`, `runtime.mac` or `include/` —
those are version-locked to the compiler and always come from the package.
Point it at a *complete* older library tree, though, and you get exactly the
silently-stale-libc problem this flag exists to prevent.

`src/uc80/lib/` is the only library directory. Nothing in uc80 looks anywhere
else; do not create a top-level `lib/`.

## Library module layout

`libc.lib` holds one module for each public function. A program links only
the functions that the program calls. For example, a program that calls
`strlen` does not link `malloc`, and a program that calls `printf("%d")`
does not link any floating-point code.

Rules for a module in `src/uc80/lib/lc/`:

- A function that other functions share a body with stays in one module
  (for example the alias entries of `strtod`).
- Data that two or more modules use is in a separate data module (for
  example `lc_rand_data.mac`). The data module has the label as PUBLIC.
  The other modules have the label as EXTRN.
- Each module has its own copy of the EQU constants that the module uses.
- `lc_printf_core.mac` holds the printf engine and the state that the
  `%d`, `%e` and `%g` handlers share. `lc_prt_float.mac` holds the `%f`
  printer and its data. Only `lc_printf_f.mac` refers to `lc_prt_float.mac`.
- `lc_qsort.mac` holds `qsort`. `lc_bsearch.mac` holds `bsearch`.
- `lc_fprintf.mac` holds `fprintf` and `vfprintf`, which share the stream sink.
  `lc_vprintf.mac` and `lc_vsprintf.mac` hold `vprintf` and `vsprintf`.
- `lc_rand.mac` multiplies with a shift and add loop, so `rand` does not link
  the 16-bit runtime module `rt_arith16` (about 600 bytes).

`src/uc80/lib/split_libc.py` made the first version of `lc/` from `libc.mac`.
Do not run `split_libc.py` again: it overwrites the edits. Edit the
`lc/*.mac` files and run `uc80 --build-libs`. The build removes a `.rel`
file that has no `.mac` file, so a module that you rename does not stay in
the library.

The test `tests/test_libc_link_size.py` checks the image size of
`printf("%d")` and checks that a `strlen` program does not link `malloc`.
