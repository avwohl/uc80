# Pair peephole rules and the discarded comma

This document describes two changes of family I step 1. The first change is the code for a comma expression whose value no code uses. The second change is a group of peephole rules for copies between the register pairs BC, DE and HL.

## Discarded comma

A for loop often has an update such as `i++, t++`. The code generator computed the right operand `t++` as a value. For a pointer in HL the value needed a copy: push HL, three `inc HL`, store, pop HL. The value had no use.

Now the code generator gives the right operand of a comma the same "value not used" mark as the whole comma expression. The right operand then has no copy of the old value. A comma expression that has a use (`r = (i++, p++, *p)`) is not changed. The left operand was always discarded.

The side effect is a gain for register locals. A pointer that had a push and pop around the update can now stay in IY or BC.

`--no-discard-comma` switches the change off.

## Pair rules

The rules run inside the register-locals pass (`src/uc80/reglocals.py`), on the text of one function, when the pass keeps a local in BC or DE. The pass has the liveness of the registers B to E, H, L and A for each instruction. Each rule that deletes a register write needs that register to be dead.

Rule A. The pair `ld H,B / ld L,C` followed by `ld A,(HL)` becomes `ld A,(BC)`. The store form `ld (HL),A` becomes `ld (BC),A`. DE has the same rule. HL must be dead after the load or store. A volatile read stays one read.

Rule B. The rule deletes the pair `ld H,B / ld L,C` when HL already holds the value of BC. HL holds that value after the copy `ld B,H / ld C,L`. No instruction of the same straight-line run can write one of the six bytes after the copy. A label, a jump, a call or a return ends the run. Rule B needs no liveness, because the deleted copy changes no register.

Rule C. The sequence `ld A,r / ... / ld (HL),A` becomes `... / ld (HL),r`. Up to three instructions can stand between the two. These instructions must not read or write A, and must not write r. The store can go to `(HL)`, `(IX+d)` or `(IY+d)`. A must be dead after the store.

Rule D. The sequence `ld H,D / ld L,E / add HL,HL / ld D,H / ld E,L` becomes `sla E / rl D`. One or more `add HL,HL` can stand in the middle. HL must be dead after the sequence. All flags must be dead too, because `sla` and `rl` change more flags than `add HL,HL`. A branch, a call, a return or a label before a write of all flags makes the flags live.

Rule R3 (older rule). The local through HL (`ld H,B / ld L,C / inc HL / ld B,H / ld C,L`) becomes `inc BC`. The rule now accepts up to four `inc HL` or `dec HL` instructions in the middle.

`--no-peep-pairs` switches the rules A to D and the longer R3 off.

## What does not change

- The pass does not touch an interrupt function. An interrupt function is on the skip list of the pass. A test compares the text of such a function with and without the options.
- A function that has no register local does not get the rules. The rules run only for a function where the pass tries a configuration.
- The checker of the pass runs after the rules. The pass drops a configuration that fails the check.

## Measured effect

The effect on the bench programs (T-states of the timed region, change against the commit before):

- snd-ymreg: 16,949,631 to 16,340,991.
- snd-cmdq: 9,904,552 to 9,710,812.

Slot folding and the HJUNK mark ([register_locals.md](register_locals.md)) add to these figures. The final values of the family are in the CHANGELOG.

## Tests

`tests/test_peep_pairs.py` runs programs with pointers to structs, pointers to the same array, shifts that use the carry, and comma expressions. Each program runs with the default options, with each switch off, with `--no-register-locals` and with `-O0`. All outputs must be equal.
