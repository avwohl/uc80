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
- [An if with nothing to do](dead_test.md): `--no-dead-test`.
- [Register pairs in a compare and in an index](pair_compare.md): `--no-pair-cmp`.
- [Loop rotation](loop_rotation.md): `--no-loop-rotate`.
- [A loop that fills an array](fill_idiom.md): `--no-fill-idiom`.
- [1 << n in line](shl_inline.md): `--no-shl-inline`.

Two bugs and the size rule changed some passes after the first version (see the repair entry in the [CHANGELOG](../CHANGELOG.md)). The pages above describe the final code.

## Not in this part

The larger causes of the analysis stay open. They are the loop-invariant loads and address expressions of `stanford-puzzle`, the byte-granular register allocation of `queens` and `davidly-ttt`, and the compile-time division of `stanford-quick`. [codegen_gap.md](codegen_gap.md) has the numbers.
