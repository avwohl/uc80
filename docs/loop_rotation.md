# Loop rotation

The code generator tests a `for` or `while` loop at the top. Each pass runs the conditional exit jump (7 T-states, not taken) and the back jump `jr` (12 T-states).

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change. The code is in `src/uc80/looprot.py`.

## The change

The pass copies the test to the end of the loop. The copy ends with the opposite condition and a `jp` to the start of the body (10 T-states). The test at the top stays. A `continue` that jumps to the top, and the entry into the loop, work as before.

```
@FOR4:  TEST                @FOR4:  TEST
        jr nc,@ENDFOR6              jr nc,@ENDFOR6
        BODY                @RB0:   BODY
        jr @FOR4                    TEST
@ENDFOR6:                           jp c,@RB0
                            @ENDFOR6:
```

Each pass saves 9 T-states. The loop grows by the size of the test minus 2 bytes.

## When it applies

The pass runs on the text of the program after the peephole pass. A loop is rotated when:

- the head label is `@FORn` or `@WHILEn`.
- the test up to the first conditional jump to `@ENDFORn` or `@ENDWHILEn` has at most 12 instructions.
- the test holds no label except the `@CMP_NVn` labels of a signed compare, no return, no `rst`, no `djnz`, no jump to another place, and no comment.
- the back jump to the head stands directly before the exit label.

A test that holds a call is copied with the call. The pass rotates a test `a && b` at its first exit jump. The body label stands after that jump.

After the copy, the pass removes the first instructions of the copy when the code before it has made them true. One case is a load that follows the store of the same value (`ld (n),HL` and `ld HL,(n)`). The other case is the copy of a pair into HL that follows the copy of HL into the pair.

## Switch

`--no-loop-rotate` keeps the test at the top. `-O0` does not run the pass.

## Tests

`tests/test_famj_kernels.py` runs nested loops, `break` and `continue`, and loops with zero passes. It runs `do` loops, `&&` and `||` tests, and a call in the test. It also has a jump into and out of a loop, a `long` counter and a `signed char` counter. The output must be the same as the host output. The options are the default build, `--no-loop-rotate`, `-O0`, `--no-register-locals`, `--no-pair-cmp`, `--no-register-cc`, `--int=32` and `--no-licm --no-range-cmp --no-narrow-index`. A shape test looks for the `@RB` labels.
