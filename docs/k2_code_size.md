# Family K step 2: smaller code

[Step 1](k1_link_size.md) of family K linked less library code. Step 2 makes the generated code and the runtime routines smaller. The goal is the same as before. The image of each benchmark program must not be bigger than the image of SDCC 4.6.0 in its size mode. The speed means must not get worse.

Each change has a switch that turns the change off. Each change has a test that runs code and compares the output with the host compiler. The tests are in `tests/test_k2_size.py`.

- [Addresses stored through a pointer](#addresses-stored-through-a-pointer): `--no-const-store`.
- [A for loop with a test at the end](#a-for-loop-with-a-test-at-the-end): `--no-for-entry`.
- [The two arms of an if and else](#the-two-arms-of-an-if-and-else): `--no-branch-merge`.
- [The signed test x < 1](#the-signed-test-x--1): `--no-sign-one`.
- [Dead register writes and copies](#dead-register-writes-and-copies): `--no-flow-opt`.
- [A jump to a return and cp 0](#a-jump-to-a-return-and-cp-0): `--no-ret-jump` and `--no-cp-zero`.
- [A shorter signed divide and modulo](#a-shorter-signed-divide-and-modulo): `--no-sdivmod-merge`.
- [A shorter 16-bit divide](#a-shorter-16-bit-divide): `--no-div16-compact`.
- [puts without a frame](#puts-without-a-frame): `--no-puts-small`.

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
2. **Dead writes.** A pass finds the loads of a register from a register or from an immediate value that no instruction reads. The pass deletes these loads. The pass follows the jumps of the run. A call reads and writes every register. A return reads every register. A load from memory stays, because the address can be a volatile register.
3. **Dead code.** The pass deletes an instruction that follows an unconditional jump or a return without a label between. It deletes a jump to the label that follows the jump. `halt` is not an end, because an interrupt resumes the program at the next instruction.
4. **Argument cleanup.** The two instructions `inc SP` after a call remove half of an argument. The compiler writes them when DE or BC holds a register local. The pass changes each pair to `pop AF`, when A is dead. This saves one byte for each pair and 4 T-states.
5. **A word that a register pair holds.** After `ld (X),HL` or `ld HL,(X)` the pair HL holds the word at X until an instruction writes HL or X. The pass deletes a later `ld HL,(X)`. A later `ld DE,(X)` becomes `ld D,H` and `ld E,L`. This removes the reload after a store of a 32-bit value (`ld HL,(t)` and `ld DE,(t+2)` after `ld (t),HL` and `ld (t+2),DE`). The pass knows labels only. A store through a pointer, a block move, a call or a label clears what the pass knows. The pass does not track the objects at fixed addresses (`___abs...`), because two names can mean the same address.
6. **The test `while (n--)`.** The sequence `push HL`, `dec HL`, `ld (X),HL`, `pop HL`, `ld A,H`, `or L` and a jump on zero becomes `ld A,H`, `or L`, `dec HL`, `ld (X),HL`, because `dec HL` does not change the flags. When HL is live after the test, one `inc HL` follows.

The pass leaves a run alone when it holds an instruction that the register tables do not know, `exx` or `ex AF,AF'`. The environment variable `UC80_FLOW_LIMIT=N` lets only the first N edits of the compile happen. It finds a wrong edit by bisection.

## A jump to a return and cp 0

Two small peephole rules save one byte at a time, but they apply very often:

- A jump `jr L` or `jp L` to a label that stands directly before a `ret` becomes `ret`. It saves 1 or 2 bytes and 5 or 6 T-states. The rule exists for the conditional jump since family J (`ret cc`). The new rule handles the unconditional jump.
- The instruction `cp 0` before a conditional jump, call or return on Z, NZ, C or NC becomes `or A`. It saves one byte. Both instructions give the same Z, S and C flags. The rule does not apply before `jp po` and `jp pe`, because the two instructions give different parity flags.

## A shorter signed divide and modulo

The routines `__sdiv16` and `__smod16` have 35 bytes each. They have the same two negations at the start and the same negation at the end. The module `rt_sdivmod16.mac` has `__sdiv16m`, `__smod16m` and a shared body `__sdm16`. The three routines have 49 bytes.

The compiler calls the new routines when a program calls both `__sdiv16` and `__smod16`, and no libc routine calls them (`div`, `cbrt`). A program that calls only one routine keeps the old routine, because the shared body is bigger than one old routine. A call of the new routines needs about 20 T-states more. The program `davidly-e` is 27 bytes smaller and has 2.7% more T-states.

## A shorter 16-bit divide

The routine `__div16` called `__dv8w` for a divisor of 256 or more. The routine `__dv8w` has 63 bytes with special paths for small values. The quotient has at most 8 bits when the divisor is 256 or more. So the new `__div16` has an eight-step loop for this case. The loop is 30 bytes.

The old routine `__dv8w` is also used by `__div32` and `__div32w`. The compact `__div16` saves bytes only when the program has no 32-bit divide. The compiler checks this at the end of the compile. It also checks the libc routines that need a 32-bit divide (`ldiv`, `lldiv`, `cbrt`, `scanf`, `strtod`, `atof`). When one of these is present, the compiler embeds the old `__div16` from `lib/rt_alt/rt_div16.mac`. The switch `--no-div16-compact` selects the old routine for each program.

The change saves 42 bytes in each of `davidly-e`, `dhrystone21` and the other programs with a 16-bit divide and no 32-bit divide. The T-states do not change for a divisor of less than 256. A division by a divisor of 256 or more is slower by about 70 T-states for a large dividend.

## puts without a frame

The routine `puts` used `IX` for a frame and saved `BC`. The routine `__conout` keeps `BC`, `DE` and `HL`, so the routine needs neither. The module `lc_puts_s.mac` has `__puts_s` (28 bytes). The routine `puts` has 38 bytes.

The compiler calls `__puts_s` for a call of `puts` in a whole program for CP/M. The program must not define its own `puts`. The call is the same as the call of `puts`: the argument is on the stack and the caller removes the argument. A ROM build, a build with an assembly file and sdas text keep `puts`.

The program `davidly-e` and the programs that print a line are 10 bytes smaller. A program that also takes the address of `puts` links both routines.

## Tests

- `tests/test_k2_size.py` runs one program for each change with the switch on and off. It compares the output with the host compiler.
- The tests of the loops use all comparison operators, the types `char`, `short`, `int`, `long` and `unsigned long`, nested loops, `continue` and `break`.
- The divide tests use divisors from 1 to 65535 and extreme dividends. They test signed and unsigned values, with and without a 32-bit divide in the program.
- The tests of the passes work on assembly text and check which instructions move or go.
- `tests/test_conditions.py` expects the new loop form for a counted loop.

## Results

The numbers are in [codegen_gap.md](codegen_gap.md) and in the file `CHANGELOG.md`.
