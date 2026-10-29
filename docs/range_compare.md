# Value ranges and the unsigned compare

A signed 16-bit compare of a variable with a constant costs 44 T-states. The signed order needs the sign flip: `ld A,H`, `xor 128`, `ld D,A` and more.

An unsigned compare costs 22 T-states: `ld A,L`, `sub lo`, `ld A,H`, `sbc A,hi`. The two compares give the same answer when both operands are in 0 to 32767. This pass finds the operands that have such a range.

The pass is part 2 of family I step 2 in [codegen_gap.md](codegen_gap.md). The code is in `src/uc80/ranges.py`.

## An example

```c
for (x = 0; x < 80; x++) { ... }
```

The variable `x` is 0 in the init clause. The update clause adds 1 only after the condition `x < 80` was true. So `x` is in 0 to 80 everywhere in the loop. The compare `x < 80` uses the unsigned form.

## Ranges of a function

A local variable has a range when all of these are true:

- The variable has the type `int` or `short`, with 16 bits and a sign. The variable is not volatile.
- It has automatic storage and is not a parameter.
- The code never takes its address.
- The function has no label, no inline assembly and no statement expression.

Each store to the variable adds to its range:

- A store `v = e` or an initializer adds the range of `e`.
- The update clause of a `for` statement (`v++`, `++v` or `v += s`) adds a value only when three things are true. The condition has a conjunct `v < L` or `v <= L`. The body and the condition do not store to `v`. The step `s` is not negative. The new value is at most the largest value that the condition allows, plus the largest `s`.
- Each other store (`v--`, `v -= e`, a `v++` in the body) gives the full range. The variable then has no range.

The pass solves these equations by iteration. A last check proves that each range holds the values that the stores give. A variable that fails the check gets no range.

## Ranges of expressions

The range of an expression comes from the ranges of the variables and from integer constants. These operators have a rule:

- `+`, `-` and `*`.
- `<<` and `>>` by a constant.
- `&`, `|` and `^`.
- `%` and `/` by a positive constant.
- the unary `-` and `~`.

A mask `x & 127` has the range 0 to 127 for each `x`. A cast keeps the range when the new type holds the range. A result outside the range of a 16-bit int has no range, because the code wraps.

## Ranges inside a loop

A `for` statement with a counter gives a range for the counter inside the statement. The init clause must store `v = e` (or declare `v` with an initializer). The update clause and the condition must have the form above. The range goes from the lowest value of `e` up to the largest value that the update can give.

The range holds also when the function stores to `v` in another place, for example `y = w->begy + w->cury;` after the loop. The body must not store to `v`, and must not declare another `v`. The loop must have no label and no case label of an outer switch. A jump into the loop could skip the condition.

## Where the code generator uses the ranges

`_cmp16_unsigned` asks for the range of both operands. When both are in 0 to 32767, the compare uses the unsigned form. `_emit_cmp16_const` then emits the short form, and the register D stays free. The function `gen_for` sets the loop range for the code of the loop.

## Switches

- `--no-range-cmp` switches the pass off.
- `UC80_RANGE_DEBUG=1` prints the variables that have a non-negative range in each function, and one line for each loop range.

## Measured effect

T-states of the whole run, change against the commit before, when only this pass runs:

- worm: 610,621,319 to 583,242,331 (-4.5%). With the loop pass: 441,322,339.
- mille: 1,065,376,490 to 1,019,084,375 (-4.3%). With the loop pass: 780,560,807.
- canfield: 760,069,409 to 727,098,191 (-4.3%). With the loop pass: 556,247,393.
- davidly-sieve: 48,139,407 to 45,681,642 (-5.1%). The loop `for (k = i + prime; k <= SIZE; k += prime)` has the range 3 to 24,576 from the ranges of `i` and `prime`.
- davidly-ttt: 12,714,889 to 12,293,479 (-3.3%). The counter `p` of the loop in `MinMax` gets its range from the init clause. The function stores to `p` in other places.
- embench-edn, stanford-intmm and embench-crc32: -1.6%, -1.5% and -0.9%.

The image of each program becomes smaller or stays equal. The queens program does not change. Its counters have the type `uint8_t`, and the compiler compares such values as bytes.

## Tests

`tests/test_range_cmp.py` runs programs in eight option sets. The output must equal the output of the host compiler. The programs cover these cases:

- a loop up to the bounds 32766 and 32767, and a step of 2 near the limit.
- a stored negative constant, and a value from a function.
- a taken address, a store in the body, and a decrement.
- a mix with unsigned operands, and a compare with a negative constant.
- a parameter, and a `char` counter.
- the sieve shape, and expression ranges.
- counters that other code assigns after the loop.
- a jump into a loop.

Shape tests read the debug lines.
