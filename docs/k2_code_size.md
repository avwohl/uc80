# Family K step 2: smaller code

[Step 1](k1_link_size.md) of family K linked less library code. Step 2 makes the generated code and the runtime routines smaller. The goal is the same as before. The image of each benchmark program must not be bigger than the image of SDCC 4.6.0 in its size mode. The speed means must not get worse.

Each change has a switch that turns the change off. Each change has a test that runs code and compares the output with the host compiler. The tests are in `tests/test_k2_size.py`.

- [Addresses stored through a pointer](#addresses-stored-through-a-pointer): `--no-const-store`.
- [A for loop with a test at the end](#a-for-loop-with-a-test-at-the-end): `--no-for-entry`.
- [Unrolling a short counted loop](#unrolling-a-short-counted-loop): `--no-loop-unroll`.
- [The two arms of an if and else](#the-two-arms-of-an-if-and-else): `--no-branch-merge`.
- [The signed test x < 1](#the-signed-test-x--1): `--no-sign-one`.
- [Dead register writes and copies](#dead-register-writes-and-copies): `--no-flow-opt`.
- [A jump to a return and cp 0](#a-jump-to-a-return-and-cp-0): `--no-ret-jump` and `--no-cp-zero`.
- [A shorter signed divide and modulo](#a-shorter-signed-divide-and-modulo): `--no-sdivmod-merge`.
- [A shorter 16-bit divide](#a-shorter-16-bit-divide): `--no-div16-compact`.
- [puts without a frame](#puts-without-a-frame): `--no-puts-small`.
- [A multiply of 25 bytes](#a-multiply-of-25-bytes): `--no-mul16c`, `--size-helpers`.

## Addresses stored through a pointer

A statement such as `t[1].f = func;` or `p->name = "text";` stores a link-time address through a pointer. The old code loaded the address into HL and pushed HL. It then computed the target address in HL, popped DE, and stored DE. This used 15 bytes for an address in an array of structs.

The new code computes the target address in HL first. It then loads the address into DE and stores DE. This uses 13 bytes and no stack. The rule applies when the value is one of these:

- The name of a function.
- A string literal.
- The address of a global object (`&name`).

The rule does not change the order of the stores. The value has no side effect, so the order of the value and the target address does not matter. The program `mojozork-size` has 497 such stores. It is 412 bytes smaller.

## A for loop with a test at the end

A `for` loop such as `for (i = 0; i < 8; i++)` tests the condition before the first pass. The test is true for the first pass when the counter starts at a constant that passes the test. The compiler knows this at compile time.

The compiler now writes such a loop with the test at the end only. The loop has this form:

1. The init clause stores the constant in the counter.
2. The body follows.
3. The update clause follows the body (this is the label that `continue` uses).
4. The test follows, and a jump back to the body when the test is true.

The loop has one test, and the first test is not in the code. The pass of [loop rotation](loop_rotation.md) used to copy the test to the end of the loop. The new loop does not need the copy. So the loop is smaller by the whole test, and it is faster by the first test.

The compiler uses the new form when all of these are true:

- The init clause stores a constant `c1` in one variable. The clause has the form `v = c1` or `int v = c1`.
- The condition compares `v` with a constant `c2` (`<`, `<=`, `>`, `>=`, `!=`, `==`, with `v` on either side).
- The constants `c1` and `c2` are from 0 to 32767. The value `c1` fits in the type of `v`.
- The type of `v` is an integer type of 1, 2 or 4 bytes. The type is not `volatile` and not `_Bool`.
- The first test is true for these constants.

Each loop is 6 to 12 bytes smaller. The programs `stanford-puzzle`, the `sort-*` programs and `pi` are 15 to 110 bytes smaller from this change alone.

## Unrolling a short counted loop

The AST optimizer of `uc_core` has a loop unroller for level 3. It never ran for a loop such as `for (i = 0; i < 4; i++)`. The tree wraps the init clause in an expression statement, and the unroller did not look inside. The class `Uc80ASTOptimizer` in `ast_opt.py` now has its own `_try_loop_unroll`.

The unroller applies these rules:

- The init clause stores a constant from 0 to 32767 in a variable. The condition compares the variable with such a constant (`<`, `<=`, `!=`). The update has the form `v++` or `v += c` with `c` above 0.
- The loop has 1 to 4 passes. The body has no declaration, `break`, `continue`, `goto` or label.
- The body does not assign the variable. The program does not take the address of the variable. The variable is not `volatile` (a local, a global or a static variable). The compiler keeps every read of a `volatile` counter.
- A loop with a call in the body and two or more passes stays a loop.

The unrolled code has one copy of the body for each pass. A local variable of type `int`, `short`, `unsigned` or `unsigned char` becomes a literal in each copy. The constant folder then folds the expressions. An index such as `p[i + 8 * (j + 8 * k)]` becomes a fixed address. A variable of another type, or a global variable, gets the assignment `v = value` before each copy. Code with `sizeof(v)` or `_Generic(v ...)` keeps the variable.

The unrolled code ends with the assignment of the value that the loop leaves in the variable. The unroller of `uc_core` stopped one step before this value. The assignments that nobody reads go away in later passes.

The compiler unrolls a loop first when it is the whole body of another loop. The decision for the outer loop then sees the folded inner copies.

The function `_cost` in `ast_opt.py` estimates the size of a tree in bytes. A variable costs 3, an operator 2, a literal 2 and a fixed address 3. An index that is not a literal costs 8 more than its index expression. A call costs 8 more than its arguments, and the control of a loop costs 12.

The compiler folds the copies and compares their cost with the cost of the loop. The loop stays when the copies cost more than `UNROLL_SLACK` (4) bytes more. The environment variable `UC80_UNROLL_DEBUG=1` prints both costs for each loop.

The program `stanford-puzzle` has 13 nests of three short loops. The nests become stores to fixed addresses. The program is about 520 bytes smaller.

## The two arms of an if and else

Many `if` and `else` statements have two arms that start with the same instructions or end with the same instructions. A 32-bit statement such as `crc >>= 1;` in one arm and `crc = (crc >> 1) ^ K;` in the other arm is an example. The peephole rule 6 in `peep_local.py` finds these arms in the assembly text. The rule does two things:

- It moves the common start P in front of the test. P holds only simple instructions: loads, ALU work and shifts, with no store. The test must not read a register that P writes, and P must not read a register that the test writes. The flags of P must not be read afterwards.
- It writes the common end S once. One arm falls into S. The other arm jumps over its own code to S. S never holds a jump, a call or a return. A shared jump would stop the change of a jump to a return.

The rule needs that the label of the else arm has no other reference. The rule saves the second copy of P and S and one jump. The program `crc` is 63 bytes smaller and 5.6% faster.

## The signed test x < 1

The signed compare of a 16-bit value with a constant uses 8 instructions and 12 bytes: `ld A,H`, `xor 128`, `ld D,A`, `ld A,L`, `sub K`, `ld A,D`, `sbc A,128`, and a jump. The test `x < 1` (and so `x <= 0`, and the complements `x > 0` and `x >= 1`) is very common in loops.

The peephole rule 7 changes this test to `ld A,H`, `or L`, then a jump on zero, then `bit 7,H`, then a jump on a set bit. The rule keeps the form of the jump (`jr`, `jp` or `ret`). The test uses 8 bytes, and it is faster by 9 T-states or more.

## Dead register writes and copies

The module `peep_flow.py` works on the assembly text after the peephole rules. It does these steps:

1. **Copy propagation.** After `ld r,s` (two byte registers) a later `ld x,r` or ALU instruction on `r` reads `s`, when no instruction has written `r` or `s`. A label, a call or a jump ends the block. A store to memory keeps its register (`ld (nn),A` has no other form).
2. **Dead writes.** A pass finds the loads of a register from a register or from a number that no instruction reads. The pass deletes these loads. The pass follows the jumps of the run. A call reads and writes every register. A return reads every register. A load from memory stays, because the address can be a volatile register. A load of the address of a symbol stays, because the compiler uses it to make the linker take a module (`ld HL,__sc_float_on`). A load that sets flags stays (`ld A,I`).
3. **Dead code.** The pass deletes an instruction that follows an unconditional jump or a return without a label between. It deletes a jump to the label that follows the jump. `halt` is not an end, because an interrupt resumes the program at the next instruction.
4. **Argument cleanup.** The two instructions `inc SP` after a call remove half of an argument. The compiler writes them when DE or BC holds a register local. The pass changes each pair to `pop AF`, when A is dead. This saves one byte for each pair and 4 T-states.
5. **A word that a register pair holds.** After `ld (X),HL` or `ld HL,(X)` the pair HL holds the word at X until an instruction writes HL or X. The pass deletes a later `ld HL,(X)`. A later `ld DE,(X)` becomes `ld D,H` and `ld E,L`. This removes the reload after a store of a 32-bit value (`ld HL,(t)` and `ld DE,(t+2)` after `ld (t),HL` and `ld (t+2),DE`). The pass knows labels only. A store through a pointer, a block move, a call or a label clears what the pass knows. The pass does not track the objects at fixed addresses (`___abs...`), because two names can mean the same address.
6. **A constant that a register holds.** After `ld HL,5` a later `ld HL,5` goes. The same holds for a byte register. A label, a call or a write to the register ends the fact. The unrolled loops use this step: the stores `ld (_p+2),HL`, `ld (_p+4),HL` share one `ld HL,1`.
7. **A constant offset in two steps.** The sequence `ld HL,4`, `add HL,BC`, `inc HL`, `inc HL` becomes `ld HL,6`, `add HL,BC`. The sequence `ld DE,4`, `add HL,DE`, `inc HL` becomes `ld DE,5`, `add HL,DE` when DE is dead afterwards. The carry flag of the add must not be read afterwards.
8. **The test `while (n--)`.** The sequence `push HL`, `dec HL`, `ld (X),HL`, `pop HL`, `ld A,H`, `or L` and a jump on zero becomes `ld A,H`, `or L`, `dec HL`, `ld (X),HL`, because `dec HL` does not change the flags. When HL is live after the test, one `inc HL` follows.

A conditional call (`call m,__neg32` in the runtime) reads every register and kills no register. The first version of the pass left a whole run alone when the run had a conditional call. So the pass changed no big program. The programs `rogue36-size`, `empire-trefonas` and `trek-size` are 6528, 2241 and 936 bytes smaller from this repair.

A run with `exx` or `ex AF,AF'` is split at the labels of functions. A part with `exx` stays as it is, because the register names change their meaning there. The other parts are done. A run with an instruction that the register tables do not know stays as it is.

The environment variable `UC80_FLOW_LIMIT=N` lets only the first N edits of the compile happen. It finds a wrong edit by bisection. The variable `UC80_FLOW_DEBUG=1` says why a run stays as it is.

## A jump to a return and cp 0

Two small peephole rules save one byte at a time, but they apply very often:

- A jump `jr L` or `jp L` to a label that stands directly before a `ret` becomes `ret`. It saves 1 or 2 bytes and 5 or 6 T-states. The rule exists for the conditional jump since family J (`ret cc`). The new rule handles the unconditional jump.
- The instruction `cp 0` before a conditional jump, call or return on Z, NZ, C or NC becomes `or A`. It saves one byte. Both instructions give the same Z, S and C flags. The rule does not apply before `jp po` and `jp pe`, because the two instructions give different parity flags.

## A shorter signed divide and modulo

The routines `__sdiv16` and `__smod16` have 35 bytes each. They have the same two negations at the start and the same negation at the end. The module `rt_sdivmod16.mac` has `__sdiv16m`, `__smod16m` and a shared body `__sdm16`. The three routines have 49 bytes.

The compiler calls the new routines when all of these are true:

- The program calls both `__sdiv16` and `__smod16`. A program that calls only one routine keeps the old routine, because the shared body is bigger than one old routine.
- No libc routine calls them (`div`, `cbrt`).
- Every signed divide stands in `main`, outside every loop. A call of the new routines needs about 55 T-states more than a call of the old routines. A hot divide must keep the old routines. The switch `--size-helpers` removes this rule. The program `davidly-e` is 27 bytes smaller with `--size-helpers` and has 2.7% more T-states. The program `bsd-wump` has 4.7% more T-states.

## A shorter 16-bit divide

The routine `__div16` called `__dv8w` for a divisor of 256 or more. The routine `__dv8w` has 63 bytes with special paths for small values. The quotient has at most 8 bits when the divisor is 256 or more. So the new `__div16` has an eight-step loop for this case. The loop is 30 bytes.

The old routine `__dv8w` is also used by `__div32` and `__div32w`. The compact `__div16` saves bytes only when the program has no 32-bit divide. The compiler checks this at the end of the compile. It also checks the libc routines that need a 32-bit divide (`ldiv`, `lldiv`, `cbrt`, `scanf`, `strtod`, `atof`). When one of these is present, the compiler embeds the old `__div16` from `lib/rt_alt/rt_div16.mac`. The switch `--no-div16-compact` selects the old routine for each program.

The change saves 42 bytes in a program with a 16-bit divide and no 32-bit divide. The T-states do not change for a divisor of less than 256. A division by a divisor of 256 or more is slower by about 90 T-states for a dividend of 0x8000 or more, and by about 280 T-states for other dividends (measured in integrate14 on 1280 random divides).

So the compiler embeds the compact `__div16` only when every 16-bit divide stands in `main` outside every loop, and no libc routine that divides is in the program. The switch `--size-helpers` removes these rules. The switch `--no-div16-compact` selects the old routine in every case.

## puts without a frame

The routine `puts` used `IX` for a frame and saved `BC`. The routine `__conout` keeps `BC`, `DE` and `HL`, so the routine needs neither. The module `lc_puts_s.mac` has `__puts_s` (28 bytes). The routine `puts` has 38 bytes.

The compiler calls `__puts_s` for a call of `puts` in a whole program for CP/M. The program must not define its own `puts`. The call is the same as the call of `puts`: the argument is on the stack and the caller removes the argument. A ROM build, a build with an assembly file and sdas text keep `puts`.

The program `davidly-e` and the programs that print a line are 10 bytes smaller. A program that also takes the address of `puts` links both routines.

## A multiply of 25 bytes

The routine `__mul16` is speed-tuned. It has 139 bytes with its helpers `__mul16w`, `__mul16ng` and `__mul8x16`. SDCC has a multiply of 23 bytes. The program `dhrystone21` multiplies once in each pass of its main loop.

The module `rt_mul16c.mac` has `__mul16c` with 25 bytes. The routine adds the left operand, shifted left, for each set bit of the right operand. It starts at the lowest bit and stops after the highest set bit. A right operand of 3 needs about 175 T-states. A right operand of 255 needs about 700 T-states.

The compiler calls `__mul16c` in place of `__mul16` when all of these are true:

- Every multiplication of the program stands in `main`, outside every loop. With `--size-helpers`, a multiplication one loop deep also counts.
- The program calls no other multiply routine (`__mul32` and the others).
- The program does not call `fread`, `fwrite`, `rand` or `srand`. These libc routines link `__mul16`.
- The int has 16 bits.

A multiplication in another function, or in a loop, keeps the fast routine. The rule is narrow on purpose. A function that `main` calls in a loop could multiply very often, and the compiler cannot see this. A loop of multiplications is up to 2.1 times slower with `__mul16c`. With `--size-helpers` the programs `dhrystone21` (three builds) and `whetstone` are 116 and 42 bytes smaller, and their T-states do not change.

## A counter in memory

A `for` loop with a bottom test reloaded a counter that lives in memory at the start of each pass. This cost 16 T-states a pass, because loop rotation kept the counter in HL. Three rules remove the loss:

- Only a local counter gets the bottom-test form. A global or static counter keeps the rotated loop.
- The liveness pass knows what HL holds at the loop label. The label of a bottom-test loop (`@FORB`) keeps the words that the line before it and every jump back to it agree on. Then the first `ld HL,(i)` of the body goes. The label must have no other jump and no other mention in the text. Turn this off with `--no-flow-opt`.
- The jump back of a hot bottom-test loop is `jp cc` (10 T-states) in place of `jr cc` (12 T-states when taken). A loop is hot when it has no call and at most 100 bytes of body. Turn this off with `--no-for-entry`.

The program `davidly-sieve` was 4.7% slower with the first version. It is now 0.002% faster than `main` and 91 bytes smaller.

## Tests

- `tests/test_k2_size.py` runs one program for each change with the switch on and off. It compares the output with the host compiler.
- `bench/loopfuzz.py` makes random nests of counted loops (`python3 bench/loopfuzz.py SEED COUNT`). It compares uc80 with the host compiler. The loops have `break`, `continue`, calls and several counter types.
- The tests of the loops use all comparison operators, the types `char`, `short`, `int`, `long` and `unsigned long`, nested loops, `continue` and `break`.
- The divide tests use divisors from 1 to 65535 and extreme dividends. They test signed and unsigned values, with and without a 32-bit divide in the program.
- The tests of the passes work on assembly text and check which instructions move or go.
- `tests/test_conditions.py` expects the new loop form for a counted loop.

## Results

The numbers are in [codegen_gap.md](codegen_gap.md) and in the file `CHANGELOG.md`.
