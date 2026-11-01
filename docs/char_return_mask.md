# A char result that a mask uses

A function that returns a `char` can return the value in A (the register convention, [calling_convention.md](calling_convention.md)). The callee does not extend the byte to HL. Each caller that needs an `int` must extend the byte. The compiler counts the calls that use the value as a byte and the calls that need an `int`. It keeps the result in A only when the byte uses are at least as many.

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change.

## The change

The call in `in(port) & 0x80` counted as an `int` use. Only the low byte of `in(port)` reaches the result of `& 0x80`. So the call is a byte use now. The rule applies to `&` with an integer literal from 0 to 255 on one side.

For `|` and `^` with such a literal, the high byte of the result is the high byte of the call. These operators take the context of the whole expression. The call is a byte use only when the expression is a byte use itself (for example, a statement, a condition or a cast to `char`).

The status loop `while (in(4) & 0x80)` of a sound driver tests `bit 7,A` after the call. The program `snd-ymreg` needs 445000 T-states less.

## Switch

`--no-char-ret-mask` counts these calls as `int` uses again.

## Tests

`tests/test_famj_kernels.py` has `char` and `signed char` functions in `f(x) & 0x0F`, `f(x) | 1`, `f(x) ^ 0x55`, in conditions and in sums. It compares the output with the host compiler for the default build, `--no-char-ret-mask`, `-O0`, `--no-register-cc`, `--no-register-locals` and `--no-param-reg`.
