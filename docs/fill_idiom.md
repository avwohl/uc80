# A loop that fills an array

```c
for (i = 0; i <= 8190; i++) flags[i] = 1.
```

The loop costs about 126 T-states for each byte. A fill with `LDIR` costs about 21 T-states for each byte.

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change. The code is in `src/uc80/fillidiom.py`. The fill is the inline fill of [blockops.md](blockops.md).

## The change

The code generator emits the init clause, one fill, and a store of the final value of `i`. The final value is the last index plus 1.

The loop must have this form:

- the init clause is `i = C0`, the test is `i < N` or `i <= N`, and the update is `i++`, `++i` or `i += 1`. C0 and N are integer constants.
- the body is one assignment `a[i] = K` with a constant K, alone or in a block.
- `i` is not volatile, and its type holds the final value.
- `a` is a global array of single bytes. It is not volatile, not const and not `_Bool`. The last index is inside the array.
- the loop runs at least 3 times.

A loop over an `int` array, a loop with a second statement, and a loop over a local array stay loops.

The program `davidly-sieve` needs 7.9 million T-states less.

## Switch

`--no-fill-idiom` keeps the loop. `--no-blockops` turns the idiom off too.

## Tests

`tests/test_famj_kernels.py` runs fills of 400 bytes and of `signed char`. It runs a fill of `char` with an `unsigned` counter, and fills of an `int` array (no change), of 2 bytes, of an empty range and of a volatile array. It prints sums of the arrays and the counters after the loops. The output must be the same as the host output for the default build, `--no-fill-idiom`, `-O0`, `--no-blockops`, `--no-register-locals` and `--int=32`.
