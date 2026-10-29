# Types, tags and conversions

This page tells how uc80 chooses the type of an enumeration and how it scopes
a tag. It tells what a bit-field assignment gives. It also tells how uc80
converts between floats and integers. The rules follow C23 (ISO/IEC
9899:2024). The sizes below are for the default `int` of 2 bytes and `long`
of 4 bytes. With `--int 32` the `int`
is 4 bytes.

## Enumerations

An enumeration without a fixed underlying type has an integer type that can
hold all of its values (C23 6.7.3.3). The choice of the type is left to the
implementation. uc80 uses the first type of this list that holds the lowest
and the highest value:

1. `int`, when all values fit. The enumeration is an `int` of 2 or 4 bytes,
   and the constants are `int`.
2. `unsigned int`, `long`, `unsigned long`, `long long`,
   `unsigned long long`, in that order. A negative value rules out the
   unsigned types.

When `int` does not hold all values, each constant of the enumeration has the
type of the enumeration, as C23 says. So `enum { E = 50000 }` is an
`unsigned int` of 2 bytes, `long l = E` gives 50000, and
`enum { F = 70000, G = -1 }` is a `long` of 4 bytes. `sizeof` gives the size
of the chosen type. The compiler gives an error when no integer type holds
the values.

The front end gives a hidden tag (`__anon_enum_...`) to an anonymous
enumeration that has an explicit value. A variable of its type then has the
size that the values need. The parser does not accept an enumeration with a
fixed underlying type (`enum e : T`).

With `--int 32` an object of enumeration type has 4 bytes, and the code
loads and stores it as an `int`. An enumeration constant above 65535 is a
32-bit value.

## Tags in blocks

The tag of a `struct`, `union` or `enum` that a block declares belongs to
that block. It hides a file-scope tag of the same name until the block ends
(C23 6.2.1). The front end renames each tag that a block declares, and the
uses that the declaration governs, to a hidden name (`S__b...`). A use with
no declaration in an outer block keeps its name. The code is in `uc_core`
(`_rename_block_scope_tags`), so `uc386` has it too. A message that names
such a tag shows the hidden name.

## Bit-fields

The value of an assignment to a bit-field is the value that the field holds
after the store (C23 6.5.16). For an unsigned field of 2 bits,
`sh = bf.x = 5` gives 1. This holds for `=`, for every compound assignment
and for `++` and `--`. A postfix `++` gives the old value of the field. The
result is correct for a field in a 4-byte unit (with `--int 32`, or a
`long` field) and for a field in a 1-byte or 2-byte unit.

A bit-field of a 64-bit type that has a width under 64 bits (`unsigned long
long i : 40`) is not supported. The compiler gives an error with the line of
the width. Use a 32-bit type or a mask. The address of a bit-field is an
error too.

## Floating constants

A floating constant keeps its value when it is a subnormal number: `1e-40f`
is the bits `0x000116C2`. You can move, print, compare and store the value.
The float runtime still flushes a subnormal result of arithmetic to
zero ([math library](libm.md)), so `1e-30f * 1e-10f` is 0, also when the
compiler folds the product.

## Conversions of floats and integers

| From | To | Routine |
| --- | --- | --- |
| signed 32-bit integer | float | `__itof` |
| unsigned 32-bit integer | float | `__uitof` |
| `long long` | float | `__lltof` |
| `unsigned long long` | float | `__ulltof` |
| float | 16-bit or signed 32-bit integer | `__ftoi` |
| float | `long long` | `__ftoll` |
| float | `unsigned long long` or `unsigned long` | `__ftoull` |

An integer converts to the nearest float. A tie goes to the even mantissa.
So `(float)16777217LL` is 16777216 and `(float)16777219LL` is 16777220.

A float converts to an integer by truncation towards zero. A value that does
not fit gives the nearest limit: `__ftoll` gives `LLONG_MAX` or `LLONG_MIN`,
`__ftoull` gives `ULLONG_MAX`. A NaN gives the limit that its sign bit
chooses. A value under 1 in size gives 0. A negative value in the unsigned
form wraps (`-1.0f` gives all ones), as it does on a host.

uc80 has a 4-byte `double`, so a `double` converts as a float does:
`(long long)1e18` is 999999984306749440, not 10^18.

The compiler converts a
constant in an initializer the same way. It truncates a float constant in an
integer object. It rounds an integer constant in a float object to the
nearest float. An initializer of a local array converts a value of another
type (`float a[2] = { i, n }`). Before, it stored the bits of the integer.
