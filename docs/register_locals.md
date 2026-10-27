# Register locals

Without this pass, every named local lives in memory. The slot is a static `??AUTO` byte range or an IX frame slot. Each access costs 13 to 19 T-states. A register move costs 4 T-states. The pass moves up to two scalar locals of a function into BC or DE. It is step 1 of family G in [codegen_gap.md](codegen_gap.md).

Step 2 adds one pointer local in IY (see "A pointer in IY"). The code is in `src/uc80/iyptr.py`. "Tuning" lists the constants and the measured effect of each one.

## Which locals qualify

A local qualifies when all of these are true:

- The type is a char, a short, an int, an enum or a pointer (one or two bytes).
- Nothing takes the address of the local.
- The local is not `volatile`.
- The function has no inline assembly, no `setjmp` call and no statement expression.
- The function is not an interrupt function. No outside code reaches the function (an assembly file, a function pointer, `public`). `main` qualifies.

A register parameter qualifies too. The prologue already stores the parameter in a slot. The pass moves the parameter from A, HL, E or DE to its new register.

The pass runs only in whole-program mode with the register convention on and with 16-bit int. Two options turn it off: `--no-register-locals` and `--no-register-cc`.

## How the pass chooses

The pass works on the text of one function after the code generator finishes the body. It does not change the code generator.

1. Each candidate gets a weight: the number of accesses, multiplied by 8 for each loop level (3 levels at most). A local with a weight below 1 stays in memory.
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

- Code generator fixes (branch `bugfix2`, merged in `integrate6`). The pass reads the text that the code generator makes, so each change of that text can break the pass. Four changes matter: the 16-bit negation that keeps DE, the float compare with the NaN test (`__fcmp` gives Z and C together), the inlined `uint16_t` literal arguments, and the 64-bit (`long long`) code. The merged tree has no break. The checker accepts all four. The 75 programs of the test family and the fuzz runs give the same output with the pass on and off. `tests/test_regl_bugfix_interaction.py` keeps one program for each of the four. The programs that use float get 56 to 71 bytes bigger, because `__fcmp` and the libc helpers (`__toll`) grew. The pass does not cause this.
- Interrupts. An interrupt function pushes AF, BC, DE, HL and IY on entry. A register local therefore survives an interrupt. The pass does not change an interrupt function. `bench/rom/isrproof.py` proves the port logs.
- Register convention. Parameter 1 can arrive in E or DE. The prologue moves the value to its register in the right order. The pass rejects a configuration that needs a cycle of moves.
- Shared storage. The `??AUTO` slot of a local that moved to a register stays reserved. The call graph analyser sizes `??AUTO` before the body exists, so the slot is not given back. This costs no code, only RAM. A later step can shrink the area.
- Recursion. A function in frame mode (IX frame) pushes and pops the resident registers around each recursive call. The cost model counts this.

## A pointer in IY

A field access through a pointer is slow. With the pointer in memory, `p->next` at offset 4 costs 77 T-states. The code is `ld HL,(p)`, `ld DE,4`, `add HL,DE`, two byte loads and `ex DE,HL`.

With the pointer in BC, the same access costs 62 T-states. With the pointer in IY, the access is `ld L,(IY+4)` and `ld H,(IY+5)`. It costs 38 T-states and does not use HL, DE or BC. IY also frees BC and DE for two other locals.

The module `iyptr.py` puts one pointer local of a function in IY. The pass for BC and DE then works on the other locals. One function can have one pointer in IY and two locals in BC and DE.

### Which pointers qualify

A pointer local qualifies when it meets the rules of "Which locals qualify". The pointer can point to a struct or to any other type. A pointer parameter in a register qualifies. A parameter on the stack does not qualify, because the code generator gives the pass no slot for it. `main` qualifies. An interrupt function does not.

### Stage 1: the accesses

The pass reads each access to the slot of the pointer. If an access has a form that the pass does not know, the pointer stays out of IY.

- `ld HL,(p)`, then `inc HL` or `ld DE,k` with `add HL,DE` (also `ld BC,k`), then an access through `(HL)`, becomes an access through `(IY+k)`. The pass handles byte and word loads and stores, `inc (HL)`, `dec (HL)`, ALU operations, `bit`, `set`, `res` and shifts. The offset must be in the range -128 to 126.
- HL must be dead after the last access. DE or BC must be dead too when the pass removes a `ld DE,k`. If HL is live, the pass keeps the old text and reads the pointer with `push IY` and `pop HL`.
- The pass folds `ex DE,HL` after a word load. The text `ld E,(IY+4)`, `ld D,(IY+5)`, `ex DE,HL` becomes `ld L,(IY+4)`, `ld H,(IY+5)`.
- The pass folds `inc (HL)`, `jr nz,L`, `inc HL`, `inc (HL)`, `L:` (a word in memory) into two `inc (IY+k)`.
- The code `p++` and `p += k` becomes `inc IY` or `ld DE,k` with `add IY,DE`. The value of `*p++` is the access followed by `inc IY`.
- Any other read of the pointer becomes `push IY` and `pop HL`. It costs 25 T-states, and the read from memory costs 16. A store of the pointer becomes `push HL` and `pop IY`. No cheaper move from HL to IY exists.
- A frame function (`--no-shared-storage` or recursion) moves a pointer parameter from HL or DE to IY at the entry. The move comes before the moves of the other parameters.

