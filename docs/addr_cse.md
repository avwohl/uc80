# Address CSE and chained constant stores

The code generator computes the address of an array element again for each access. The expression `a[i] = a[i] + a[i + 1]` builds `&a[i]` two times and `&a[i + 1]` one time. Each build costs 37 to 60 T-states and uses BC, DE and HL as scratch registers. This pass computes a repeated address one time. It is part 1 of family H in [codegen_gap.md](codegen_gap.md). The code is in `src/uc80/addrcse.py`.

The pass does two things:

- Address CSE. A repeated element address becomes a new pointer local. All accesses go through the pointer.
- Chained constant stores. `a[i] = b[j] = 1` becomes `b[j] = 1; a[i] = 1`.

## An example

```c
if (sortlist[i] > sortlist[i + 1]) {
    j = sortlist[i];
    sortlist[i] = sortlist[i + 1];
    sortlist[i + 1] = j;
}
```

The pass makes one pointer for the group `sortlist[i + k]` and declares the pointer before the `if`. The six accesses become `*t`, `t[1]`, `*t`, `*t`, `t[1]` and `t[1]`. The pointer is an ordinary local. The register-locals pass can move the pointer to IY, BC or DE ([register_locals.md](register_locals.md)).

## Where the pass runs

The pass changes the AST of each function. It runs in `generate()`, after the inliner and the global tables. It runs before `_plan_register_cc` and before the code that finds the size of each frame. The new locals are real declarations, so the size of `??AUTO` and of the IX frame includes them. Each declaration has the type `typeof(&a[i])`, which the code generator resolves.

## What is a key

An element address is a key when all of these are true:

- The access has the form `base[index]`. The type of the element is not an array, `void` or `volatile`.
- The base is a named array (a global, a static, a local array, an array member `s.arr` or `p->arr`, or a row `a[i]` of an array of arrays), or a local pointer.
- The index is an integer expression of `+ - * << >> & | ^`, unary `- ~ +`, casts to integer types, constants, and these leaves:
  - a local variable of integer type. The variable is not volatile, and no `&` is on its name.
  - a load from a named array, or a global scalar (a memory leaf, see below).
- A constant at the end of the index (`i + 1` or `i - 2`, at most 100) is not part of the key. The accesses `a[i]` and `a[i + 1]` share one pointer, and the second access becomes `t[1]`.

A group is all accesses with the same key (the same variables, not the same names) in one block.

## When a run ends

A run is a list of consecutive statements of one block. All the accesses of a run use one pointer, which the pass declares before the first statement that has an access. A statement ends the run (and does not belong to it) when, anywhere inside the statement, one of these is true:

- The statement assigns a variable of the key (`=`, `+=`, `++`, `--`) or declares the variable. The pass looks into loops and `if` branches. A `for` statement that changes `i` therefore ends the run for every key with `i`.
- The statement holds a label (a `goto` target, `case` or `default`). The exception is a `switch` that is wholly inside the statement with its own `case` labels.
- The key has a memory leaf, and the statement has one of these: a call, a store through a pointer, a store into the named object of the leaf, or a volatile access.

A leaf that is a local variable can only change by an assignment in the function. A call, a store through a pointer or an interrupt cannot change such a variable, so they end no run. A memory leaf can change in all these ways. A function that the program marks as an interrupt function (`#pragma uc80 interrupt`, `nmi`, `im2` or `rst N interrupt`) turns memory leaves off for the whole program. In a separate compilation, a module that has no handler cannot know about a handler in another module, and memory leaves stay on in that module.

A handler that `#pragma uc80 vector` binds, and a program with hand-written assembly files, do the same. A naked function and a function with inline assembly are not changed. A function with a variable length array is not supported by the compiler, so the pass does not treat this case in a special way.

The pass does not touch a function with inline assembly, a statement expression or a call of `setjmp`.

Each variable of a key must be visible at the declaration of the pointer. A variable of an inner block cannot be in the key of a run of an outer block.

## The cost model

