# Unsigned 8-bit divide and modulo by a constant

Back to [codegen_gap.md](codegen_gap.md). This page describes the code that uc80 makes for `x / K` and `x % K`.
Here x is a byte value and K is a constant from 1 to 255.

## When the rule applies

The compiler tests the value range of the left operand with `_byte_kind` in `codegen.py`. It does not test the type.
The compiler must know that the left operand is 0 to 255. These operands qualify:

- an `unsigned char` variable, array element, member or `*p`
- a masked value, such as `x & 0xFF`
- the result of another such division
- a constant
- a local `int` that only holds bytes (see [byte_paths.md](byte_paths.md))

A `signed char` does not qualify. A `signed char`, an `int` and an `unsigned short` stay on the 16-bit path.
That path calls `__sdiv16`, `__smod16`, `__div16` or `__mod16`.

## What the compiler makes

A divisor that is a power of two becomes a shift or a mask. The compiler makes no call.

Another divisor uses one of two methods.

1. A reciprocal multiply. The quotient is `(x * M) >> S`, with `M = ceil(2**S / K)`. The compiler uses this method
   when the result is exact for all x from 0 to 255 and `x * M` fits in 16 bits. The multiply is the inline
   shift-and-add chain of `_gen_mul_const`. The remainder is `x - q * K`. For 192 of the 247 divisors that are not
   a power of two, such a pair `M`, `S` exists. The compiler accepts the pair only when the cost is below a limit
   (`_RECIP_MAX_T` and `_RECIP_MAX_T_REM` in `codegen.py`).
2. A call of `__dv8n`. This helper is the byte step of the 16-bit divide. The compiler sets A to 0, E to x and
   C to K. The call returns the quotient in E and the remainder in A. The helper changes B.

The program links `__dv8n` only when a call remains. The program links `__sdiv16`, `__div16` and `__dv8w` only
when a 16-bit division remains.

## One division for a pair

Two adjacent block items give one division:

```c
q = x / 10;  r = x % 10;
unsigned char block = note / 12;  unsigned char semi = note % 12;
```

The first division keeps the other result in register B. The second item reads B.

The compiler checks the instructions between the two items. These instructions must not use B or BC. They must
not branch, call or define a label. If the check fails, the second item makes its own division.

Each target must be a plain integer variable. The variable x must be a plain variable that is not volatile.
The two targets and x must be three different variables.

## Choice between the two methods

The choice comes from the bench programs, from `bench/run.py` (uc80, size mode, T-states and image bytes).

- `snd-fnum`: 18.83M T and 2830 B before family D. With `__dv8n` only: 15.33M T and 2611 B. With the reciprocal
  multiply: 13.98M T and 2634 B.
- `snd-cmdq`: 14.32M T and 3075 B before family D. With `__dv8n` only: 14.17M T and 2904 B. With the reciprocal
  multiply: 14.05M T and 2879 B.

The reciprocal multiply is faster in both programs. In `snd-fnum` it costs 23 B more than `__dv8n`. In `snd-cmdq`
the program is smaller, because the divisor 3 needs a short chain and no helper stays in the image. The compiler
uses the reciprocal multiply when its cost is low. For a divisor that needs a long chain, the call of `__dv8n`
is smaller and the compiler uses the call.

## Test

`tests/test_u8_divmod.py` runs every dividend from 0 to 255 with every divisor from 1 to 255 on the Z80
(`cpmemu`). It compares a checksum of the quotients and of the remainders with a Python model. The test uses
fused statements, fused declarations, a compound assignment and statements that are not adjacent. A smaller set
covers an int context, a masked int and a reversed pair. Another test checks that signed and 16-bit operands
give the C result.
