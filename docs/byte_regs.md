# Byte locals in B, C, D and E

The register-locals pass ([register_locals.md](register_locals.md)) moved at most two locals into BC and DE. A byte local used only C or E. The other byte of the pair had to be dead. The program `queens` has four byte locals (`row`, `c`, `d1` and `d2`) and three arrays. The old pass kept three of them in IX slots (19 T-states for each access) and rebuilt each array address with a copy and a push.

Family J step 4 changes the pass. Now four byte locals can sit in B, C, D and E. Step 5a gives register locals to a function with a taken address. The code is in `src/uc80/reglocals.py`, `src/uc80/reglocals_gen.py` and `src/uc80/codegen.py`.

## What changed in the allocation

- A byte local can take B, C, D or E. A word local takes BC or DE as before. The pass tries up to four locals together: four bytes, a word and two bytes, or two words (`UC80_RL_MAXBYTES` sets the limit, 4 is the default).
- The code of a function that uses a pair is made safe for each pair as a unit. The unit holds a word local, or one or two byte locals. A wrap (`push rr` before and `pop rr` after) saves both bytes of the pair. A scratch value in the other byte of the pair that crosses the end of the wrap joins the wrap.
- The search first builds each candidate alone (in C and E, and the same cost for B and D). It then builds pairs of the best choices. It extends the best two pairs by one local, and the best triples by one more.
- A counter in the cost model: code that follows a forward branch and ends in a return (an early exit) counts a quarter. The loops in such a block are rarely the hot loops.
- The exit mask of a function that returns a char in A is A only. H and L are free to take part in the code of the function.
- A cleanup `pop rr` that nobody reads as a scratch value becomes two `inc SP`, also when a local is live in that pair. The check for the depth of the stack follows `inc SP` and `dec SP` (the old check stopped at them).

## Scratch-free forms

The code generator uses BC and DE as scratch pairs for address sums. When both pairs hold locals, such a sum needs a wrap. These forms need no scratch pair (`sf` in the debug output).

The pass uses a form only when three things are true. The register `A` is dead after the sum. The scratch pair holds no scratch value after the sum. No flag is live. The forms:

- `ld L,x / ld H,0 / ld BC,arr / add HL,BC` becomes `ld A,x / add A,LOW (arr) / ld L,A / ld A,HIGH (arr) / adc A,0 / ld H,A`. It costs 33 T-states, the wrap costs 53.
- `ld BC,arr / add HL,BC` becomes the same sum on the bytes of HL.
- `ld BC,(slot) / add HL,BC` reads the two bytes of the slot with `add A,L` and `adc A,H`.
- `ld H,Rh / ld L,Rl / ... / ld R,K / add HL,R`, where R is a pair of locals, becomes `ld HL,K / add HL,R`.
- The signed compare of HL with a constant keeps the flipped high byte in H, not in D.
  - If HL is live after the compare, `ld H,Rh` puts the copy back.
  - If HL is dead, the compare reads the registers of the locals.
- `inc C / jr nz,L / inc B / L:` becomes `inc BC` for a word local in BC.

## The code generator

- The compare of two signed chars keeps the flipped values in H and L, not in D and E.
  - A local in shared storage gives the right operand `(HL)`.
  - Then the flipped values stay in D and E.
- `a[i] = c` with a simple char variable `c` computes the address first and loads `c` afterwards. The old code kept `c` in E or on the stack while it computed the address.

## Step 5a: a function with a taken address

The pass skipped each function that has its address taken (a callback, or an entry of a table of functions). A caller through a pointer treats the callee as a routine that changes every register. The assembly callers in the library (`qsort`, `bsearch`, `atexit`) read their own state from the stack. A function that assembly code calls by name stays out. In the nine winner functions of `davidly-ttt`, the variable `x` (`register int x = board[0]`) is now in BC. The switch `--no-rl-addr-taken` restores the old rule.

## Range of a loop counter

The code compares a counter in the range 0 to 255 as one byte (`ld A,C / cp 9`). An example is the counter of `for (p = 0; p < 9; p++)`. The old code compared 16 bits. [range_compare.md](range_compare.md) describes the ranges. The switch `--no-range-byte-cmp` restores the 16-bit compare.

## Switches

- `--no-byte-regs`: at most two locals, a byte only in C or E, the old signed compare of chars and the old order of a char store.
- `--no-rl-addr-taken`: no register local in a function that has its address taken.
- `--no-range-byte-cmp`: 16-bit compare for a counter of the range 0 to 255.
- `UC80_RL_DEBUG=1` prints each configuration with its gain. `UC80_RL_DUMP=a:C,b:D` prints the text of one configuration.

## Tests

The file `tests/test_famj_byte_regs.py` runs five programs in nine option sets against the host compiler:

- the queens search.
- byte locals across calls with both conventions.
- signed and unsigned compares of chars.
- 16-bit counters beside bytes.
- a recursive search with stack arguments. Shape tests check the scratch-free forms. `tests/test_conditions.py` has the new text of the signed compare. The tests of the register-locals pass, the ISR tests and the 75 programs of the test family run with the pass on.