A group is worth a pointer when the estimated gain is at least 12 T-states and at least one access is sure (no `if`, `switch`, `&&`, `||` or `?:` between the first statement of the run and the access). The estimate assumes that the pointer stays in memory:

- The address costs about 10 for an array base, 16 for a variable, 11 for each add, 11 for each shift and 100 for a multiplication.
- The pointer costs the address and a store of 16 at the start, and 16 for each access (30 for a `t[k]` access).
- An access in a loop counts 8 times for each loop level, and an access in a branch counts one half.

The register-locals pass makes the gain bigger when the pointer gets a register.

## Chained constant stores

`a = b = K;` is a statement of its own. The pass changes the statement when K is an integer constant that fits each target exactly. At least one target must be a byte that is not a plain variable. Then the pass writes one store for each target, from the right to the left. The targets can be variables, array elements, `*p` and members.

A run of plain variables stays a chain of its own (`x = y = K`), because one load of K serves all of them. A chain of word stores through pointers stays as it is. The pass does not change the statement when:

- One target has a type that cannot hold K. Examples are a bit-field, a `bool`, a float and a volatile object. The statement `uc = n = 300` stays a chain, because `uc` becomes 44.
- The program uses the value of the chain, as in `if ((a = b = 0))`.

## Options and debug switches

- `--no-addr-cse` turns off the address CSE.
- `--no-chain-store` turns off the chained constant stores.
- `UC80_ACSE_DEBUG=1` prints each new pointer (function, key, number of accesses, estimated gain, memory roots) and each chain.
- `UC80_ACSE_MAX=n` makes at most n pointers in the program. Use the switch to find the pointer of a wrong answer: a binary search over n names the pointer.
- `UC80_ACSE_ONLY=f,g` restricts the pass to the functions `f` and `g`.
- `UC80_ACSE_NO_MEM=1` turns off memory leaves.
- `UC80_ACSE_MIN_GAIN=x` sets the least gain (default 12).
- `UC80_ACSE_BRANCH_W` sets the weight of an access behind a branch (default 0.5).
- `UC80_ACSE_LOOP_W` sets the weight of an access in a loop (default 8 for each level).
- `UC80_ACSE_NEED_CERT` sets the number of sure accesses that a group needs (default 1).

## Tests

`tests/test_addr_cse.py` runs four programs in ten modes. The expected text comes from the host compiler. The programs hold the cases that must stay as they are:

- an index that changes between two accesses
- an index with its address taken
- a pointer to the element
- a call or a store between two accesses
- a label in the run
- a volatile array
- a `switch`
- a `do` loop with `++i` in the condition
- a shadowed index

The shape tests check which functions get a pointer. Part of the register-locals tests (`tests/test_regl_bugfix_interaction.py`) uses `--no-addr-cse`, because the pass moves the gain of `fcount` out of the register-locals pass.

## Constant addresses and the ROM pass

The ROM pass (`ptrabs.py`) runs first. It changes a volatile access through a pointer with a constant address into a named object at that address (`static volatile uint8_t *const p = (volatile uint8_t *)0x4000;`). The address CSE pass sees such an object as a global volatile object. A volatile access ends a run, and the object is never a key.

An access that is not volatile stays an access through the pointer. An example is `static struct S *const sp = (struct S *)0xFA00;` with plain members. The address CSE pass can make a new pointer local for a repeated element address there. The writes are the same with `--no-direct-pointers`. `tests/test_famh_rom_integration.py` compares the writes and the RAM with the build that has no pass.

## Measured effect

The runs use `bench/run.py -c uc80` (size mode) on the 109 programs of the size run, without `battlestar-size`, `rogue36-size` and `empire-trefonas`. The base is the file `bench/results/results.csv` of the tree before this work (commit e28deae). No program changes its status.

