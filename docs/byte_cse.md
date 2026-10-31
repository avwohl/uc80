# A reload of A that the path already did

A chain of `else if` tests the same masked byte again and again:

```c
if ((t->reg & 0xF0) == 0x40)
    ...
else if ((t->reg & 0xF0) == 0xA0 && (frame & 7) == 0)
    ...
```

The code generator loads the byte and masks it for each test. After the first test, the register A has the value `t->reg & 0xF0` still. The reason is that `cp` does not change A. The second load and mask (`ld A,(IY+1)` and `and 240`) cost 24 T-states.

Family J step 5b in [family_j.md](family_j.md) lists the change. The rule is rule 5 of `src/uc80/peep_local.py`.

## The rule

```
        ld      A,(IY+1)            ld      A,(IY+1)
        and     240                 and     240
        cp      64                  cp      64
        jr      nz,@ELSE            jr      nz,@ELSE
        ...                         ...
        jr      @END                jr      @END
@ELSE:                      @ELSE:
        ld      A,(IY+1)    (gone)
        and     240         (gone)
        cp      160                 cp      160
```

The rule deletes the instructions at the label when all of these are true:

- The label has one reference only, a conditional jump `jr cc,L` or `jp cc,L`. No other line holds the name of the label (no jump, no table, no `dw`).
- The line before the label is an unconditional jump or a return. No path runs on into the label.
- The label has `ld A,x`, then one or two instructions that change A and read no memory. The forms allowed: `and K`, `or K`, `xor K`, `sub K`, `add A,K`, `adc A,K` and `sbc A,K` with a constant `K`.
- The same instructions stand before the jump, and at most one `cp` stands between them and the jump.
- No label stands between the instructions. A volatile access has a fence label beside it, so the rule never removes the second read of a volatile object.

On the path from the jump to the label no instruction runs, so A has the value that the instructions before the jump made.

## Switch

`--no-byte-cse` keeps both loads.

## Measured effect

`snd-ymreg`, T-states between `TIMER_START` and `TIMER_STOP`: 14,734,825 before, 14,292,405 after (-3.0%). The function `frame_update` has 64 table entries, and 48 take the second test.

## Tests

The file `tests/test_famj_byte_cse.py` has the tests. Two programs run in six option sets against the host compiler: an `else if` chain on a struct member, a chain on `x & 15` and `x ^ 0x55`, and a `goto` into the chain. A shape test counts the loads `ld A,(IY+1)`. A second shape test checks that a volatile register is read two times.
