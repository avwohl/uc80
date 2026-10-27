# Register locals

Without this pass, every named local lives in memory. The slot is a static `??AUTO` byte range or an IX frame slot. Each access costs 13 to 19 T-states. A register move costs 4 T-states. The pass moves up to two scalar locals of a function into BC or DE. It is step 1 of family G in [codegen_gap.md](codegen_gap.md).

## Which locals qualify

A local qualifies when all of these are true:

- The type is a char, a short, an int, an enum or a pointer (one or two bytes).
- Nothing takes the address of the local.
- The local is not `volatile`.
- The function has no inline assembly, no `setjmp` call and no statement expression.
- The function is not an interrupt function, not `main`, and no outside code reaches the function (an assembly file, a function pointer, `public`).

A register parameter qualifies too. The prologue already stores the parameter in a slot. The pass moves the parameter from A, HL, E or DE to its new register.

The pass runs only in whole-program mode with the register convention on and with 16-bit int. Two options turn it off: `--no-register-locals` and `--no-register-cc`.

## How the pass chooses

The pass works on the text of one function after the code generator finishes the body. It does not change the code generator.

1. Each candidate gets a weight: the number of accesses, multiplied by 10 for each loop level.
2. For the four best candidates, the pass builds the text with the local in BC and in DE. It builds each pair of two locals too.
3. A cost model gives each text a score in T-states weighted by loop depth. The text with the best score wins. The pass keeps memory when no text gains more than 3 T-states.
4. A local declared with `register` gets a bonus of 25 percent on the gain, and it sorts first when weights are close.

A byte local uses C, E, B or D. A wrap or a rename needs the other byte of the pair to be dead.

## What the pass does to the other code

The code generator uses BC and DE as scratch registers (`ld BC,_arr` then `add HL,BC`). Every call destroys BC and DE. The pass makes this safe in three steps.

- Rename. A run of instructions that works on one scratch value moves to the other pair when that pair is dead.
- Wrap. If the other pair is busy, the pass puts `push rr` before the run and `pop rr` after the run (21 T-states, 2 bytes).
- Prune. After the peephole pass, `reglocals.prune` removes each wrap when the routines inside never write the register. At that time the pass knows the runtime helpers and the final callees.

Around a call, only a register that holds a live local gets a push and a pop. A call to a function that never writes BC or DE gets no wrap.

The liveness analysis knows the result of the function. A `void` function leaves nothing live at the return, so DE is free for the rename. An `ex DE,HL` counts as a read only for the bytes that are live after it.

## The checker

A checker proves the finished text for each configuration. For each resident byte, every read as a local must see only writes as a local. Every scratch read must see only scratch writes. The entry value counts as both. If a configuration fails the check, the pass drops the configuration and keeps the local in memory.

The checker runs two times: before the peephole and after the peephole. The peephole can remove a copy of an entry value, for example `ld L,E` that reads parameter 1 in DE. Without the first run, the checker does not see that the code wrote a local into DE before that read.

The exit of the function counts as a read of the bytes in the exit mask. A scratch value that reaches the exit in a result byte (for example the high word of a long in DE) is not renamed to the other pair. The pass does not wrap such a value in `push` and `pop` either. A local that is the last write to a result byte at the exit also fails the check.

A text that the pass does not know makes the whole function keep its locals in memory. Examples are an unknown instruction, `exx`, a barrier region, a conditional call and a function with more than 2500 lines.

## Interaction with other parts

- Interrupts. An interrupt function pushes AF, BC, DE, HL and IY on entry. A register local therefore survives an interrupt. The pass does not change an interrupt function. `bench/rom/isrproof.py` proves the port logs.
- Register convention. Parameter 1 can arrive in E or DE. The prologue moves the value to its register in the right order. The pass rejects a configuration that needs a cycle of moves.
- Shared storage. The `??AUTO` slot of a local that moved to a register stays reserved. The call graph analyser sizes `??AUTO` before the body exists, so the slot is not given back. This costs no code, only RAM. A later step can shrink the area.
- Recursion. A function in frame mode (IX frame) pushes and pops the resident registers around each recursive call. The cost model counts this.

## Debugging

- `UC80_RL_DEBUG=1` prints the candidates, the gain of each configuration and the rejections.
- `UC80_RL_DUMP=name:BC` (or `name:DE`) prints the text of one configuration.
- `--no-register-locals` turns the pass off.

## Tests

`tests/test_register_locals.py` checks the shape of the code and the answers of the answer programs, with and without the pass, in both storage modes. Two programs are regression tests for ccfuzz seeds 40548 (parameter 1 in DE) and 40278 (the high word of a long result in DE). `bench/ccfuzz.py` and `bench/difftest.py` run random programs.

## Known limits

- Only two locals, only BC and DE. IY and HL are not used.
- Array element addresses still use `ld BC,_arr` and `add HL,BC`. When both pairs are busy, the code pays a wrap. The chain assignment `a[i]=b[j]=1` is the worst case.
- A local that lives across many calls often loses against memory. The cost model then keeps the local in memory.
