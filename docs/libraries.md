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
uc80 --build-libs          # assembles libc.lib, libc_rom.lib, runtime.lib and crt0.rel (~40 s)
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

## Runtime and math modules

`runtime.lib` also holds one module for each entry point. For example,
`rt_mul16`, `rt_div16` and `rt_shl16` are three modules, and a program that
only multiplies does not link the division code. Fall-through aliases stay
in one module (for example `_floor`, `_floorf` and `_floorl` in `lc_floor`,
and `__ltof` and `__itof` in `rt_ltof`).

Rules for the split:

- A module is a group of entry points. Two entry points are in one module
  when one falls through into the other. They are also in one module when
  one uses a non-PUBLIC label of the other, or a `JR` target of the other.
- A shared work area is in a data module that has its labels PUBLIC:
  `rt_fdata` (`__fop1` to `__fman2`), `rt_cplx_data`, `rt_acc64`,
  `rt_divrem`, `rt_rem32`, `rt_rem64` and `lc_scanf_data`. The code modules
  have the labels as EXTRN. A work area that only one function uses is in
  the module of that function (for example `__fdiv_rem` in `rt_fdiv`).
- Each module that has data has its own `COMMON /name/` block. Two modules
  must not use the same block name, because the linker puts the blocks of one
  name at one address.
- `lc_scanf.mac` holds `scanf`. `lc_sscanf.mac` and `lc_fscanf.mac` hold the
  other two entry points. All three jump to `_scanf_loop` in
  `lc_scanf_core.mac`.
- `lc_fpclassify`, `lc_isfinite`, `lc_isinf`, `lc_isnan`, `lc_isnormal` and
  `lc_signbit` are six modules. Each of the four `is*` functions has its own
  copy of the 3-instruction return of zero.
- `lc_strtod.mac` holds `strtod`, `strtof` and `strtold`. `lc_atof.mac`
  holds `atof`.
- The C11 atomic functions (`lc_atomic_*`) and the thread functions
  (`lc_thrd_*`, `lc_mtx_*`, `lc_cnd_*`, `lc_tss_*`) have one module each.

The compiler embeds the runtime functions from `runtime.mac`. The build
writes `runtime.mac` as all `rt/` modules in one file. `build_libs.py` puts a
`CSEG` line before the banner of each module, so that the banner does not
come after the data of the module before.

Measured result (`bench/run.py -c uc80 -m size`, 109 programs, without
`battlestar-size`, `rogue36-size` and `empire-trefonas`): the status of each
program is the same, no program grows, and 58 programs get smaller. The total
image goes from 817,781 to 814,805 bytes. The geometric mean against SDCC
4.6.0 (size mode, `bench/report.py` method, 96 programs, minus the empty
program of each compiler) goes from 0.982 to 0.976. The ratio of the whole
images goes from 0.944 to 0.937.

`src/uc80/lib/split_libc.py` made the first version of `lc/` from `libc.mac`.
Do not run `split_libc.py` again: it overwrites the edits. Edit the
`lc/*.mac` files and run `uc80 --build-libs`. The build removes a `.rel`
file that has no `.mac` file, so a module that you rename does not stay in
the library.

The test `tests/test_libc_link_size.py` checks the image size of
`printf("%d")` and checks that a `strlen` program does not link `malloc`.
