# Byte paths and peephole rules (speed family E)

Back to [codegen_gap.md](codegen_gap.md). uc80 promotes a `char` to a 16-bit `int` in HL. Many values need only
one byte. This page lists the rules that keep such a value in register A. It also lists the small peephole rules
that remove a useless instruction.

## Where each rule lives

The rules in the first part are in `codegen.py`. The rules in the second part are in `src/uc80/peep_local.py`.
The file `main.py` runs `peep_local` after the `upeepz80` pass.

## Byte paths in the code generator

1. **Argument for a char parameter.** The call is a direct call of a function that the program defines. The
   parameter has the type `char`, `signed char` or `unsigned char`. The caller pushes the argument as a word,
   but uc80 compiles the callee, and the callee reads the low byte only. The compiler leaves H as it is. A
   constant becomes `ld L,K`. When a byte path can compute the expression, the compiler computes it in A and
   moves it with `ld L,A`. A call through a pointer, and a function that another file defines, keep the full word.
2. **Switch key.** A `switch` on an `int` expression that is 0 to 255 (or -128 to 127) compares one byte. A case
   value outside the range cannot match, so the compiler drops it. Before this rule, `switch (cmd >> 4)` made a
   16-bit key and a test of the high byte.
3. **Operand through a pointer.** The second operand of a byte `cp`, `sub`, `add`, `and`, `or` or `xor` can be
   `*p`, `p->m` or an element of a char array. The compiler loads HL with the address and uses `(HL)`. The old
   code had `push AF`, `ld E,(HL)`, `pop AF` and `cp E`.
4. **Operand order.** Take a byte comparison of a variable and a complex expression that has no side effect.
   The compiler turns the comparison round, so that the variable is the operand of `cp`. The complex side goes
   to A.
5. **Casts to a char.** The compiler does not evaluate the casts inside the operand. `(uint8_t)(int8_t)c` makes
   one extension, and `(uint8_t)c` of an `unsigned char` makes none. A cast of a variable or an element to a
   char goes through A.
6. **Sign test.** `(int8_t)x >= 0` and `(int8_t)x < 0` become `bit 7,A`. The old code had `xor 128` and `cp 128`.
7. **Locals that hold bytes.** The compiler treats an `int` local as a byte value (`_byte_kind`) when each value
   that the program assigns to the variable is a byte value. The variable must not be a parameter. The code must
   not take its address. `++`, `--` and a compound assignment must not change it. The function must have no inline
   assembly. The function must declare the variable one time. The names in the assigned expressions must mean the
   same thing at every place. A signed value in an unsigned variable is not a byte value.
   See `_scan_byte_holders` and `_holder_kind`.

## Peephole rules in `peep_local.py`

1. **Dead register write.** The rule deletes `ld R,x` when the next instruction writes R again and does not read R.
   `x` must be an immediate or a register. A load from memory stays, because it can be a volatile read.
   Example: `ld H,0` and then `ld H,L`.
2. **Byte to the high half.** `ld L,S` / `ld H,L` / `ld L,0` becomes `ld H,S` / `ld L,0`.
3. **Tail call.** `call X` and then `ret` (with labels between them) becomes `jp X`. A cleanup instruction
   between the two prevents the rule. The rule skips `setjmp`, because `setjmp` saves the return address.

A label, a directive, an inline-asm region and a volatile fence stop rules 1 and 2. Rule 3 keeps the labels
where they are.

## Not done

- The value form of a signed byte compare against 0 (`x < 0` as a number) still uses `xor 128`.
- A `char` result of a call is not a byte value, so `p = f()` does not make `p` a byte holder.
- A loop counter that changes by `++` is not a byte holder, because the compiler has no range analysis.

## Tests

`tests/test_byte_paths.py` builds programs, runs them on the Z80 (`cpmemu`) and compares the results with a
Python model. It also checks the shape of the code and the rules of `peep_local`.