### Stage 2: the calls

A callee can change IY. Stage 2 runs after the pass for BC and DE. If the pointer is live after a call, the pass puts `push IY` before the call group and `pop IY` after it. The comment `; RYn` marks the two instructions. The call group contains the pushes of the arguments, so the stack of the callee stays correct.

If an argument is a call, the wraps nest. Both wraps stay, because the code reads the pointer after the inner call and inside the outer region.

The code generator does not wrap a call to a clean callee. These callees are clean:

- A runtime helper of `lib/rt` (`__mul16`, `__div32` and others). Each routine keeps IY. `__callhl` is not clean.
- A function that the code generator finished earlier, if the text has no IY and calls only clean code.

All other callees are of unknown type. Examples are a function that the code generator has not yet made, a library routine, an assembly file and a call through a pointer.

`prune_iy` runs on the final program, after `reglocals.prune`. It removes a wrap when the routines inside never change IY. It follows calls and jumps through the whole text. A callee that the code generator did not know is now known. A routine that the pass cannot follow counts as a routine that changes IY.

### Interrupts

An interrupt function pushes AF, BC, DE, HL and IY on entry and pops them at the exit. A pointer in IY therefore survives an interrupt. An interrupt function never keeps a local in IY. The functions that an interrupt function calls can use IY, because the interrupt function saves IY.

The proof `iy` in `bench/rom/isrproof.py` runs a function with IY, a callee with IY and an NMI function that calls a function with IY. It compares the three sums with a Python model.

### The choice

The pass tries each of the three heaviest pointers in IY. For each pointer, it runs the pass for BC and DE on the other locals and adds the wraps. The cost model of "How the pass chooses" gives the cost. The text with IY wins when its cost is lower than the best text without IY by more than 3 weighted T-states. The estimate counts a wrap for each unknown callee.

### Debugging IY

- `UC80_NO_IY=1` turns off IY.
- `UC80_IY_ONLY=f,g` allows IY only in the functions `f` and `g`. Use this switch to find the function of a wrong answer.
- `UC80_RL_DEBUG=1` prints the cost of each IY candidate and the cost without IY.

## Tuning

The cost model has a few constants in `reglocals.py` and `iyptr.py`. An environment variable overrides each constant. The variables are for experiments.

The measurement uses 32 programs of the size run. The set has the four `snd-*` kernels, `sieve`, `queens`, `tak`, five `stanford-*` programs, `davidly-ttt`, `dhrystone21`, `coremark-unofficial`, `micro`, `lz4`, `crc`, `fannkuch`, `binary-trees`, `fasta` and others. The total image of the default build is 156,553 bytes.

Each row changes one constant and keeps the others. The columns show the change of the total image, the number of programs with a smaller or bigger image, the number of programs with more or fewer T-states, and the change of the geometric mean of the T-states. A positive change is worse.


The first five rows give the value of each part of the pass. The rename of scratch values is the largest (1.7% of the T-states). The IY pass gives 0.8%. A second local in BC or DE gives 0.24%. The prune of the wraps gives 0.04% (BC and DE) and 0.09% (IY).

The other rows show that the constants of step 1 are near the best value. A smaller weight of one loop level (4) and a weight of a byte of 12 give a smaller image, but they make some programs slower. An IY margin of 0 does the same. The defaults stay.

One change is better in every column: the lightest candidate goes from 2 to 1. Six programs get smaller, six get faster, and no program gets worse. The value 0 gives the same result. The new default is 1.

## Debugging

- `UC80_NO_IY=1` turns off IY.
- `UC80_IY_ONLY=f,g` allows IY only in the functions `f` and `g`. Use it to find the function of a wrong answer: switch IY off in the other functions.
- `UC80_RL_DEBUG=1` prints the cost of each IY candidate and the base cost.

## Debugging

- `UC80_RL_DEBUG=1` prints the candidates, the gain of each configuration and the rejections.
- `UC80_RL_DUMP=name:BC` (or `name:DE`) prints the text of one configuration.
- `--no-register-locals` turns the pass off.

## Tests

`tests/test_register_locals.py` checks the shape of the code and the answers of the answer programs, with and without the pass, in both storage modes. Two programs are regression tests for ccfuzz seeds 40548 (parameter 1 in DE) and 40278 (the high word of a long result in DE). `bench/ccfuzz.py` and `bench/difftest.py` run random programs.

## Known limits

- Only two locals in BC and DE, and one pointer in IY. HL is not used.
- A function with two hot pointers (`*d++ = *s++`) puts only one of them in IY.
- A call to an unknown callee costs 29 T-states when the pointer is live (`push IY`, `pop IY`). A loop with an unknown call in it often keeps the pointer in BC.
- Array element addresses still use `ld BC,_arr` and `add HL,BC`. When both pairs are busy, the code pays a wrap. The chain assignment `a[i]=b[j]=1` is the worst case.
- A local that lives across many calls often loses against memory. The cost model then keeps the local in memory.
