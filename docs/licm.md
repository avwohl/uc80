# Loop-invariant code motion

A loop often computes the same integer value in each pass. The loop `for (x = 0; x < 80; x++) i = y * 80 + x;` multiplies `y` by 80 in each pass, but `y` does not change in the loop. The multiply costs about 110 T-states. This pass computes the value one time, before the loop. It is part 1 of family I step 2 in [codegen_gap.md](codegen_gap.md). The code is in `src/uc80/licm.py`.

## An example

The function `tc_wr` of the curses library has this loop:

```c
for (y = 0; y < NR; y++) {
    for (x = 0; x < NC; x++) {
        i = y * NC + x;
        c = tc_vi[i];
        ...
```

The pass changes the inner loop to this form:

```c
for (y = 0; y < NR; y++) {
    {
        int __licm1 = y * 80;
        for (x = 0; x < NC; x++) {
            i = __licm1 + x;
            ...
```

The new variable is an ordinary local. The register-locals pass can move the variable to BC or DE ([register_locals.md](register_locals.md)).

## Where the pass runs

The pass changes the AST of each function. It runs in `generate()` after address CSE and before `_plan_register_cc` and the code that finds the size of each frame. The new locals are real declarations, so each frame includes them. Each declaration has the type `typeof_unqual(expression)`, which the code generator resolves. The pass for 16-bit indexes ([narrow_index.md](narrow_index.md)) runs first. A 16-bit expression is cheaper to move than a 32-bit expression.

## What can move

An expression moves when all of these are true:

- It has only these parts:
  - integer constants and local variables.
  - casts to integer types.
  - the unary operators `-`, `~` and `+`.
  - the binary operators `+`, `-`, `*`, `<<`, `>>`, `&`, `|` and `^`.
- A shift has a constant count from 0 to 31.
- Each variable is an integer local with automatic storage. The loop never assigns the variable (`=`, `op=`, `++`, `--`). The loop does not declare a variable of the same name. The code never takes the address of the variable. The variable is not volatile.
- The expression costs at least 40 T-states. A table in the pass gives the cost of each operator. A multiply by a constant costs 11 T-states for each shift and 12 for each add. A multiply by a variable costs 200. A 32-bit operation costs 2.5 times as much. `UC80_LICM_MINCOST` changes the limit.

The expression has no load, no call and no division. The expression cannot trap, and no other code can change the value. The pass also evaluates a moved expression when the loop runs zero times. The evaluation has no side effect, so this is safe.

The expression must run in each pass of the loop, or the pass must find an equal expression that does. The pass does not move an expression from these places:

- an `if` branch.
- the right side of `&&` or `||`.
- a branch of `?:`.
- a `case`.
- a statement after a `break`, `continue`, `goto` or `return` in the same block.

The pass does not move parts out of `do { ... } while (0)`. A loop that runs one time has no gain.

The pass takes the largest such part of each expression. In `y * 80 + x` the part `y * 80` moves, because `x` changes. Equal parts in one loop share one new local. The pass handles the outer loop first. An expression that is invariant in both loops moves out of the outer loop.

## When the pass does nothing

- The loop has a label. A jump into the loop would skip the moved code.
- The loop has a `case` or `default` label that belongs to a switch outside the loop (Duff's device).
- The function is a naked function, an interrupt function or a vector handler.
- The function has inline assembly, a statement expression or `setjmp`.

An expression that reads a global, an array element or a pointer target never moves in this part of the pass. [licm_load.md](licm_load.md) describes the part that moves the load of an array element and the address of a row (family J step 3).

## Switches

- `--no-licm` switches the pass off.
- `UC80_LICM_DEBUG=1` prints one line for each moved expression.
- `UC80_LICM_ONLY=f,g` limits the pass to the named functions. `UC80_LICM_MAX=n` limits the number of moved expressions to n. Both help to find a wrong change.

## Measured effect

T-states of the whole run, `bench/run.py -c uc80`. The column "pass alone" is the change against the commit before when only this pass runs (the other two switches are off). The column "all" is the final tree.

- worm: 610,621,319, pass alone 468,701,284 (-23.2%), all 441,322,339.
- mille: 1,065,376,490, pass alone 826,852,922 (-22.4%), all 780,560,807.
- canfield: 760,069,409, pass alone 589,214,273 (-22.5%), all 556,247,393.
- coremark-unofficial: 35,622,373, pass alone 29,755,312 (-16.5%, with the range pass), all 26,892,044.
- curses-demo, robots, snake and invaders: -25%, -23%, -20% and -21%. These programs use the same curses library.

One program grows: `micro` has 7 bytes more, the size of one new local of `copy_init` (`i * 16` costs 44 T-states). No program is slower.

The pass skips the places in the list above, and it skips `do { ... } while (0)`. An earlier version also moved parts of this kind. That version made `umchess` 11% slower. An expression in a rare branch of the inner loop of the chess search was the cause. The same version made `embench-nettle-sha256` 503 bytes bigger. The `ROUND` macro was the cause.

## Tests

`tests/test_licm.py` runs programs in nine option sets (default, `--no-licm`, `-O0`, `--no-register-locals`, `--no-shared-storage`, `--no-addr-cse`, `--no-inlining`, `--no-register-cc` and `--isr-safe`). The output must equal the output of the host compiler. The programs cover these cases:

- the shape of `tc_wr` with a checksum.
- a loop that changes `y` in the body or in the update clause.
- a taken address, a volatile local, a global and a division.
- a loop that runs zero times.
- `break`, `continue` and `goto`.
- a switch in the loop, and Duff's device.
- a recursive function, three nested loops and a shadowed name.
- 32-bit and narrow types.

 Shape tests read the debug lines and check which functions the pass changes. A test of a ROM program checks that an interrupt function stays unchanged. Shape tests check that a part that only some passes evaluate and a `do ... while (0)` stay in place.
