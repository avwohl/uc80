# Volatile objects

uc80 makes every access to a volatile object as the C source writes it
(C23 5.1.2.3 and 6.7.3). It does not remove, merge, repeat or reorder a
read or a write of a volatile object. Each read in the source is one read
in the program. Each write in the source is one write.

An object is volatile when its declared type says so. These forms count:

- `volatile T x`, and `static` or `extern` forms of the same
- a typedef of a volatile type, also a typedef of a typedef
- `T * volatile p`: the pointer is volatile
- `volatile T *p`: the object that the pointer addresses is volatile
- an array of volatile elements
- a struct member with the volatile qualifier, and a struct that has one
- a cast to any of these types, for example `*(volatile unsigned char *)0x40`
- a local variable or a parameter of any of these types

## What each pass does

Three passes can change the number of accesses. Each one has a rule.

- *AST optimizer (uc_core).* The optimizer collects the names of the
  volatile objects in each function and at file scope. The test uses the
  name only. A name that is volatile anywhere in its function is volatile at
  every use in that function. A second object with the same name can only
  cost an optimization. The optimizer does not delete a store to a volatile
  object. It does not delete a store that has a volatile read in its
  right side. It does not fold `v == v`, `v | v` or `v % 1`. It does not
  turn `v * 2` into `v + v`. It does not unroll a loop over a volatile
  variable, and it does not reuse a volatile value through copy
  propagation or common subexpression elimination.
- *Inliner.* A call whose argument reads a volatile object is not
  inlined, because the body can use its parameter twice or not at all. A
  volatile parameter is never replaced by a constant.
- *Peephole pass.* The peephole pass sees assembly text. The text does not
  say which loads and stores are volatile. The code generator therefore
  brackets the code of each statement part that can access a volatile
  object with two comment lines (`; __UC80_VOL_BEGIN__` and
  `; __UC80_VOL_END__`). Before the peephole pass runs, `main.py` puts a
  label named `??VOLn` before and after each instruction in such a region
  that reads or writes memory. The peephole matcher does not match a
  pattern across a label, so no rule can merge a volatile load or store
  with a neighbor. After the pass, `main.py` removes the markers and the labels. The
  code is in `src/uc80/volatile_fence.py`. The peephole pass changes code
  outside the regions as usual. A program that has no volatile object compiles to the
  same assembly as before.

A 16-bit or 32-bit volatile object is read and written in full on each
access, low byte first. The `++` and `+=` operators on a 32-bit volatile
object read and write all four bytes. They do not stop after a byte that
does not carry.

## Known limits

- One access by an instruction that reads and writes (`inc (HL)`, for
  a `char`) is one read-modify-write, not a separate read and write.
- The width of an access is the width of the object. A `uint16_t` volatile
  object is read with `ld HL,(addr)`, which reads the low byte first.
- The region test is by name. A function that only copies a pointer to a
  volatile object (`q = p`) has the code of that statement bracketed.
  That costs speed, not correctness.
- Regions stop the peephole pass only. They do not stop the assembler or
  the linker.

## Tests

- `uc_core/tests/test_volatile.py` tests the AST optimizer.
- `tests/test_volatile.py` compiles C and counts the loads and stores of
  each volatile symbol in each function. It also tests the fence pass,
  and runs one program under cpmemu.
