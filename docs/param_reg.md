# A byte parameter in a spare register

A function with the register convention gets its first parameter in A (a byte) or HL, and its second in E (a byte) or DE. The function stores a parameter that its body names in a slot. After a label, a write of the register, or a call, the body loads the slot again. A load of a byte slot costs 13 T-states.

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change.

## The pass

`keep_params_in_regs` in `src/uc80/regcc.py` runs after `forward_slots`. It works on a function that has no call, no `rst`, no `exx`, no inline assembly and no jump out of the function. In such a function a register that no instruction names keeps its value from the entry to the return.

For each byte parameter that has loads in the text, the pass looks for a register:

- the register in which the parameter arrives (A or E), when no instruction writes it.
- else the first of D, C, B and E that no instruction names and that no parameter uses.

The store of the parameter becomes `ld R,A` (or disappears). Each load `ld X,(slot)` becomes `ld X,R`. A parameter that the body reads in another way (the address of the slot, `inc (slot)`) keeps its slot. The register-locals pass does not see the slot any more, so it cannot give the parameter a register of its own.

An example is the function `out(port, value)` of a sound driver. The `port` goes to D. A call of `out` costs 173 T-states before and about 130 T-states after.

## Two more changes

- **Dead register copies.** After the peephole pass, `reglocals.drop_dead_copies` removes `ld B,r` and `ld C,r` from a function of the compiler. It does this when no instruction reads the byte before the next write or the return. The register-locals pass leaves such a copy (`ld B,H`, `ld C,L`) when the peephole rules use HL instead of BC. A function of the compiler does not return a value in B or C. A call reads B or C only for a helper of `lib/rt` that does. A table gives the reads of each helper.
- **RET cc.** A `jr cc,L` or `jp cc,L` to a label that stands directly before a `ret` becomes `ret cc` (`peep_local.py`, rule 4). The instruction is 1 byte and uses 5 T-states when the condition is false.

## Switch

`--no-param-reg` turns off the three changes.

## Tests

`tests/test_famj_kernels.py` has functions with byte parameters that the body reads after a branch and a loop, that it assigns, and that it uses around calls. The output must be the same as the host output for the default build, `--no-param-reg`, `-O0`, `--no-register-locals`, `--no-register-cc`, `--no-shared-storage` and `--isr-safe`. One test checks that the text of `out` has no `??AUTO` slot and has a `ret nz`.
