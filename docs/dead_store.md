# Dead locals and overwritten stores

The pass has two parts. The first part removes a local variable that no expression reads. The second part removes a store that a later store overwrites before anything reads the value.

A local variable that no expression reads does no work. Its stores cost code and T-states. The `count` of `sieve` is one example. The program prints `count` only if you define `PRINTF`. The timed build stores to `count` in the inner loop and never reads the value. The pass removes such variables. The pass is part 2 of family H in [codegen_gap.md](codegen_gap.md). The code is in `src/uc80/deadstore.py`.

## An example

```c
count = SIZE - 2;
for (k = i_sq; k < SIZE; k += i) {
    count -= !flags[k];
    flags[k] = 1;
}
```

Nothing reads `count`. The pass removes the declaration, the first store and the statement `count -= !flags[k]`. The expression `!flags[k]` has no effect, so it goes with the store. The loop body is `flags[k] = 1`.

## Where the pass runs

The pass changes the AST of each function. It runs in `generate()` before the address CSE pass, so the CSE pass sees fewer accesses. It runs before the code generator finds the size of each frame. A removed local takes no slot and no register. The AST optimizer of `uc_core` removes only a store that the next statement overwrites. This pass finds the stores that the optimizer cannot see.

## What the pass removes

A variable is dead when no expression reads it. The pass counts the reads of each candidate. Every occurrence of the name is a read, with these exceptions. An occurrence is a store of the variable (and not a read) when the value of the expression is not used:

- `x = e;` and `x op= e;` as a statement
- `x++;` and `--x;` as a statement
- `T x = e;` as a declaration
- the same forms in the `init` and `update` parts of `for`
- either half of a comma expression, when the value of the comma expression is not used

The statements `y = x`, `a[x] = 0`, `f(x)`, `sizeof x`, `(void)x` and `z = (x = 1)` all read `x`.

A read of `x` inside the right side of a store to `x` is not a read when the right side has no effect (`x = x + 1`, `y += x; y = y * 2`). It is a read when the right side has an effect (`x = f(x)`). The pass cannot drop the call, and the call needs `x`.

The pass finds the dead variables, removes their stores and declarations, and starts again. A store whose right side read another variable can make that variable dead. This repeats until no variable is dead. `a = n; b = a + 1; c = b * 2; d = c - n;` is removed in four rounds from the last to the first.

## What stays

A right side that may have an effect stays as a statement. `r = bump();` becomes `bump();`. A right side has no effect when it holds only names, constants, operators, casts, `sizeof`, member access, array access and `*`. These stay: a call, `++`, `--`, an assignment, a statement expression, `va_arg`, a compound literal and a volatile access.

A variable is a candidate when all of these are true:

- It is a local of the function, and the declaration is the only declaration of that name in the function. A parameter, a name that is declared in two blocks, and the name of an enumerator are not candidates.
- No global or function of the file has the same name. A use of the global in another block would look like a use of the local.
- The declaration has no storage class but `register` or `auto`. A `static` local is not removed. It is not read, but the rule of the project is that a store to a static stays.
- The type is a scalar or a pointer. An array, a function and a type with `struct`, `union`, `enum`, `typeof` or `_BitInt` in the declaration are not candidates. The only qualifier is `const`.
- The name is not volatile (the table of [volatile.md](volatile.md)) and has no `&` on it (the rule of `address_taken_names`). A name under `&a[i]` counts as an address taken for `a`, not for `i`.

A declaration with several declarators and an effect in the initializer of a dead variable stays as it is. The pass cannot move the effect and keep the order of the initializers.

A function is not changed when it holds a statement expression, `setjmp`, a naked function, or inline assembly with operands or with instructions. A label alone (`asm("TIMER_START::")`, the timing label of the benchmark harness) is not a reason for the first part: it holds no instruction and cannot read a local. Other assembly can jump to the label, so a function with any `asm` skips the overwritten-store part.

An interrupt function and a function with `--isr-safe` are changed like other functions. The locals of a function are not visible to an interrupt, so no interrupt can read a dead local.

## Overwritten stores

The first store of a variable that is read somewhere can be dead too. The second part looks at one list of statements (the items of a block). It removes the store at one item when a later item of the same list overwrites the variable. The flow must not be able to read the first value on the way:

```c
int i = 0, s = 0;
for (i = 0; i < n; i++)     /* the init of the for loop overwrites i */
    s += i;
```

All of these must be true:

- The first store is `x = e;` or `T x = e;`, and `x` is a candidate (see the rules above). A candidate has no address, so a call or a pointer store cannot read the value.
- The later item is `x = e2;` or a `for` loop with `x = e2` as the init part. It runs on every path after the first store, because the two items are in the same list. `e2` does not read `x`.
- No item between the two names `x`, and none holds a label (`case` and `default` too), `goto`, `break` or `continue`. A jump out of the region or into the region could skip the later store, or enter after the first store.
- A later declarator in the same declaration does not read `x` (`int x = 0, y = x;`).

An effect in the first right side stays as a statement. The pass does not follow a store from a branch into the code after the branch: `if (c) x = 1; else x = 2;` is not changed.

## Not done

- A store that is dead on some paths only, for example the store before a loop that stores again inside the loop. The pass does not build a flow graph.
- A call that reads the variable that the call stores (`r = twice(r)`).
- A `static` local that nothing reads.
- A local array or structure that is only stored to.

## Switches

- `--no-dead-store` turns the pass off.
- `UC80_NO_DSE=1` does the same, for a script.
- `UC80_NO_DSE_OVERWRITE=1` turns the second part off.
- `UC80_DSE_DEBUG=1` writes one line for each round to stderr: the function and the dead names (`dead [...]`), and one line for each overwritten store (`overwritten x`).
- `UC80_DSE_ONLY=f,g` limits the pass to the named functions, for a bisect.
- `-v` prints the number of removed variables and stores.

## Results

The numbers come from `bench/run.py -c uc80`. They cover 109 programs (not `battlestar-size`, `rogue36-size` and `empire-trefonas`). The reference is the same tree with `--no-dead-store`. No status changes.

- Image: 26 programs smaller, none bigger. The total goes from 775,324 to 774,814 bytes.
- T-states: 17 programs faster and 1 slower by 2 T-states (`embench-huffbench`). The BSS of that program gets 2 bytes smaller, and the timed code is the same.
- `sieve` -46.13% T-states and -49 bytes, `fannkuch` -2.79% and -67 bytes, `dhrystone21` -0.55%, `embench-tarfind` -0.51% and -22 bytes, `whetstone` -0.26% and -43 bytes, `binary-trees` -54 bytes, `monop-size` -41 bytes.
- `sieve+static` does not change. Its locals are `static`.

## Tests

`tests/test_dead_store.py` runs one program in ten modes against the host compiler. The program has dead stores and stores that only look dead. These are: a call in the right side, a volatile read, an address taken, a global with the same name, a read in a branch, and a value that the enclosing expression uses. The shape tests check which names the pass removes and which it keeps.
