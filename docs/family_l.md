# Family L: the last gaps to SDCC

Family L is the work that follows [family K](k1_link_size.md). The aim has two parts. The image of each benchmark program must not be bigger than the image of SDCC 4.6.0 in its size mode. The program must run faster than SDCC in its speed mode. The plan with the measured numbers is in [family_l_plan.json](family_l_plan.json). The tools that measured the plan are in `bench/tools/faml`.

Each change has a switch that turns the change off. Each change has a test that runs code with uc80 and with the host compiler and compares the output.

Step LA (link, library and tree level):

- [Static pointer to a fixed address](#static-pointer-to-a-fixed-address): `--no-ptr-const`.
- [qsort of 16-bit elements](#qsort-of-16-bit-elements): `--no-qsort-wr`.
- [Calls of empty functions](#calls-of-empty-functions): `--no-empty-call`.
- [One divide for x / y and x % y](#one-divide-for-x--y-and-x--y): `--no-divmod-pair`.
- [An empty counted loop](#an-empty-counted-loop): `--no-empty-loop-fold`.
- [A short multiply in a loop with calls](#a-short-multiply-in-a-loop-with-calls): `--no-mul16c-loop`.

Step LB (code generator and ROM):

- [External functions in registers](#external-functions-in-registers): `#pragma uc80 regcall`, `--no-regcall`.
- [A compare of a register local with memory](#a-compare-of-a-register-local-with-memory): `--no-cmp-mem`.
- [A char result in L](#a-char-result-in-l): `--no-char-noext`.
- [Small changes of the liveness pass](#small-changes-of-the-liveness-pass): `--no-hl-reuse`, `--no-dead-alu`, `--no-pop-cleanup`, `--no-be16`, `--no-ex-load`, `--no-a-mem`, `--no-zero-xor`, `--no-call-reads`.
- [Bytes in A](#bytes-in-a): `--no-mul8x16`, `--no-byte-shl`, `--no-byte-tern`.
- [A frame without a reservation](#a-frame-without-a-reservation): `--no-frame-drop`.
- [Loop rotation budget](#loop-rotation-budget).

## Static pointer to a fixed address

Some programs keep their state in one object and reach the object through a pointer variable:

```c
static State state_object;
static State *g = NULL;
int main(void) { g = &state_object; /* ... */ g->field = 1; }
```

Each `g->field` loads the pointer from memory and adds the offset of the member. The program `mojozork-size` has 390 such accesses.

The compiler replaces `g` by `&state_object`. It then changes `(&state_object)->field` to `state_object.field`, a fixed address. The compiler removes the pointer variable.

The change is made only when all of these are true:

- The pointer has internal linkage (`static`), is not volatile, and no assembly names the pointer.
- The program never takes the address of the pointer. Only `=` assigns the pointer, and `++` and `+=` do not occur.
- The pointer points to one object of the same type. The object has file scope, or is a `static` variable of a function. The compiler moves a `static` variable of a function to file scope under the name `__ls_<function>_<name>`.
- One of two cases is true. In the first case, the declaration reads `static T *p = &obj;` and no statement assigns the pointer. In the second case, the declaration has no initializer (or a null one) and the only assignment is the statement `p = &obj;` among the first items of `main`. Each item before the assignment is a declaration that makes no call and does not use the pointer.
- No function calls `main`, and `main` is the only entry. The build is a CP/M whole-program build with no interrupt function and no assembly file.

In the second case no code reads the pointer before the assignment. So the pointer is never null when the program reads the pointer.

The compiler also changes `(&obj)->member` to `obj.member` and `*(&obj)` to `obj` everywhere. A macro such as `#define G (&state)` makes such expressions.

The change saves 3306 bytes in `mojozork-size`.

## qsort of 16-bit elements

A call `qsort(base, n, 2, cmp)` calls the library routine `__qsort_wr` (`lc_qsort_w.mac`, 336 bytes) in place of `qsort` (855 bytes). The routine takes `HL` = base, `DE` = number of elements and `BC` = comparator. The comparator takes its two pointers in `HL` and `DE` and returns the result in `HL`.

The routine checks first if the array is one run. A run that goes up needs no work. The routine reverses a run that goes down in every step. Other arrays use a quicksort. The pivot is the element at one quarter of the part. The middle element of an array in organ-pipe order is the largest one, and it gives about n squared compares.

The recursion goes into the smaller part, so the stack depth is at most log2(n) levels.

The comparator gets the register convention for calls only when all of these are true:

- Every use of the name of the comparator is the fourth argument of a call `qsort(b, n, 2, name)`.
- The comparator has two parameters of one word each, and it returns an `int`.
- The comparator cannot reach `qsort`, `bsearch` or a function that calls back (`raise`, `signal` and the like). The check follows the direct calls. An indirect call or inline assembly in the comparator, or in a function that the comparator calls, turns the change off.
- The program has no interrupt function, no function named `qsort` and no assembly file. The type `int` has 16 bits.

The routine keeps its state in static storage and is not reentrant. The checks above make sure that no comparator calls a sort. If a check fails, the call keeps the stack convention and the generic `qsort`. A call with a constant count of 32 or less and an element size of 1 to 8 still uses `__qsort_s` when the checks fail.

The eight sort programs of the benchmark shrink and run faster. The program `sort-ran-5000` goes from 36.0 million to 28.6 million T-states.

The routine has no insertion sort for small parts. For a constant count of 32 or less, `__qsort_s` (insertion sort) is faster on an input that is almost sorted. We measured 20 elements. A random input needs 1.09 times the T-states of `__qsort_s`.

A sorted array with one element moved needs up to 2.7 times. An array with few different values needs up to 2 times. A sorted, reversed or equal input needs 0.70 to 0.73 times. With 200 elements or more, the routine wins or ties on every shape tried.

The benchmark `sort-ran-20` gains from the routine: 50,424 against 56,863 T-states, and 107 bytes less. Use `--no-qsort-wr` for a program that sorts small arrays that are almost sorted.

## Calls of empty functions

A debug function such as `static void dbg(const char *fmt, ...) { }` has no effect when the debug switch is off, but each call still pushes the arguments. The compiler deletes a call when all of these are true:

- The call is a whole expression statement, or a cast of a call to `void`.
- The function has one definition, and the body has no code. A body of empty statements and of expressions without side effects counts as empty.
- Each argument has no call, assignment, `++`, `--`, statement expression or compound literal, and does not access a volatile object.
- The function that contains the call has no local variable or parameter with the name of the called function.

The function stays when the program takes its address. Otherwise dead function removal deletes the function and the string literals that only its calls used. The change saves 1259 bytes in `mojozork-size`.

## One divide for x / y and x % y

Two statements often divide the same operands, one for the quotient and one for the remainder:

```c
a[n] = x % n;
x = 10 * a[n - 1] + x / n;
```

The routine for division computes both results. `__div16` returns the quotient in `HL` and the remainder in `DE`. The new routine `__sdivmod16` (`rt_sdqr16.mac`, 58 bytes) does the same for signed values.

The compiler finds a pair of operations when the operands are variables of 16 bits or constants, and the two operations are in the same block. The compiler divides at the first operation and stores the other result in a hidden local variable. The second operation reads the hidden variable. A pair needs all of these:

- The operands are not volatile and the program does not take their address.
- The first operation always runs when its statement runs. It is not on the right of `&&` or `||` and not in an arm of `?:`.
- No statement between the two operations changes an operand. A call between the two operations is possible only when the operands are local variables. The second statement can be the assignment `x = ... x / n ...` to an operand.
- Between the two statements there is no label, no loop, no `if` and no other control statement.
- For signed values, the pair needs two conditions. The program has no other division or remainder that can call `__sdiv16` or `__smod16`. The pair is in a loop or in a function other than `main`. Else the two routines of family K (or the shared body) are smaller than `__sdivmod16`, and the pair stays as two operations. Unsigned pairs never make the image bigger.

If the first operation does not end in a divide call (a constant divisor that is a power of two, for example), the compiler computes the second operation after the first. The result is correct in all cases.

The routine `__sdivmod16` calls `__div16`. The call of `__sdivmod16` counts as a divide site for the rule of the compact `__div16` (`docs/k2_code_size.md`): a pair in a loop or in a function other than `main` selects the fast `__div16`.

The change gives a gain of 38% in T-states for `davidly-e`. The hidden local variable adds 2 bytes of BSS there, and the image is 27 bytes smaller.

## An empty counted loop

A loop `for (v = c1; v < c2; v += c) ;` with constant bounds and an empty body only sets the counter. The compiler changes the loop to `v = final;`. The counter must be a non-volatile variable of an integer type of 16 bits or more, and the final value must be 32767 or less. The loop test uses `<`, `<=` or `!=`.

A ROM build or a build with `--freestanding` does not make this change. A ROM program often waits with such a loop. The fold removes the wait: a loop of 3000 iterations took 162,409 T-states, and then 357. Use `--empty-loop-fold` to fold the loops in a ROM build too. Else make the counter `volatile`.

The sort programs have such a loop in `main`, because the print statements of the loop are empty in a timed build. The change saves 35 bytes in each sort program.

## A short multiply in a loop with calls

Family K step 2 uses `__mul16c` (25 bytes) in place of `__mul16` and its helpers (139 bytes) when each multiplication stands in `main` outside every loop. The new rule also allows a multiplication in a loop of depth 1 when the loop has three heavy calls or more. A call is light when the callee is a function of the program with no call and no loop in the body. Light calls do not count.

Library calls and indirect calls are heavy. A heavy call costs much more than the multiply, so the slower short routine does not change the run time in a measurable way. A loop with fewer heavy calls keeps `__mul16`. A loop of three light calls and two multiplies was 80 percent slower with the first version of this rule.

The change saves 116 bytes in each of the three `dhrystone21` programs.

## External functions in registers

A ROM program calls `out` and `in` many times. The stack convention costs about 100 T-states and 11 bytes for each call. The pragma `regcall` gives an external function the register convention of family J:

```c
void out(uint8_t port, uint8_t value);
uint8_t in(uint8_t port);
#pragma uc80 regcall out in
```

The first parameter goes in `A` (a byte) or in `HL` (a word). The second parameter goes in `E` (a byte) or in `DE` (a word). A char result comes back in `A`. Another result comes back as before, in `HL`. The assembly routine must use the same registers. `bench/rom/z80io.mac` is the routine of the sound kernels, and `bench/rom/z80io_stack.mac` is the old routine for `--no-regcall`.

The compiler stops with an error in these cases:

- the program defines the function itself.
- the prototype has more than two parameters, a `...`, or a parameter that is not an integer or a pointer of one or two bytes.
- the program uses the name in another way than as the callee of a call. The address of the function would call it with the stack convention.
- a call has another number of arguments than the prototype.
- the program has no declaration for a name in the pragma (for example a misspelled name).
- `int` has 32 bits (`--int=32`). The routines of the register convention take 16-bit words. Use `--no-regcall` with `--int=32`.

`--no-regcall` ignores the pragma. `--no-register-cc` does not turn the pragma off, because the assembly routine does not change.

The calls of the register convention are shorter. A register local in `BC` stays in the register when the call reads only `A` and `E`. The liveness pass knows what the call reads (see `--no-call-reads` below).

## A compare of a register local with memory

The compare of a register local in `DE` or `BC` with a word in memory needed the same pair for the word. The compiler saved the pair:

```
push DE / ld DE,(X) / ld A,C / sub E / ld A,B / sbc A,D / pop DE
```

When HL is dead afterwards, the word goes to HL: `ld HL,(X) / ld A,C / sub L / ld A,B / sbc A,H`. The change saves 21 T-states and 3 bytes for each compare.

A loop that tests `i < n` often has a copy of the counter in HL for the body. The pass of copies now works across a label of the compiler. The copy facts that hold on every jump to the label stay true. A pair that the code saves with `push` and restores with `pop` keeps its facts. So the body reads `E` and not `L`, and HL is dead at the compare.

The signed compare of two bytes used the bias 128:

```
ld A,(IX-6) / xor 128 / ld H,A / ld A,E / xor 128 / cp H / jr nc,T
```

It is now `ld A,E / sub (IX-6) / jp po,N / xor 128 / N: jp p,T`. The overflow flag gives the sign. The change saves 4 bytes and about 5 T-states for each compare. The label of the copy has the form `@CMP_NVn`, so the loop rotation still copies such a test.

`--no-cmp-mem` turns off the compare changes and the copy facts across labels. `micro+loop` goes from 1,256,037 to 1,060,947 T-states (SDCC: 1,102,397).

## A char result in L

A function that returns a char put the char in `L` and the sign extension in `H`. A call that uses only the byte did not need `H`. Now a function of the program leaves the char in `L` and does not set `H`. Only the code of the program calls the function. A function that assembly names, that a pragma names, or that the library names keeps the old form.

Each call site that returns a char extends the result itself (`ld A,L / rlca / sbc A,A / ld H,A` for `signed char`, `ld H,0` for `unsigned char`). The liveness pass deletes the extension when the program uses only `L`. A call that stands alone in `return g(x);` keeps the tail jump. Two more rules go with this change:

- the register-locals pass knows that the return of such a function reads only `L` (the marker `;@CHARL` after the line `; Function`).
- a call of a function that returns a char has the byte range. A variable that gets such a value and a loop counter has the range -128 to 127 and compares as a byte.

`davidly-ttt` goes from 8,239,388 to 7,912,478 T-states (SDCC: 7,960,572). `--no-char-noext` turns the change off.

## Small changes of the liveness pass

The pass `peep_flow` has these new steps. The first lines of the file `peep_flow.py` list them with numbers.

- `--no-hl-reuse`: `ld HL,X / add HL,BC` goes when HL already holds the sum. A conditional jump between the two does not matter. After a store through a base pointer, HL is up to three bytes away from the next address. Then `inc HL` (or `dec HL`) takes the place of the two instructions. `mojozork-size` is 411 bytes smaller.
- `--no-dead-alu`: an instruction that writes only A and the flags goes when A is dead and no flag is read. This removes a sign extension that no code uses.
- `--no-pop-cleanup`: `inc SP` twice after a call becomes `pop HL` (or `pop DE`, `pop BC`) when A is live and the pair is dead. Before, only `pop AF` was possible.
- `--no-be16`: the word `(p[0] << 8) | p[1]` of two bytes, with the pointer in `BC`, becomes `ld D,(HL) / inc HL / ld E,(HL) / ex DE,HL`. It saves 19 bytes and 67 T-states for each word. A, DE and the flags must be dead afterwards. `mojozork-size` has 35 such words and is about 130 bytes smaller.
- `--no-ex-load`: `ld HL,(X) / ex DE,HL` becomes `ld DE,(X)` when HL is dead.
- `--no-a-mem`: after `ld A,(X)` or `ld (X),A`, the register A holds the byte at X. A later `ld A,(X)` goes. The fact crosses a label of the compiler when every jump to the label has the fact. A store to memory, a call, a write to A or another label ends the fact.
- `--no-zero-xor`: `ld A,0` becomes `xor A` when no flag is read before the next instruction that sets all flags, a call or a return.
- `--no-call-reads`: a call (or a tail jump) of a function of the program reads no register in the stack convention. In the register convention it reads the registers of the plan. Before, each call read all registers, and a value in `A`, `D` or `E` stayed live until the call.

## Bytes in A

- `--no-mul8x16`: the product `x * y` of 16 bits calls `__mul8x16` when one factor is a byte (0 to 255). The routine is the loop that `__mul16` ends in. The call does not need `__mul16` and its three helpers any more, 61 bytes of code in `snd-fnum`. The compiler also uses `__mul16c` for the other multiplications of a loop, as before.
- `--no-byte-shl`: `(uint8_t)(1 << n)` with a variable `n` is a loop of `add A,A` in A, with the count in E. The code has 10 bytes. Before, the code shifted HL and took 19 bytes.
- `--no-byte-tern`: `(uint8_t)(c ? a : b)` computes each arm in A. `ld A,k` replaces `ld HL,k` and `ld A,L`.

## A frame without a reservation

The register-locals pass can move all locals of a function out of its frame. The function may still need `IX` for its parameters (`rom_printf` reads its variable arguments through `IX`). The reservation `ld HL,-n / add HL,SP / ld SP,HL` has no use then. The compiler deletes the reservation and the `ld SP,IX` of the epilogue when the body names `IX` only with a non-negative offset. `--no-frame-drop` keeps the reservation.

## Loop rotation budget

Loop rotation copies the test of a loop to its end. The copies may use a share of the code. The share was 2 percent. A change that makes the program smaller makes the budget smaller, and one loop lost its rotation (`micro+copy`, 3605 T-states more). The share is now 3 percent. `UC80_ROT_PERCENT` sets another share for an experiment.

## ROM kernels

The four kernels of `bench/rom/sndrom.py --layout neogeo`, uc80 against SDCC 4.6.0 (size and speed mode of the standalone ROM build of `bench/tools/faml/sdrom.py`):

Bytes of the ROM image, and T-states of the whole run. "uc80 before" is the figure before step LB. SDCC speed and size are the standalone ROM builds of `bench/tools/faml/sdrom.py` (`sdcc -mz80 --max-allocs-per-node 200000`, own `crt0`, `out` and `in` in registers).

| Kernel | uc80 before | uc80 now | SDCC speed | SDCC size |
|---|---|---|---|---|
| snd-cmdq | 1475 / 7,874,380 | 1185 / 6,138,897 | 1190 / 7,089,258 | 1188 / 8,255,396 |
| snd-ymreg | 1040 / 13,831,135 | 970 / 12,402,479 | 1041 / 13,166,418 | 1021 / 13,219,824 |
| snd-seq | 2249 / 9,625,961 | 2066 / 8,833,951 | 2222 / 11,988,349 | 2199 / 12,957,292 |
| snd-fnum | 1040 / 8,713,840 | 897 / 7,084,240 | 925 / 15,158,156 | 915 / 15,533,087 |

All four kernels are at or below SDCC in bytes (size mode) and in T-states (speed mode). `snd-cmdq` has 3 bytes of margin. The output of each kernel equals the golden output, in both layouts and in both assembler flows.

## Known timing noise

The T-states of some programs depend on the address of their data. Step LA: the program `embench-tarfind` needs 1185 T-states more (0.04 percent) than before the step. The code is 13 bytes shorter, so the heap array starts at another address. A build with one extra `nop` gives 3,228,426 T-states, fewer than before.

Step LB: the program `embench-md5sum` needs 1214 T-states more (0.035 percent). The code is 3 bytes shorter (`ld HL,(X) / ex DE,HL` became `ld DE,(X)`). The allocator computes `pointer % 2` with `__mod16`, and the time of the routine depends on the pointer.

Three `nop` instructions give back the old image size and 3,433,492 T-states, fewer than before (3,433,500). The program `embench-huffbench` had the same effect with a smaller change of the code. So the cause is the address of the heap, not the code.

Speed rule exception: the T-state count of `embench-md5sum` is 1214 higher than in step LA. The cause is the address of the heap, not the code, so the program has a documented exception to the speed rule.

## Basis of the figures

The SDCC figures of the ROM plan (`docs/family_l_plan.json`) are the size of the program without the empty-program base. The rows of `bench/results/results.csv` are the whole linked image (for example `davidly-e` 540 against 489, `sort-ran-5000` 780 against 735). Uc80 is below SDCC on both bases.

The bench figures in this page use the whole image. Five rows (`empire-trefonas`, `trek-size`, `adventure-size`, `battlestar-size`, `rogue36-size`) are TOO-BIG: they have no SDCC row and cannot run. The bench compared 107 rows with SDCC. The 5 rows are size-only.

The difftest seed 90065 (`--focus llong`) gives output that differs from the model in uc80 and SDCC. The uc80 output is the same as the output of the build before step LB, so step LB is not the cause. The program has a bit-field struct with a `long long` member. Step LC must look at this seed.

## Not built

These items stay open for step LC.


- L10, a table for runs of constant stores. Only `mojozork-size` has such runs, and the program is 4350 bytes under SDCC.
- The small items of L13: `inc BC` twice for `p += 2`, a shorter `__conout`, and the IX-free prologue of a small leaf function. The constant of the local in the init clause of a `for` is not built too.
