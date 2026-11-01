# Family J: start-up, console and kernel passes

Family J is the work that follows [family I](codegen_gap.md). The aim is that each program that SDCC 4.6.0 runs faster than uc80 runs faster with uc80. The programs are the ones in [codegen_gap.md](codegen_gap.md), among them the sound-driver kernels `snd-cmdq` and `snd-ymreg`.

Each pass has a page, a test in `tests/test_famj_startup.py` or `tests/test_famj_kernels.py`, and a switch that turns the pass off.

Step 1 changes the start-up code, the console and the runtime:

- [Fast BSS clear](startup_fast.md): `--no-fast-bss-clear`.
- [putchar as a statement](void_putchar.md): `--no-void-putchar`.
- [Runtime shortcuts](runtime_shortcuts.md): `--no-rt-shortcuts`.

Step 2 changes the code of kernels and loops:

- [A char result that a mask uses](char_return_mask.md): `--no-char-ret-mask`.
- [A byte parameter in a spare register](param_reg.md): `--no-param-reg`.
- [A frame without slots](frame_drop.md): `--no-frame-drop`.
- [An empty if](dead_test.md): `--no-dead-test`.
- [Register pairs in a compare and in an index](pair_compare.md): `--no-pair-cmp`.
- [Loop rotation](loop_rotation.md): `--no-loop-rotate`.
- [A loop that fills an array](fill_idiom.md): `--no-fill-idiom`.
- [1 << n in line](shl_inline.md): `--no-shl-inline`.

Two bugs and the size rule changed some passes after the first version (see the repair entry in the [CHANGELOG](../CHANGELOG.md)). The pages above describe the final code.

Step 3 moves loads out of loops:

- [Loop-invariant loads and row addresses](licm_load.md): `--no-licm-load`.

Step 4 changes the register allocation, and step 5 adds smaller items that use it:

- [Byte locals in B, C, D and E](byte_regs.md): `--no-byte-regs` (step 4), `--no-rl-addr-taken` (step 5a).
- [A counter of the range 0 to 255 in one byte](range_compare.md): `--no-range-byte-cmp` (step 5b).
- [A reload of A that the path already did](byte_cse.md): `--no-byte-cse` (step 5b).
- [Division by a constant that the range decides](range_div.md): `--no-range-div` (step 5c).
- [The shared automatic storage is not cleared](startup_fast.md): `--no-skip-auto-clear`.

## Not in the code

One item of the plan is not in the code:

- The demotion of an `int` local with byte values to a `char` (step 5a). A hand edit of `MinMax` in `davidly-ttt` gained 0.8 percent. A byte local in an index sum costs a sign extension, so the pass would need a rule for each use.
