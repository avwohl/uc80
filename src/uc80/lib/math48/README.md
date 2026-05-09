# Math48 Library - Converted from z88dk

This directory contains the 48-bit floating point library converted from z88dk
using the `z88dk_to_um80.py` converter.

## Source

Original source: z88dk's math48 library (Anders Hejlsberg's 1980 floating point routines)
- Location: `z88dk/libsrc/math/float/math48/z80/`

## Format

- 48-bit (6 bytes) floating point representation
- BCDEH = 40-bit mantissa with sign in B bit 7
- L = 8-bit exponent with bias 128

## Conversion Status

### Working Files (mm48_* core routines)

The core implementation files assemble correctly with um80:
- `mm48_fpadd.mac` - Floating point addition
- `mm48_fpsub.mac` - Floating point subtraction
- `mm48_fpmul.mac` - Floating point multiplication
- `mm48_fpdiv.mac` - Floating point division
- `mm48_sin.mac`, `mm48_cos.mac`, `mm48_tan.mac` - Trigonometry
- `mm48_exp.mac`, `mm48_log.mac`, `mm48_ln.mac` - Exponential/logarithm
- `mm48_sqr.mac` - Square root
- `mm48_pwr.mac` - Power function
- And others...

### Failing Files (am48_* wrapper/alias files)

Many am48_* files fail to assemble with error:
```
Error: Cannot use external in EQU
```

These files use z88dk's `defc` directive to create symbol aliases:
```
defc am48_sin = mm48_sin
```

This converts to:
```
am48_sin  EQU  mm48_sin
```

But MACRO-80's .REL format doesn't support using external symbols in EQU
because externals aren't resolved until link time.

## Possible Solutions

1. **Use JP trampolines** - Convert aliases to jump instructions:
   ```
   am48_sin:  JP  mm48_sin
   ```
   Adds 3 bytes overhead per alias.

2. **Skip alias files** - Use mm48_* symbols directly in C runtime.
   The am48_* files are just wrappers; actual code is in mm48_*.

3. **Linker modification** - Add symbol alias support to ul80 linker.
   Would require extending .REL format or adding a post-processing step.

4. **Assembler modification** - Have um80 emit special relocations for
   external EQUs that the linker can resolve.

## Usage Notes

For now, to use this library:
- Reference mm48_* symbols directly (not am48_*)
- Or implement trampolines manually for the C API

## Files

- 185 total .mac files converted
- ~37 files fail due to external EQU issue
- ~148 files assemble successfully

## Converter

Converted using `/home/wohl/src/uada80/tools/z88dk_to_um80.py`

Converter handles:
- `$xx` hex -> `xxH`
- `0xXXXX` hex -> `0XXXXH`
- `%binary` -> `binaryB`
- `SECTION` -> removed (CSEG added)
- `EXTERN` -> `EXTRN`
- `DEFQ` (32-bit) -> 4x DB bytes
- `DEFC name = const` -> `name EQU const`
- Local labels `.name` -> `global$name`
