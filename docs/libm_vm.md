# The Float Interpreter of the Math Library

Back to the [README](../README.md) and the [math library](libm.md).

## Why an interpreter

The compiler writes 25 to 40 bytes of code for each float operation. A
math function has 20 to 100 float operations, so a compiled `pow` was 3.7 KB.
The math functions that do float work are now byte programs for a small
stack machine. One operation is one byte or two bytes. The interpreter
`lc_vm` is 700 bytes and all programs share it.

The results are bit for bit the same as the results of the C versions that
the programs replaced, with one exception: the table of `pow` has 16 values
instead of 32. A test ran both versions with 300 to 1500 arguments for each
function. The arguments include special values, subnormal numbers
and the edges of each range.

These functions are programs: `sin cos tan asin acos atan atan2 sinh cosh
tanh asinh acosh atanh exp exp2 expm1 log log2 log10 log1p pow hypot fma`.
Their kernels (`m_ksin`, `m_kcos`, `m_reduce`, `m_expk`, `m_logk`,
`m_kasin`, `m_2sum`, `m_2prod`) are programs too. Functions that work on
the bits of a float stay in C or assembly: `floor`, `fmod`, `frexp`,
`nextafter`, `cbrt`, `sqrt` and the others.

## The machine

The stack has 64 elements of 4 bytes. Each element is a float. The stack is
in the `COMMON` block `lc_vm`, so a ROM image does not hold it. The machine
is not reentrant, as the compiled math functions were not.

- BC is the program counter. IX is the address of the top element.
- A C function calls a program through `__vm_run`. The stub of the function
  loads the address of the program in HL and jumps to `__vm_run`. The stub is
  6 bytes. `__vm_run` copies the float arguments to the stack and runs the
  program. It returns the top element in DEHL.
- A program calls another program with the operation CALL. The arguments are
  the elements on the top of the stack. The called program replaces them with
  its results.
- Every program starts with two bytes (the number of arguments and the number
  of constants). The constants follow, 4 bytes each, and then the code.

The generator `gen_vm_modules.py` writes the stack height of each program as
a comment (`;STACK`). The test `tests/test_libm_vm.py` adds the heights along
the chains of calls and checks that the longest chain fits in the stack
(`pow` needs 50 elements).

## Operations

One byte selects an operation. The operations that have an operand take it
from the next bytes.

- `ADD SUB MUL NEG ABS DROP`: the arithmetic. Division is a call of `__fdiv`
  (`CALLB`), so a program without a division does not link `__fdiv`.
- `PICK n`, `PUT n`, `LDC n`: copy the element n below the top to the top,
  store the top into the element n below the new top, push the constant n.
  The short forms hold n up to 31. `PICKX`, `PUTX` and `LDCX` take n from the
  next byte.
- `POLY first,count`: evaluate a polynomial with the constants from `first`
  by Horner's rule. `TBL first`: replace an index by a constant of the table.
- `TRUNC`: `float(int(x))`. `SGN`: 1 if the sign bit is set, else 0. `EXPO`:
  the biased exponent.
- `JLT JLE JGT JGE JEQ JNE`: pop two elements and jump by the signed byte
  that follows. `JMP` and `JMPL` jump always. A jump of more than 127 bytes
  becomes the opposite jump over a `JMPL`.
- `CALL p`, `CALLU r`, `CALLB r`, `CALLU2 r`: call the program p, or an
  assembly routine r. `CALLU` takes the top element in DEHL. `CALLB` takes
  the next element in DEHL and the top element in `__tmp32`. `CALLU2`
  returns a second result in `__tmp32`. The routines keep BC and IX.
- `RET k`, `RET2 k`, `RET3 k`: return the top 1, 2 or 3 elements and remove
  the k elements below them.

These assembly routines exist. A program links a routine only when it calls
the routine.

- `__vm_sqrt` is in `lc_sqrt`.
- `__vm_ldexp` is in `lc_vm_ldexp`.
- `__vm_frexp1` is in `lc_vm_frexp`.
- `__vm_phred`, the Payne-Hanek reduction, is in `lc_m_phred`.

## The language of a program

The file `vmsrc/lc_NAME.vc` has the functions of one module. The language is
a small subset of C with only float values.

```
float sin(float x)            a function that C code can call
sub m_ksin(float r)           a program for other programs
sub2 m_reduce(float ax)       a program with two results (sub3: three)
(r, q) = m_reduce(ax);        take two results
if (x < 1.0f && !(y > z)) ... conditions, with && || !
return poly(z, TABLE) + trunc(y) + fabs(w);
return2(r, q);                the results of a sub2 program
```

The file `vmsrc/vm.vh` declares the programs and the routines of the other
modules. A comment `/* ALIAS: sin = sinf sinl */` gives more entry points.
The constants `inf` and `nan` are floats. A local variable that a branch
assigns needs a value before the branch.

## Change a program

1. Edit the file in `src/uc80/lib/vmsrc/`.
2. Run `python3 src/uc80/lib/gen_vm_modules.py`.
3. Run `uc80 --build-libs`.
4. Commit the `.vc` file and the generated `.mac` file.

Do not edit a generated `.mac` file. `tests/test_libm_vm.py` fails when a
`.mac` file is older than its source.

## Sizes

Programs that use one function are about as large as before. Programs that
use several functions are smaller, because they share the interpreter:

- `pow`: 0.9 KB and the interpreter, 3.7 KB before.
- `atan2`: 275 bytes, 1.4 KB before.
- `exp`: 97 bytes and `m_expk` 107 bytes, 361 and 419 bytes before.

The ldexp routine does not link `_m_pack` and `_m_unpack`, and `__vm_frexp1`
does not link `_m_unpack`.
