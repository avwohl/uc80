# Library Modules Written in C

Back to the [README](../README.md).

Most modules of `libc.lib` are assembly files in `src/uc80/lib/lc/`. Some are
made from C. The C sources are in `src/uc80/lib/csrc/`. The script
`src/uc80/lib/gen_c_modules.py` compiles each `csrc/lc_NAME.c` to
`lc/lc_NAME.mac`. The generated `.mac` files are in the repository. A normal
library build needs only `um80` and `ulib80`.

These modules are in C:

- the scanf family (`lc_scan_*`, `lc_scanf`, `lc_sscanf`, `lc_fscanf` and the
  `v` versions)
- `strtol`, `strtoul`, `strtoll`, `strtoull`, `atoll`, `strtoimax`,
  `strtoumax`, `strtod`, `strtof`, `strtold` and `atof`
- the math library ([docs/libm.md](libm.md)), except `sqrt`, `fabs`,
  `copysign` and the classification functions, which are assembly

Two assembly modules help the C code. `lc_sqrt` is the square root.
`lc_sc_num` multiplies and negates a number of 1 to 8 bytes in memory, so the
integer readers need no 32-bit or 64-bit multiply routine.

## Change a C module

1. Edit the file in `src/uc80/lib/csrc/`.
2. Run `python3 src/uc80/lib/gen_c_modules.py lc_NAME`. Without an argument,
   the script makes all modules again.
3. Run `uc80 --build-libs`.
4. Commit the C file and the generated `.mac` file.

Do not edit a generated `.mac` file. The first line says that the script made
the file.

## Rules for a C module

- A module has one public function, or a small group of functions that share
  code. A program links only the modules that it calls.
- The comment `/* ALIAS: floor = floorf floorl */` gives the function more
  entry points at the same address. `double`, `float` and `long double` are
  the same 32-bit type, so one function serves three names. The name in an
  `ALIAS` comment is the C name. For a name with a leading underscore, write
  the underscore (`_sc_float = _sc_float_on`).
- The script makes the labels of the compiler unique. It adds the module name
  to labels such as `@WHILE1` and `??AUTO`. The joined file `libc.mac` holds
  every module. A static object or function therefore needs a name that is
  not in another module. Start the name with a short prefix of the module
  (`sd_` in `lc_strtod.c`).
- Static storage that starts as zero goes into a named `COMMON` block. A ROM
  image does not hold the zero bytes ([docs/rom.md](rom.md)). The script does
  this conversion.
- A C file that contains the word `CONCAT-SKIP` makes a module that
  `build_libs.py` keeps out of `libc.mac`. `lc_scan_fdefault` is such a
  module, because `lc_scan_float` defines the same symbol.
- The compiled functions keep their local variables in static storage, as all
  uc80 functions do. They are not reentrant.
- The files `m.h` and `scan.h` are the headers that the C modules share.

## Code size

The compiled code is larger than the assembly that it replaces. A program
that calls `sin` is about 2.5 KB larger than before. Three measures keep the
growth small:

- The tests of a float use bytes (`M_SPEC`, `M_TINY` and the other macros of
  `m.h`). A 32-bit mask costs more code on the Z80.
- A polynomial is a table and a call of `_m_poly`.
- `pow`, `hypot`, `cbrt`, `exp2` and `fma` need exact sums and products of
  floats. Two small functions, `_m_2sum` and `_m_2prod`, provide them.

The floating-point part of `scanf` (about 5 KB with `strtod`) links only when
the program needs it. The compiler decides this from the format strings
(`src/uc80/scan_hook.py`). See [docs/scanf.md](scanf.md).
