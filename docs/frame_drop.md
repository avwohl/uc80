# A frame without slots

A function with stack locals or with register parameters that the body names has a frame: `push IX`, `ld IX,0`, `add IX,SP`, the pushes of the parameters and the reservation of the locals. The epilogue is `ld SP,IX` and `pop IX`. The frame costs about 79 T-states for each call.

After the register-locals pass, a recursive function such as `fib` or `ack` can keep all values in BC and DE. The slots are dead, but the frame stayed.

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change.

## The change

`_drop_dead_frame` in `src/uc80/codegen.py` runs after the register-locals pass. It checks the text of the body:

- no line names IX.
- no call of `setjmp`.
- the function has no statement expression.
- the pushes of the register parameters and the reservation `ld HL,-n / add HL,SP / ld SP,HL` stand where the prologue put them.

If all is true, the pass deletes the pushes and the reservation. Then `_frame_is_unused` deletes the other prologue lines and the epilogue. A function without a frame can use the tail call: `call _ack` followed by `ret` becomes `jp _ack` (the rules are in `_tail_call_returns` and `peep_local.py`).

A function that takes the address of a local, or that has a stack parameter, names IX. It keeps its frame.

The programs `tak` needs 6.3 million T-states less.

## Switch

`--no-frame-drop` keeps the frame.

## Tests

`tests/test_famj_kernels.py` runs `fib`, `ack` and `tak`. It runs two functions that call each other. It runs a function that takes the address of a local, and a function with an array on the stack. A last test uses `longjmp` through a recursive function. It compares the output with the host compiler for the default build, `--no-frame-drop`, `-O0`, `--no-register-locals`, `--no-register-cc`, `--isr-safe` and `--no-shared-storage`.
