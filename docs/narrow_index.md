# Index narrowing

A pointer of uc80 has 16 bits. In `a[i * n + j]` with 32-bit `i`, `n` and `j`, only the low 16 bits of the index reach the address. The code computed the index in 32 bits, with a call to `__mul32`. This pass computes the index in 16 bits. It is part 3 of family I step 2 in [codegen_gap.md](codegen_gap.md). The code is in `src/uc80/narrow.py`.

The plan named `ast_opt.py` as the place of this pass. That module does not know the types of variables. So the pass is a separate module in the code generator, as address CSE and the loop pass are.

## An example

```c
unsigned long i, n, j;
x = a[i * n + j];
```

The index becomes `(unsigned)i * (unsigned)n + (unsigned)j`. The 16-bit multiply is a short routine. The 32-bit multiply costs about 30 times as many T-states.

## What changes

The pass changes the index of `a[e]` and the integer operand of `p + e`, `e + p`, `p - e`, `p += e` and `p -= e`. The type of `e` has 32 bits. The top operator of `e` must be one of `+`, `-`, `*`, `<<`, `&`, `|`, `^`, unary `-` or unary `~`.

The low 16 bits of the result of these operators depend on the low 16 bits of the operands only. The pass applies the rule to the whole tree. Each leaf gets a cast to `unsigned`, which is the same value modulo 65536.

A leaf is a variable, a call, a division, a modulo, a right shift, a comparison or a cast to a type narrower than 16 bits. A constant becomes its low 16 bits. A shift to the left needs a constant count from 0 to 15. A cast to a type of 16 bits or more does not change the low 16 bits, so the pass looks through the cast.

The code evaluates each leaf one time, as before. A volatile read or a call stays one read or one call.

## When the pass does nothing

- The index has 64 bits, or has a float or pointer leaf. The pass leaves such an expression alone.
- The configuration has `--int 32`. The pass needs a 16-bit `int` and a 16-bit pointer.
- The top operator of the index is a division, a modulo, a right shift or a call. A change would only add a cast.
- The subtraction of two pointers has pointer operands and no integer operand, so the pass does not change `p - q`.
- The function is a naked function, or has inline assembly, a statement expression or `setjmp`.

## Switches

- `--no-narrow-index` switches the pass off.
- `UC80_NARROW_DEBUG=1` prints one line for each narrowed index. `UC80_NARROW_ONLY=f,g` limits the pass to the named functions.

## Measured effect

T-states of the whole run, change against the commit before:

- coremark-unofficial: 35,622,373 to 29,943,916 with this pass and the range pass (-15.9%). The loop pass alone and the range pass give 29,755,312 without this pass. All three: 26,892,044 (-24.5%).
- The image of coremark-unofficial falls from 16,053 to 15,883 bytes.

The other bench programs have no 32-bit index, so the pass does not change them.

## Order of evaluation

C does not set the order of the two operands of `+`. When an index has two calls, such as `nxt() + nxt() * 3`, the narrowed code can call them in a different order than the 32-bit code. Both orders are legal. Use `--no-narrow-index` if a program depends on one order.

## Tests

`tests/test_narrow_index.py` runs programs in eight option sets. The output must equal the output of the host compiler for the indexes that stay inside the arrays. A second program has indexes that wrap (the address is the base plus the index modulo 65536), and the text of the expected output comes from the arithmetic in the comments. The programs cover these cases:

- a 32-bit multiply that overflows 16 bits, and a negative 32-bit index.
- shifts and bit operations.
- a division, a modulo and a right shift as leaves.
- `char` leaves, a volatile leaf, and a post-increment in the index.
- pointer arithmetic in all five forms.
- struct member arrays, two-dimensional arrays and a `long` index.

Shape tests check that a 64-bit index and a pointer difference stay unchanged. They also check that the call of `__mul32` is gone from the code of the example.