- Image: 56 programs get smaller, 1 gets bigger (`bsd-bcd` +2 B) and 50 stay the same. The image total of the 107 programs with an image in both runs goes from 677,727 to 672,357 bytes (-0.79 percent).
- T-states: 48 programs get faster and 2 get slower. The geometric mean of the T-state ratio is 0.984 (104 programs).
- Against SDCC 4.6.0 (method of `bench/report.py`): size 0.9353 to 0.9276, whole image 0.9000 to 0.8928, T-states in speed mode 0.5628 to 0.5515 (without `sort-*`: 0.6844 to 0.6691), and uc80 is faster than SDCC on 62 of 78 programs (60 before).

The table shows the programs that the pass changes most. "Part" says which part of this work gives the change.

| Program | Image | T-states | Part |
|---|---|---|---|
| `coremark-unofficial` | 16,766 to 16,138 B | -21.55% | address CSE |
| `queens` | 868 to 844 B | -20.57% | chained stores |
| `embench-matmult-int` | 6,944 to 6,896 B | -18.57% | address CSE |
| `stanford-towers` | 2,256 to 2,209 B | -12.89% | address CSE (memory keys) |
| `stanford-bubble` | 1,554 to 1,514 B | -12.45% | address CSE |
| `embench-ud` | 2,743 to 2,655 B | -5.95% | address CSE |
| `stanford-perm` | 475 to 463 B | -5.17% | names under `&` |
| `fannkuch` | 593 to 579 B | -4.96% | address CSE |
| `stanford-quick` | 1,803 to 1,763 B | -2.64% | address CSE |
| `stanford-tree` | 2,382 to 2,333 B | -2.31% | address CSE and names under `&` |
| `invaders` | 12,140 to 11,943 B | -2.22% | names under `&` |
| `dhrystone21` | 1,873 to 1,833 B | -2.01% | address CSE |
| `curses-demo` | 6,529 to 6,206 B | -1.91% | names under `&` |

The part "names under `&`" is a change of the register-locals pass: `&a[i]` and `&p->m` no longer count as an address taken for `i` and `p` ([register_locals.md](register_locals.md)). This pass does not need the change, because the pointer that it makes carries a flag that the register-locals pass accepts. The change helps the same code patterns, so it comes with this work.

The two parts, each measured alone:

- The change of the names under `&`, with both options of this pass off: 18 programs smaller, none bigger, 15 faster and 3 slower.
- The address CSE and the chained stores, with the old rule for the names (`UC80_RL_TAINT_ALL=1`): 49 programs smaller, 1 bigger, 42 faster and 1 slower.

Programs that get slower or bigger:

- `snd-ymreg` +0.42% T-states (-3 B). The change of the names under `&` lets `ym_write` keep `reg` in BC and `s` in DE. The cost model of the register-locals pass estimates a gain of 50 (against 28 for the old choice), but the real code is slower.
- `v7-arithmetic` +42 T-states of 1.8 million (+0.002%, -4 B). One pointer in `main`, estimated gain 44 T-states, whose accesses are not repaid in this run.
- `bsd-bcd` +2 B (-2.23% T-states). One pointer in `printcard`.
- `v7-wump` and `bsd-arithmetic` get slower by 0.15% and 0.06% when only the change of the names under `&` is on. With the whole work they are faster (-0.16% and -0.01%).

Two first designs were worse.

- The first design counted an access behind a branch as one half, and it needed no sure access. A pointer in front of a branch that the program rarely takes made `umchess` 1.6% slower. The rule now is: a group needs at least one access that no branch skips.
- The first design split every chain. Five programs got 6 bytes bigger, because a chain of word stores through struct pointers is smaller as it is. The rule now is: split a chain only when a target is a byte.

## Not done

- Value CSE. The pass keeps the address, not the loaded value. A value in a register would also save the load.
- The loads of `p[i]` through a pointer as a memory leaf (any store would end the run).
- The initial expression of a pointer does not use an inner pointer. In `cell[stack[s]]`, the initial expression computes the load `stack[s]`, and the other accesses of `stack[s]` compute the address again.
- An address in a loop that has a call: the call ends the run for each key that has a memory leaf.
