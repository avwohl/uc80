# Division by a constant that the range decides

A 32-bit division costs about 2000 T-states. The benchmark `stanford-quick` does this in a loop:

```c
long temp;
temp = Rand();                        /* Rand returns an int */
sortlist[i] = temp - (temp/100000L)*100000L - 50000L;
```

The variable `temp` is a `long`, but it holds the result of a function that returns an `int`. The value is in -32768 to 32767. So `temp / 100000L` is 0. This pass finds such a division and puts the result in the place of the operation.

Family J step 5c in [family_j.md](family_j.md) lists the change. The code is in `src/uc80/rangediv.py`.

## How the pass finds the range

The range of an expression comes from these rules:

- A constant has its value.
- An expression of a type of 16 bits or less holds a value of that type. The code cannot change this.
- A cast keeps the range of the operand when the new type holds the range. When the new type is narrower, the range is the range of the new type.
- A local variable of 32 bits has the union of the ranges of all values that the function stores to the variable. Each store has the form `v = e` or `T v = e`.

A local variable has a range when all of these are true:

- The variable is not a parameter. It is not `static`. It is not `volatile`.
- The code never takes the address of the variable.
- No store has the form `++`, `--` or `op=` (for example `+=`).

An expression of another form has no range.

## What the pass changes

For `x / k` and `x % k` with a constant `k` above 0, the pass uses the range of `x`:

- A signed operation: when -k < x < k, `x / k` gives 0 and `x % k` gives `x`.
- An unsigned operation: when 0 <= x < k, `x / k` gives 0 and `x % k` gives `x`.

The pass puts a literal 0 in the place of `x / k`. The literal has the type of the operation. The pass puts `x` in the place of `x % k` when `x` has the type of the result. The dividend must have no side effect (no call, no `++`, no assignment).

The pass runs before the loop-invariant pass. Then the product `0 * 100000L` has a constant value.

The pass needs a 16-bit `int`. It does not look at the 64-bit types or at float.

## Switches

- `--no-range-div` keeps the division.
- `UC80_RANGEDIV_DEBUG=1` prints one line for each folded operation.
- `UC80_RANGEDIV_ONLY=f,g` limits the pass to the named functions.

## Measured effect

`stanford-quick`, T-states between `TIMER_START` and `TIMER_STOP`: 308,481,039 before, 277,555,354 after (-10.0%). The function `Initarr` calls `__div32` 25,000 times before. After the change, it calls the routine no more.

## Tests

The file `tests/test_famj_range_div.py` has the test. It runs one program in six option sets against the host compiler. The program has these cases:

- the shape of `Initarr`, with negative values.
- unsigned operands.
- ranges that are too wide.
- a store of the form `+=`, `++` or through a pointer.
- a `volatile` variable, a parameter and a global.
- chars and shorts.
- constants at the limit (99 and 100 for a divisor of 100).

A shape test checks that the function of the `Initarr` form has no call of the division routine.
