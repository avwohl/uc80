# Library Modules Written in C

Back to the [README](../README.md).

Most modules of `libc.lib` are assembly files in `src/uc80/lib/lc/`. Some are
made from C. The C sources are in `src/uc80/lib/csrc/`. The script
`src/uc80/lib/gen_c_modules.py` compiles each `csrc/lc_NAME.c` to
`lc/lc_NAME.mac`. The generated `.mac` files are in the repository. A normal
library build needs only `um80` and `ulib80`.

These modules are in C:

- the scanf front ends (`lc_scanf`, `lc_sscanf`, `lc_fscanf`, the `v`
  versions and the readers `lc_scan_str`, `lc_scan_file`, `lc_scan_stdin`,
  `lc_scan_float`)
- `strtoll`, `strtoull`, `atoll`, `strtoimax`, `strtoumax`, `strtod`,
  `strtof`, `strtold` and `atof`
- the math functions that work on the bits of a float (`floor`, `fmod`,
  `frexp`, `nextafter`, `cbrt` and others, see [docs/libm.md](libm.md))

The scanf engine and the integer reader are assembly. The functions of the
math library that do float arithmetic are programs for the float
interpreter, made from `src/uc80/lib/vmsrc/` by `gen_vm_modules.py`
([float interpreter](libm_vm.md)). These assembly modules help the rest:

- `lc_scan_core` is the scanf engine. It does the integer conversions, the
  literal characters, `%%` and `%n`. `lc_scan_text` does `%c`, `%s` and
  `%[`. `lc_scan_float` does the float conversions. The compiler links the
  last two only when a format needs them ([scanf and strtol](scanf.md)).
- `lc_strtoint` reads an integer of 4 or 8 bytes for `strtol` and the
  other integer conversions. `lc_sc_num` multiplies and negates a number of
  1 to 8 bytes in memory, so the integer readers need no 32-bit or 64-bit
  multiply routine.
- `lc_sqrt` is the square root. `lc_scalbn` and `lc_vm_ldexp` are `scalbn`
  and `ldexp`. `lc_m_pack`, `lc_m_unpack` and `lc_m_phred` pack a float,
  unpack a float and reduce an argument of `sin`.

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
- `cbrt` is the only user of `lc_m_2prod`.
- `lc_strtod` is correctly rounded for every decimal string. It has a fast
  path in 64-bit arithmetic and a big-integer comparison with the tie for a
  string that is near a tie ([docs/scanf.md](scanf.md)). The test is
  `tests/test_strtod_exact.py`.

## Code size

The compiled code of a float function is large: the compiler writes 25 to
40 bytes for each float operation. This is the reason why the math functions
that do float arithmetic are programs for the float interpreter
([docs/libm_vm.md](libm_vm.md)). The tests of a float in the C modules use
bytes (`M_SPEC`, `M_TINY` and the other macros of `m.h`), because a 32-bit
mask costs more code on the Z80.

The floating-point part of `scanf` (about 6.4 KB with `strtod`) and the text
conversions (0.3 KB) link only when the program needs them. The compiler
decides this from the format strings (`src/uc80/scan_hook.py`). See
[docs/scanf.md](scanf.md).
