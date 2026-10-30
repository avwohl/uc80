# Runtime shortcuts

Some runtime routines now have an early exit for a common case. The exit is only an addition. The routine gives the same result as before.

Family J step 1 in [codegen_gap.md](codegen_gap.md) lists the change.

## 32-bit division

`__div32` (`src/uc80/lib/rt/rt_div32.mac`) divides the dividend in DEHL by the divisor in `__tmp32`. For a divisor of 65536 or more, the old code ran a loop of 16 division steps. It needed about 2000 T-states.

When the dividend is below 65536 and the divisor is 65536 or more, the quotient is 0 and the remainder is the dividend. The new code tests this first, at `__div32_big`. A division such as `temp / 100000L` with a 16-bit `temp` needs about 100 T-states. The program `stanford-quick` does 25000 such divisions. It needs 61 million T-states less.

## Switch

The lines of the exit have the tag `;@rt-shortcut`. In a whole-program build, the compiler embeds the routine. `--no-rt-shortcuts` removes the tagged lines from the embedded copy. The library copy in `runtime.lib` always has the exit. A program that links the library copy (for example, one with `--no-whole-program`) has the exit too.

## Tests

`tests/test_famj_startup.py` divides 13 unsigned and 15 signed dividends by 16 and 16 divisors, with all sign combinations. The values are around 255, 256, 65535, 65536 and 2^31. It prints the quotient and the remainder and compares the text with the host compiler. It uses the default build, `--no-rt-shortcuts`, `-O0` and `--no-whole-program`.
