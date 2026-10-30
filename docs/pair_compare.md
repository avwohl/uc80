# Register pairs in a compare and in an index

The code copies a local in BC or DE to HL before an 8-bit compare or an index: `ld H,B`, `ld L,C`, `ld A,L`, `sub 255`, `ld A,H`, `sbc A,31`. The index of `flags[k]` uses `push BC`, `ld BC,_flags`, `add HL,BC`, `pop BC`.

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change. The rules are in `src/uc80/reglocals.py`, in `_try_pair_rules`.

## The rules

- **(e)** `ld H,R / ld L,R`, then code that does not write HL or R, then `push R / ld R,K / add HL,R / pop R`. HL still holds R. The window becomes `ld HL,K / add HL,R`.
- **(e2)** The same with `ld rr,K / add HL,rr` for the other pair rr, when rr is dead afterwards.
- **(f)** `ld H,R / ld L,R` followed by instructions that read H and L only as bytes (`ld A,H`, `ld A,L`, `or L`, `sub H`). These instructions must not write HL or R. HL must be dead after the window. The reads use the bytes of R, and the two copies go away.

An example is the inner loop of the sieve. The test and the index cost 94 T-states. They cost 44 T-states now. The program `sieve` needs 388000 T-states less.

The signed compare `k < 8191` has the form `ld A,B / xor 128 / ld D,A / ld A,C / sub 255 / ld A,D / sbc A,159`. It loses only the copy. The program `davidly-sieve` needs 1.3 million T-states less.

Each rule checks the liveness of the bytes with the same data that the peephole pass of the register locals uses. The checker of the register-locals pass proves the final text.

## Switch

`--no-pair-cmp` turns the rules off.

## Tests

`tests/test_famj_kernels.py` has a sieve, loops with signed and unsigned counters and negative values, and bounds near 255 and 256. The output must be the same as the host output for the default build, `--no-pair-cmp`, `-O0`, `--no-register-locals`, `--no-loop-rotate` and `--no-licm --no-range-cmp --no-narrow-index`.
