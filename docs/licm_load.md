# Loop-invariant loads and row addresses

A loop often reads the same array element in each pass. The loop `for (k = 0; k <= piecemax[i]; k++)` loads `piecemax[i]` in each pass, and `i` does not change. The load costs about 80 T-states (index, scale, base address, two byte loads). The address `p[i]` of a row of a two-dimensional array costs about 60 T-states. The expression `p[i][k]` computes this address in each pass. This pass loads the element one time, before the loop.

The pass is part of [licm.md](licm.md). The code is in `src/uc80/licm.py`. Family J step 3 in [family_j.md](family_j.md) lists the change.

## An example

The function `Fit` of `stanford-puzzle` has this loop:

```c
int Fit(int i, int j) {
    int k;
    for (k = 0; k <= piecemax[i]; k++)
        if (p[i][k]) if (puzzl[j+k]) return 0;
    return 1;
}
```

The pass changes the code to this form:

```c
{
    int  __licm1 = piecemax[i];
    int *__licm2 = &p[i][0];
    for (k = 0; k <= __licm1; k++)
        if (__licm2[k]) if (puzzl[j+k]) return 0;
}
```

The new variables are ordinary locals. The register-locals pass can move them to BC, DE or IY ([register_locals.md](register_locals.md)).

## What can move

The pass looks at each `Index` node of the loop. It moves three kinds of node.

- A load `arr[e]` moves when all of these are true:
  - `arr` is a named array. The array is global or local, and the loop does not declare the name again.
  - The element type is an integer type or a pointer. The type is not volatile.
  - `e` is a loop-invariant integer expression (see [licm.md](licm.md)) that is not a constant. A chain `m[e1][e2]` of arrays is the same.
  - The loop has no call and no store through a pointer, an array element reached through a pointer, or a `->` member.
  - The loop does not store to `arr`, in any way. A store to another array or to a scalar does not matter.
  - No interrupt function writes or reads the name. The pass collects the names of each interrupt function and of each function that the interrupt function calls.
  - The node is only read. The node is not under `&`, `++`, `--` or on the left of an assignment.
- A row `m[e]` of a two-dimensional array moves when `e` is invariant and not a constant. The row must stand where a pointer would stand (the array operand of another `Index`). The new local is a pointer to the element. The pass moves the address, not memory, so a store in the loop does not matter. A call does not matter.
- A sum `base[inv + var]` becomes `ptr[var]`, with `ptr = &base[inv]`. The sum `inv + var` has 16 bits or less. The part `inv` is invariant and `var` is not. The pass does this only when `inv` is expensive, because a cheap sum gains little. The switch `UC80_LICM_MINCOST_ADDR` sets the limit.

The use must run in each pass of the loop (the same rule as in [licm.md](licm.md)). A use in an `if` branch, in the right side of `&&`, in a branch of `?:` or after a `break` does not count. A move for a use that a branch may skip costs more than the move gains. In `Fit`, the test `puzzl[j+k]` runs for only a few values of `k`.

The switch `UC80_LICM_MINCOST_LOAD` sets the cost limit for a load or a row. The default is 40 T-states.

## Why a volatile or a call stops the move

A call can write any global array. A store through a pointer can write any array whose address the program took. A named array that the loop stores to is not the same value in the next pass. An array that an interrupt function uses can change at any time, and the program must declare such an array `volatile` (the compiler never moves a volatile access).

## When the pass does nothing

The old rules of [licm.md](licm.md) stop the pass in these cases:

- The loop has a label.
- The loop has a `case` label of an outer switch.
- The function is a naked function or an interrupt function.
- The function has inline assembly, a statement expression or `setjmp`.

## Switches

- `--no-licm-load` keeps the loads and the row addresses in the loop. The old pass (`--no-licm` turns it off) still runs.
- `UC80_LICM_DEBUG=1` prints one line for each moved part. The key starts with `M:` for a load, `R:` for a row and `A:` for a sum.
- `UC80_LICM_MINCOST_LOAD` and `UC80_LICM_MINCOST_ADDR` change the cost limits.

## Measured effect

`stanford-puzzle`, T-states between `TIMER_START` and `TIMER_STOP`: 431,403,793 before, 283,370,063 after (-34.3%). The functions `Fit`, `Place` and `Unfit` hold most of the run. The image grows by 23 bytes (each moved part adds a store before the loop).

The file `tests/test_famj_licm_load.py` has the test. It runs one program in nine option sets against the host compiler. The program has loops that must keep the load:

- a store to the array.
- a store through a pointer.
- a call.
- a volatile bound.
- a label.
- a `break` before the use.
- a loop with no passes.
