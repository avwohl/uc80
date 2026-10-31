# Changelog

All notable changes to uc80 are recorded here. Versions follow
`major.minor.patch`: the minor number is raised when a release changes the
observable behaviour of a program uc80 compiles.

## Unreleased

### Family J, step J1: start-up, console, kernels and loops (branch `famj`)

The step has eleven changes. Each one has a switch that turns it off, a docs page ([family_j.md](docs/family_j.md)), and tests that run code. The tests are in `tests/test_famj_startup.py` and `tests/test_famj_kernels.py`. Each program runs under cpmemu and on the host compiler.

Step 1, start-up, console and runtime:

- **Fast BSS clear.** The start-up code cleared the BSS with one `LDIR` and some tests. A BSS of less than 256 bytes uses `crt0_small.mac`: a link-time `LD BC,size-1` and `LDIR`. The empty program needs 135 T-states (212 before), and its image is 32 bytes (49 before). A program with 256 bytes of BSS or more embeds `crt0.mac`. For a BSS of 257 bytes or more this file clears 16 bytes in each pass with 8 `PUSH HL`, with interrupts off. `--no-fast-bss-clear` embeds the old text. [startup_fast.md](docs/startup_fast.md).
- **`putchar` as a statement.** `putchar(c);` and `(void) putchar(c);` call the new `__putchar_v` with the char in E. A character costs about 103 T-states (254 before). `__conout` calls `__putchar_v`, so each `__conout` call costs 10 T-states more. `--no-void-putchar`. [void_putchar.md](docs/void_putchar.md).
- **`__div32` for a small dividend.** A dividend below 65536 and a divisor of 65536 or more gives quotient 0 and remainder = dividend at once. `--no-rt-shortcuts` (embedded copy only). [runtime_shortcuts.md](docs/runtime_shortcuts.md).

Step 2, kernels and loops:

- **Char result and a mask.** `f(x) & 0x80` counts as a byte use of `f`, so `f` returns the char in A. `--no-char-ret-mask`. [char_return_mask.md](docs/char_return_mask.md).
- **Byte parameter in a spare register.** A call-free function can load a byte parameter after a label. The parameter then stays in D, C, B or E. After the peephole pass, dead copies `ld B,r` and `ld C,r` go away. `jr cc,L` to a label before a `ret` becomes `ret cc`. `--no-param-reg`. [param_reg.md](docs/param_reg.md).
- **Frame without slots.** After the register locals, a function that names IX no more loses the frame code. `call f` and `ret` then become `jp f`. `--no-frame-drop`. [frame_drop.md](docs/frame_drop.md).
- **Empty if.** `if (c) ;` with a pure `c` goes away. `--no-dead-test`. [dead_test.md](docs/dead_test.md).
- **Pair compare.** A register pair is read directly in a compare, and `ld HL,K / add HL,rr` replaces the wrap `push rr / ld rr,K / add HL,rr / pop rr`. `--no-pair-cmp`. [pair_compare.md](docs/pair_compare.md).
- **Loop rotation.** The test of a `for` or `while` loop is copied to the end of the loop. Each pass saves 9 T-states. A test has at most 14 bytes, and at most 9 bytes in a loop that holds another loop. `--no-loop-rotate`. [loop_rotation.md](docs/loop_rotation.md).
- **Fill loop.** `for (i = C0; i < N; i++) a[i] = K;` over a global byte array becomes one `LDIR` fill. `--no-fill-idiom`. [fill_idiom.md](docs/fill_idiom.md).
- **`1 << n` in line.** `--no-shl-inline`. [shl_inline.md](docs/shl_inline.md).

Other changes: `sdas.py` computes the BSS size at run time (sdas cannot subtract two linker symbols). `reglocals.helper_reads` knows that `__putchar_v` reads only E, so a local in BC or DE is not read by the call. Three older tests changed. `test_iy_pointers` uses `--no-pair-cmp`, because the pair rules can prefer BC to IY. `test_register_cc` uses `--no-frame-drop` in its frame test. `test_integrate10_interactions` compares the handler writes only, because the interrupt lands in another place of the main loop when the speed changes. `test_rom_target` accepts the header of `crt0_small.mac`.

Numbers, `bench/run.py -c uc80` on 111 programs, against SDCC 4.6.0:

- The 13 programs that were above SDCC (total T-states of uc80 over SDCC, geometric mean 1.196): now 0.958, and 10 of them are below 1. `bsd-ppt` 0.974 (1.598), `sieve` 0.938 (1.475), `bsd-bcd` 0.941 (1.390), `empty` 0.776 (1.218), `davidly-sieve` 0.842 (1.195), `tak` 0.881 (1.055), `stanford-quick` 0.866 (1.041), `snd-cmdq` 0.955 (1.033), `sieve+static` 0.825 (1.013), `snd-ymreg` 0.965 (1.012).
- Still above SDCC: `davidly-ttt` 1.493 (1.523), `queens` 1.099 (1.134), `stanford-puzzle` 1.069 (1.072). The causes are byte-granular register allocation and loop-invariant loads (steps 3 and 4 of the plan).
- All programs: size (bytes minus the empty program) 0.9530 (0.9117), whole image 0.9083 (0.8794), T-states (speed mode) 0.5038 (0.5275), without `sort-*` 0.6050 (0.6367). uc80 is faster than SDCC on 74 of 78 programs (66).
- The size means are worse for two reasons. The empty program is 17 bytes smaller, and the bytes column subtracts it, so each program looks 17 bytes bigger. The loop rotation (about 1.8% of the size mean), the push fill (56 bytes for a program with 256 bytes of BSS or more) and the runtime shortcut add bytes. 92 of 104 programs are bigger, by 90 bytes in the median. The biggest are `stanford-puzzle` (+358) and the `micro` programs (+324). 12 programs are smaller: `tak` -63, `bsd-bcd` -50, `binary-trees` -45.
- Eleven programs use more T-states, by 1.6% or less. In ten of them a `__conout` call costs 10 T-states more: `hamurabi` +1.6%, `bsd-morse` +0.7%, `v7-fish` +0.45%, `fuzix-startrek` +0.35%, `game-guess` +0.24%, `fasta+static` +0.15%, `bsd-fish` +0.14%, `embench-md5sum` +0.03%, `bsd-arithmetic` +0.02% and `umchess` +0.001%. `embench-slre` is 0.27% slower: the pair rules change which local the cost model puts in BC. No status changed.

Gates: `pytest -n 6`: 3209 passed (3066 on `main`, 143 new). `run_ctests.py`: 220 of 220. A first run of this gate had 219 of 220, with 00129 as a compile failure. The program compiles with `main` and with this tree, and the failure did not come back. `difftest.py --count 800 -j 8` with the seeds 9000 and 19000: no UC80-WRONG and no MODEL-DIFF. `ccfuzz.py`: 2000 programs, 1000 with `--ll`, and 400 of each in the option sets `-O0`, `--no-register-locals`, `--no-licm --no-range-cmp --no-narrow-index`, `--int=32` and all eleven new switches: all PASS. The 75 programs of the gen3 family: no RLDIFF. `isrproof.py` with `ul80` and with `--sdas`: five of five OK. `sndrom.py`: four of four OK.

### Integration of `fami` (family I) into the bug round 3 tree (branch `integrate10`)

The merge of `fami` (head d1071f9) into `main` 4702a63 had text conflicts in four files. `CHANGELOG.md` and `docs/codegen_gap.md` keep both sides. `bench/results/results.csv` and `docs/benchmarks_speed.md` come from `bench/run.py` and `bench/report.py`. The files `codegen.py`, `main.py`, `reglocals_gen.py`, `lc_printf_core.mac` and `libc.mac` merged without a conflict. The combined code was read. No code of a family I pass changed.

- **Check of the auto-merges.** In `codegen.py`, the mixins `LicmMixin`, `RangeMixin` and `NarrowMixin` come after `AddrCseMixin`. The passes run in this order: dead stores, address CSE, index shrink, LICM. The integrate9 fixes are in the tree before the new passes see the code. In `main.py`, the six new switches sit next to the switches of family H. The `noreturn_labels` mapping is there too. The new `_prt_div16` and `_div32_10` do not touch the width, precision and `*` code of bug round 3. `__conout` and `putchar` keep the CRLF translation.
- **Interactions, checked with programs that run.** Printf: 4,200 random formats give the same text as the host with 16-bit `int` and with `--int 32`. The precision above 255 for `%f` is 255 digits, as before the merge. Loop passes: `tests/test_integrate10_interactions.py` has bit-field stores through 32-bit index expressions and `array + n` in static initializers. It also has `long` loop counters, volatile objects and block-scope enumerators. One program is an interrupt handler with a loop. The index pass turns itself off with `--int 32`. `[[noreturn]]` works with a static function, in a two-file build, and with a semcheck error in the second file.
- **Timeouts.** Every `subprocess.run` that starts a host program has a `timeout=` now. This covers `tests/test_range_cmp.py`, `tests/test_licm.py`, `tests/test_narrow_index.py`, `tests/test_printf_flags.py`, the TICKS call in `tests/test_romrun.py` and the host run in `bench/run.py`. An earlier bad version of the range test ran for 6 hours.

### Repair of integrate10

A check of the merged tree found six real problems. The first two come from bug round 3 and the oldest `--int 32` code. Family I makes the first one worse. The passes `narrow.py` and `licm.py` compute constants before the code generator runs. Each fix has a test that runs code (`tests/test_integrate10_repairs.py`, 13 tests, each compared with the host).

- **Enumerator that names an earlier enumerator.** `enum { GA = 2, GB = GA << 3, GC = GB | 1 };` gave GC = 3 instead of 17. The code registered the constants of an enumeration after the loop, so `_eval_enum_expr` did not see the earlier ones. The three loops (`_register_enum_type_values`, `_register_decl_spec_enums`, and the enum code of the declaration) now register each constant at once. A loop that used such a constant in an index or a mask gave different code with the loop passes on and off.
- **`--int 32`, operations on `short` operands.** `us + u2` (two `unsigned short`) wrapped at 16 bits. The same held for `-`, `&`, `|`, `^`, `<<`, `~` and unary `-`. `_is_long_expr` now treats these as 32-bit operations when one operand is a `short` (for `*`, also two `char`). `gen_binary_op` uses `_is_long_expr` of the whole expression. Unary `-` and `~` widen the 16-bit operand first. The code is bigger. The c-testsuite test 00200 failed before this repair and passes with `--int 32` now.
- **`--int 32`, `(a * b) * (a + 3)` of `short` values.** The left product overwrote `__tmp32`, which held the right operand. `_uses_tmp32` now answers yes for a 32-bit operation on `short` operands. The result 3 was 12.
- **A parameter or a local with the name of a function.** `static void viaptr(void (*die)(short), short v) { die(v); }` called the file-scope function `die`. `_gen_call` now treats a local or parameter that is not a function as a function pointer. A `[[noreturn]]` function of the same name no longer ends the flow after the call.
- **`fputc`, `putc` and `setvbuf` with `--int 32`.** The library reads an `int` argument as 2 bytes, so the `FILE *` argument was read from the wrong place and the call returned -1. `stdio.h` declares the character (and the mode) as `unsigned char` when `__SIZEOF_INT__` is 4.
- **Host runs without a timeout.** The host run in `bench/run.py` and the TICKS call in `tests/test_romrun.py` now have `timeout=`.

Not changed, with the reason:

- A `typedef` that an inner block declares again with a different type stops the parser (`unexpected token`, and the line number is wrong). The parser is in `uc_core`, which this branch does not change. The problem is in `docs/ANSI_C_COMPLIANCE.md`.
- Semcheck does not reject some invalid code. One example is `--3`. Others are an assignment to an enumeration constant, and a `case` outside a `switch`. These checks do not exist yet. The full list is in `docs/ANSI_C_COMPLIANCE.md`.
- The `%f` precision above 255 is 255 digits, and `putchar('\t')` prints 8 spaces. `docs/printf.md` and `docs/console_output.md` describe both limits.
- `src/uc80/lib/libc.rel.mac` still has the old `_prt_div16`. `build_libs` does not use the file, and neither branch changed the file.

Gates of the repaired tree (13 new tests included):

- `pytest -n 6`: 3066 passed. `run_ctests.py`: 219 of 220 (only 00129). `run_ctests.py --int 32`: 219 of 220 (only 00129). The earlier 218 had 00200 as a second failure. `main` 4702a63 has 217 of 220 with `--int 32`. The `--int 32 --long 64` run has 218 of 220 (00129 and 00200).
- `uc_core`: 384 passed, 1 skipped. `uc386`: 515 passed, 7 skipped.
- `isrproof.py` with `ul80` and with `--sdas`: nmi, seq, bank, iy and memisr OK. `sndrom.py`: the four kernels OK.
- `difftest.py --count 800 -j 5` with the seeds 9000 and 19000: every kept failure has `uc80.out` equal to `model.out`. No UC80-WRONG and no MODEL-DIFF. The MODEL-DIFF of seed 2000 (program 2429) in the first merge report was not seen again. The earlier run said that uc80, SDCC and the tree of `main` gave the same output, so the generator model was the cause. The model fault is not located.
- `ccfuzz.py`: 2000 and `--ll` 1500 all PASS. 400 and `--ll` 400 with `-O0`, `--no-register-locals`, `--no-addr-cse`, `--no-dead-store`, `--no-blockops` and `--no-licm --no-range-cmp --no-narrow-index` all PASS. `--flags=--int 32` 400 and `--ll` 400 all PASS.
- The gen3 family (75 programs, absolute paths): no failure. The output has only `hostdiff(info)` lines.

Bench of the repaired tree: `bench/run.py -c uc80` on all programs (112 rows) gives the same status, image, BSS and cycles in every row as the merge run. So `bench/results/results.csv` and `docs/benchmarks_speed.md` stay as the merge run made them. Geometric means against SDCC 4.6.0:

- Merged tree: size 0.9117, whole image 0.8794, T-states 0.5275, 0.6367 without `sort-*`, faster on 66 of 78 programs.
- `main` 4702a63: 0.9151, 0.8826, 0.5456, 0.6611, 63 of 78.
- `fami`: 0.9128, 0.8805, 0.5274, 0.6366, 66 of 78.
- `battlestar-size`, `rogue36-size`, `empire-trefonas`, `trek-size` and `adventure-size` end as TOO-BIG, as in `main`.

No row is slower than both parents. Rows that are bigger than both parents (the causes come from a diff of the linked symbol tables of the three trees):

- `bsd-pig` image 4565 against 4558 (`main`) and 4444 (`fami`). Against `main`: `_prt_div16` +8, `__conout` +5, `_putchar` -3 and `_main` -3, so +7. These are the library changes of `fami`. Against `fami`: the 121 bytes are the printf code of bug round 3 (`_printf_padw`, the `*` handlers, wider width and precision).
- The BSS of `coremark-unofficial` (2433, `main` 2421, `fami` 2432), `mojozork-size` (5870, 5868, 5869) and `trek-size` (3345, 3343, 3343). Against `main`: +2 to +12 bytes in the shared auto area (`??AUTO`) from the new locals of the loop passes. For `trek-size` the area is 1321 bytes in `main` and 1323 in the merged tree. Against `fami`: +1 byte in `coremark-unofficial` and `mojozork-size`, +2 in `trek-size`. The cause is the printf pad counters of bug round 3 (`_pa_pad`, `_phs_pad` and `_pf_pad` have 2 bytes, where `fami` has 1). A program that links two of the counters has +2.

### Integration of `bugfix3` into the ROM and family H tree (branch `integrate9`)

The merge of `bugfix3` (head 0dd1b90) into `main` cf0c5e4 (ROM work, address CSE, dead stores, inline block operations) had no text conflict that needed more than a union of both sides in `CHANGELOG.md`; the interactions were checked with programs that run. `uc_core` is the worktree `bugfix3core` (a fast-forward of `uc_core` `main`); `uc_core` is not changed.

- **Three bugs of `--int 32` that the integration found** (all three were in `main` cf0c5e4 or in `bugfix3`, none came from the merge of the two sides; `tests/test_int32_hl_only_values.py` runs each one with `--int 32` and with the 16-bit `int`).
  - A `?:` of two `unsigned short` (or `short`) operands has the type `int`, 4 bytes with `--int 32`, but `gen_ternary` left the value in HL. `(unsigned long)(c ? (unsigned short)19163 : (unsigned short)gl)` gave 143477467 (DE was whatever the last code left). `gen_ternary` now loads both branches as 32-bit values when the type of the operator is 32-bit. This was in `main` cf0c5e4.
  - A comparison cast to a 64-bit type, `(long long)(unsigned long long)(a < b)`, had the same fault: the cast code assumed that a 4-byte `int` is in DEHL, but the code of a comparison sets HL only, so the high word of the result came from the stack (`-65536` for 0). `gen_cast` extends a value that is not a long expression. This was in `main` cf0c5e4.
  - A bit-field of an enumeration type must be unsigned (c-testsuite 00218). `bugfix3` turns an enumeration into `int` when an enumeration object has 4 bytes (`--int 32`), and the layout code then took the field as signed (`x.code = 149` read back as -107). `_to_legacy` marks the converted type (`from_enum`) and the layout code reads the mark. With the fix, `run_ctests.py --int 32` has the same 217 passes as `main` (00129, 00200 and 00219 fail in both; before the fix 00218 failed too).
- **ccfuzz with `--int=32` was a fuzzer limit, plus the two real bugs above.** The program of `ccfuzz.py` types `I16` as `int` for uc80 and as `short` for the host (the program models a 16-bit `int`). With `--int=32` the uc80 `I16` has 32 bits and the checksums differ at once: 204 of 400 seeds (and 289 of 400 with `--ll`) were WRONG, and `main` cf0c5e4 gives the same. `ccfuzz.py` now types `I16` as `short` for uc80 when the flags have `--int=32` (or `--int 32`). Result on the same seeds: 369 of 400 PASS (31 WRONG, and 396 of 400 with `--ll`); the 31 and the 4 that remained are the two bugs above (the same seeds fail on `main` cf0c5e4, and all PASS after the fixes). New seeds with `--int=32` are in the gates below.
- **Interactions of family H with `bugfix3`, checked with programs that run.** (1) The semcheck errors do not reject a valid program: all 111 bench programs and the 220 c-testsuite programs compile as before (219 pass, only 00129). (2) The saved pointer word of `_gen_bitfield_rmw_once` stays when `deadstore` runs, and the `addrcse` keys do not include a bit-field read-modify-write: `tests/test_bitfield_side_effects.py` passes with the default options and with `--no-addr-cse`, `--no-dead-store` and `--no-blockops`. (3) The hidden names of block-scope enumeration constants do not change the output of `addrcse` or `deadstore` (`tests/test_block_scope_enum_constants.py`). (4) 4-byte enumerations with `--int 32` run in the c-testsuite and in ccfuzz as above. (5) The compile-time memos change no code: the generated `.mac` of the 111 bench programs is byte-identical with and without the memos (`memocmp`, 111 compared, 0 different).
- **Gates of the merged tree** (with the three fixes): `pytest -n 6` 2877 passed; `run_ctests.py` 219 of 220 (only 00129); `run_ctests.py --int 32` 217 of 220 (as `main`); `isrproof.py` nmi, seq, bank, iy and memisr OK with `ul80` and with `--sdas`; gen3 family (75 programs, `harness.py`, absolute paths, no `RLDIFF`) no output except `hostdiff(info)` lines; `difftest.py --count 800 -j 8` with the seeds 1 and 7001: no UC80-WRONG and no MODEL-DIFF (the 251 and 172 kept failure directories are SDCC rows: in each, `uc80.out` equals `model.out`); `ccfuzz.py --count 600` and `--ll --count 1500` all PASS, and 300 programs each (default and `--ll`) with `-O0`, `--no-register-locals`, `--no-addr-cse`, `--no-dead-store` and `--no-blockops` all PASS; with `--int=32` (the `I16` of the program typed `short`) 400 + 400 new seeds and `--ll` 400 all PASS; `uc_core` 384 passed, 1 skipped; `uc386` 515 passed, 7 skipped. The generated `.mac` of the 111 bench programs is byte-identical before and after the three fixes (16-bit `int`), so the bench rows did not need a second run.
- **Bench of the merged tree** (`bench/run.py -c uc80` on all 111 programs, `bench/results/results.csv`, docs by `bench/report.py`), geometric means against SDCC 4.6.0, next to `main` cf0c5e4: size 0.9151 (0.9162), image 0.8826 (0.8837), T-state speed 0.5456 (0.5456), without `sort-*` 0.6611 (0.6611), faster than SDCC on 63 of 78 programs (63 of 78). 50 uc80 rows differ from `main`: `image` of 45 rows (the printf width and precision code, 8 to 32 bytes smaller in most, and +121 to +130 for the four programs that use `*`), `cycles` of 21 rows (the longer padding and digit code of printf, as in stage C), and the status of three size-only rows: `battlestar-size` and `rogue36-size` (BUILD-TIMEOUT(link) to TOO-BIG), `empire-trefonas` (WRONG to TOO-BIG). Every other row has the same status and `check` result.

### Repair of the `integrate9` merge (verifier findings)

A second check of the merged tree found the problems below. Each fix has a program that runs, in `tests/test_integrate9_repairs.py` (each program with 16-bit `int` and with `--int 32`, compared with the host output). The merge itself was a text conflict in `CHANGELOG.md` and in `src/uc80/reglocals_gen.py` (`address_taken_names`: main's `taint_all`, `Member`, `Index`, `ArrowMember` and dereference logic with the `_dcfast` calls of `bugfix3`); the earlier note said `CHANGELOG.md` only. The indentation that the hand merge left in `reglocals_gen.py` is fixed.

- **A bit-field of an enumeration type is signed when an enumerator is negative** (and unsigned otherwise, as GCC and clang do). The earlier fix of this merge made every enumeration bit-field unsigned: `enum Neg { N_A = -3 }; struct { enum Neg n:4; } r; r.n = N_A;` read back 13 instead of -3 (also a `switch` on the field). `ctx.enum_negative` records the tags with a negative enumerator; the converted type keeps its tag (`enum_tag`).
- **A bit-field store lost its value under `--int 32` when the address expression called a 32-bit helper.** `a[i++].f = 1005`, `a[n/3].f = 4`, `a[n%4].f = 11`, `a[i%3].f += 1` and `sel(k+1)->y += 7` stored nothing or a wrong value: `_gen_bitfield_write_32` kept the value in `__tmp32`, and `__smod32`, `__add32` and the other helpers of the address expression use `__tmp32` too. The value now waits on the stack until the address is known. This was in `main` cf0c5e4 and in `bugfix3`.
- **A struct initializer with bit-fields in a 4-byte unit overwrote the whole unit** (`--int 32`, or `unsigned long` fields): `struct B b = { 31, 3 };` with two fields in one unit gave 3 and 0, because the second field stored a full word. Each field now does a read-modify-write of its own bits.
- **A local struct of 1 or 2 bytes initialized from an element, a member or a pointer** (`struct N b = n[1];`, `= n[k]`, `= p[0]`; also a 2-byte struct of bit-fields) stored the address of the source instead of its bytes. These forms go through the byte copy now.
- **A typedef declared in a block lost the initializer of its variables** after an earlier function (`typedef struct {int v;} W; W w = {4};` printed garbage for `w.v`; `_Generic(w.v, ...)` also gave the wrong branch). `_type_scope_restore` replaced the dictionaries of the type scope, and `_ACTIVE_TYPEDEFS` kept pointing at the old typedef dictionary, so the next function resolved nothing. The scope is restored in place now, and the frame-sizing pre-pass (`_register_body_types`) registers the typedefs of the body, as it did for the enumerators, so that frame sizing and the body agree on the size of `W q;` (a stale dictionary had hidden that before).
- **`array + n` in a static initializer was not scaled by the element size**: `static int nv[] = {1,2,3}; int *p = nv + 1;` gave `dw _nv+1` (a byte offset); it is `_nv+2` now, for every element type (int, long, struct, array of arrays), also in the form `n + array` and `array + n - m`.
- **`constexpr` objects and `offsetof` are integer constants.** `constexpr int K = 5; int a[K];` gave `ds 0` (the array overlapped the next objects and the program wrote into them), `enum { Z = e + 1 }` with a `constexpr int e` gave 0, `int arr[e] = {1,2,3}` with `--int 32` hung, and `int arr[offsetof(struct P, c) + 1]` gave size 0. The value of each `constexpr` integer object is recorded (`ctx.constexpr_values`, in the frame-sizing pre-pass for locals, restored after the function) and used by the constant evaluators; `&((T *)0)->m` is a constant. The object is still emitted.
- **Designator indexes that are constant expressions, not literals, were ignored.** `int a[] = { [N] = 9, [N - 2] = 4 };` with an enumerator `N` gave size 2 (and `a[3]` was outside the array); the same for `['a' - 'a' + 2]`, `[2 + N]`, and for the enumerators of a block-scope enumeration (the renamed `K__c...` names). The designators are folded once the names are known, at file scope and after the pre-pass of each function body.
- **An initializer of a static object that reads a variable is an error.** `const int e = 3; static int q = e;` and `int q = e;` gave 0 or garbage without a message. `semcheck.py` reports `file:line: initializer element is not constant: 'e' is a variable`. The walk looks only through operators, casts and braces, so addresses, `sizeof`, calls and members never trigger it (C23 6.7.10). GCC accepts a `const` object here as an extension; uc80 does not. A program that did this was wrong before.
- **`_Generic(3, int: ...)` with `--int 32` chose the default association.** The type of an integer literal that fits `int` was `long` when `int` has 4 bytes. It is `int` (`unsigned int` for a `u` suffix or a hex literal that needs it). With this, c-testsuite 00219 passes with `--int 32`.
- **`x % 1` has the type of x, not `int`.** `u /= ((1 ? bf : (l % 1)) | 1)` with a `long l` divided by an unsigned int: uc_core folded `l % 1` to an `int` 0, the `?:` lost its `long` type, and the division was unsigned (0 instead of 65535). The identity is left to the code generator (`Uc80ASTOptimizer._simplify_identity`), as `x * 0` and `x & 0` already are. This was the difftest seed 93370 (focus bitfields), which is PASS now; the earlier guess that the model was wrong was itself wrong: the model and SDCC were right.
- **`sizeof(__func__)`** was 2 (the size of a pointer); it is the length of the function name plus 1.
- Not changed, with the reasons. (1) A block-scope typedef that redeclares a file-scope typedef name (`typedef int T; ... { typedef long T; T v; }`) gives `unexpected token IDENT v`. The cause is the typedef filter of `uc_core/frontend.py`, which treats the redeclared name as a hiding variable. The task says to change `uc_core` only when the merge needs it; the fix is a one-flag change there (do not shadow a name that a `typedef` declaration declares), and the parser error also needs `srcmap`. (2) `semcheck` diagnostics ignore `#line` and are one line early after a backslash-newline inside a string literal. The map comes from the preprocessor of `uc_core` (`Preprocessor.line_map`). (3) `printf("%ld")` with `--long 64` reads 4 bytes (documented in `docs/configurable_int_sizes.md`). (4) c-testsuite 00129 (a typedef name used as a member name) is a known failure. (5) `offsetof(struct { int x; int y; }, y)` with an anonymous struct type in the argument is an internal error (same in `main`). (6) The verifier saw `ul80` print `File not found: .../libc.lib` twice while many builds ran at the same time; it was not reproduced (a likely cause is a `--build-libs` run in the same tree replacing the libraries while another build reads them).
- **Test gap closed:** `tests/test_integrate9_repairs.py` has casts of 32-bit `int` values to `long long` (the program `casts32`: an enumeration object and constant, an `int` member, bit-fields of `int` and enumeration type, a call, `++`, `+=`, comma, `?:`, `!`, `&&`, unary minus and `~`, a dereference, an array element, shifts, `%`, `/` and a comparison), run with 16-bit `int` and with `--int 32` and compared with the host. The verifier had checked these by hand.
- **Bench after the repair:** `bench/run.py -c uc80` on all 111 programs (112 rows) gives the same result as the rows of the merge in every column except `version` and `command` (0 rows differ: no program changed its `image`, `bss`, `cycles` or `status`), so `bench/results/results.csv` and `docs/benchmarks_speed.md` are not regenerated; the `version` column of the uc80 rows keeps the merge commit 1c0bd27 (a run on the repaired tree writes the commit of the tree it ran on, and a scratch-path `command`). The summary table in `docs/benchmarks.md` was stale (0.94, 0.56, 0.68 for SDCC 4.6.0) and now shows the numbers of `docs/benchmarks_speed.md`; the c-testsuite counts in `README.md`, `docs/benchmarks.md` and `docs/ANSI_C_COMPLIANCE.md` are the measured 219 of 220 (218 of 220 with `--int 32`); `docs/codegen_gap.md` has a status entry for `integrate9`.
- **Gates after the repair:** `pytest -n 6` 2904 passed (27 new); `run_ctests.py` 219 of 220 (00129), `--int 32` 218 of 220 (00129 and 00200; 00219 passes now); `uc_core` 384 passed, 1 skipped; `uc386` 515 passed, 7 skipped; `isrproof.py` nmi, seq, bank, iy and memisr OK with `ul80` and `--sdas`; gen3 family 75 programs, only `hostdiff(info)` lines; `difftest.py --count 800 -j 8` seeds 1 and 7001 no UC80-WRONG and no MODEL-DIFF, and seed 93370 (focus bitfields) PASS; `ccfuzz.py` `--count 2000` PASS 2000, `--ll --count 1500` PASS 1500, 400 programs each of default and `--ll` with `-O0`, `--no-register-locals`, `--no-addr-cse`, `--no-dead-store` and `--no-blockops` all PASS, and `--int=32` 800 programs and `--ll --int=32` 400 programs all PASS.

### Family I step 2 repair

- The range of a loop counter no longer applies to the init clause of the `for` statement (`for (f = (i < 5), i = 0; ...)` compared with the new range). The address-taken and volatile sets are now computed before the function-wide analysis, so a function with a label, inline assembly or a `case` label in a loop no longer gives a loop counter a range when a pointer changes the counter.  `bench/ccfuzz.py` runs `--no-licm --no-range-cmp --no-narrow-index` as a variant. Tests: `tests/test_range_cmp.py` (four new programs). Note in docs/narrow_index.md on the order of unsequenced calls in an index.

### Family I step 2 (branch `fami`): loop optimisation, gates and numbers

- Gates of the final tree: `pytest -n 6` 2891 passed; `run_ctests.py` 219 of 220 (only 00129); `bench/difftest.py --count 800` with seeds 1 and 2, no UC80-WRONG and no MODEL-DIFF (547 and 529 PASS, the other results are SDCC failures); `bench/ccfuzz.py` 2000 PASS, `--ll` 1000 PASS, and 400 PASS each (plain and `--ll`) with `-O0`, `--no-register-locals` and `--no-licm --no-range-cmp --no-narrow-index`; the gen3 family (75 programs, 8 option sets) no RLDIFF, and the same 34 informational host differences as the commit before; `bench/rom/isrproof.py` nmi, seq, bank, iy and memisr OK with `ul80` and with `--sdas`.
- `bench/run.py -c uc80` on the programs of `--list` (without `battlestar-size`, `rogue36-size` and `empire-trefonas`), 112 uc80 rows: no status change, no program slower. Image: `micro` and its 9 groups have 7 bytes more (one new local in `copy_init`), the sum of all images falls by 1,584 bytes. BSS: `coremark-unofficial` +12 bytes, `embench-edn`, `mojozork-size` and `trek-size` +2 bytes (the new locals). Against SDCC 4.6.0 (speed mode, `bench/report.py` method): size 0.9157 to 0.9128, whole image 0.8831 to 0.8805, T-states 0.5304 to 0.5274, without `sort-*` 0.6406 to 0.6366, faster than SDCC on 66 of 78 programs (65 before). The ratios of the 13 programs that were slower than SDCC (total cycles, uc80 over SDCC), step 1 to step 2: worm 1.255 to 0.907, mille 1.210 to 0.887, canfield 1.206 to 0.883, coremark-unofficial 1.182 to 0.892, davidly-sieve 1.259 to 1.195, davidly-ttt 1.575 to 1.523, and no change for bsd-ppt (1.597), bsd-bcd (1.390 from 1.396), sprintf, davidly-tm, snd-ymreg, snd-cmdq and queens. The geometric mean of the 13 falls from 1.098 to 0.992.

### Family I step 2 (branch `fami`): loop-invariant code motion, value ranges, index narrowing

- **Loop-invariant code motion** ([docs/licm.md](docs/licm.md)). A pure integer expression that a loop does not change (`y * 80` in `for (x...) i = y * 80 + x;`) is computed once before the loop into a new local. The expression has integer constants, locals that the loop does not assign (no `&`, not volatile), casts, unary `- ~ +` and `+ - * << >> & | ^`. It has no load, call or division. It costs at least 40 T-states, and the loop evaluates it in each pass (not in an `if` branch, the right side of `&&` or `||`, a `?:` branch, a `case` or after a jump). Loops with a label, `do { } while (0)`, interrupt functions, naked functions and functions with assembly are left alone. `--no-licm` switches the pass off. worm, mille and canfield fall by 22% to 23% with this pass alone. Tests: `tests/test_licm.py`.
- **Value ranges and the unsigned compare** ([docs/range_compare.md](docs/range_compare.md)). A 16-bit signed compare of two values in 0 to 32767 uses the 22 T-state unsigned form (44 before). The ranges come from the stores to each local (`k = i + prime`, a counted `for` update guarded by its condition) and, inside a counted `for` statement, from the init clause of the loop. `--no-range-cmp` switches the pass off. davidly-sieve -5.1%, davidly-ttt -3.3%, worm, mille and canfield -4% to -5% more. Tests: `tests/test_range_cmp.py`.
- **Index narrowing** ([docs/narrow_index.md](docs/narrow_index.md)). `a[i * n + j]` with 32-bit `i`, `n` and `j` computes the index in 16 bits (the low 16 bits are the only bits that reach a 16-bit address). The 32-bit multiply is gone. The pass is the module `narrow.py`, not a rule of `ast_opt.py` (the AST optimizer knows no types). `--no-narrow-index` switches the pass off. coremark-unofficial falls from 35.6M to 26.9M T-states with the three passes. Tests: `tests/test_narrow_index.py`.
- The first version of the loop pass moved every invariant expression. It made `umchess` 11% slower (an expression in a rare branch) and `embench-nettle-sha256` 503 bytes bigger and 1.4% slower (the `ROUND` macro is a `do { } while (0)` loop that runs once). The pass now moves only an expression that the loop evaluates in each pass, and skips a loop that runs once.

### Family I step 1 (branch `fami`): gates and numbers

- Gates of the final tree: `pytest -n 4` 2831 passed; `run_ctests.py` 219 of 220 (only 00129); `bench/difftest.py --count 800` with seeds 1 and 5000, no UC80-WRONG and no MODEL-DIFF; `bench/ccfuzz.py` 2000 PASS, `--ll` 1000 PASS, and 400 PASS each (plain and `--ll`) with `--no-peep-pairs`, `--no-slot-fold` and `--no-discard-comma`, and 400 PASS each with `-O0` and `--no-register-locals`; the gen3 family (75 programs, 8 option sets) no difference; `bench/rom/isrproof.py` nmi, seq, bank, iy and memisr OK with `ul80` and with `--sdas`. The tree after part 1.1 passed the same gates (pytest 2810) before parts 1.2 and 1.3 were added.
- `bench/run.py -c uc80` on 109 programs (without `battlestar-size`, `rogue36-size` and `empire-trefonas`): no status change. Image: 40 programs bigger by 1 to 19 bytes (13 bytes where the program links `_prt_div16`, 5 where it links `putchar`), the total image falls by 872 bytes. T-states: no program is slower except `embench-huffbench` (+0.011%, a data-address effect of the 5 bytes in the library: the same figure appears with all three switches off) and 6 programs with +17 T-states in the print after the timer. Against SDCC 4.6.0 (speed mode, `bench/report.py` method): size 0.9162 to 0.9157, whole image 0.8837 to 0.8831, T-states 0.5456 to 0.5304, without `sort-*` 0.6611 to 0.6406, faster than SDCC on 65 of 78 programs (63 before). The 13 programs that were slower than SDCC (total cycles, uc80 over SDCC): bsd-ppt 1.743 to 1.597, bsd-bcd 1.581 to 1.396, davidly-ttt 1.578 to 1.575, sprintf 1.371 to 0.311, davidly-sieve 1.260 to 1.259, worm 1.257 to 1.255, davidly-tm 1.234 to 0.957, mille 1.212 to 1.210, canfield 1.207 to 1.206, coremark-unofficial 1.183 to 1.182, snd-ymreg 1.142 to 1.012, queens 1.134 to 1.134, snd-cmdq 1.129 to 1.033. The geometric mean of the 13 falls from 1.297 to 1.098.
- Not in step 1: the BSS clear in `crt0.mac` keeps `LDIR` (see [docs/codegen_gap.md](docs/codegen_gap.md)).

### Family I step 1 (branch `fami`): slot folding and calls that do not return

- **Slot folding in the register-locals pass** ([docs/register_locals.md](docs/register_locals.md)). `ld HL,??AUTO+k` followed by `inc (HL)`, `dec (HL)`, an ALU operation, `bit`, `set`, `res`, a shift or a byte load or store through `(HL)` is one access of the slot when HL is dead afterwards. A byte local that the code reaches in this way can now live in a register (the log no longer says "address of a slot"). The push of a char argument carries the comment `; HJUNK`: the callee never reads the high byte, so H is not live. T-states of the timed region: snd-cmdq 9,710,812 to 9,352,016, snd-ymreg 16,340,991 to 15,507,547.
- **Calls that do not return end the flow graph of the pass.** `exit`, `abort`, `_Exit`, `quick_exit`, `_assert_fail`, and a function with `_Noreturn`, `[[noreturn]]` or `__attribute__((noreturn))` (found by a text scan, `sdcc_compat._noreturn_pass`). stdlib.h and assert.h mark `exit`, `abort` and `_assert_fail` `_Noreturn`. No local is live after the call, the error path `printf(...); exit(1);` needs no wrap, and the cleanup after the call is deleted. davidly-tm 137,785,507 to 106,905,507 T-states.
- Repair: the noreturn names are mapped through the rename table of their file (a static noreturn `die` no longer marks an extern `die` of another file), and a declaration with several declarators records no name. Tests run two-file programs.
- `--no-slot-fold` switches both changes off. Tests: `tests/test_regl_slotops.py`, `tests/test_regl_noreturn.py`.

### Family I step 1 (branch `fami`): comma and pair peepholes

- **A discarded comma computes no value for the right operand** (`for (...; i++, t++)`). The right operand `t++` of a comma that nothing uses had a copy of the old value (push HL, `inc HL`, pop HL). The copy is gone, and a pointer that had the copy can now stay in IY or BC. `--no-discard-comma` switches the change off.
- **Pair peephole rules in the register-locals pass** ([docs/peephole_pairs.md](docs/peephole_pairs.md)): `ld H,B / ld L,C / ld A,(HL)` becomes `ld A,(BC)` (and the DE and store forms), a copy of BC to HL that HL already holds is deleted, `ld A,r / ... / ld (HL),A` becomes `ld (HL),r`, and `ld H,D / ld L,E / add HL,HL / ld D,H / ld E,L` becomes `sla E / rl D` when HL and the flags are dead. The rule for `inc BC` through HL takes up to four steps. `--no-peep-pairs` switches the rules off. T-states of the timed region: snd-ymreg 16,949,631 to 16,340,991 and snd-cmdq 9,904,552 to 9,710,812. Tests: `tests/test_peep_pairs.py`.

### Family I step 1 (branch `fami`): library speed

- **Decimal conversion is no longer quadratic.** `_prt_div16` (the 16-bit divide by 10 of `printf`, `%d`, `%u`) divided by repeated subtraction (up to 6553 steps for a digit of 65535). It now returns at once for a value below 10, uses 8 shift and trial-subtract steps for a value below 256 (the byte moves to H, and the 16-step loop runs 8 times), and 16 steps for the rest (a first version with 16 steps for every value made the programs that print single digits slower: `game-guess` +3.9%, `v7-arithmetic` +3.8%). `_div32_10` (`%ld`, `%lu`) kept the 32-bit value in memory; it now keeps the value in `DE:HL` for 32 steps. The register contract of both routines is the same. The `sprintf` benchmark falls from 33.3M to 7.4M T-states in the timed region.
- **`putchar` and `__conout` take a short path for a character that is not LF.** `putchar` reads the argument without popping the return address. `__conout` tests for LF first and only the LF path reads and writes the state byte for the CR rule. All console output still goes through `__conout` (the test of the choke point is unchanged). Tests: `tests/test_lib_decimal.py` (every 16-bit value with `%u` and `%d`, 32-bit and 64-bit edge values, the CR LF rules with and without `--no-crlf`).

### Investigation: wrong ISR total with `--no-register-locals` (no compiler change)

- A ROM program whose NMI handler sums a buffer that `main` fills gave isr_total 34336 at every NMI period with `--no-register-locals`. The cause is the test program, not the compiler or the library: the first NMI came at T-state 20000, while `main` was still in its fill loop (slower with every local in memory), so the handler summed a half-written buffer. With the first NMI later, or with a `ready` flag, every option set gives the host value. The program also uses `%` in `main`; the handler does not, so the work-area warning does not apply. New proof `isrproof.py memisr` (NMI function with `memset`, `memcpy`, struct copies and a helper call, against a Python model, a `ready` flag, and a count of nested entries) with tests for `ul80` (seven option sets: default, `--no-register-locals`, `--isr-safe`, `-O0`, `--no-inlining`, `--no-shared-storage`, `--no-register-cc`) and `--sdas`. Documented in [docs/rom.md](docs/rom.md): an NMI cannot be masked, so a short period nests the handler.

### Stage C of the bug-fix round 3 (branch `bugfix3`): wrong values

Details of the type rules are in [docs/types_and_conversions.md](docs/types_and_conversions.md).

- **The value of an assignment to a bit-field is the stored value** (`src/uc80/codegen.py`). `sh = bf.x = 5` for an unsigned 2-bit field gave 5 with `--int 32`; it is 1. The field lived in a 4-byte unit, and `_gen_bitfield_write_32` gave back the right operand unmasked (the 1-byte and 2-byte units were right). The prefix `++` and `--` and every compound assignment use that path, so they were wrong too, and a postfix `++` on such a field lost the high word of the old value (`-65533` for 3). `_emit_truncate_dehl` cuts the result, and the postfix form keeps DE. A bit-field of a 64-bit type that is narrower than 64 bits (`unsigned long long i : 40`) had no code at all (a read gave garbage); it is now an error with the line of the width. The address of a bit-field was an internal error (`no attribute '_error'`); it is an error with a line.
- **A tag declared in a block belongs to the block** (`uc_core/frontend.py`, `_rename_block_scope_tags`). `struct S` in a function with another layout than a file-scope `struct S` got the file-scope layout (sizes and member offsets), also for a union, an enum, a forward declaration `struct U;` and a tag inside a tag body. The front end renames each tag that a block declares, and the uses that it governs, before the parse. The renaming is also in `uc386`.
- **A subnormal floating constant keeps its bits.** `1e-40f` was folded to 0 on its way to the object file (the runtime flushes subnormal results, and the emitter did the same for every value). A constant of the source (and its negation) keeps its bits; a value that arithmetic made still flushes, as the runtime does (`1e-30f * 1e-10f` is 0).
- **An enumeration has an integer type that holds its values** (C23 6.7.3.3). `enum { E = 50000 }` was an `int` of 2 bytes (`long l = E` gave -15536), and `enum { F = 70000 }` lost its high bits. The type is the first of `int`, `unsigned int`, `long`, `unsigned long`, `long long`, `unsigned long long` that holds all values; the constants have that type when `int` does not hold them. An anonymous enumeration with explicit values gets a hidden tag (front end) so that its variables are as big as the values need. With `--int 32` an enumeration constant above 65535 was truncated to 16 bits, and an object of enumeration type was stored as 16 bits; both are 32 bits now.
- **printf takes a width or a precision above 255** (`lc_printf_core`, `lc_printf_s`, `lc_printf_c`, `lc_printf_a`, `lc_prt_float`, `lc_printf_o`, `lc_printf_lo`, `lc_printf_llo`). `%300d` printed 255 characters. The width and the precision are 16-bit numbers (at most 32767; the digit parser saturates, and `*` clamps), and `_printf_padw` prints a 16-bit count of padding. A precision of a float conversion above 255 is still 255 (the digit count is one byte). The code grows by the 16-bit arithmetic: `lc_printf_core` +44 bytes (842 to 886), `%s` +15, `%a` +4, `%o`, `%lo`, `%llo` and the float printer +3 each, `%c` +0.
- **Conversions between `long long` and float** (`rt_lltof`, `rt_ftoll`, `codegen.py`). `(long long)1e18f` gave 0x7fffffff and `(float)ll` used the low 32 bits. New runtime routines `__lltof`, `__ulltof` (round to nearest, a tie to even) and `__ftoll`, `__ftoull` (truncate; the nearest limit for a value that does not fit). `__ftoull` also converts a float to a 32-bit `unsigned long` (3e9f was 2147483647). All sites of the code generator use `_emit_int_to_float` and `_emit_float_to_int`: casts, assignments, compound assignments, arguments, returns, `?:`, initializers. A float constant in a static integer object (`static long long x = 1e18f`) was stored as float bits; it is converted. An integer in a static float object is rounded once (`float(n)` rounded twice). A local array initializer with a value of another type (`float a[2] = { i, n }`, `int b[2] = { f, 1 }`) stored the bits; it converts.
- Not reproduced: the ccfuzz seeds 1138 (`--isr-safe`), 21810, 22164 and 22287 that an earlier note lists as WRONG all give PASS in this tree and in `~/src/uc80` (22164 and 22287 are the uc_core CSE-key fix of the wide-integer repair above). A sweep of the seeds 21800 to 22319 (520 programs) and of 1100 to 1199 with `--isr-safe` (100 programs, with the variant of the seed and with `--exact`) has no WRONG. `tests/test_ccfuzz_seeds.py` pins the four seeds with the variant that the seed picks for itself.
- Not fixed: a bit-field of a 64-bit type narrower than 64 bits (now an error); an enumeration with a fixed underlying type (`enum e : T`, the grammar of `uc_core` does not parse it); a float precision above 255 in `printf` (still 255); a subnormal operand or result of runtime arithmetic (the runtime flushes it).
- Tests: `tests/test_bitfield_assign_value.py`, `tests/test_block_scope_tags.py` (and `uc_core` `tests/test_block_scope_tags.py`), `tests/test_subnormal_literals.py`, `tests/test_enum_wide.py`, `tests/test_printf_wide_fields.py` (the printf tests that expected 255 were changed to the new lengths), `tests/test_ll_float_conversion.py`. Each program is run on the emulator and compared with the host output.
- Gates of stage C: `pytest -n 6` 2707 passed; `run_ctests.py` 219 of 220 (only 00129, the typedef-name used as a member name, not one of these bugs); `difftest.py --count 600 -j 8` no UC80-WRONG, no MODEL-DIFF (411 PASS; the 189 SDCC-* rows are the other compiler: build failures, hangs and wrong output of SDCC); `ccfuzz.py --count 1500` and `--ll --count 1000` all PASS; `isrproof.py` nmi, seq, bank, iy OK; uc_core 381 passed, 1 skipped; uc386 515 passed, 7 skipped. Bench size on the 112 programs of `bench/run.py --list` against `bench/results/results.csv`: 64 rows differ. (1) Every program that prints with `printf` has an `image` 48 to 75 bytes bigger (most +66, +69 or +72) and a `bss` 1 or 2 bytes bigger: the printf width and precision are 16-bit now (`lc_printf_core` +44 bytes, `%s` +15, `%a` +4, `%o`, `%lo`, `%llo` and the float printer +3 each; the state block has 2 more bytes), and `check_com` and `com` rise by one 128-byte sector where the image crosses a boundary (all `check_com` +128 rows). `empty` and the other programs that do not call printf are equal. (2) `cycles` of printf-heavy programs rise by the longer padding and digit code: `bsd-arithmetic` +17,369 (+0.93%), `v7-fish` +30,232 (+0.82%), `bsd-fish` +24,815 (+0.71%), `fuzix-startrek` +141,284 (+0.35%), `sprintf` +172,276 (+0.5%), `game-guess` +5,669, `bsd-wump` +16,168, `umchess` +68,478; programs with little output differ by 21 to 414 cycles (`mille`, `robots`, `snake`, `worm`, `bsd-caesar`, `bsd-ppt`, `coremark-unofficial` +204 as in stage B). No row changed its `status` or `check` result because of stage C. (3) The rows of stage B are the same as in the stage B repair: `battlestar-size` and `rogue36-size` (BUILD-TIMEOUT(link) to TOO-BIG), `empire-trefonas` (WRONG to TOO-BIG, .com 69,504), and the `data` and `objects` fields of the size-only rows (`adventure-size`, `curses-demo-size`, `mojozork-size`, `monop-size`, `trek-size`: the BSS block of each module). No program uses `long long` with a float, an enumeration above `int`, a block-scope tag that hides another or a narrow 64-bit bit-field, so stage C changes nothing else.

#### Repair pass of stage C

- **A bit-field update evaluates its object once** (`codegen.py`, `_gen_bitfield_rmw_once`). `a[i++].y++`, `++a[i++].y`, `a[i++].y += 2` and `pf()->x += 9` took the address of the object for the read, for the operation and for the write, so `i` rose by 3 and `pf` ran 3 times (`r = a[i++].y++` gave 0 where C gives 3). This was in `main` too. When the object expression of a bit-field has a side effect, the compiler now evaluates the address once into a static pointer word (2 bytes of BSS, the old value is kept on the stack so a call in the right operand or an interrupt can reach the same code) and runs the operation on that pointer. Test: `tests/test_bitfield_side_effects.py` (int 16, int 32, `-O0`, `--no-shared-storage --int 32`, `--isr-safe`; also a call in the right operand that updates a bit-field of another element).
- **Diagnostics for a bit-field name the file and the line of the real source.** `bit-field 'i' of a 64-bit integer type narrower than 64 bits` and `cannot take address of bit-field 'a'` gave `line 133:` (the line of the preprocessed text, no file) after an `#include`. `CodegenError` now finds the first token of the node and maps it through `srcmap`, so every diagnostic that has a node shows `file:line`. The address message names the field. Tests in `tests/test_bitfield_assign_value.py`, with an `#include` before the error line.
- **Enumeration constants are block scoped** (`uc_core/frontend.py`, `_rename_block_scope_tags`). `enum E { X1 = 5 }; ... { enum F { X1 = 77 }; } printf(X1)` printed 77 before, inside and after the block (5 77 5 in C). The constants of a block-scope enumeration get hidden names for the rest of the block, and a variable that is declared with a basic type keyword and hides a constant (`int X1 = 3;`, which gave the constant) gets its own name. Limits are in `docs/types_and_conversions.md` (a hiding variable of a typedef or pointer type, and a parameter, are not renamed). Tests: `tests/test_block_scope_enum_constants.py` (runs, int 16 and int 32) and uc_core `tests/test_block_scope_tags.py`.
- **printf `*` width and precision with `--int 32`** (`lc_printf_star`, `printf_hook.py`). The runtime read 2 bytes for the `*` argument, so `printf("[%*d]", 5, 7)` printed `[458752]` and `%.*s` printed garbage. The `*` code is now a module of its own, `lc_printf_star` (`__printf_handle_star` for a 2-byte int, `__printf_handle_star32` for `--int 32`, which limits a value outside -32768 to 32767 to the largest size of its sign). The printf hook adds the `*` entry to the dispatch table when a literal format has a `*` or the format is not a literal (the default table `lc_printf_all` has it for 16-bit `int`). A program without `*` does not link the code, so the printf core is smaller than in the first pass of stage C. The compile-time warning `takes its width or precision from an argument, which this libc does not implement` was stale (the runtime has implemented `*` since `ee74ed3`, and `main.py` removed the warning) and is gone from `codegen.py`. Tests: `tests/test_printf_wide_fields.py` (int 16 and int 32 against the host, a format that is not a literal, and the link test for the module).
- Seeds of item (7): seed 22287 does fail when the uc_core fix `e95e0d4` (CSE key of a cast names the whole target type) is removed from the stage C tree (WRONG) and passes with it, so that seed is that fix. Seeds 22164, 21810 and 1138 (`--isr-safe`) pass with and without `e95e0d4` and in every tree that was available, so no commit can be named for them; they stay pinned in `tests/test_ccfuzz_seeds.py`.
- The uc386 gate is 515 passed, 7 skipped (run in `~/src/uc386` with `PYTHONPATH` on the uc_core worktree, with and without `-n 6`; the 7 skips are the doom, awk and micropython binaries that are not built). A verifier saw 498 passed, 1 skipped in another environment; the figure in this file is the one of this run.
- Not changed: a variable of a typedef or pointer type that hides an enumeration constant, and a parameter with the name of a constant (see the limits above); a `*` value above 32767 in printf (limited).
- Gates of the repair: `pytest -n 6` 2719 passed; `run_ctests.py` 219 of 220 (only 00129); `difftest.py --count 600 -j 8` no UC80-WRONG, no MODEL-DIFF (the other rows are SDCC-*); `ccfuzz.py --count 1500` and `--ll --count 1000` all PASS; `isrproof.py` nmi, seq, bank, iy OK; uc_core 384 passed, 1 skipped; uc386 515 passed, 7 skipped. Bench size on the 112 programs of `bench/run.py --list` against `bench/results/results.csv` (48 rows differ; this supersedes the printf sizes of the first pass above): (1) The `*` code left `lc_printf_core` for `lc_printf_star`, so a program that prints without `*` has an `image` 8 to 32 bytes SMALLER than in `results.csv` (most -14; the 16-bit width and precision of stage C cost less than the old inline `*` code) and a `bss` 1 or 2 bytes bigger; `com` and `check_com` move by one 128-byte sector where the image crosses a boundary (up or down). (2) Four programs use `*` in a format and link the new module: `bsd-pig` +121, `fuzix-startrek` +130, `v7-fish` +130, `v7-wump` +130 bytes. (3) `cycles` of printf-heavy programs rise by the longer padding and digit code, as in the first pass (`bsd-arithmetic` +13,734, `bsd-fish` +17,133, `bsd-pig` +89,588, `fuzix-startrek` +113,178, `sprintf` +148,884, `umchess` +40,326, `v7-backgammon` +452,823, `v7-fish` +20,802; programs with little output differ by 21 to 345; `coremark-unofficial` +204 as in stage B). (4) The stage B rows are as before: `battlestar-size` and `rogue36-size` (BUILD-TIMEOUT(link) to TOO-BIG), `empire-trefonas` (WRONG to TOO-BIG, .com 69,504), and the `code`, `data` and `objects` fields of the size-only rows. No row changed its `check` result.

### Stage B of the bug-fix round 3 (branch `bugfix3`): compile-time blow-ups

- **A long sum compiles in near-linear time** ([docs/compile_time.md](docs/compile_time.md), `src/uc80/codegen.py`). A sum such as `a[i]+a[i+1]+...` of 20, 40 and 80 terms took 0.8, 5 and 62 s (the first report: 2, 16 and 224 s). The code generator asked for the type of each operand again at every level of the tree (`_get_expr_type`, `_arith_operand_type`, `_is_float_expr`, `_is_pointer_arith`, `_is_long_long_expr` and `_is_long_expr` call each other and walk both operands). The six methods now keep one result for each node during the outermost call (`_scoped_memo`). The memo first lived for one outermost query, so 20, 40, 80, 160 and 320 terms took 0.4, 0.6, 1.1, 2.9 and 15 s (quadratic with a small factor). The repair keeps the memo for the whole `gen_expr` call of an expression (cleared at a declaration or a statement inside a statement expression) and caches `FloatInfo.maybe_float` of `uc_core` for one statement. Now 80, 160, 320 and 640 terms of `(a[i+k]+b[k]*2)` take 0.4, 0.7, 1.6 and 4.4 s. The .mac text is the same with and without both memos (`tests/test_compile_time.py`). The generated code is the same: the .mac text of the 225 single-file programs of the c-testsuite is byte-identical before and after.
- **The assembly dead-data pass is linear** (bug C8, `src/uc80/asm_dce.py`). `_find_referenced_data` matched one regular expression for each data label against each line of each live function, and again for each line of each live data object until nothing changed. `battlestar-size` did not finish in 1800 s (BUILD-TIMEOUT(link): the time was in `uc80`, not in `um80` or `ul80`) and `rogue36-size` the same. The pass now splits each line into names once and looks each name up in the table of data labels (one scan for each data object, with a work list). The set of kept labels is the same as before for every label (the old pattern and the new split use the same rule for `?` and `@` prefixes). `battlestar-size` now takes 37 s in `uc80`, 7 s in `um80` and 1 s in `ul80`; `rogue36-size` 82 s, 7 s and 1 s.
- **Cached `is_dataclass` and `fields`** (`uc_core/_dcfast.py`). The AST walks of the optimizer, the call graph, the inliner and the register-local passes called `dataclasses.is_dataclass` and `dataclasses.fields` some 20 million times for Rogue. The answer depends on the class only, so it is now kept for each class. Whole-program compile time of `battlestar-size` goes from 53 s to 43 s on a quiet machine.
- **Bench: a program that cannot load is TOO-BIG, not HANG or FITS** (`bench/run.py`, [docs/bench_harness.md](docs/bench_harness.md)). `empire-trefonas` links to a 69,376-byte .com; `z88dk-ticks` stops with "Incorrect length" and no output, and the row said HANG. It is TOO-BIG now (the image is more than the 64 KB of a CP/M machine). `rogue36-size` links to a 82,048-byte .com, but `ul80` wraps `__BSS_START` to 16 bits and only warns, so the row said FITS with image 38,328; the harness now takes the larger of image + BSS and the .com size.
- Gates of stage B (repair): `pytest -n 6` 2681 passed; the .mac text of the 220 c-testsuite programs and 97 bench programs is byte-identical between the tree before the repair and after; `bench/run.py -c uc80 -m size` on the three big programs gives TOO-BIG rows with image, bss and com (empire-trefonas 69,359 / 1,245 / 69,376; rogue36-size 73,585 / 8,463 / 82,048; battlestar-size 141,588 / 5,868 / 147,456); uc_core 376 passed, 1 skipped; uc386 515 passed, 7 skipped. Gates of the first pass of stage B: `run_ctests.py` 219 of 220 (only 00129, the typedef-name used as a member name, not one of these bugs); `difftest.py --count 600 -j 8` no UC80-WRONG, no MODEL-DIFF; `ccfuzz.py` 1500 and `--ll` 1000 all PASS; `isrproof.py` nmi, seq, bank, iy OK; uc_core 376 passed, 1 skipped; uc386 515 passed, 7 skipped. The .mac text is byte-identical with and without the change for the 225 c-testsuite files (219 compile, 6 give the same error) and for the 109 benchmark programs that the old code can compile (`battlestar-size` and `rogue36-size` are not in the list: the old code needs more than 30 minutes). Bench size on the 112 programs against `bench/results/results.csv`: `status`, `image`, `bss`, `com`, `cycles` and `check_com` are equal except `battlestar-size` (BUILD-TIMEOUT(link) to TOO-BIG, image 81,885, .com 147,456), `rogue36-size` (BUILD-TIMEOUT(link) to TOO-BIG, .com 82,048) and `empire-trefonas` (WRONG to TOO-BIG, .com 69,376: the older row came from a build of 63,360 bytes that gave WRONG output; the build before it, da4d172, was 69,888 bytes. The growth of the .mac between `aa6cac7` and `510f883` is in the float run-time routines: `__fdiv_man` +111 lines, `__fdiv` +63, `__fmul_unpack`, `__fmul_man`, `__fmul` and `__fadd`, from the commits that round float add, subtract, divide and int-to-float to nearest even and handle infinity and NaN, so it is not a regression of stage B), and the known rows of earlier stages (`adventure-size` -1 byte, `mojozork-size` +1 byte, `coremark-unofficial` -14 bytes and +204 cycles, and the `code`, `data` and `objects` fields of the size-only rows: BSS block of each module).
- Bench rows of a TOO-BIG program (`bench/run.py`): the row now holds `com`, `bss` and the real `image` (the image that the .com holds, not the 16-bit wrapped `__BSS_START`), for the check build and for the time build (a build with other defines can be too large too).
- Tests: `tests/test_compile_time.py` (a 100-term sum compiles, runs and gives the right value; a 320-term sum compiles in under 30 s; the .mac text of a mixed-type program with a statement expression that shadows a global and of a 30-term sum is the same with and without the long-lived memos; the dead-data pass takes under 2 s for 800 labels and 800 lines; the label boundary rules; the harness size rules and the fields of a TOO-BIG row).
- Found, not fixed (not stage B): a local of a GNU statement expression that shadows a global (`long x = 70000L; ... x + ({ char x = 3; x; })`) is bound to the global's storage and the sum is computed in 16 bits (prints 4467 3, gcc prints 70003 70000). The compile output is the same before and after stage B.
- Not in this repository: `ul80` (um80_and_friends) does not stop for a CP/M image past 64 KB. `check_limits` in `um80/ul80.py` reports "Address space overflow" only for a ROM layout. For the CP/M layout the linker wraps the addresses with `& 0xFFFF` and prints many "a reference to X leads outside the bytes the module loaded" warnings (with the wrong advice about `--aseg`). It should give the same "Address space overflow: data and BSS end at XXXXH, past 10000H" error when `bss_end` (or origin + code + data) is above 10000H, and write no .com.

### Stage A of the bug-fix round 3 (branch `bugfix3`): diagnostics

- **An undeclared name is an error with `file:line`** ([docs/ANSI_C_COMPLIANCE.md](docs/ANSI_C_COMPLIANCE.md), `src/uc80/semcheck.py`). C23 has no implicit declarations. Before the change a name that no scope declared (a variable used outside the block that declares it, a name that the program only assigns) became an implicit external: the link failed with `Undefined symbol _D6`, or nothing failed. A call of an undeclared function is an error too; `--allow-implicit-functions` makes it a warning. No c-testsuite test and no `bench/` program needs the option. The check covers every function, also a function that nothing calls. The compiler reports all errors of a file.
- **A change of a const object is an error** (C23 6.5.16, 6.5.2.4, 6.5.3.1, 6.3.2.1). `=`, `op=`, `++` and `--` on a const variable or parameter, a `T *const` pointer (`*p--` changes `p`), a const member, a member of a const structure, an object behind a pointer to const, an element of a const array, and the assignment of a structure with a const member. A pointer to const that you move is correct.
- **File and line of the source in diagnostics.** The new `uc_core` map `Preprocessor.line_map` (uc_core `da2681d`) gives the file and line of each preprocessed line, so the new messages name the real source line, also in a header. The older messages of the code generator still say `line N` of the preprocessed text.
- **Variable length arrays are an error.** uc80 does not implement VLAs. Before the change `short v[k+2]` made no code, and its stack slot could overlap other temporaries (wrong code with no message). `__STDC_NO_VLA__` was already 1; a block-scope array with a size that is not a constant is now an error that says so. A parameter `int a[n]` is a pointer and still works. The c-testsuite test 00207 declares `char test[argc]`; `tests/c-testsuite-z80/00207.c` has `char test[4]`.
- **`--no-whole-program` and the start-up code.** A program that was linked without `crt0.rel` started in the first function of the first module and printed nothing (a hello-world worked only by luck, because `main` was first). The module that defines `main` now gives a warning that says to link `crt0.rel` first; `--no-embed-startup` silences it. `bench/compilers.py` links `crt0.rel` first when `UC80_BENCH_FLAGS` has `--no-whole-program`. A test builds a two-file `--no-whole-program` program and runs it under cpmemu. The code that the compiler writes did not change: only the warning and the harness are new, and the compiler cannot see the link line, so it warns for each module that defines `main`.
- **Array sizes in blocks.** A `constexpr` object and `offsetof` (the `&((T *)0)->m` form) are constants, and are not VLAs. A size from a plain `const int` object is a VLA in C23 and is an error ([docs/ANSI_C_COMPLIANCE.md](docs/ANSI_C_COMPLIANCE.md)).
- Gates of stage A: `pytest -n 6` 2673 passed; `run_ctests.py` 219 of 220 (only 00129, a parse error for a typedef-name used as a member name, not one of these bugs); `difftest.py --count 600 -j 8` no UC80-WRONG, no MODEL-DIFF; `ccfuzz.py` 1500 and `--ll` 1000 all PASS; `isrproof.py` nmi, seq, bank, iy OK; uc_core 375 passed, 1 skipped; uc386 515 passed, 7 skipped. Bench size on 112 rows against `bench/results/results.csv`: `status`, `image`, `bss`, `com` and `cycles` are equal except the known rows (`adventure-size` -1 byte: path text; `coremark-unofficial` -14 bytes, +204 cycles and `mojozork-size` +1 byte: the `integrate7` repair; `empire-trefonas` HANG, as before) and the `code`, `data` and `objects` fields of the size-only rows (the BSS block of each module, stage B).
- Tests: `tests/test_semcheck.py` (exact `file:line` for each error, legal programs that run), `uc_core` `tests/test_line_map.py`.
### Added

- **RST vectors and IM 2 in the ROM target** ([docs/rom_hardware.md](docs/rom_hardware.md)). `#pragma uc80 rst ADDR NAME` binds a plain function (a software call target) to RST 08h to 30h, and `rst ADDR NAME interrupt [critical]` binds an interrupt function. `#pragma uc80 im2 SLOT NAME [critical]` and `#pragma uc80 im2table ADDR` select IM 2: the start-up code sets I and `IM 2`, and writes an aligned table of 128 vectors (an unbound slot points to a default `EI`, `RETI`). The compiler stops with an error for an address out of range, a vector or slot in use twice, and a misaligned table. A program with no such pragma has the same image as before. `bench/romrun` delivers an interrupt with a data bus byte (`add_irq_at(t, vec)`, `--irq-vec`), and no longer forces bit 0 of the IM 2 byte to 0. Tests: `tests/test_rom_vectors.py`, with `ul80` and with `--asm-syntax=sdas`.
- An `im2` or `rst ... interrupt` pragma can name a handler that an assembly input file defines (declare the function in C). `bench/romrun`: a level-triggered source (`set_irq_level`, `YM2610`) can give an IM 2 data bus byte. A test checks that the IM 2 start-up code stays below the table.

### Stage B (branch `rom3b`): direct access and the BSS block

- **A pointer variable that holds a constant address is a direct access** ([docs/rom_hardware.md](docs/rom_hardware.md), `src/uc80/ptrabs.py`). `volatile uint8_t *const p = (volatile uint8_t *)0x4000; *p = x;`, a file-scope `static` pointer, a local pointer, a struct pointer (`chip->data`, `p->a.b`, `p[1].reg`, at the address plus the member offset), and a non-const pointer that the program sets only to one constant, all become `LD (nn),A`, `LD A,(nn)` and the 16-bit forms. Each volatile access stays one access with the same width and order. The pointer variable and its assignments go away when no use of the pointer is left. Measured on a memory-mapped YM2610 driver (`bench/rom/mmio.py`): 421 to 393 bytes and 88211 to 79071 T-states (-10.4%), with an equal log of the 272 writes. The snd-* kernels and the isrproof programs use port instructions and have no such pointer, so they do not change (same bytes, T-states and port logs). `--no-direct-pointers` switches the pass off. Tests: `tests/test_direct_pointers.py` (`bench/romrun` with a breakpoint on each read, with `ul80` and with sdas, and cpmemu).
- **One BSS block for each separately compiled module** ([docs/rom.md](docs/rom.md)). Today (before the change) a module compiled with `--no-whole-program` had its zero-start data in DSEG: in the CP/M `.com` file, and in a ROM copied from ROM to RAM by the start-up code. The data is now in one named COMMON block, `uc_<stem>_<hash>` (the hash of the full path keeps two files with the same name apart), that the start-up code clears. Two modules of the test: ROM image 705 to 515 bytes, bytes copied at the start 198 to 8. A CP/M program with two 600-byte arrays: the `.com` file is smaller by 1200 bytes. `--no-module-bss` selects the old placement. The interrupt check does not treat the block as a work area of the library. Tests: `tests/test_module_bss.py`.
- Repair of stage B. (1) A local that starts from a read through a constant pointer (`uint8_t s = ch->status;`, `T t[2] = {*p, p[1]}`, `for (int i = *p; ...)`) kept a reference to the removed pointer, and the link failed with an undefined symbol. The pass now replaces the read in the initialiser. (2) An access outside the address space (`p[-3]`, a 16-bit access at FFFFh) made the same dangling reference. Pass 1 and pass 2 now use one test, and the pointer stays for such an access. (3) The BSS block name hashes the path relative to the working directory, so two checkouts give the same object. (4) The docs say: a local struct is found by its tag (existing limit); a 16-bit compound write has another byte order than before; the snd-* kernels do not use the feature; `data` of the bench does not count BSS blocks. Tests: `tests/test_direct_pointers_init.py` (both flows, five option sets) and stronger BSS tests (sdas area sizes, the range that the CP/M start-up code clears).
- `bench/rom/sndrom.py` and `bench/rom/isrproof.py` take `--uc80-flag=FLAG` (one more uc80 option).
- Gates of stage B: `pytest -n auto` 2612 passed; `run_ctests.py` 219 of 220 (only 00129); `isrproof.py` nmi, seq, bank, iy OK with `ul80` and with `--sdas`, same figures with and without `--no-direct-pointers`; `difftest.py --count 600 -j 8` no UC80-WRONG, no MODEL-DIFF (411 PASS); `ccfuzz.py` 1500 and `--ll` 1000 all PASS. Bench size on 109 programs against `bench/results/results.csv`: `image`, `bss`, `com` and `cycles` are equal except the old known rows (`adventure-size` -3 bytes: path text; `coremark-unofficial` -14 bytes, +204 cycles and `mojozork-size` +1 byte: the `integrate7` repair, as in stage A). The `data` field (the DSEG bytes of the object list) falls for `adventure-size` (68049 to 3521), `curses-demo-size` (4456 to 144), `monop-size` (13147 to 10012), `trek-size` (13469 to 10613) and `mojozork-size` (9562 to 3558): the zero-start data of each module is now a BSS block, not DSEG. The linked image and the BSS size do not change, so this is a change of what the field counts, not of the program.

### Second repair of stages A and B (branch `rom3b`)

- **The `volatile` of a pointee typedef-name was lost** (`volatile uint16_t *q`, `typedef volatile uint16_t vreg; vreg *q`): the legacy type of `*q` had no volatile flag, so `*q += k` and `(*q)++` through a pointer that is not a constant wrote the high byte first, and `(*q)++` was a partial read-modify-write. The pointee now keeps the qualifier (`_legacy_pointee` in `codegen.py`). The qualifier on a variable of a typedef type (`volatile uint16_t x`) is not changed: a wider change made 80 benchmark programs bigger and slower. All 16-bit volatile stores write the low byte first, as SDCC does, with a constant pointer or any other pointer. The old high-first order of the compound path was inconsistent with the plain store, so it is not restored. Tests: `tests/test_rom3b_repair2.py` (both flows, with and without `--no-direct-pointers`).
- **`--isr-save-workareas` with `--no-whole-program`** is an error only for a handler that reaches a work area or a routine of another module, and for the pragma. A handler that reaches nothing builds without a message.
- **The range test of `ptrabs.py` and the index scale of `absaddr.py`** use the type size of the compiler (`--int`, `--long`), not a fixed table. A 4-byte `int` at FFFEh is not rewritten.
- **`isrproof.py iy`** reads the text of the compiler in the sdas flow too (`k.s`) and checks `(IY+d)` in `walk_main`, `walk_isr` and `mark`.
- Not changed: a handler that reaches `lc_vm` or `lc_sqrt` work areas keeps the warning (the labels are private to the library; documented). Modifying a `T *const` pointer is accepted without an error (older, outside this work; ptrabs does not rewrite such a pointer).
- Gates of this repair: `pytest -n 6` 2642 passed (a first run lost one xdist worker under load on `test_ccfuzz_seeds` seed 80548; the test passes alone and in the second full run); `run_ctests.py` 219 of 220 (only 00129); `isrproof.py` nmi, seq, bank, iy OK with `ul80`, with `--sdas` and with `--save-workareas` (the `iy` line now lists `uses_iy` for both flows); `difftest.py --count 600 -j 6` no UC80-WRONG, no MODEL-DIFF (411 PASS); `ccfuzz.py` 1500 and `--ll` 1000 all PASS. Bench size on 112 rows of `bench/results/results.csv`: equal in `status`, `image`, `bss`, `com` and `cycles` except the known rows (`adventure-size` -3 bytes: path text; `coremark-unofficial` -14 bytes and +204 cycles and `mojozork-size` +1 byte: the `integrate7` repair; `empire-trefonas` HANG, as the tree before this repair).
- Gates of the repair of stage B (`b746808`, recorded late): `pytest -n auto` 2627 passed.

### Repair of stage A (branch `rom3b`)

- **`--isr-save-workareas` saved the wrong bytes for 64-bit math and float.** The save took the first label of the lib file and the sum of the label sizes. The whole-program build lays the work areas out in its own order (`__acc64`, `__div64_dvd`, `__mul64_mcd`, ...), so `__tmp64` was not saved. `isr_check.save_plan` now takes the address and the size of each label from the program text, and joins labels that follow each other into one area. It never names a label that the program text does not define. This also removes the second copy of the float data (`__fop1` pulled the module `rt_fdata`: a duplicate-symbol link error in the sdas flow, wrong results in the um80 flow). The cost figures changed: the save code is 27 bytes for each big area, not 43 to 70. Measured with romrun (equal to the figures of the compiler): u32 multiply 16 bytes and 106 T-states, u32 divide 27 and 592, u64 multiply or divide 27 and 1432, float add 27 and 1054, float multiply 27 and 1516, float divide 27 and 844. Tests: `tests/test_isr_workareas.py::test_wide_math_survives_with_the_option` (u64 and float, `interrupt` and NMI, both flows; the result without the option is wrong).
- **The option with `--no-whole-program`** is now an error: the library labels of the work areas are private, and a routine of another module is invisible. The compiler warns for a handler that calls a routine that no text of the compilation defines. The data labels of the libc modules (`sin`, `sqrt`, ...) are private too: the save does not cover them and the warning stays (documented in `docs/rom_hardware.md`).
- **The note for a call through a pointer** lists the functions that the handler really reaches (it was empty for `sin`), skips data tables in the code segment, and now also appears for `qsort` (the call through the trampoline in RAM).
- **Docs.** `rom_target.md` no longer lists the per-ISR cost as unverified. The math library does not use the alternate registers (only `__fdiv` does). With `ul80` a second binding of the same vector is a warning (the second handler is dropped); `--fatal-mult-def` makes it an error. The docs name the nested-interrupt stack use and the `JP (HL)` after `EX DE,HL` limit of the check.
- **`adventure-size` is 3 bytes smaller than `results.csv`: not a change of the compiler.** The program has `__FILE__` text for `save.c` with the full path of the work directory. The worktree path `.../wt/integ80g/...` (main) is 3 characters longer than `.../wt/rom3b/...`. A build of main and of the branch give a byte-identical `prog.mac` apart from this one line; the linked symbol files differ by exactly 3. The `data` field of `save.c` in the object list goes from 1248 to 1245 bytes: this is the path string. `coremark-unofficial` (14 bytes, 204 cycles) and `mojozork-size` (1 byte) differ from `results.csv`, and a build of main (1adae2c) gives the same figures: they come from the repair of `integrate7`, not from stage A. The other 106 of 109 rows are equal field by field.

### Added in stage A (interrupt safety, branch `rom3b`)

- **`--isr-save-workareas` and `#pragma uc80 isr_save_workareas NAME`.** The prologue of an interrupt function copies the work areas of the runtime that its call graph reaches (`__tmp32`, `__div32_dvd`, `__rem32`, `__acc64`, the float areas, and so on) to the stack, and the epilogue copies them back. State blocks (`errno`, heap, `atexit`, signal, `strtok`, file table) are never saved. The cost for one interrupt is in the repair entry above. The handlers of `isrproof.py` reach no work area, so their cost is zero (`isrproof.py --save-workareas`). A main loop and an IM 2, NMI or INT handler that both do 32-bit arithmetic give a wrong sum without the option and the right sum with it (`tests/test_isr_workareas.py`, both flows, frequent interrupts).
- **A call through a function pointer in an interrupt function.** The check of `isr_check.py` now treats every function whose address is taken as reachable, for the alternate registers and for the work areas, and prints a note. The old test that hid an `EXX` routine behind a pointer now shows that the alternates are saved.
- **`--with-nullsound`.** A warning for every C function and runtime helper on a path from `--entry`, `main` or an interrupt function that uses `EXX` or `EX AF,AF'` (the float division). The ngdevkit style with `--entry` has the check as before (`tests/test_nullsound_alt.py`).
- **Vectors in separate compilation.** `crt0_rom_vec.rel` (made by `uc80 --build-libs`, and by `--build-sdas-libs` as `crt0_rom.rel` and `crt0_rom_vec.rel`) jumps to the labels `__int_handler`, `__nmi_handler` and `__rstNN_handler`. A module that binds a vector defines the label at its handler and refers to `__crt0_rom_vec`, so a link with `crt0_rom.rel` stops with an undefined symbol. `libc_rom.lib` has a default module for each label (`lc_vec_*.mac`). IM 2 in separate compilation is an error. Tests: `tests/test_vectors_separate.py`, both flows.
- Gates of the repair: `pytest -n auto` 2585 passed; `run_ctests.py` 219 of 220 (only 00129); `isrproof.py` nmi, seq, bank, iy OK with `ul80`, with `--sdas` and with `--save-workareas`; `difftest.py --count 600 -j 8` no UC80-WRONG, no MODEL-DIFF, no UC80-BUILD-FAIL; `ccfuzz.py` 1500 and `--ll` 1000 all PASS; bench size on 109 programs as described above.
- Gates of stage A (before the repair): `pytest -n auto` 2568 passed; `run_ctests.py` 219 of 220 (only 00129); `isrproof.py` nmi, seq, bank, iy OK with `ul80` and with `--sdas`, with and without `--save-workareas`; `difftest.py --count 600` no UC80-WRONG, no MODEL-DIFF, no UC80-BUILD-FAIL (412 PASS, the rest are SDCC results); `ccfuzz.py` 1500 and `--ll` 1000 all PASS. Bench size run on 109 programs: 106 equal to `bench/results/results.csv`. `adventure-size` is 3 bytes smaller, `coremark-unofficial` 14 bytes smaller and 204 cycles slower, `mojozork-size` 1 byte larger. A build of `main` (1adae2c) gives the same three figures, so the cause is the repair of `integrate7` (stricter call group), not stage A.
- `docs/rom_hardware.md`, `docs/rom.md`, `docs/rom_interop.md` and `docs/rom_target.md` describe the four items. The docs said that the float runtime does not use the alternate registers; `__fdiv` does.

### Fixed in the ROM work

- **IM 2 table at an address that starts with A to F.** `#pragma uc80 im2table 0xA000` wrote `LD A,A0H` and `ORG A000H`, and `um80` stopped with "Undefined symbol A0H" (sdas: "ORG needs a constant address"). The start-up text now writes `0A0H` and `0A000H`. The accepted range is now 0100h to FE00h: a table at FF00h put the program code at 10000h, which `um80` wrote over the start of the image without a message. Tests at 4000h, A000h and FE00h run in `bench/romrun`, with `ul80` and with sdas; FF00h is an error.
- **`--asm-syntax=sdas` checks the ROM size of the table.** `sdldz80` built a 32802-byte image for a table at 7F00h, where `ul80` stops with "ROM overflow". The compiler now stops with "ROM overflow" when the program code starts at the end of the ROM or above. The new option `--rom-size HEX` (default 8000) sets the end.
- **A vector pragma names a function in the file that holds the pragma.** Two `static` functions `h` in two files made both IM 2 slots (or `interrupt` vectors) jump to the `h` of the last file. The pragma now binds to the function of its own file: the merge renames each `static` `h` (`h__s0`, `h__s1`) and the pragma, `__interrupt` and `__naked` of a file use the rename map of that file (see the integration entry below). The compiler stops with an error that names both files only when two input files define a non-`static` function with the name of a vector pragma.
- **The vector pragmas warn in a CP/M program.** `im2`, `im2table`, `rst`, `vector`, `interrupt` and `nmi` had no effect with the CP/M target and no message. They now print the "not set" warning, as for `--no-embed-startup`.
- **`__attribute__((naked))` is a naked function.** It was dropped without a message, so a naked handler got a normal prologue. `__attribute__((interrupt))` stays an error.
- **An asm statement after `case` or `default`** (`case 0: asm("nop");`) gave "internal error: unexpected token KW_ASM". The prepass now adds the braces, as for `if` and `for`.
- **The naked checks run for a naked function that nothing calls.** The big structure result, the variable argument list and the "no asm statement" checks were skipped, because dead-function removal dropped the function first. A naked function is now a root of the call graph, so the function stays in the output (asm code can enter a naked function by name). The docs say where a `char`, `long`, `float` and `long long` result is read.
- **The CP/M warnings of the vector pragmas carry the file and the line** of the pragma.
- **`--rom-size` is checked in every flow.** A value above 10000h or 0 is an error. With `um80` and `ul80`, the `ul80` line that `-v` prints now uses the value. The docs say that the sdas check looks at the code start only.
- A naked function that returns a structure or union of more than 2 bytes, or takes `...`, is an error. A naked marker that differs between prototype and definition gives a warning. `typedef int F(int) __naked;` is an error. The "no asm statement" warning and the naked errors carry file and line.
- **A label with two colons in an `__asm` block is PUBLIC** (`_g_alt::`), as in sdas and MACRO-80. It was an ordinary label, so the link failed with "Undefined symbol".
- **An asm statement as the body of `if`, `else`, `for`, `while` and `do` without braces** (`for (;;) __asm__("halt");`) gave "unexpected token KW_ASM". The prepass in `sdcc_compat.py` adds the braces. The grammar of uc_core (`statement` in `data/c23.json`) has no asm as a statement, only as a declaration in a block; this is not changed in uc_core.

### Fixed in the repair of `integrate7`

- **`rst n` in asm text was not a call for the register-locals pass.** The summary of a function that holds `asm("rst 10h")` showed no register write, so the `push BC` and `push IY` around the call of that function were removed. A handler that changes BC, DE or IY (naked or C) then changed the locals of the caller. The summary now treats `rst n` as a call to an unknown routine (`reglocals._Summaries`, `function_writes`). Tests in `tests/test_rom_vectors.py` run a naked handler and a C handler, in both flows, with `UC80_NO_IY` 0 and 1.
- **An IY wrap spanned an earlier call group (ccfuzz seed 777109, `--no-shared-storage`).** For a call that pops its arguments with `ld HL,n / add HL,SP / ld SP,HL`, `_call_group` took every earlier push without a pop as an argument. The `push IY` went before the arguments of an earlier call, and the `pop IY` after the call, so one wrap covered a place where IY was not set yet. The group now takes n/2 pushes, skips the pushes of an inner call that has its own `ld SP,HL`, and gives up (no wrap region, so the pass rejects the function) when it cannot read n. The cleanup is only searched until the next push, jump or call. The seed is now a test in `tests/test_ccfuzz_seeds.py`. This bug was also in `main`.
- **Asm text names a static of its own file.** With two files that each define `static helper` or `static cnt`, the text `call _helper` or `ld a,(_cnt)` in a naked function or an `asm` statement stopped the link with "Undefined symbol", because the merge had renamed both statics. The merge now writes the private name into the asm text of the file (`multi_unit._Renamer.asm_text`). Tests in `tests/test_rom_vectors.py`, both flows. This bug was also in `main`.
- **A vector pragma cannot name a static of another file.** `#pragma uc80 im2 1 h` in `a.c`, with `static void h` only in `c.c`, bound to the static of `c.c`. It is now an error that says `h` is not defined in the file.
- **Messages show the C name of a renamed static** (`h`, not `h__s0`) in the vector errors and in the naked mismatch warning. `--entry NAME` with a static `NAME` in two files is an error that says so.
- **`0x5A5A` in asm text builds in the sdas flow.** The translator said "bad number"; `um80` accepted the number.
- Tests: the multi-file pragma tests of `tests/test_rom_vectors.py` run in the sdas flow too. `tests/test_naked_reglocals.py` runs an IY pointer across a naked call that sets IY, and checks the mismatch warning of two static naked functions.

- Gates of the repair: `pytest -n auto` 2511 passed; `run_ctests.py` 219 of 220 (only 00129); `isrproof.py` nmi, seq, bank, iy OK in both flows (NMI 630 T-states); `ccfuzz.py` 1500 and `--ll` 1000 all PASS; `difftest.py --count 600` no UC80-WRONG and no MODEL-DIFF; gen3 family 75 files, no RLDIFF. Bench size run on the same 109 programs: 105 are identical to `main`. `coremark-unofficial` is 14 bytes smaller and 204 cycles slower (0.0005 percent), and `mojozork-size` is 1 byte larger. The cause is the stricter call group in `_call_group`: a few wraps now start at the right push.

### Changed

- **Integration of `famh` (family H: address CSE, dead stores, inline block operations) into `integrate8`.** One tree now holds `main` (register locals, naked functions, RST and IM 2 vectors, ROM wave 4) and `famh`. The merge had four conflict regions (the `CodeGenerator` argument list, the `main.py` option list, the `main.py` call, and the CHANGELOG, both sides kept in each). The files `README.md`, `docs/codegen_gap.md`, `docs/compiler_options.md` and `bench/results/results.csv` merged without a conflict. The uc80 rows of the CSV and `docs/benchmarks_speed.md` are made again. The merge needed no change of a pass. `tests/test_famh_rom_integration.py` (28 cases, each in the um80 flow and in the sdas flow) runs the interactions in the ROM runner and compares the writes to the addresses, in order, and the RAM with the build that has `--no-addr-cse --no-chain-store --no-dead-store --no-blockops`:
  - Address CSE and direct accesses. The ROM pass (`ptrabs.py`) runs first and changes a volatile access through a constant pointer into a named volatile object. The address CSE pass takes such an object as a volatile global: it ends a run and is never a key. Non-volatile elements of a struct or an array behind a constant pointer, and of a named array, get a temporary pointer (the pass fires, the listing differs from `--no-addr-cse`), and the RAM and the writes are equal with `--no-direct-pointers`.
  - Dead stores. A store through a constant-address volatile pointer (byte and 16-bit), a store through a pointer that the ROM pass made direct, and stores in a loop stay, in the same order. A dead local and an overwritten store of a local go. A handler for an interrupt with dead locals writes the same port bytes in the same order.
  - Inline block operations. `memcpy` and `memset` through a constant volatile pointer, and through a pointer of a `volatile` typedef type stay calls. A cast to `(void *)` of a constant address is inline code. A structure copy to a volatile target (`*regs = img`, 16-bit and 32-bit members, also `volatile struct`) uses `ldi` or `ldir` and writes the low byte first, in the order of the build without the pass.
  - `--isr-save-workareas`. A handler with inline `memcpy`, `memset` and a structure copy, plus 32-bit arithmetic, saves the same work areas (`rt_rem32`, `rt_tmp32`) with and without `--no-blockops` (`memcpy` and `memset` have no work area, so the plan does not shrink here, and does not grow). With interrupts every 9973 T-states the sum of the main loop and the copied buffer are right. The handler text has `ldir` and no call of `_memcpy` or `_memset`.
  - Naked functions. `deadstore.py` skips the names in `naked` (checked in the code), `addrcse.py` skips them, and a naked function has no call that `blockops.py` can change. A program with a naked function and a caller with dead locals and a `memcpy` runs right.
  - `--no-whole-program`. Two modules with per-module BSS, a constant-address struct, dead locals, `memset` and `memcpy`: the writes are the same with and without the three passes, and the images link with `ul80` and `sdldz80`.
  - Documentation: sections on constant addresses and interrupt functions in [docs/blockops.md](docs/blockops.md), [docs/dead_store.md](docs/dead_store.md) and [docs/addr_cse.md](docs/addr_cse.md).
  - Gates on the merged tree: `pytest -n 6` 2783 passed (2755 before the 28 new cases). `run_ctests.py` 219 of 220 (only 00129 fails). `bench/rom/isrproof.py` nmi, seq, bank and iy pass with `ul80`, with `--sdas`, and each of the two with `--save-workareas` (NMI 630 T-states; seq ticks 19315 to 50026 T-states, as in the `main` tree). `bench/difftest.py --count 800 -j 8` with the seeds 1 and 5000: 549 and 554 PASS, no `UC80-WRONG` and no `MODEL-DIFF` (the other results are SDCC failures). `bench/ccfuzz.py` 2000 programs and `--ll` 1500 programs all PASS. The variants `-O0`, `--no-register-locals`, `--no-inlining`, `--no-addr-cse`, `--no-dead-store`, `--no-blockops` and `--isr-safe` run 400 programs each, and the same seven with `--ll` run 400 programs each (5600 programs): all PASS. The 75 programs of the `gen3` family (8 variants each): no RLDIFF, and every `adv_w` directory has a `prog.com`.
  - Bench (`bench/run.py -c uc80`, the 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`; these three keep their rows of `main`: the build of the first two ends in a timeout and `empire-trefonas` in WRONG, as in `main`). No status change against the CSV of `main`. Against the CSV of `main`: 72 programs smaller and 1 bigger (`bsd-bcd` +2 B), 59 faster and 3 slower (`snd-ymreg` +0.42%, `fasta` +0.001%, `v7-arithmetic` +0.002%), the causes are those of `famh` (see the part 1 and part 2 entries). Mean against SDCC 4.6.0 (`bench/report.py` method, 4 digits): size 0.9162, whole image 0.8837, T-states (speed mode) 0.5456, without `sort-*` 0.6611. uc80 is faster than SDCC on 63 of 78 programs. Next to `main` (0.9353, 0.9000, 0.5628, 0.6844, 60 of 78) and to `famh` alone (0.9164, 0.8838, 0.5459, 0.6615, 63 of 78): the merged tree differs from `famh` alone by 0.0002 to 0.0004 in each mean, and the faster count is the same. The cause of this small change was not bisected. The version column of the uc80 rows names the merge commit 5f88460, not the tree that was measured.

- **Integration of `rom2` (naked functions, RST and IM 2 vectors) into `integrate7`.** One tree now holds `rom2`, the bug-fix round and register locals. The merge had two text conflicts (the `CodeGenerator` and `main.py` argument lists, both sides kept). These interactions are resolved, and each has a test that runs code (`tests/test_naked_reglocals.py`, `tests/test_rom_vectors.py`):
  - Register locals and naked functions. The pass skips a naked function (`reglocals_gen.py`, the skip set). A call to a naked function is a call like any other: the callee may change A, BC, DE, HL and IY, so the caller saves live locals in BC, DE and IY around the call, and a naked function is never IY-clean. The output is the same with the pass on, with `--no-register-locals`, with `UC80_NO_IY=1`, with `--no-inlining` and with `--no-register-cc`. See [docs/register_locals.md](docs/register_locals.md).
  - File-static renaming and vector pragmas. `multi_unit.uniquify_statics_map` returns the rename map of each file. The pragmas (`keep`, `public`, `rst`, `im2`, `vector`, `interrupt`, `nmi`) and the SDCC `__interrupt` and `__naked` marks are mapped with the map of their own file before the roots are made. Two `static` handlers `h` in two files now run, each in its own slot (the rom2 test that expected an error now checks the run). The error stays for two non-`static` definitions of one name. Static naked functions of one name in two files work.
  - T-states. The NMI handler of `bench/rom/isrproof.py` takes 630 T-states (rom2: 667; register locals). The tests already pin 630. The `seq` proof ticks take 19315 to 50026 T-states (rom2: 19589 to 50300, 274 less each). `docs/rom_hardware.md` has the new figure. The `bank` and `iy` proofs and the sdas build give the same numbers as the `ul80` build.
  - A naked function is a root of the call graph. This works with the dead-function pass (an uncalled naked function and the helper that only its asm text calls stay) and with the inliner (a naked function is never inlined). `docs/rom_hardware.md` no longer says that the compiler removes an uncalled naked function.
  - Gates: 2491 tests pass (`pytest -n auto`); the four cases of `test_naked_root_with_dead_code_and_inlining` came after that run and pass alone (7 tests in `tests/test_naked_reglocals.py`). `run_ctests.py` gives 219 of 220 (only 00129 fails). `bench/rom/isrproof.py` (nmi, seq, bank, iy) passes with `ul80` and with `--sdas`. `tests/test_rom_vectors.py` and `tests/test_naked.py` pass in both flows (144 tests). `bench/difftest.py --count 600 -j 8`: no UC80-WRONG and no MODEL-DIFF. `bench/ccfuzz.py` 1500 programs and `--ll` 1000 programs: all PASS. The 75 programs of the `gen3` family (8 variants each): no RLDIFF, and every `adv_w` directory has a `prog.com`. Size run (`bench/run.py -c uc80 -m size`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`): every field (status, image, bss, com, cycles, code, data, objects) equals `bench/results/results.csv` of `main`, so no program is bigger or slower.

- **Inline block operations (family H, part 3; branch `famh`).** A new pass (`src/uc80/blockops.py`, [docs/blockops.md](docs/blockops.md)) changes a call of `memset` or `memcpy` with a constant size (1 to 65535) into inline code when the program does not use the result and no argument is volatile. A copy is `ldi` up to 4 bytes and `ld bc,N` with `ldir` above that. A fill stores the first byte and copies the byte with `ldir` (`ld (hl),v / ld de,hl+1 / ldir`), or stores up to 4 bytes directly (word stores `ld (addr),hl` at a constant address). An argument that is a constant address goes straight into HL or DE. `memmove`, a size that is not constant, a volatile argument, a used result and a size of 0 keep the call. A structure assignment and a structure initialisation use the same copy (`ldi` up to 4 bytes, and no `push hl` and `pop hl` for a destination at a constant address). A local array with a zero initialiser in shared storage is one `ldir` fill when the size is over 4 (it was one `xor a / ld (addr),a` for each byte). `--no-blockops` keeps the old code; `UC80_NO_BLOCKOPS=1`, `UC80_BLOCKOP_ONLY=f,g` and `UC80_BLOCKOP_UNROLL=n` help a bisect.
  - Register locals (change of `reglocals.py`). `ldi` and `ldd` no longer read BC as an input (the count only changes P/V), their cost is 16 T-states (the default was 8), and two wrap intervals that touch (one starts right after the other ends) join to one `push` and `pop`. Before this change a loop counter in BC lost its register around four `ldi` (`micro` copy4: the counter went to memory). The join also changes some programs that have no block operation.
  - Size and speed (`bench/run.py -c uc80`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`), against the tree before (6cc473b, H2), no status change: 35 programs smaller, 12 bigger, the image total 774,824 to 774,053 bytes; 28 programs faster and 3 slower. Bigger: `monop-size` +21 B and `embench-slre` +6 B (three 4-byte structure copies as `ldi`, 3 bytes more each than `ldir`), the 10 `micro` programs +3 B, all from the join of wraps or the `ldi` unroll. Slower: `embench-md5sum` +1008 T-states (+0.03%; one `memcpy` of 4 bytes becomes `ldi`, but one extra `nop` in the old code moves the count by +1240 T-states, so the cause is the address of the code and the data), `mille` +8664 and `fasta` +2104 T-states (+0.001%, from the join of wraps). `sieve` -33 B (the main `memset`), `embench-statemate` -3.17% T-states and -42 B, `embench-huffbench` -104 B, `zx0` -1.14% T-states, `pi_ldiv` -0.39%, `dhrystone21` -0.20% and -2 B (`+nostructassign` -2.2%), `embench-tarfind` -0.27% and -36 B, `fannkuch+static` -0.62%, `curses-demo` -35 B, `snake` -29 B, `trek-size` -59 B, `worm` -39 B, `canfield` -38 B, `mojozork-size` -14 B. Mean against SDCC 4.6.0: size 0.9216 to 0.9164, whole image 0.8879 to 0.8838, T-states (speed mode) 0.5466 to 0.5459, without `sort-*` 0.6624 to 0.6615. uc80 is faster than SDCC on 63 of 78 programs (62 before). Against main (e28deae) the means are 0.9353, 0.9000, 0.5628 and 0.6844, with 60 of 78; against the main csv 74 programs are smaller and 59 faster, with `bsd-bcd` +2 B, `snd-ymreg` +0.42% and `v7-arithmetic` +42 T-states bigger or slower (the causes in H1 and the register-locals change, see part 2). uc80 has one code selection, so the size run and the speed run are the same builds.
  - Tests: `tests/test_blockops.py` (one program in eleven modes against the host compiler: fills and copies of 1 to 400 bytes at global, pointer, local and string addresses, side effects in the arguments, a used result, overlapping `memmove`, volatile objects, aliasing pointers; a second program with loops that keep a counter in BC around `ldi`; the shape of which calls stay and which become inline code).
  - Gates: 2442 tests pass. `run_ctests.py` gives 219 of 220 (only 00129 fails). `bench/difftest.py --count 800` with the seeds 1 and 1001 gives 548 and 545 PASS, no `UC80-WRONG` and no `MODEL-DIFF` (the other results are SDCC failures). `bench/ccfuzz.py` runs 1500 programs, and `--ll` runs 1000 programs, with no wrong result. The variants `-O0`, `--no-register-locals` and `--no-inlining` run 300 programs each, and the same variants with `--ll` run 300 programs each, with no wrong result. All four proofs of `bench/rom/isrproof.py` pass. The 75 programs of the register-locals family give no `RLDIFF` in 8 variants.
- **Dead locals and overwritten stores (family H, part 2; branch `famh`).** A new pass (`src/uc80/deadstore.py`, [docs/dead_store.md](docs/dead_store.md)) works on the AST of each function before the address CSE pass. A local that no expression reads is removed with every store to the local (`x = e`, `x op= e`, `x++`, `T x = e`, in a statement, in a `for` init or update, in a comma expression). A store that a later store of the same statement list overwrites is removed too (`int i = 0; ...; for (i = 0; ...)`), when no statement between them names the variable or holds a label or a jump. A right side with an effect (a call, `++`, a volatile access) stays as a statement. The pass repeats until nothing changes: removing `count -= !flags[k]` makes the names that only fed the statement dead. A global, a static, a volatile, a name under `&`, a parameter, an array and a struct are never removed. A function with inline assembly (a label alone, such as the benchmark timing label, is no reason), a statement expression or `setjmp` is not changed. `--no-dead-store` turns the pass off, `UC80_NO_DSE_OVERWRITE=1` turns the second part off, and `UC80_DSE_DEBUG=1` and `UC80_DSE_ONLY=f,g` help a bisect. `generate()` has a `dead_store` argument (default True); `tests/test_codegen.py` and `tests/test_inplace_update.py` pass `dead_store=False` where a test needs locals that nothing reads.
  - Size and speed (`bench/run.py -c uc80`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`), against the same tree with `--no-dead-store`, no status change: 26 programs smaller, none bigger, the image total 775,324 to 774,814 bytes; 17 programs faster and 1 slower (`embench-huffbench` +2 T-states, from a BSS that is 2 bytes smaller). `sieve` -46.13% T-states and -49 B, `fannkuch` -2.79% and -67 B, `dhrystone21` -0.55%, `embench-tarfind` -0.51% and -22 B, `whetstone` -0.26% and -43 B, `binary-trees` -54 B, `monop-size` -41 B. Mean against SDCC 4.6.0: size 0.9276 to 0.9216, whole image 0.8928 to 0.8879, T-states (speed mode) 0.5515 to 0.5466, without `sort-*` 0.6691 to 0.6624. uc80 is faster than SDCC on 62 of 78 programs (no change). Against main (e28deae) the means are 0.9353, 0.9000, 0.5628 and 0.6844, with 60 of 78. `sieve+static` does not change (a store to a static stays).
  - Tests: `tests/test_dead_store.py` (one program in ten modes against the host compiler, with dead stores and with stores that only look dead: a call in the right side, a volatile read, an address taken, a global of the same name, a read in a branch, a jump between two stores; the shape of what the pass removes and keeps).
  - Repair of H2. The overwritten-store scan was quadratic (1200 locals: 41 s against 5 s); the scan now uses a cached name set per statement and is linear (1500 locals: 2.5 s against 2.1 s with the pass off). A function with any `asm` (also a label alone) skips the overwritten-store part, because other assembly can jump to the label. New tests: two files with a local of the name of a global in the other file, an NMI handler with dead locals in a ROM run (port log equal with and without the pass), the `-v` line, a long flat function, the label rule.
  - Three programs differ from main (9b7bea4); bisected: `bsd-bcd` (+2 B, T-states -2.2%) and `v7-arithmetic` (+42 T-states) are from the address CSE pass of H1 (`--no-addr-cse` gives main's numbers). `snd-ymreg` (+0.42% T-states, -3 B) is from the register-locals change f4cbf1a (names inside `&a[i]` are not address taken); the tree before H1 has the same figure. H2 causes none of the three.
  - Gates: 2411 tests pass. `run_ctests.py` gives 219 of 220 (only 00129 fails). `bench/difftest.py --count 800` with the seeds 1 and 5000 gives 548 and 552 PASS, no `UC80-WRONG` and no `MODEL-DIFF` (the other results are SDCC failures). `bench/ccfuzz.py` runs 1500 programs, and `--ll` runs 1000 programs, with no wrong result. The variants `-O0`, `--no-register-locals` and `--no-inlining` run 300 programs each, and the same variants with `--ll` run 300 programs each, with no wrong result. All four proofs of `bench/rom/isrproof.py` pass. The 75 programs of the register-locals family give no `RLDIFF` in 8 variants.
- Address CSE repair: a `#pragma uc80 vector` handler and hand-written assembly files turn the memory keys off like an interrupt function does; a naked function is skipped. See docs/addr_cse.md.
- **Address CSE and chained constant stores (family H, part 1; branch `famh`).** A new pass (`src/uc80/addrcse.py`, [docs/addr_cse.md](docs/addr_cse.md)) works on the AST of each function before the frame is sized. The code generator computed the address of an element again at each access. Now a repeated address (`a[i]` with `a[i + 1]`, `p[i]`, `s.arr[i]`, `p->arr[i]`, `a[i][j]`) is computed once into a new pointer local, and the accesses are `*t` and `t[k]`. The register-locals pass can move the pointer into IY, BC or DE. A key holds local variables (no `&`, not volatile), and, when the program has no interrupt function, loads from named arrays and global scalars. A run of statements ends at an assignment to a variable of the key, a label, and for a key with a load a call, a store through a pointer, a store into the array of the load and a volatile access. A group needs an estimated gain of 12 T-states and one access that no branch skips. The second part: `a[i] = b[j] = K` with a constant K that fits each target becomes one store of K for each target, when a target is a byte (`queens`: the chain of three flag arrays). `--no-addr-cse` and `--no-chain-store` turn the parts off; `UC80_ACSE_DEBUG=1`, `UC80_ACSE_MAX=n` and `UC80_ACSE_ONLY=f,g` help a bisect. `lc_strtod.mac` is made again (the only library module that changes).
  - Names under `&` (register locals). `&a[i]`, `&p->m` and `&*p` no longer count as an address taken for `i` and `p` (`reglocals_gen.address_taken_names`; `UC80_RL_TAINT_ALL=1` gives the old rule). The names stay candidates for BC, DE and IY. Measured alone: 18 programs smaller, none bigger, 15 faster, 3 slower (`snd-ymreg` +0.42%, `v7-wump` +0.15%, `bsd-arithmetic` +0.06%, because the cost model picks a register that is slightly worse). `stanford-perm` -5.17%, `invaders` -2.2%, `curses-demo` -1.9% and -323 B.
  - Size and speed (`bench/run.py -c uc80`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`), against the tree before (e28deae), no status change: 56 programs smaller, 1 bigger (`bsd-bcd` +2 B, -2.23% T-states), the image total of 107 programs 677,727 to 672,357 bytes; 48 programs faster and 2 slower (`snd-ymreg` +0.42%, `v7-arithmetic` +0.002%). `coremark-unofficial` -21.55% T-states and -628 B, `queens` -20.57%, `embench-matmult-int` -18.57%, `stanford-towers` -12.89%, `stanford-bubble` -12.45%, `embench-ud` -5.95%, `fannkuch` -4.96%. Mean against SDCC 4.6.0: size 0.9353 to 0.9276, whole image 0.9000 to 0.8928, T-states (speed mode) 0.5628 to 0.5515, without `sort-*` 0.6844 to 0.6691. uc80 is faster than SDCC on 62 of 78 programs (60 before). The compile time does not change (`embench-nsichneu` 5.5 s with and without the pass).
  - Tests: `tests/test_addr_cse.py` (five programs in ten modes against the host compiler, and the shape of what the pass does and does not touch: an assignment, a label, a call, a store, a volatile access, an address taken, an interrupt function) and `tests/test_regl_taint.py`. `tests/test_regl_bugfix_interaction.py` runs with `--no-addr-cse` because the pass moves the gain of `fcount` out of the register-locals pass.
  - Gates: 2394 tests pass. `run_ctests.py` gives 219 of 220 (only 00129 fails). `bench/difftest.py --count 800` with the seeds 1 and 5000 gives 549 and 554 PASS, no `UC80-WRONG` and no `MODEL-DIFF` (the other results are SDCC failures). `bench/ccfuzz.py` runs 1500 programs, and `--ll` runs 1000 programs, with no wrong result. The variants `-O0`, `--no-register-locals` and `--no-inlining` run 300 programs each, and the same variants with `--ll` run 300 programs each, with no wrong result. All four proofs of `bench/rom/isrproof.py` pass. The 75 programs of the register-locals family give no `RLDIFF` in 8 variants.
- **Integration of `bugfix2` and `reglocals` (branch `integrate6`).** One tree now holds both branches. `bugfix2` brings wide integers, IEEE NaN compares, calls through `**f`, several C files, C23 `auto` and inlined `uint16_t` literals. `reglocals` brings register locals in BC, DE and IY (family G, steps 1 and 2). The two failures of `test_printf_puts` that `bugfix2` reported came from its older base and are gone.
  - Test: `tests/test_regl_bugfix_interaction.py` keeps one program for each place where the two branches meet: 16-bit negation, the float compare with the NaN test, inlined `uint16_t` literals and `long long` values.
  - Gates: 2333 tests pass. `run_ctests.py` gives 219 of 220 (only 00129 fails). `bench/ccfuzz.py` runs 2000 programs, and `--ll` runs 1500 programs, with no wrong result. The six flag variants (`-O0`, `--no-ast-optimize`, `--no-inlining`, `--no-shared-storage`, `--isr-safe`, `--no-register-locals`) run 500 programs each with no wrong result. `bench/difftest.py --count 800` with two seeds gives no `UC80-WRONG` and no `MODEL-DIFF`. All four proofs of `bench/rom/isrproof.py` pass. The 75 programs of the register-locals family give no `RLDIFF` in 8 variants.
  - Size and speed, against the tree before the merge (`reglocals` alone), on the 109 programs of the size run: 15 programs get smaller (`robots` -30 B, `monop-size` -8 B, the others 1 to 4 B). 12 programs get bigger by 56 to 135 bytes. These are the programs that use float, because of the NaN test in `__fcmp`. `fasta` is 0.73% slower, `mandelbrot` 0.22% slower and `trek-size` is 135 B bigger.
  - Mean against SDCC 4.6.0: the size ratio goes from 0.934 to 0.935. The T-state ratio goes from 0.5627 to 0.5628 (speed mode) and from 0.6842 to 0.6844 (without `sort-*`). uc80 is faster than SDCC on 60 of 78 programs and smaller on 66 of 96. Before both families (84cfebf) the values were 0.958 (size), 0.585 and 0.715 (T-states).
  - `battlestar-size` times out in the link step (1800 s) with this tree and with 84cfebf. The whole-program compile of that program takes more than 25 minutes.
- **Register locals (family G, step 2): a pointer in IY.** One pointer local of a function (a struct pointer, a string pointer) lives in IY. A field access through the pointer is one instruction, `ld A,(IY+d)` (19 T-states), and `p++` is `inc IY`. The pointer needs no HL, DE or BC, so the two other locals can use BC and DE. A call that can change IY gets `push IY` and `pop IY` while the pointer is live (the tag `; RYn`), and `prune_iy` removes each wrap whose callees never change IY. The runtime helpers never change IY, and an interrupt function saves IY already. The cost model chooses between the text with IY and the text without IY. `UC80_NO_IY=1` turns IY off. Against step 1 (the 109 programs of the size run, no status change): 38 programs get smaller and one gets 3 bytes bigger (`embench-huffbench`), 38 programs get faster and none slower. `snd-seq` -6.95% T-states and -118 B, `stanford-queens` -7.5%, `stanford-tree` -2.7%, `dhrystone21` -0.7%, `mille` -365 B, `monop-size` -320 B. The mean against SDCC 4.6.0 goes from 0.936 to 0.934 (size) and from 0.565 to 0.563 (T-states). A local needs a weight of 1 (not 2) to be a candidate for BC and DE: six programs get smaller and six faster, none worse. The other constants of step 1 stay: a table of the measured effect of each one is in [docs/register_locals.md](docs/register_locals.md). Tests: `tests/test_iy_pointers.py`, and the proof `iy` of `bench/rom/isrproof.py` (a pointer in IY under a real NMI).
- **Register locals (family G, step 1).** In whole-program mode up to two scalar locals of a function (a char, an int, a pointer; not address-taken, not volatile) live in BC or DE instead of in `??AUTO` or the IX frame. The scratch uses of BC and DE in the same function are renamed to the other pair or wrapped in `push` and `pop`, and a checker proves the result; `register` is a priority hint. Against the tree before: 87 of 109 programs smaller (794,019 to 782,696 bytes of image), none bigger, 79 faster and 1 slower. `snd-cmdq` 10.60M to 9.90M T-states, `sieve` -17.8%, `tak` -14.4%. Mean against SDCC 4.6.0: size 0.95 to 0.94, T-states 0.59 to 0.56. `--no-register-locals` turns the pass off. The NMI handler test of `bench/rom/isrproof.py` now takes 630 T-states (was 667). The checker runs before and after the peephole, and the exit result bytes count as reads, which fixes two miscompiles found by review (parameter 1 in DE, high word of a long in DE). See `docs/register_locals.md`.
- **Register calling convention merged with the libc rewrite in C, the float interpreter and the ROM work.** The generated library modules (`lc/*.mac`) are unchanged by the merge: the generators compile with `--no-whole-program`, so every library function keeps the stack convention. Against the tree before the merge (run again with the same harness): 82 of 103 OK programs smaller, none bigger (the FITS games `mojozork-size` +2,229 bytes of image and `monop-size` +325 bytes of image are bigger: the call graph change below moves their locals from `??AUTO` to stack frames), image total 638,662 to 619,188 bytes; 56 programs faster, 2 slower; no status change. Mean against SDCC 4.6.0: size 0.987 to 0.958, T-states (speed mode) 0.606 to 0.585, without `sort-*` 0.743 to 0.715. `snd-cmdq` 13.72M to 10.60M T-states, `snd-seq` 14.52M to 12.34M (faster than SDCC speed mode). `docs/benchmarks.md`, `docs/benchmarks_speed.md`, `docs/codegen_gap.md` and `docs/benchmark_proposal.md` have the new numbers.
- Fix: the push and pop of a register parameter across a call (register convention, step 2) is not used when the call is `__save_tmp64`, `__restore_tmp64` or `__push64_acc`. These routines change SP by 8 bytes, so the `pop` read the wrong word (wrong answers with long long values). `bench/ccfuzz.py --ll` finds this; `tests/test_register_cc.py` pins it. `docs/benchmarks.md` marks two old tables as not recomputed.
- The call graph of the shared-storage analysis now has an edge from every function that calls through a pointer (or into a library routine that takes a callback) to every function with a taken address. Recursion through a pointer is now found, and a function that an interrupt function reaches through a pointer keeps its locals and parameters on the stack. This also fixes locals in shared `??AUTO` of a function that recursed through a pointer, wrong before the register convention.
- `bench/ccfuzz.py` makes cycles that run through a pointer variable; `tests/test_register_cc.py` pins two such programs and an interrupt that calls a hook pointer.
- `peep_local` rule 3 (`call` then `ret` becomes `jp`) now applies to calls of register-convention functions that take no stack argument, because the caller has nothing to pop. In the stack convention the rule almost never applied.
- `generate()` has a `register_cc` argument (default True). The unit tests that pin the shape of stack-convention parameters pass `register_cc=False`.
- `bench/ccfuzz.py` also makes tail calls (`return g(...)` with a result of the same type) and returns of an expression of another scalar type. The second shape found an old wrong answer with `-O0` (seed 40035, also wrong with `--no-register-cc` and with the compiler before the register convention); the wide-integer repairs of the same release fix it: seed 10657 with `--no-ast-optimize` passes from the commit "Wide integers: long long literals, unary ops, ?:, switch, initialisers", seed 40035 with `-O0` from the repair pass (inlined 16-bit typedef parameters) (`tests/test_ccfuzz_seeds.py`).
- **Register calling convention, step 2** (family F of `docs/codegen_gap.md`; `docs/calling_convention.md`). A function of the register convention that returns `char`, `signed char` or `unsigned char` returns the byte in A when the calls of the function use the byte as a byte at least as often as they need an int. The callee no longer widens the byte, and a call that is a statement, a condition, a comparison, a store to a char or a `return` in a char function does not widen it either. `char f(void)` and `char f(long)` also qualify. A register parameter that is stored once and loaded once after a call, with only straight-line code between, uses `push` and `pop` instead of a slot in `??AUTO` (`regcc.push_pop_slots`). `return g(...);` in a function with no stack local becomes `pop IX` and `jp _g` (or `jp _g` when the function has no frame). Measured against step 1: the image of the 104 programs falls by 408 bytes (41 smaller, none bigger), 21 programs get faster and one slower (`embench-tarfind`, 0.04%), `stanford-perm` -1.5% T-states, `binary-trees` -1.05%, `snd-cmdq` -1.05%. Geometric mean against SDCC 4.6.0: size 0.9484 to 0.9476, T-states (speed mode) 0.6020 to 0.6015.

### Fixed

- **`vsnprintf` is new, and `snprintf` and `vsnprintf` share one routine** ([docs/printf.md](docs/printf.md)). Both write at most `n - 1` characters and a NUL, write nothing when `n` is 0 (`buf` can then be a null pointer), and return the length that the full output has. A program that calls `vsnprintf` gets every printf conversion, as for a format that is not a literal (the format scan of the code generator does not know the function; `main.py` sets `--printf all`). Tests: `tests/test_stdio_library.py`.
- **`_Exit`, `quick_exit` and `at_quick_exit` are new** ([docs/exit_and_files.md](docs/exit_and_files.md)). The header had macros that turned `quick_exit` into `exit` and `at_quick_exit` into `atexit`, which is wrong: `quick_exit` runs the `at_quick_exit` functions (32) and not the `atexit` functions. `_Exit` and `quick_exit` close no file and write no buffer. A program that does not use them links none of the code.
- **A TAB reaches the console.** The byte 09H never reached the console: the emulator `cpmemu` dropped it. The fix is in `cpmemu` (BDOS 2 and 9 expand a TAB, as the CP/M 2.2 BDOS does), not in the library, so it costs no bytes. `bench/run.py` compares text with the TABs expanded, because `z88dk-ticks` passes the byte through and the golden files hold it ([docs/console_output.md](docs/console_output.md)).
- **`fopen` has the modes `a`, `r+`, `w+` and `a+`** ([docs/exit_and_files.md](docs/exit_and_files.md)). Mode `a` made a new file, and the `+` modes were not accepted. Now each write to an `a` stream goes to the end of the data, `r+` changes only the bytes that it writes, and a stream that can read and write changes between the two by itself (`fflush`, `fseek` or `rewind` between a read and a write are not required, as the C standard requires them). `fputc` on a stream that cannot write and `fgetc` on a stream that cannot read return EOF. Also fixed in this code: `fseek` on a stream that writes lost the bytes of the record after the position when it wrote at the start of a record, and a write that crossed the end of a record in the middle of a file replaced the bytes of the next record with Ctrl-Z (`w` mode after `fseek`). `tmpfile` returned NULL always (it passed the arguments of `fopen` in the wrong order, and `tmpnam` wrote the name to a wrong place); it opens the file with `w+` now. Tests: `tests/test_stdio_library.py` compares a stream of each mode with a model of the C rules for random series of operations.
- **8 files can be open at the same time** (4 before). Each file costs 170 bytes of RAM in the BSS segment, so the program on disk is not bigger, and a program that links `fopen` has 680 bytes of RAM less than before. `UC80_MAX_FILES=n uc80 --build-libs` builds the libraries for another number (1 to 64).
- Fix: `freopen` always returned NULL, because it passed the arguments of `fopen` in the wrong order. It now opens the new file in the same `FILE` and returns the stream. `freopen` on `stdin`, `stdout` and `stderr` returns NULL and changes nothing (documented). `fseek` past the end of the data on a stream that can write lost a write in the same record, and left Ctrl-Z fill (and not zeros) before the position; the fill is now zeros. Tests in `tests/test_stdio_library.py`.
- **The code for the update and append modes links only when the program can need it** (`file_hook.py`). When the whole program has no `fopen` mode with `a` or `+`, no `tmpfile`, and no mode that is not a literal, the compiler adds a reference to `__fio_lite`, and the linker takes 8 bytes of stubs and not `lc_ftowrite` and `lc_ftoread` (about 300 bytes). Not with `--no-whole-program`, for the ROM target, or with assembly files. A program that has `fputs(s, stdout)` or `fprintf(stderr, ...)` only is 89 bytes bigger than before with this hook (it was 251 without).
- **Size effect of the vsnprintf, exit, TAB and file changes** (`bench/run.py -c uc80 -m size`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`, against the tree before): no program changes status. The image total goes from 794,239 to 798,701 bytes (+4,462, +0.56%). 62 programs are 60 bytes bigger (the TAB code of `__conout`), 3 are 73 bigger, 6 are 149 bigger (the 60 bytes and 89 bytes for `fputc` and `fprintf` on a stream, with files that are never opened), 7 are 53 smaller (they link the record writer of `fclose` or `fflush`, which was inside the module of `fputc`, and is a module of its own now), and 31 are the same. No program links `fopen`, so the BSS is the same for all. Programs that print a lot are 0.1% to 0.3% slower (`invaders` 994,106,550 to 996,610,003 T-states).
- **`printf` of floats is exact and keeps the field width.** `printf("%5.2f", x)` and `%10.3f` lost the width, the `+`, space and `0` flags did nothing for floats, a tie rounded away from zero, and the last digit could be one off. All six float conversions (`%f %F %e %E %g %G`) are now in `lc_prt_float.mac`. The code makes the exact decimal digits of the float with integer arithmetic (it doubles or halves a digit string) and rounds to nearest, a tie to even, so the text is the text of the host `printf` for the same float value, for every precision, for subnormal numbers, `inf`, `nan` and `-0.0`. `lc_printf_e`, `lc_printf_f` and `lc_printf_g` are gone. The float runtime is no longer linked for `printf`: `printf("%f")` goes from 3,979 to 2,807 bytes, and a program with `%f %e %g` from 6,128 to 2,841. The code uses 214 bytes of RAM. The c-testsuite expectations of 00174 and 00195 (`tests/c-testsuite-z80/*.expected`) held the old inexact digits (`-44.439998` and `56.779998`); the exact values are `-44.439999` and `56.779999`. Tests: `tests/test_printf_float_exact.py`; the audit compared more than 20,000 generated combinations of format and value with the host `printf`.
- **Size effect of the printf, exit and file changes** (`bench/run.py -c uc80 -m size`, 109 programs without `battlestar-size`, `rogue36-size` and `empire-trefonas`, against the tree before): no program changes status; the image total goes from 802,812 to 794,242 bytes. Nine programs that print floats are smaller: `v7-fish`, `v7-wump` and `fuzix-startrek` by 2,866 bytes each, `bsd-arithmetic` by 782, `pi` and `davidly-tpi` by 4. 78 programs are 1 byte bigger (the crt0 jump through `__exit_hook`). 12 programs that call `exit()` are 8 or 9 bytes bigger, 9 programs that use `fputc` or `fputs` on a stream are 35 bytes bigger (the record logic of `fputc` and the exit code), and `trek-size` is 340 bytes bigger (`fseek` and `ftell` now do the work of all three whence values).
- **printf audit** ([docs/printf.md](docs/printf.md)). `%hhd %hhu %hhx %hho` narrow the argument. `%td` works. `%lf` works without any long conversion in the program (the table had no `l` entry, so `%lf` was printed as text). `%n` stores the count. `%a` and `%A` print hexadecimal floats. `%llo` and `%llX` work. `%p` prints `0x` and the hex digits (`0x0` for a null pointer, it was `0`). `ptrdiff_t` was not defined when `<stddef.h>` came after `<stdio.h>`. A field width or a precision above 255 is 255 (deliberate, documented). `%jd`, `%ls` and `%lc` are not supported. The code generator writes the dispatch table and does not know the new entries, so the rarely used parts are modules of their own and the new `src/uc80/printf_hook.py` adds a table entry when a format string of the program needs one (the method of the float part of scanf): a program that does not use them links none of them, and `printf("%d")` is 1 byte bigger (crt0). The compiler warning "no handler in this libc" is removed for the conversions that the hook handles, and the warning that `%*d` is not implemented is removed, because `*` works.
- **`main` returning runs the `atexit` handlers and closes the open files** ([docs/exit_and_files.md](docs/exit_and_files.md)). Before, crt0 jumped to the warm boot, so the output of an `atexit` handler was missing and a file that the program wrote but did not close was empty. crt0 now jumps through the word `__exit_hook`, which `atexit()` and `fopen()` set; a program that uses neither is 1 byte bigger. `atexit()` takes 32 functions (4 before). `exit()` closes the open files. CP/M 2.2 has no exit status: the status of `exit()` and `main` is not used.
- **`ftell` and `fseek` are exact** ([docs/exit_and_files.md](docs/exit_and_files.md)). `ftell` was wrong after any read (it read a field of the FCB that sequential reads do not set) and never added the position in the buffer. `fseek` took only `SEEK_SET`, only 16 bits of the offset, and it lost the buffer of a stream that writes. Files are now read and written with the random access BDOS calls (33 and 34), so `SEEK_SET`, `SEEK_CUR` and `SEEK_END` work up to 8 MB, for reading and for writing, and `fflush` no longer leaves a Ctrl-Z in the middle of the file. `fgetpos`, `fsetpos` and `rewind` use them. `fopen` mode `a` still makes a new file. Tests: `tests/test_file_position.py`, `tests/test_atexit_exit.py`.
- Float comparison follows IEEE 754 for a NaN: `==`, `<`, `<=`, `>` and `>=` are false and `!=` is true when an operand is a NaN, in values, `if`/`while`/`for` conditions, `!`, `&&`, `||` and `?:`. `__fcmp` returns Z and C both set for an unordered pair (an ordered compare never gives that pair), and the code after the call tests the pair (`==` and `!=` and `<` and `<=` gain one or two `jr`; `>` and `>=` need no change). `__fcmp` tests the exponent of both operands first (about 59 T-states more for each call, 57 bytes more in the runtime, 0 to 4 bytes more at each comparison). Measured on the benchmarks: image +59 to +71 bytes and T-states +0.2% (`mandelbrot` +0.22%, `whetstone` +0.18%, `fasta` +0.73%, `bsd-caesar` +0.10%, `snake` +0.12%) for every program that compares floats, and `binary-trees` +4818 of 161.6 million. No status changes. `tests/test_float_nan_compare.py`. The library modules that compare floats (`lc_fdim`, `lc_fmax`, `lc_fmin`, `lc_strtod`, `lc_remquo`, `lc_m_toll`) are generated again.
- `sizeof('a')` is `sizeof(int)`, and `typeof('a')` and `_Generic('a', ...)` see an `int` (C23 6.4.4.5); a plain character constant was a `char` in all three. `L'a'` and the other prefixed forms are unchanged. `tests/test_c_type_corners.py`.
- C23 `auto` takes the type of its initializer: `auto x = 1.5;` is a `double` (it was an `int`), and likewise `float`, `long`, `unsigned`, `long long`, a pointer (an array decays), a struct, a member (`auto u = s.b;`), a character constant (`int`), with or without a `for` declaration. The type is the unqualified type of the initializer. `typeof(s.m)` and `auto x = s.m` of a struct defined in the same file stopped with an internal error ("reserved 14 bytes but used 16") because the pass that resolves `typeof` did not know the struct yet; it registers the struct now.
- Fix (repair of stage C): an inlined `uint16_t` parameter given a plain decimal literal in 0..32767 (`f(300)`) kept the signed literal, so `-x`, `~x`, `2 - x` and `(-x) > 100` ran signed. Only a `U` literal is left as the argument; any other literal gets the `Cast` to the parameter type.
- Fix: `auto s = "str";` is a `char *` (the initializer is a list of string literals, which the `auto` pass skipped, so `s[1]` read a 16-bit word). `auto fp = f;` with a function designator is a pointer to function (was a link error). `auto a = 1, b = 2.5;` deduces each declarator on its own (the declaration is split). `auto *p = ...` is an error: a plain identifier is required.
- Fix: `sizeof(typeof(x))`, `sizeof(typeof(s.m))` and a cast to `typeof(x)` use the type of the operand (they gave the size of an `int`). The constant evaluator for `(int)sizeof('a') == 2` uses the same size as the code generator (an `int`).
- An inlined function with a `uint16_t` (or other 16-bit unsigned typedef) parameter and a decimal literal above 32767 as the argument (`f(40000)`) kept the literal as a `long`, so `x + x`, `x << 1` and `(long)(x + 1)` were 32-bit (80000 instead of 14464, 65536 instead of 0). The literal is converted to the parameter type now; a literal that has a `U` suffix or a hex form was right already.
- `typeof(int (void))` and other `typeof` of a function type name parse (the retry rewrite of uc_core handles a parameter list; `typeof(int (*)[3])` is still not handled).
- `const int *p = (const int[]){1, 2};` in a function: not reproduced on this tree (it compiles and runs with the default settings, `--target=rom`, `-O0`, `--no-ast-optimize`, `--int=32`); the old note in `docs/rom_target.md` is corrected and the case is pinned in `tests/test_c_type_corners.py`.
- Fix: `arr->m` with a bare array operand (a global, local or static array, a member array, `arr[i]->m` of a 2D array, `(c ? a : b)->m`) read and wrote the member at the start of the element: the address code took the struct only from a pointer type, so the member offset was dropped. This also made an inlined call `f(gs)` for a struct-pointer parameter read the wrong member (ccfuzz seeds 80548 and 90258). `tests/test_arrow_bare_array.py`.
- Fix: a cast to a pointer to a typedef name kept the name opaque, so `(**(F *)p)(x)` loaded code bytes, and `((PT *)p)->b`, `(*(PT *)p).b`, `(*(A3 *)p)[i]` lost the member offset or the element type. The typedefs behind a cast type are now resolved through pointers, arrays and function types. `tests/test_cast_typedef_pointee.py`.
- Fix: the 16-bit negation `-x` used DE as scratch. A register call keeps argument 2 in DE while argument 1 is computed, so `pr(-g, h + 1)` passed the un-negated `g` as argument 2 (found by difftest seed 910956). The negation now uses A only. `tests/test_neg_first_arg.py`.
- Fix: `uc80 m1.c m2.c`: an enumerator that the two files define with different enums (`BLUE` is 6 in one file and 100 in the other) now has a private name in each file, so the constant initialisers of static data, array sizes and code use the values of their own file. `tests/test_multi_file.py`.
- A call written `(**f)(x)`, `(***f)(x)` or `(**tab)(x)` through a function or a function pointer loaded two bytes of the function's code and called that address. The type of `*g` for a function `g` was unknown, so the dereference of a function designator was a memory load. `*g` now has the type of `g` and `*` of a function designator is a no-op, as in the C rules. `(*f)(x)`, `(&g)(x)`, `s.fp(x)`, `(*s.fp)(x)` and arrays of function pointers already worked. `tests/test_funcptr_call.py` runs every spelling.
- `(gs + i)->m` (an array plus an integer, then `->`) read the member from the start of the element: the type of `gs + i` was the array type, so the offset of `m` was left out. The sum now has the pointer type. `tests/test_array_plus_index_member.py` runs it (ccfuzz seed 669, wrong in every variant but `--no-inlining`).
- `uc80 a.c b.c`: two files with a `static` function or variable of the same name shared one object, and a typedef or struct tag defined differently in two files (`typedef int T;` and `typedef long T;`) shared one type. New `src/uc80/multi_unit.py` gives each file its own private name (`helper__s0`, `T__t1`) before the merge, follows block scope (a parameter, a local or a `for` declaration that hides the file-scope name keeps its name) and leaves typedefs and tags that the files define identically (the same header) alone. `tests/test_multi_file.py` runs two programs of two files.
- `bench/ccfuzz.py` has `--no-ast-optimize` in its variants again, and `--exact` (use only the `--flags`). Seeds 10140, 10657 and 11636 with `--no-ast-optimize` pass; `tests/test_ccfuzz_seeds.py` pins them and seed 40035 with `-O0`.
- Repair pass for the wide-integer fixes (tests in `tests/wide_int/`: `incdec_ll_binary`, `inline_narrow_param`, `bool_static_addr`, `cse_cast_types`, `cast_ll_literal_arg`). (1) `++` and `--` of a `long long` as the left operand of a binary operator (`x++ * 2`, `a++ + g()`) lost the right operand: the increment stages its constant 1 in `__tmp64`, and `_uses_tmp64` did not know that. (2) An inlined function whose parameter has a typedef of a 16-bit type (`I16`, `uint16_t`) did not cut a wider argument to the parameter type (`r(I16 x){return x;}` with `r(0xfffffff0UL)`); the cast is now made unless the argument is known to be a 16-bit int of the same signedness (a variable, element, literal or cast), and the same holds for a result of such a typedef (`U16 f(U32 a){return a;}`). The size and speed benchmarks are unchanged except fuzix-startrek (+4365 T-states of 40.1 million). (3) A `_Bool` object with a static initialiser that is an address (`arr`, `arr+1`, `m[1]`, `(_Bool)arr`, `(long)arr`, `&a[3]-&a[0]`, `arr != 0`, `c ? x : y` of constants) is 1 and not the low byte of the address. (4) A long long literal under a narrowing cast as an argument (`(ULL)(char)27187LL` for an `int` parameter) was passed as 27187; the cast now cuts the value. (5) uc_core: the CSE key of a cast did not name the target type, so `(unsigned long)(char)p & ...` and the same expression with `(short)p` were one common subexpression (ccfuzz seeds 22164 and 22287). (6) `bench/ccfuzz.py --ll` also makes `GX++`-style operands in binary expressions and arguments that are wider than the parameter, without a cast.
- Wide integers (`tests/test_wide_ints.py`, programs in `tests/wide_int/`). `bench/ccfuzz.py --ll` found 15% wrong programs, with every register convention; it now passes 3000 programs (seeds 1 to 2000 and 5000 to 5999). Causes: (1) `gen_expr` of a `long long` literal loaded only DEHL, so `(LL)p` of a constant argument, an inlined `long long` parameter above 32767 and a nested cast of a literal left `__acc64` stale; the literal now fills `__acc64` too. (2) `~` and unary `-` of a `long long`, and the value of a `long long` assignment, left DEHL stale. (3) A `long` (or narrower) initialised from, assigned from or returned from a `long long` expression sign-extended HL over the high word in DE. (4) `?:` with a 64-bit arm and a narrower arm built a 16 or 32-bit value. (5) `switch` on a `long long` compared 16 bits. (6) A `long`, `int` or `char` argument for a `long long` parameter was zero-extended when the parameter was unsigned; the source decides. (7) `op=` on a `long long` lvalue with a side effect (`a[i++] += 5`) evaluated the lvalue twice. (8) Local initialisers of `long long` array elements and struct members stored 2 bytes; a compound literal of `long long` elements reserved too little data. (9) `(LL[]){...}` as the initial value of a pointer was an internal error. (10) The type of an integer literal that does not fit an `int` and has a `U` suffix or a hex form (`0xffffffffU`, `2147483648u`) was taken as `unsigned int`; as a `long long` argument it became 65535.
- `(u + 65535u) - 256ULL` was folded to `u + 65279`, which skips the wrap of the `unsigned int` addition. The nested constant fold (`(x op c1) op c2`, with `+ - *`) now needs c1 and c2 of the same width (also in uc_core, which uc386 shares). `bench/difftest.py` found this: uc80 and SDCC agreed and the model did not.
- `_Bool`: a value is converted by testing every byte of it, for an `int`, `long`, `long long`, pointer or float argument, parameter, return value, initialiser, assignment and compound assignment. `(_Bool)0x100` and `(_Bool)0x100000000LL` were 0 (a `_Bool` function without inlining returned the low byte; a `_Bool` array, struct or global initialised with 256 held 0; a `long long` was tested by its low 32 bits). `(_Bool)-0.0` is 0.
- `bench/difftest.py` has a `llong` focus (and `long long` in `all`): `long long` and `unsigned long long` variables, constants, arrays, struct members and parameters; the status is `UC80-WRONG` when SDCC cannot build the program and uc80 differs from the model.
- scanf `%f` is correctly rounded for a mantissa of any length. Before, the scanner dropped all mantissa characters past 119 without a correction, so a long number could round wrong or get the wrong magnitude. `tests/test_strtod_exact.py` now tests `atof` and `sscanf` too.
- `strtod`, `strtof`, `strtold`, `atof` and scanf `%f` round every decimal string correctly, also a string with more than 19 digits that is close to the middle of two floats (before, such a string could give the neighbour: `2.52540519340982427820563316345214843751e-6` was one unit too low, and a string just above 2^-150 gave 0 and not the smallest subnormal number). The converter uses 64-bit arithmetic and, for a string within 2^-48 of a tie, compares the digits with the tie in big integers (`lc_strtod.c`). Cost for a program that links `strtod`: +708 bytes of code and +170 bytes of RAM (`atof` program 6059 to 6767 bytes, `sscanf("%f")` 8422 to 9130). Test: `tests/test_strtod_exact.py`.
- `__fdiv` gives the IEEE result for an infinity or NaN operand and keeps the sign of a zero numerator (`inf/2` was a huge finite number, `inf/inf` was 1.0, `0/-1` was `+0`). Float comparison with a NaN was non-IEEE when this entry was written; the NaN compare entry above in this section (Float comparison follows IEEE 754) replaces this statement.
- `strtol`, `strtoul`, `strtoll`, `strtoull` and scanf `%i` accept the C23 prefix `0b`; scanf has the `%b` conversion.
- The tests use the `src` directory of their own checkout (`PYTHONPATH` set in `tests/conftest.py`).
- `?:` after a side effect in the condition: `int a=v; return (a++ > 0) ? a : 99;` read `a` as `v` (uc80's own `?:` walk skipped the sequence point after the condition). uc80 now uses uc_core's walk and keeps only its typed constant fold (`_fold_ternary`). Needs the uc_core copy-propagation fix.

- **scanf family rewritten in C** (`src/uc80/lib/csrc/`, generated into `lc/` by `gen_c_modules.py`). `sscanf`, `fscanf` and `scanf` now do `%f %e %g %a` (and `%lf`, `%Lf`), `%n`, widths (`%3c%d`), `%*` suppression, `%[...]` scan sets, `%%`, `%i` with `0x` and `0` prefixes, `%o %u %x` with signs, `%hhd %hd %ld %lld %zd %jd %td`, `%p`, and return EOF when the input ends before the first item. New `vscanf`, `vfscanf` and `vsscanf`. `sscanf` and `vsscanf` no longer link BDOS code.
- `strtod`, `strtof`, `strtold` and `atof` rewritten: the result is correctly rounded for every decimal string (the old code was up to 20 units in the last place off, and `1e-38` or `99999999999` were far wrong), and `inf`, `infinity`, `nan`, `nan(chars)` and hex floats (`0x1p3`) work. The end pointer is the input pointer when no number is read. `errno` is `ERANGE` on overflow and underflow.
- `strtol` and `strtoul` rewritten: bases 0 and 2 to 36, `0x` prefix only before a hex digit, overflow gives `LONG_MAX`, `LONG_MIN` or `ULONG_MAX` with `errno == ERANGE`, a bad base gives `EINVAL`.
- `errno` is its own module (`lc_errno`), so `strtol` stays in the ROM library.
- **Float `+ - / ` and int-to-float round to nearest, ties to even.** `__fadd` and `__fsub` dropped the bit they shifted out (so `x - y` was inexact when the exponents differed by one), `__fdiv` made only 23 bits when the dividend was smaller than the divisor (a result could be 2 units in the last place off), and `__itof`, `__ltof`, `__uitof` truncated (`(float)50066279` was one unit low). `__fadd` also handles infinity and NaN operands, and `x/0` is `+-inf` with the sign of `x`, `0/0` is NaN.
- **libm rewritten** (programs for a float interpreter in `src/uc80/lib/vmsrc/`, C in `src/uc80/lib/csrc/`). Every `math.h` function is now within 2 units in the last place of the exact result (the old library was off by 5 to 10^9 for many arguments), handles infinity, NaN, signed zero and the C99 special cases, and reduces the argument of `sin`, `cos` and `tan` with Payne-Hanek, so `sin(1e30)` is right. `sqrt`, `cbrt`, `fmod`, `remainder`, `floor`, `ceil`, `trunc`, `round`, `hypot` are correctly rounded or exact; `pow` is exact when the result is a float (`pow(3,2)` is 9) and within 1 ulp otherwise. Fixed: `floor`, `ceil`, `trunc`, `round` clamped to +-2^31; `cbrt(27)` was 3.00007; `sinh(0)` and `tanh(0)` were `-inf`; `atan2` was wrong in the second and third quadrant; `acos(-1)` was 0; `exp(100)` was 2e-34; `modf` of a negative number and of a large number; `frexp(0)`; `ldexp(0, n)`; `remainder`; `log` near 1.
- **The float part of `scanf` links only when the program needs it.** The compiler adds a reference when a scanf call has a float conversion in its format, a format that is not a literal, or when a scanf function is used without a call (`scan_hook.py`). `sscanf("%d")` is 1.3 KB (1.5 KB with the old library), and 7 KB with the float part.
- `printf("%f")`, `%e` and `%g` print the same digits as before with the rounding float runtime. The `%f` printer adds its pre-round term with rounding toward zero (`printf("%f", 12.34)` had become `12.340001`), and the `%e` printer no longer adds an ulp to make up for a truncating runtime. c-testsuite 00174 expects `sin(2)` as `0.909297`, the correct value.
- `__fadd` is 511 bytes (was 439 bytes) and `__fdiv` 385 bytes (was 432), so float programs that only add, subtract and divide change by +72 to -47 bytes. `sscanf` and `vsscanf` are in the ROM library.
- `sqrt` is correctly rounded and 259 bytes: an integer digit-by-digit root in assembly. The old one was off by one ulp in 25% of the cases.
- `fma(1e20, 1e20, -inf)` is `-inf` (it was NaN: the product overflowed before the sum). An infinite third argument is returned when the first two are finite.
- **The math functions that do float arithmetic are programs for a float interpreter** (`src/uc80/lib/vmsrc/*.vc`, made into `lc/*.mac` by `gen_vm_modules.py`, `docs/libm_vm.md`). `sin cos tan asin acos atan atan2 sinh cosh tanh asinh acosh atanh exp exp2 expm1 log log2 log10 log1p pow hypot fma` and their kernels were C code, 25 to 40 bytes for each float operation; a float operation is now one byte. The results are bit for bit the same as the results of the C versions, except `pow`, which has a table of 16 values instead of 32 (still within 1 ulp). A program that calls `sin` is 3307 bytes (5550 before, 2748 with the library before the C rewrite), `pow` 4211 (8075), `atan2` 3639 (5604), `exp` 2869 (3291). `whetstone` is 10135 bytes (14252 before, 9649 with the old library), `binary-trees` 5454 (9318, 4422), `trek-size` 66928 (76111, 65540), `bsd-caesar` 9623 (10116, 8724). The interpreter makes `whetstone` 21% slower than the C versions (788 M T-states, 654 M before; the library before the C rewrite needed 1226 M).
- The scanf engine (`lc_scan_core`) and the integer reader of `strtol` (`lc_strtoint`) are assembly; the conversions `%c`, `%s` and `%[` are a module of their own that the compiler links when a format has them (`--scanf text`). `sscanf("%d")` is 1312 bytes, `sscanf("%s")` 1605. `_m_pack`, `_m_unpack` and the Payne-Hanek reduction are assembly (`_m_reduce` was 1614 bytes in C, it is 111 bytes and 402 bytes of `lc_m_phred`). `scalbn` and `ldexp` are assembly and do not link `_m_pack` and `_m_unpack`.
- `rint`, `nearbyint`, `lrint`, `llrint`, `lround`, `llround`, `remquo`, `nexttoward` and their `f` and `l` forms are new (`math.h` declared several of them but the library had none).

### Added

- **Naked functions: SDCC `__naked` and `[[uc80::naked]]`** ([docs/rom_hardware.md](docs/rom_hardware.md)). A naked function has no prologue, no epilogue, no frame and no `RET`: the body is the asm of the programmer. The body may hold only `asm(...)` statements, `__asm` blocks and empty statements; any other statement is an error with the line number. Parameters use the stack convention (first parameter at `(SP+2)` at entry). A naked function can be a vector target of `#pragma uc80 interrupt` or `nmi`; the compiler then writes nothing around the body. Never inlined, never given a constant parameter, never in the register convention; the call graph follows each `_name` in the asm text. Tests: `tests/test_naked.py`, `tests/test_sdcc_compat.py`, `tests/test_rom_interop.py` (sdas flow).


- **Watchdog hook for the ROM target.** A program that defines `__watchdog_kick` (a C function with `#pragma uc80 public _watchdog_kick`, or an assembly file) gets a call of the hook at reset, before `main` and before each `HALT` of the idle loop. A program without the hook has no call and no extra byte. Tested in `bench/romrun` with a port that logs the kicks (`tests/test_rom_target.py`, `TestWatchdogHook`). See `docs/rom.md`.
- **Interop with SDCC and nullsound** (Phase 5 of `docs/rom_target.md`; see `docs/rom_interop.md`):
  - SDCC `__asm ... __endasm;` blocks, at statement level and at file level. The prepass in `sdcc_compat.py` turns the sdas text of a block into an `asm("...")` statement with MACRO-80 text (`#imm`, `N(ix)`, `.db`, `.dw`, `.ds`, `.ascii`, `.globl`, `1$` labels, `0x` numbers). A directive or an operand it cannot translate is an error with the line number.
  - `--asm-syntax=sdas` (with `--sdas-style=sdcc|ngdevkit` and `--sdas-areas`): the compiler writes sdas text for `sdasz80` and `sdldz80`. New module `src/uc80/sdas.py` (with `asmexpr.py` and `sdas_inline.py`). The default output does not change. The translator keeps the `JR` range rule of um80 (a far `JR` becomes `JP`), splits DSEG into an image area and a RAM area, and maps the vector area to an absolute area (marker `;@RELOC` in `crt0_rom.mac`).
  - An `__asm` block as the unbraced body of `if`, `else`, `for`, `while` or `do` gets braces. `link_flags(heap=True)` defines `__END__` for a program that links malloc. In the ngdevkit style, uc80 warns when an `--entry` function reaches `EXX` code and when the module has initialised globals. `bench/rom/nullsound.py --irq-period T` injects timer interrupts into the nullsound proof (command 06h starts timer B).
  - `uc80 --build-sdas-libs DIR` translates and assembles the runtime and libc into `uc80_rt.lib` and `uc80_lc.lib` for `sdldz80 -l`.
  - `bench/rom/sndrom.py --sdas` and `bench/rom/isrproof.py --sdas`: the snd-* kernels and the interrupt proofs link with `sdldz80` to the same images, sizes and T-states as the `ul80` build.
  - `bench/rom/nullsound.py`: builds nullsound (ngdevkit commit pinned, sha256 checked, fetched into `bench/_cache`, not vendored) and links it with C commands from uc80 (`ns_cmds.c`, `ns_user.s`). The ROM runs in romrun and the YM2610 writes equal the expected sequence.
  - Tests: `tests/test_sdas.py` (every library module and every benchmark program translate and assemble, the instruction-size table equals `sdasz80`), `tests/test_sdcc_compat.py`, `tests/test_rom_interop.py`. They skip without the SDCC tools.
- **Register calling convention, step 1** (family F of `docs/codegen_gap.md`; rules in `docs/calling_convention.md`). In whole-program mode, a function that only direct calls reach takes its first two scalar parameters in registers: parameter 0 in A (byte) or HL (word), parameter 1 in E (byte) or DE (word). Other parameters are pushed as before, and the caller does not pop the register parameters. A function qualifies when its name occurs only as the callee of direct calls, no assembly (inline, file scope, appended `.mac`, library sources) mentions the name, and it is not `main`, variadic, an entry point (`--entry`, `#pragma uc80 keep`, `public`, `vector`), an interrupt function or reachable from one, or a function that returns a wide struct. Every other function keeps the stack convention. The callee stores each parameter that the body names into `??AUTO` or, in a stack frame, pushes it in the prologue (recursion saves the parameter per activation). The pass `regcc.forward_slots` replaces the loads that a register still serves and deletes stores that nothing reads, so a small leaf like `out(port, v)` is `add A,E / ld (a),A / ret` with no IX frame. `--no-register-cc` turns the convention off. Measured against `--no-register-cc`: snd-cmdq 13.72M to 10.72M T-states, snd-ymreg 21.21M to 17.87M, snd-seq 14.52M to 12.34M, snd-fnum 13.69M to 11.06M, stanford-perm -19%, stanford-towers -19%, dhrystone -13.5%, tak -6.6%; 82 of 104 programs change, none gets bigger (total image -19,114 bytes). Geometric mean against SDCC 4.6.0: size 0.9757 to 0.9484, T-states (speed mode) 0.6228 to 0.6020.
- `bench/ccfuzz.py`: a calling-convention fuzzer. Random programs with many small functions (0 to 4 parameters of char, short, int, long, unsigned, pointer and struct pointer type; recursion and mutual recursion; calls in arguments, conditions, loops and `?:`; function pointer tables; variadic helpers; static and extern definitions) run on the host and on uc80, and the checksums must agree. `tests/test_register_cc.py`.
- `bench/run.py` honours `UC80_BENCH_RESULTS` (write the rows to another file), and the uc80 adapter honours `UC80_BENCH_FLAGS` (extra uc80 options), for A/B runs.
- docs/neogeo_driver.md and examples/neogeo_driver.c: a Neo Geo sound driver example (NMI command, timer IRQ, YM busy wait, bank select) with the port table and a test that runs the example in romrun.
- **Interrupt functions and hardware access** (Phase 2 of `docs/rom_target.md`; see `docs/rom_hardware.md`):
  - `#pragma uc80 interrupt NAME [critical]` and `#pragma uc80 nmi NAME` make a C function an interrupt function: `EI` first (for `interrupt`), `PUSH AF, BC, DE, HL, IY`, the matching pops, then `RETI` or `RETN`. The compiler sets the INT (38h) or NMI (66h) vector, keeps the function, and refuses parameters, a return value and a direct call. The prologue saves the alternate registers only when the call graph reaches code that uses them. `asm_dce` knows that `RETI` and `RETN` end a block.
  - SDCC spellings `void f(void) __interrupt`, `__interrupt(n)`, `__critical __interrupt[(n)]`, `__sfr __at(n) NAME;`, `__sfr __banked __at(n) NAME;`, `T name __at(addr);` and `__critical { }` (prepass `sdcc_compat.py`). Every other SDCC spelling is still an error.
  - `<z80.h>`: `z80_out`, `z80_in`, `z80_out16`, `z80_in16` (BC is the whole port), `z80_di`, `z80_ei`, `z80_irq_save`, `z80_irq_restore`, `z80_critical_enter`, `z80_critical_leave` and `z80_load16` (a 16-bit read that an interrupt cannot tear). The compiler writes each in line, and no optimizer pass moves, merges or removes one.
  - `T name __at(addr)` and `[[uc80::at(addr)]]` make an object at a fixed address with no storage. A volatile integer object at a constant address (`*(volatile unsigned char *) 0xF900`) becomes the same kind of name, so it uses `LD A,(nn)` and `LD (nn),A`. This also changes CP/M code that has such an expression.
  - `--isr-safe` keeps the locals of functions that an interrupt function reaches on the stack. Without it, the compiler warns when a function that an interrupt function and other code both call uses the shared `??AUTO` area. The compiler also warns, with the call chain, when an interrupt function reaches a routine with work areas at fixed RAM addresses.
  - `bench/rom/isrproof.py`: an NMI command queue, a YM2610 timer interrupt that drives the sequencer, and a bank select with `z80_in16`, each compared with the expected port log. Tests: `tests/test_rom_isr.py`, `tests/test_rom_hw.py`, `tests/test_rom_isr_proof.py`.
- ROM target: a warning for each `#pragma uc80 vector`, because a C function has no RETI. Tests for const objects in two C modules and for an assembly module that reads a const array.
- **Const data in the ROM** (`--target=rom` only): const objects, string literals and const compound literals go to CSEG, so the start-up code does not copy them to RAM. RAM of the four snd-* kernels drops by 33 to 232 bytes; ROM size and T-states stay the same. The CP/M output is byte-identical. See `docs/rom.md`.
- **Roots and vectors for the ROM target**: `--entry NAME` (repeatable) and `#pragma uc80 keep NAME` keep a function the code never calls; a ROM program with no `main` starts at the first `--entry`; `#pragma uc80 vector 38h NAME` and `66h NAME` make the INT and NMI vectors jump to a C function; `#pragma uc80 public NAME` gives a static name or an unused BSS object a PUBLIC label. BSS objects of the ROM target keep their PUBLIC label. uc_core records `#pragma uc80 ...` lines in `Preprocessor.target_pragmas`.
- `strtoll`, `strtoull`, `atoll`, `strtoimax` and `strtoumax`.

- `tests/conftest.py` builds `libc_rom.lib` and `crt0_rom.rel` when a checkout lacks them. The ROM tests fail, not skip, if a library is missing.
- **ROM target (Phase 1 of `docs/rom_target.md`): `uc80 --target=rom`** (alias
  `-ffreestanding`). The compiler embeds `lib/crt0_rom.mac` (reset vector,
  RST stubs, INT and NMI vectors, SP from `__RAM_END` or `--stack-top`, copy
  of the initial data from ROM, BSS clear, `HALT` loop after `main`) and
  refuses `exit`, `abort`, console and file I/O, the heap, `main(argc, argv)`
  with a clear error. A `__int_handler` or `__nmi_handler` label in an
  assembly input file becomes the INT or NMI vector. Link with the ROM options
  of `ul80`. New `libc_rom.lib` (93 libc modules with no BDOS use) is built by
  `uc80 --build-libs`. See `docs/rom.md`.

- **The four snd-* kernels as ROM images** (`bench/rom/sndrom.py`): real `OUT`
  and `IN`, debug port output, run in `bench/romrun`; the decoded output is the
  CP/M golden output. All four fit the 2 KB RAM of the Neo Geo sound CPU.
  `tests/test_rom_target.py` runs one kernel end to end.

- **Strict mode for the interrupt and fastcall markers.** uc80 asks the uc_core
  frontend to refuse `__interrupt`, `interrupt`, `__interrupt(1)`,
  `[[gnu::interrupt]]`, `__attribute__((interrupt))`, `__critical __interrupt`
  and bare `__fastcall`, with `uc80: error: file:line: uc80 does not support
  '...' yet` and exit code 1. They were erased without a message before.

- **`bench/romrun`, a Z80 ROM test runner** (Phase 1 of
  `docs/rom_target.md`). The runner starts a raw ROM or Intel HEX image at
  address 0 and logs every port IN and OUT with the full 16-bit address and
  the T-state. The runner injects IM 1 interrupts and NMIs (EI delay, RETN),
  models banked windows, ROM write faults, a YM2610 timer model, scripted
  port reads and cycle spans between symbols. The core counts T-states
  exactly; the count matches z88dk-ticks on the benchmark programs. See
  `docs/romrun.md`.

- **romrun repair pass**: `ram_fill` and `sp` (start state of RAM and SP,
  so a broken crt0 fails), `peek8`/`peek16`/`peek` and `--dump`,
  `--dump-ram`, `track=True` (lowest SP and RAM written), `nmi_gate=True`
  (Neo Geo NMI enable port 08h and disable port 18h) and a golden file of
  the per-opcode timing sweep against z88dk-ticks
  (`bench/romrun/golden/ticks_sweep.txt`). The random-stream test no longer
  produces an indexed instruction without the displacement byte.

### Changed

- The runtime library (`lib/rt/`) and the math, `strtod` and `scanf` parts of libc (`lib/lc/`) hold one module for each entry point, or for each group of entry points that share a body. `rt_arith16`, `rt_arith32`, `rt_arith64`, `rt_float`, `rt_complex`, `rt_mul`, `rt_div8`, `rt_setjmp`, `lc_atof`, `lc_scanf`, `lc_floor`, `lc_fabs`, `lc_fmax`, `lc_fmod`, `lc_fpclassify`, `lc_frexp`, `lc_logb`, `lc_math_helper`, `lc_atomic` and `lc_thread` are split. `runtime.lib` has 104 modules (was 11) and `libc.lib` has 250 (was 177). The image of no bench program grows; 58 of 109 get smaller (817,781 to 814,805 bytes in total). See `docs/libraries.md`.
- `runtime.py` no longer reads a comment line as initialized data, so a `DS` block at the end of a module stays in the BSS of the embedded runtime.

- **Zero-start runtime and libc variables are in the BSS.** `__tmp32`,
  `__divrem`, `__div32_dvd`, `__rem32`, the 64-bit, float and complex
  temporaries, `__sret_buf` and the static variables of libc are in a named
  COMMON block for each module instead of DSEG. A ROM image does not copy them,
  and a CP/M `.com` file no longer holds their zero bytes.

- **Unsigned 8-bit divide and modulo by a constant 1 to 255 (speed family D).** When the left operand is known to be 0..255 (an unsigned char, a masked value, another such quotient), `x / K` and `x % K` no longer use the signed 16-bit chain `__sdiv16 / __div16 / __dv8w / __dv8n`. A power of two is a shift or a mask. For 192 of the other 247 divisors the quotient is an exact reciprocal multiply `(x * M) >> S` (inline shift-and-add), and the remainder is `x - q * K`. The rest call `__dv8n` once. Two adjacent statements (or declarations) `q = x / K; r = x % K;` make one division. A signed operand stays on the signed path. The helpers that lose their last caller are not linked. `snd-fnum` runs 18.83M to 13.98M T-states and shrinks by 196 bytes; `snd-cmdq` runs 14.32M to 14.05M and shrinks by 196 bytes. See `docs/u8_divmod.md`.

- **Byte paths and peephole rules (speed family E).** Seven rules in the code generator and three in the new `src/uc80/peep_local.py`; `docs/byte_paths.md` has the list. The arguments for a `char` parameter of a function in the program go in L without extension (`ld L,K`, or a value computed in A). A `switch` on an int value known to be 0..255 compares one byte. A second operand `*p` or `p->m` is `cp (HL)`, not `push AF / ld E,(HL) / pop AF / cp E`. A byte comparison puts the variable on the `cp` side. A cast to a char evaluates the operand once. `(int8_t)x >= 0` and `< 0` are `bit 7,A`. A local `int` that only receives byte values is a byte value for compares and switch keys. The peephole pass deletes a dead `ld H,0`, turns `ld L,S / ld H,L / ld L,0` into `ld H,S / ld L,0`, and turns `call X` + `ret` into `jp X`. Bench (uc80 size mode, T-states and image bytes): `snd-cmdq` 14.32M to 13.72M and 3075 to 2798 B (with D), `snd-ymreg` 22.22M to 21.21M and 2470 to 2443 B, `snd-seq` 15.38M to 14.52M and 3939 to 3867 B, `snd-fnum` 18.83M to 13.69M and 2830 to 2598 B (with D), `davidly-ttt` 14.38M to 13.12M and 4210 to 3816 B. The mean size against SDCC 4.6.0 falls from 0.992 to 0.982 (together with the float fixes). The mean T-state ratio (speed mode, 78 programs) falls from 0.629 to 0.623. The tests that count `call X` in a function that ends in a call now also count `jp X`.

- **`bsearch`, `vprintf` and `vsprintf` are their own modules, and `rand` links no runtime multiply.** A `qsort`-only program is 192 bytes smaller, an `fprintf` program about 220 bytes smaller, and a `rand` program about 550 bytes smaller.
- **Smaller programs: libc is split into one module per function.** The
  float printer was part of `lc_printf_core`, so every program that called
  `printf` linked the soft-float code (about 2.8 KB). It is now
  `lc_prt_float.mac` and only `%f` links it: a `printf("%d")` program drops
  from 4343 to 1498 bytes. The modules that held several public functions
  (`lc_string`, `lc_mem`, `lc_stdlib`, `lc_atoi`, `lc_ctype`, `lc_wchar`,
  `lc_malloc`, `lc_file`, `lc_misc`, `lc_time`, `lc_puts`) are split so a
  program links only the functions it calls: `strlen` no longer pulls in
  `malloc`, and `exit` no longer pulls in `abort`. `uc80 --build-libs`
  removes the stale `.rel` files of removed modules.

- **`printf("text\n")` becomes `puts("text")` again.** The rewrite in
  whole-program mode had stopped matching after the AST change and never
  fired. It now handles adjacent literals and escapes, `printf("%s\n", s)`,
  only an unused result, and only when no other `printf`-family call is left.
  A hello program drops from 3,968 B to 256 B.
  `printf("text")` without a newline becomes `cputs("text")`.
- **Measured result of the library work.** In the size-mode benchmark run
  the mean size ratio against SDCC 4.6.0 fell from 1.53 to 0.99 (96
  programs OK on both; uc80 is smaller on 52 and bigger on 44). The T-state
  ratio is the same (0.63). The four `snd-*` kernels are now smaller than
  SDCC. See `docs/benchmarks_speed.md` and `docs/codegen_gap.md`.
- **Faster `qsort`.** `qsort` was a selection sort (n² comparisons, 37
  billion T-states for 5,000 equal ints) and sorted in descending order.
  It is now a non-recursive quicksort with a median-of-three pivot, a
  partition that stops on equal keys, insertion sort for parts of 8 or
  fewer elements, and a check for sorted or reversed input. 5,000 equal
  ints now take 2.6 million T-states. A comparator can call `qsort()`.
- **Faster runtime helpers.** 16-bit multiply is 3 to 5 times faster,
  16-bit divide and modulo about 3 times, 32-bit multiply and divide
  about 10 times, and long shifts up to 8 times. `strlen`, `strcpy`,
  `strcmp`, `memcpy` and `memset` are faster too. The new modules
  `rt_mul`, `rt_div8`, `rt_neg32` and `rt_tmp32` hold shared pieces, so
  a program links only the pieces it uses.

- **Code generator speed work (eight families).** Over the 61 programs that
  were OK on uc80 in the first benchmark run, the geometric mean of the
  size is now 0.83 of the first run. The mean of the T-states is 0.67 of
  the first run. The numbers below are the effect of each step when we
  made the step, measured with `bench/run.py`. Programs that the text
  does not name stayed the same or improved.
  - *Constants in arithmetic.* A multiply, divide, modulo or shift by a
    constant no longer calls a runtime helper in the common cases. 16-bit
    multiply uses a shift-and-add chain, unsigned divide and modulo by a
    power of 2 use shifts and masks, and 32-bit shifts use byte and word
    moves. `stanford-puzzle` 1,162 M to 861 M T-states (-26%),
    `stanford-intmm` -19%, `crc` -13%, `fannkuch` -7%. Total of 41
    programs -6.0%.
  - *Operands and byte values.* A constant or simple variable on the right
    of `+ - & | ^` goes straight into DE, and a value that is only used as
    a `char` is computed in A. `sieve` -19%, `micro+a8` -22%,
    `micro+loop` -22%, `snd-cmdq` -28%, `davidly-sieve` -19%.
  - *In-place update.* `x++`, `x--`, `op=` and stores to `char`, array
    element, member and `*p` work in place, and an unused value is not
    kept in HL. `sieve` 4.69 M to 4.41 M, `snd-cmdq` 20.1 M to 17.1 M,
    `fannkuch` 58.2 M to 51.5 M, `stanford-puzzle` 758 M to 720 M. The
    image of `micro` is 326 bytes smaller.
  - *Bit-fields.* A bit-field that lies in one byte is read and written
    as that byte, with shifts, masks and `bit n,(hl)`. `micro+bits`
    5.49 M to 1.39 M T-states (-75%).
  - *Frames and switch.* A function that never uses IX has no frame
    (68 T-states and 9 bytes less per call). A `switch` on a `char`, or on
    an `int` whose case values share a high byte, compares bytes, and
    dense cases use an 8-bit table. Every one of 41 images is 12 to 399
    bytes smaller. `embench-statemate` 5.64 M to 5.24 M, `zx0` 10.2 M to
    9.3 M, `snd-cmdq` 17.1 M to 16.1 M.
  - *32-bit values.* `+ - & | ^`, compares, casts, shifts and `switch` on
    a `long` read their operands straight into BC and A, with no call
    through `__tmp32`. `embench-md5sum` 7.07 M to 3.64 M (-49%), `crc`
    -41%, `embench-huffbench` -33%, `embench-crc32` -34%,
    `micro+a32` -25%.
  - *Addressing.* `x`, `s.m` and `a[K]` of a named object use `ld (nn)`
    and `(IX+d)` directly, `p[K]` and `p->m` use `inc HL`, a 16-bit truth
    test uses `ld A,(HL)` and `or (HL)`, and a word that is already in HL
    is not loaded again. `davidly-ttt` 16.4 M to 14.2 M, `snd-cmdq`
    15.7 M to 14.2 M, `queens` 369 M to 336 M, `statemate` 4.79 M to
    4.32 M, 40 of 41 programs faster.
  - *Conditions.* The code compares and tests a 16-bit variable in the IX
    frame without a register load (`ld A,(IX+d)` and `sub (IX+e)`). `tak` 48,229,623
    to 47,816,155, `embench-statemate` 4,324,681 to 4,315,073,
    `davidly-ttt` 14,229,879 to 14,197,127.

### Fixed

- **`typeof(x)` where `x` was declared with `typeof(type-name)`** was an
  int (`typeof(unsigned char) a; typeof(a) b;` gave `b` two bytes).  The
  operator tokens are now normalised before the typeof pass reads them.
- **Ordering of two negative floats was reversed** (`-44.0 < -1.0` was 0).
  `__fcmp` now compares the magnitudes the other way round when both signs
  are negative.
- **`**qf` of a `float **` read 2 bytes.**  The dereference size of `*p`
  was the size of `p`'s pointee type, a pointer; it is now the size of
  what `*p` points to.
- **An inlined function with a typedef'd parameter or return type**
  (`typedef signed char I8; I8 f(I8 c) { return c * 8; }`) was not
  narrowed; the result was 168 and not -88.
- **A float or double times a power of two was not multiplied.** `float nv =
  1.5f; float r = nv * 4;` gave 1.5 and `double d = 2.5; d = d * 8;` gave
  2.5. The AST optimizer turned `x * 2^n` into `x << n`, a shift of the
  integer value of the float. Also `x / 8u` (shift), `(x * 3) * 5` and
  `(x + 1) + 2` (an integer constant, wrapped), `x - x`, `x == x`, `x * 0`
  and `x + 0` were integer rewrites of a float. uc_core now skips them for any
  operand that may be floating (see the uc_core changelog), and
  `Uc80ASTOptimizer` leaves the power-of-two multiply to the code generator,
  which knows the type. Its integer code is the same or better: a multiply
  by 128 uses the 7-bit shift sequence, a multiply by a power of two that is
  stored in a char is computed in A, and `x * 2` no longer reads `x` twice
  (`(a % 2) * 2` computed `a % 2` twice). Over the benchmark programs 16 of
  them get shorter and none gets longer. New tests run float, double and long
  double, negative values, 2 to 2^14, multiply, divide and compound
  assignment (`tests/test_float_strength.py`).
- **`typeof(char_object)` was two bytes, and `typeof(volatile unsigned
  char)` did not parse.** Code generation took every `typeof` for `int`.
  uc80 now resolves the type of the operand (an object, a member, an
  expression, at file scope or in a function, before the call-graph analyzer
  sizes the declarations). The qualified type-name form `typeof(type-name)`
  parses (uc_core change).
- **A block-scope `extern` object had no `EXTRN`.** `void f(void) { extern
  int g; g = 1; }` with `g` defined in another unit emitted no `extrn _g`,
  so the link failed with an undefined symbol (or bound to a wrong one). The
  name is now an `EXTRN` when this unit does not define the object.

- **Three miscompiles in the inliner and the copy propagation.** (1) A
  small function that changed its by-value parameter (`v++`, `*p++`,
  `v += 3`) changed the variable of the caller when the call was inlined;
  the function is now not inlined. (2) An inlined call did not narrow the
  argument when the parameter type, or the return type, was a typedef name
  such as `u8` or `s8`. (3) Copy propagation ignored `++` and calls in the
  initialiser of a declaration, so `int a = v; int b = a++; return a;`
  returned `v`. `tests/test_repair_miscompiles.py` runs all three.

- **Every access to a volatile object now happens as written (C23
  6.7.3).** `volatile unsigned char ymaddr; ymaddr = 0x28; ymaddr = 0x28;
  ymaddr = 1;` compiled to one store, and `g = 1; x = g;` to one store and
  no load. Four passes caused it, and each is fixed:
  - the AST optimizer (uc_core) deleted adjacent stores to a volatile
    global, static, extern, local or typedef object, and a store whose
    right side read a volatile object. It folded `v == v`, `v | v`,
    `v % 1` and `v * 2`, and it unrolled a loop over a volatile variable;
  - the inliner dropped a volatile argument that the body did not use, and
    repeated one that the body used twice;
  - the peephole pass merged `ld (_g),A / ld A,(_g)`, dropped the second of
    two equal reads, and removed the store of `g = g`. The code generator
    now brackets the code that can access a volatile object, and `main.py`
    fences the bracketed code with labels that the peephole matcher does
    not match across (`src/uc80/volatile_fence.py`);
  - `++` and `+=` on a 32-bit volatile object read and wrote the high word
    only after a carry. They now access all four bytes;
  - `x++`, `--x` and the like on a volatile `float` or `double` read the
    object twice, and on a volatile `char` reached through a pointer, an
    array or a member (value used) they read it twice. Each is now one read
    and one write;
  - `v * 2` on a volatile object is no longer rewritten to `v << 1`, which
    gave wrong code for a `float`.
  Code that uses no volatile object compiles to the same assembly as
  before. Details are in [docs/volatile.md](docs/volatile.md).

- **Integer promotion and `char` handling.** Each operand now gets its own
  promotion (`unsigned char`, `short`, `_Bool`, enum and narrow bit-fields
  become `int`). Thus with `unsigned char c = 10`, `(c - 20) < 0` is 1.
  `long` with `unsigned int` divides as signed. `++` and `--` on a
  one-byte local read two bytes, so the value of `c++` had the next
  local in H and an index `i++` read the wrong element. The value of
  `++c`, `(c = 300)` and `(s.f = 9)` is the narrowed stored value.
  `unsigned int` with `long` is `long`. A switch on a `long` compared
  only the low word. `++` and `--` on a `long`, `float` or `long long`
  element, member or `*p` changed only the low 16 bits.
- **The inliner and constant folder dropped types.** The inliner now
  converts arguments and the return value to the declared types and does
  not inline a call whose arguments have side effects (`sq(k++)` ran
  `k++` twice). The folder of `ast_opt.py` kept the type of a literal
  (`x * 8UL`, `-2L`, `-256L >= 65280u`).
- **`!x` of a `long long` tested only the low 32 bits,** and `-0.0` was
  true in `if`, `!`, `?:` and `&&`. `x && y` with a `float` operand gave 0
  or -1. The runtime BSS had its own `COMMON` block that overlaid the
  program's block, so `__acc64` shared bytes with the first global.
- **`div()` returned `denom / numer`.** `_div` passed its operands to
  `__sdiv16` and `__smod16` in swapped registers (they compute `DE op HL`),
  so `div(4425, 3)` gave quotient 0, remainder 3. `ldiv()` was right.
- **`calloc()` wrapped `nmemb * size`.** `calloc(300, 300)` returned a
  24,464 byte block. It now returns NULL when the product does not fit in
  16 bits, and counts with the smaller factor.
- **`%#.0o` and `%#.0lo` of 0 printed nothing;** the C standard says `0`.
- **A literal width or precision over 255 wrapped** (`%300d` made a 44
  column field). It now stays at 255, as `%*d` already did.
- **`__fmul` mishandled infinity, NaN and the sign of zero.** `x * inf`
  returned garbage, `inf * 0` and any NaN operand were not NaN, and a zero or
  an underflowed product was always `+0`. An infinity times a finite value
  is now a signed infinity, `inf * 0` and a NaN operand give a NaN, and a
  zero result keeps the sign. A subnormal operand is read as zero (a
  subnormal result was already flushed to zero).

- **A runtime function was linked twice.** When libc needs a runtime
  module, the linker takes the whole module, and a function that the
  compiler had embedded from the same module was then in the image twice
  (ul80 warned `%Mult. Def. Global`; `ldiv()` could return `quot = -1`
  next to 32-bit arithmetic because there were two copies of `__tmp32`).
  The compiler now predicts the runtime modules the link will pull in
  and leaves their functions and data to the library. A program with
  floating point and `printf` is about 1.7 KB smaller.
- **`free()` frees.** `malloc`, `calloc`, `realloc` and `free` use a
  first-fit free list kept in address order, and `free()` merges the
  block with free neighbours. `realloc` keeps a block that is already
  big enough and frees the old block after a copy. A program that
  allocates and frees in a loop (binary-trees) no longer runs out of
  memory.
- **`printf` honours precision, `-`, width on long forms, `%s` width and
  `*`.** `%.4d`, `%5.3d` and `%.3x` now print the minimum number of
  digits, `%-5d`, `%-5u` and `%-5x` left-justify, `%8ld`, `%-8lu` and
  `%08lx` (and the `ll` forms) pad to the width, `%5s` and `%-5s` pad,
  and `%*d`, `%.*d` and `%*s` read the width or precision from the
  arguments. The flags `+`, space and `#` work for every integer
  conversion. All integer handlers now end in one routine,
  `_printf_numout`. `%llx`, `%lld` and `%llu` also write through
  `sprintf` and `fprintf` now (they wrote to the console before).
  `tests/test_printf_flags.py` compares about 1,200 conversions with the
  host `printf`.
- **Float multiply is correctly rounded.** `__fmul` lost one of two
  carries when it added the partial products, so a product could be
  wrong by up to 513 units in the last place, and it truncated instead
  of rounding. It now rounds to nearest even. 300,000 random products
  match Python's `float32` rounding. A product that would be subnormal
  is still flushed to zero. The c-testsuite output of test 00174 changed
  to the correctly rounded digits.
- **Every program linked about 9 KB of libc it never called.** uc80
  declares each prototype in each included header as `EXTRN`. Since um80
  0.3.50, um80 keeps an unreferenced `EXTRN`, as MACRO-80 does, and ul80
  then links the library module that defines the name. `puts("hello")`
  linked to 9,088 bytes instead of 256, and c-testsuite as a whole to
  752,000 bytes instead of 450,176. The compiler now drops every `EXTRN`
  that nothing in the module uses, as its last step, so a name used only
  by an appended `.mac` file keeps its `EXTRN`. When hand-written assembly
  uses `include`, a macro or `&`, nothing is dropped, because the names it
  uses cannot be seen.
- **`malloc` memory overwrote globals.** The heap started at a word in
  `lc_malloc`'s own data segment, below the program's later data and BSS,
  so `malloc(1000)` followed by `memset` cleared global and static
  variables. The heap now starts at `__END__`, the first free byte after
  the whole program. The smaller links above made the old bug hit with
  much smaller blocks. Rebuild the libraries (`uc80 --build-libs`).
- **Fixes that the benchmark programs found.** The benchmark programs
  (Embench, CoreMark, whetstone and the games) exposed these bugs:
  - `sizeof (void *)` or the `sizeof` of another pointer or array type
    name in a call argument stopped the compiler with an internal error
    (`embench-tarfind`).
  - `a + n * n` with an `unsigned long` index took the 32-bit path, which
    does not scale by `sizeof(*a)`. Pointer plus or minus a `long` index
    is now pointer arithmetic with scaling (`coremark`).
  - A pointer difference had the pointer type, so `(p - q) + n` scaled `n`
    by the size of the pointee. A pointer difference is now an `int`
    (`mille`).
  - A function whose name differs only in case from a global got a
    suffixed label at its definition, but its call sites used the plain
    name (`robots`).
  - A label may stand in front of an `asm` statement (`whetstone`). This
    needs `uc_core` 0.4.2.
  - `"a\\b"` lost the backslash, because the code generator read the
    decoded backslash as the start of a second escape.
  - The assembly dead-code pass kept a `PUBLIC` in the data segment
    together with the data block before it. A dead string literal in front
    of `__tmp32` then took the `PUBLIC` along, so the library and the
    program had two copies of `__tmp32`. `rand()` returned 0 next to
    `exit()`, and floating-point code called `__fadd` with the right
    operand in the wrong copy.
  - `atof` pushed the `strtod` arguments in the wrong order, lost
    fractions, had the words of the factor 10.0 swapped and unbalanced the
    stack for a string without digits. State of an earlier call leaked in.
  - `scanf` called the white-space loop as a subroutine, `ungetc` did
    nothing, the integer readers destroyed the argument offset, `%ld` read
    zero, and a conversion without digits counted as a match.
  - `pow` with a whole-number exponent up to 64 now uses repeated
    squaring, so `(int)pow(3, 2)` is 9 and `pow(-2, 3)` works.

## 0.7.0

An ABI change, and a correctness release around it. A function returning a
struct or union wider than HL now takes a hidden destination pointer from
its caller, so **every translation unit of a program must be recompiled
together**, and the libraries rebuilt -- in a git checkout, that is:

```bash
uc80 --build-libs
```

A wheel ships `libc.lib`, `runtime.lib` and `crt0.rel` already built, so
`pip install --upgrade uc80` is the whole of it there.

Two of the fixes are for regressions in 0.6.0, so 0.6.0 is superseded
rather than merely improved on.

Two passes are recorded here. The first was an independent verification of
the seven bugs the mbasic project reported, carried out by verifiers working
from the reports rather than from the fixes; it found more than it
confirmed. The second started from the 256-byte limit on returning an
aggregate and went looking for what else was wrong nearby.

### Changed

- **A function returning an aggregate writes it where the caller says.**
  The result used to go into `__sret_buf`, one static buffer the whole
  program shared, whose address came back in HL. That capped a returned
  aggregate at 256 bytes -- uc80 refused anything larger, which is the entry
  below about writing past the buffer -- and it meant only one result could
  be in flight: `arr[g(1).a] = f(90);` computes the destination address after `f`
  returns, so `g` overwrote `f`'s bytes on the way and the assignment stored
  `g`'s result, silently.

  The caller now passes the address to write to as a hidden first argument,
  pushed last so it lands at `IX+4`, with the declared parameters starting
  at `IX+6`. The callee copies its result there and still leaves that
  address in HL, so nothing that reads a result changed. The destination is
  a slot in the caller's own frame, one per call site, which is what removes
  the size limit and separates one call's result from the next.

  This is an ABI change: **recompile every translation unit of a program
  together**, and rebuild the libraries. An aggregate of two bytes or fewer
  still comes back in HL and is unaffected. `__sret_buf` remains only for a
  call whose return type could not be resolved when the slots were laid out,
  so an ordinary program no longer reserves its 256 bytes.

- **Separately compiled units no longer share one another's storage.**
  Uninitialized statics, and the `??AUTO` region holding a shared-storage
  function's locals, went into the blank COMMON block. That is what a common
  block is for -- L80 puts every module's blank COMMON at the same address --
  so with two uc80 modules in a link, one unit's globals sat on top of the
  other's and one unit's automatic storage sat on top of the other's live
  locals. `--no-whole-program` now puts that storage in DSEG, which the
  linker gives each module its own space for; the bytes cost their size in
  the image, where they are already zero. Whole-program output is unchanged.

- **`%F` prints `INF` and `NAN`** (C17 7.21.6.1p8), through its own alias
  entry point rather than sharing `%f`'s.

- **A frame that does not match its body stops the compile.** The prologue
  reserves automatic storage before a line of the body is generated, and
  the body then hands out the slots. When the two disagree the extra locals
  sit below SP, or on the return slots, or -- with nothing reserved at all
  -- at `(IX+0)`, on top of the frame pointer the prologue had just pushed,
  which hangs the program. Every symptom of that is silent, so
  `gen_function` now compares what the body took against what it reserved
  and refuses to emit the function. Both known causes are fixed below: a
  tag defined in the body, and a typedef that leaked out of the function
  that declared it.

- **Dependency floors move.** uc80 requires `uc_core >= 0.4.1` (was 0.4.0)
  and `uplox >= 3.3.0` (was 3.2.0); `pip install --upgrade uc80` pulls
  both. Building a wheel from source needs `um80 >= 0.3.46` (was 0.3.43,
  added in 0.6.0) and `setuptools >= 64.0`, because the wheel assembles the
  CP/M libraries with um80. 0.3.46 is also the ul80 that reports an error
  recorded during a link that still succeeded, which is what makes a
  duplicate definition such as the `__sret_buf` below visible at all.

### Fixed

- **A struct or union tag defined inside a function body was sized as
  zero.** Frame sizing runs before the body is generated and the tag was
  not registered yet, so `struct L { int p, q, r, s; }; struct L x;` gave
  `x` no storage and the next local was laid on top of it. A typedef or tag
  declared in one function also stayed registered for the next, which let
  frame sizing and the shared-storage plan -- they run at different times --
  size the same declaration two different ways.

- **A call in a declaration inside a statement expression was invisible to
  the call graph,** so `({ S q = f(4); q; })` as the only use of `f` had it
  removed as dead and the link failed on the call still emitted for it.

- **A name declared in a block stayed visible after the block closed.**
  C23 6.2.1p4 ends its scope at the brace; only the extern set was being
  restored. With a different type inside -- a block-local function pointer,
  say -- every later call through the outer name was emitted with the inner
  one's calling sequence.

- **A function reached only through a pointer shared storage with its
  caller.** Which functions can be on the stack at once was answered from
  the direct call graph alone, so an address-taken function looked
  unrelated to whoever called it indirectly and was packed at overlapping
  offsets in ??AUTO: two calls through the same pointer returned the second
  result for both.

- **A local further than 128 bytes from the frame pointer was addressed
  with the low byte of its offset.** `(IX+d)` holds a signed byte and um80
  assembles a larger operand without complaint, so the access landed in the
  caller's frame -- and once a frame passed 256 bytes, two locals aliased
  each other. Only functions with a real stack frame were affected
  (recursive, variadic, or built with `--no-shared-storage`); shared
  automatic storage is addressed absolutely. Scalar loads and stores now
  walk IX to the slot when the displacement cannot reach, and anywhere that
  still cannot, the compiler says so instead of emitting the wrong access.

- **An expression whose value is a struct was stored, not copied.** A call
  returning a struct, a member read, a cast, a conditional: each designates
  bytes, and evaluating one yields their address. Wherever an initializer or
  an assignment expected a scalar it stored that address into the first
  field and left the rest zero, so `struct S a[] = { mk(7), mk(9) };` filled
  the array with two pointers. The same shape reached a struct member
  (`struct O o = { mk(3), 5 };`), a union's first member, a designated
  element, and the right-hand side of an assignment. A conditional was
  worse: taking an address had no case for one and no fallback, so nothing
  at all was emitted and `(c ? q : r).b` read through whatever HL held. A
  chained assignment (`a = b = c;`) and a statement expression
  (`s = ({ ...; obj; });`) emitted nothing for the same reason, which lost
  the inner assignment and the statements' side effects outright. A shape
  that still has no case is now a compile error rather than silence.

- **A local declared anywhere the frame sizer did not look got no storage.**
  Sizing walked a hand-written list of statement kinds, so `label: { int
  a[8]; }` and `default: { int b[8]; }` reserved nothing and the array sat
  below SP, where the next `push` wrote over it. It also sized each
  declarator from its declared type alone, so `char s[] = "hello world"`
  counted as zero bytes. Both were silent, and a function eligible for
  shared storage escaped the second half, so it showed only in recursive and
  variadic functions.

- **An unnamed parameter was skipped rather than stepped over,** so in
  `int f(int, int x)` every parameter after the unnamed one read the
  argument before it -- `x` returned the first argument. Abstract
  declarators (`char *`) did the same.

- **A local array ignored index designators:** `int a[] = {[9]=91, [0]=2}`
  filled positionally, putting 91 in `a[0]`. The same declaration at file
  scope was always right, so the two storage durations disagreed about one
  initializer.

- **`--no-shared-storage` produced unlinkable output.** The flag gated the
  allocation pass but not the decision to use it, so functions addressed
  `??AUTO+n` and the symbol was never defined.

- **`INFINITY` was not an infinity and `NAN` was not a NaN.** math.h defined
  them as `((double)0x7FFFFFFF)` and `((double)0)`, so `isinf(INFINITY)` was
  false and `NAN` compared equal to zero. `HUGE_VAL` -- what the math
  functions return on overflow -- was that same `0x7FFFFFFF`, a finite
  value of about 2.1e9 that an ordinary result can exceed. All three are
  now folded from the GCC builtins, which keeps them constant expressions
  usable in a static initializer, and `HUGE_VALF` and `HUGE_VALL` are
  defined alongside them. Reaching them needed two more fixes: a literal
  too large for binary32 stopped the compiler with "internal error: float
  too large to pack with f format", and C23 6.4.4p2 makes a constant's
  representability a constraint, so `1e39f` warns and becomes an infinity
  now rather than crashing; and `printf("%f")` decided "infinity" from the
  exponent alone, after a rounding pass that does not preserve the mantissa
  telling an infinity from a NaN -- so every NaN printed as `inf`.

- **A printf under a `default:` label or inside an initializer list was
  invisible to auto-detection,** so the unit reported that it called no
  printf, its dispatch table collapsed to the sentinel, and the conversion
  came out verbatim at run time. No warning either: the compile-time check
  reads the same scan. The scan now walks the AST rather than a list of
  statement kinds it knows.

- **`%*d` and `%.*f` are diagnosed.** The runtime parser implements
  neither: it echoes the specification and never consumes the int argument,
  so every later conversion in the call reads the wrong one. The
  compile-time scan skipped the star without recording it.

- **A relative `UC80_LIB_DIR` came back as written,** so a captured
  `LIB=$(uc80 --print-lib-dir)` named a different directory -- or nothing --
  when used from anywhere else, and the link went to the packaged tree with
  nothing said.

- **Inline assembly kept a colon-less label named like a mnemonic.**
  `_format_asm_block` indented any column-0 word that is a mnemonic, but
  `SET`, `AND`, `OUT`, `PAGE` and `NAME` are legal symbol names and a
  MACRO-80 label written without a colon must be in column 0, so `SET equ 5`
  became a `SET` instruction. What follows the word decides now.

- **`printf("%f")` was wrong for every `|value| >= 65536`,** and usually did
  not even emit digits: `1000000.0` printed as `3906.00///` and `-123456.0`
  emitted a raw backslash. The integer part was built in `H:L:E` and printed
  with the 16-bit `_prt_dec`, which drops `E`; above 2^24 a second path
  shifted a 16-bit `HL` and printed `0`. `sprintf` in the same libc had
  always been right, so one program could print one value two ways and get
  two answers. Values beyond 2^31 -- where an `int32` no longer holds the
  integer part -- now print exactly, by decimal doubling of the mantissa, so
  `3.4e38` gives all 39 digits. Not a regression; it predates the %e/%g work.

- **The whole printf family shares one formatter.** `sprintf`, `snprintf`,
  `fprintf`, `vprintf`, `vfprintf` and `vsprintf` each carried their own
  conversion chain, so 0.6.0's `%e` and `%g` reached `printf` and nothing
  else -- they printed an empty field and, because a table miss does not
  advance the vararg offset, desynced every later conversion in the call.
  `fprintf` and `snprintf` had no `%f` at all. A program calling both
  `printf` and `sprintf` shrinks from 7040 to 5248 bytes, the duplicate
  formatters having been linked in alongside the real one.

- **A comma expression yielding `long long` carried a stale value.**
  Regression in 0.6.0: once the comma operator had a result type,
  `(f(), 42LL)` reported itself as 64-bit, and three places took that to mean
  the value was in `__acc64`. A 64-bit literal has a 16-bit fast path, so it
  was not, and the expression evaluated to whatever the accumulator last
  held. Affected initialisers, every condition (`if`/`while`/`do`/`for`/
  `&&`/`||`/`?:`) and variadic arguments.

- **A struct-valued comma produced garbage** and, in argument position,
  dropped the left operand's side effect entirely. Not a regression -- an
  unfixed gap of the same kind.

- **Under `--no-whole-program`, link order decided which printf conversions
  worked.** Regression in 0.6.0: the per-specifier float filter made each
  unit emit a different `PUBLIC __printf_format_table`, and L80 keeps the
  first definition. A unit calling no `printf` emitted an *empty* table, so
  linking it first disabled every conversion in the program. The table is now
  emitted by the unit defining `main()` -- exactly one per link -- and a unit
  that prints conversions that unit does not is told to pass an explicit
  `--printf`. Auto-detection cannot be trusted across a separate build
  either, since a unit sees only its own format strings: a `main()`
  compiled under `--no-whole-program` without an explicit `--printf` now
  registers every handler and says so, so such a build links a larger
  binary than it did until it passes the flag. Whole-program mode is
  byte-identical.

- **An array bound spelled with an enum constant was ignored** when
  compositing `extern int a[N];` with `int a[];`, giving a zero-byte object
  that the next global was written through, and skipping the C 6.7.6.2p6
  conflict check. Enum constants are now registered before declarations are
  composited.

- **Adjacent string literals initialised nothing.** `char s[] = "ab" "cd";`
  emitted correctly sized, entirely zero storage; inside a struct initialiser
  the pieces were spread across the following members, so a `char *` member
  got a string's bytes where its pointer belonged.

- **An appended `.mac` is fenced from the optimizers,** as inline `asm()`
  already was. The peephole fused `LD A,(HL)` + `LD C,A` with `A` still live,
  deleted a label caught between the fused pair (turning a working helper
  into an `Undefined symbol` from um80), and discarded `ORG` and `ASEG`.

- **`printf("%f", -0.0)` printed `0.000000`** -- the zero test masked the sign
  bit off before anything checked it -- and **`printf("%.0f", 2.0)` printed
  `2.`**, a trailing point with no digits after it. `%e` and `%g` already got
  both right.

- `%ld` and `%lu` count the bytes they write. `_prt_dec32` wrote straight to
  `__conout`, so `printf("[%ld]\n", 1234567L)` returned 3 for the ten bytes
  it produced; it goes through `_printf_putc` now, the same choke point as
  the rest of printf land, so the count is right and CR LF translation is
  unchanged. `_prt_dec64` and `_prt_hex` still write to `__conout`, so
  `%lld`, `%llu` and `%llx` remain uncounted -- recorded under Known
  limitations below.

- **The documented link line defined `__sret_buf` twice.** The struct-return
  buffer lived in the same runtime module as the 32-bit arithmetic helpers,
  so it had two homes: the runtime the compiler embeds, and `rt_arith32` in
  `runtime.lib`. A program that returned a struct embedded the buffer, and
  any later pull of `rt_arith32` -- printing a `long`, say, which reaches the
  32-bit helpers through libc rather than through the compiler -- brought a
  second definition with it. L80 keeps the first and links on, so this was
  silent until ul80 0.3.46 started reporting a recorded error; the second
  buffer was still allocated, costing 256 bytes of every affected binary.
  `__sret_buf` now has its own module, `rt_sret.mac`, which the linker pulls
  only when the symbol is genuinely unresolved.

- **Returning a struct larger than 256 bytes wrote past `__sret_buf`.** The
  return copy was an `ldir` of the struct's full size into a `DS 256` buffer
  with nothing bounding it, so a 402-byte struct put 146 bytes over whatever
  storage followed. It stayed quiet because the value still reads back
  correctly -- the clobbered bytes are not the ones the caller looks at --
  and how far it reached scaled with the struct. The first fix was to refuse
  the return and name both sizes; the ABI change above then removed the
  limit itself, so ordinary code is no longer refused. The refusal survives
  only on the `__sret_buf` fallback path -- a call whose return type could
  not be resolved when the slots were laid out -- where the 256 bytes still
  bound the copy.

- **Compiling the same source twice produced different assembly.** The
  embedded runtime functions came out in set-iteration order, which Python
  varies per process. Same functions, same binary, different file.

### Known limitations

Recorded so they are not rediscovered; `todo.txt` has the full list.

- Field width and the `-`, `+` and ` ` flags do nothing on a float
  conversion. The format parser discards those three flags for every
  conversion, so they have never been implemented.
- `%.*f` / `%*f`, and `%lf` / `%le` / `%lg` under auto-detection or
  `--printf float`, are not registered: the conversion is echoed verbatim and
  every later one in that call reads the wrong argument. The `*` forms are
  now diagnosed at compile time where the format string is a literal.
- `%lld`, `%llu` and `%llx` write their digits through `__conout` rather
  than through the sink the shared formatter hands each entry point, so
  `sprintf`, `snprintf` and `vsprintf` print those digits on the console and
  leave the buffer holding the surrounding text alone -- `sprintf(b,
  "<%lld>", 42LL)` puts `<>` in `b` and `42` on the screen -- and `printf`
  does not count them in its return value. Against 0.5.0 this is a
  regression for the buffer-writing calls, whose separate formatter wrote
  the digits into the buffer; `%ld` is the way round it until `_prt_dec64`
  and `_prt_hex` go through `_printf_putc`.
- Float comparison does not implement NaN's unordered result, so `x == x` is
  true for a NaN. No arithmetic in the runtime produces one; only the `NAN`
  macro and a bit pattern read back as a float can. (Fixed later: see the
  NaN compare entry in the Unreleased section.)

## 0.6.0

A behaviour-changing release. Rebuild the libraries after upgrading:

```bash
uc80 --build-libs
```

### Changed

- **Console output now ends a line with CR LF.** A bare LF is "cursor down"
  only on a real CP/M terminal, so output written with a bare LF stair-steps
  down the screen. CP/M translates nothing below the program, z88dk builds
  its CP/M library the same way, and C23 7.23.2 allows a text stream to alter
  characters on output to match the host convention.

  Compile with `--no-crlf`, or assign to `__crlf_mode` from `<stdio.h>`, to
  get the old raw LF. `--no-crlf` only takes effect on the translation unit
  that defines `main()`.

  This also removes an inconsistency that predates the change: `puts()`,
  `assert()`, `perror()` and `abort()` already emitted CR LF while
  `printf`, `putchar`, `fputs` and `fprintf` emitted a bare LF, so one
  program printed two different line terminators depending on which stdio
  call it used. All of them now follow one policy. Under `--no-crlf` that
  means `puts()`, `assert()` and `perror()` emit a bare LF, which they did
  not before; `abort()`'s message is still CR LF.

  Anything that compares a uc80 program's console output byte for byte will
  now see the CR. Python harnesses that read the output with `text=True`,
  including all four test drivers in this repository, are unaffected.

  File streams are not translated, with or without the flag: `fwrite` and
  `fputc` to a `FILE *` write the bytes they are given, in both `"w"` and
  `"wb"` mode.

- `--printf` and `--scanf` feature sets are closed over the features they
  imply, so `--printf float` no longer silently drops `%d`. An existing
  build script that passes a narrow `--printf` will link more handlers and
  produce a larger binary than it did in 0.5.0.

- An unknown printf conversion is echoed verbatim instead of vanishing. It
  still cannot resync the varargs, so the rest of that call is still
  misaligned, but the failure is now visible and is diagnosed at compile
  time when the format string is a literal.

- The package no longer declares `py.typed`. It never shipped the file, so
  the PEP 561 claim was false either way; uc80 carries inline annotations
  but has never been type checked, so the claim is dropped rather than
  made true.

### Added

- Basic inline assembly: `asm("...")`, spelled `asm` or `__asm__`, with or
  without `volatile`, at file scope and in a function body. The block is a
  barrier that no optimizer crosses. Extended asm and `asm goto` are
  rejected with an error instead of being silently dropped.
- The `%e`, `%E`, `%g` and `%G` printf conversions, and `%F`, which
  0.5.0 dropped along with every other conversion it had no handler for.
- `--no-crlf`, and `extern char __crlf_mode` in `<stdio.h>`.
- **Wheels now ship `libc.lib`, `runtime.lib` and `crt0.rel`.** `pip install
  uc80` previously installed a compiler that could not link anything: those
  three are assembler output, are gitignored, and were produced only by a
  manual step, so a wheel built from a clean checkout never contained them.
  They are assembled at wheel-build time, which adds `um80` to
  `[build-system].requires` and about 45 KB and 40 s to a release build. They
  are still not committed to git — `libc.lib` is not byte reproducible, so a
  committed copy would churn on every rebuild and could drift out of step
  with `lc/*.mac` unnoticed.
- `uc80 --print-lib-dir` prints the library directory on one line and exits
  0 without needing an input file, for `LIB=$(uc80 --print-lib-dir)`.
- `uc80 --build-libs` assembles the three link artifacts on demand.
- `uc80.lib_dir()` and `uc80.lib_file(name)` — the same answer from Python,
  and the supported way for another tool to locate the libraries. Consumers
  that guessed the path could land on a stale tree and link against it with
  no diagnostic from anything.
- `UC80_LIB_DIR` overrides the library directory, applied per file so a
  partial override cannot shadow the compiler's own version-locked inputs.

### Fixed

- The comma operator's result type is inferred from its right operand. A
  `(a, b)` yielding `double`, `float`, `long` or `long long` was compiled as
  a 16-bit signed `int`. Both operands are also evaluated exactly once.
- Merging an `extern` array declaration with its definition no longer
  crashes the compiler, and two different known sizes are now reported as
  conflicting types instead of being silently reconciled.
- Assembly appended from a `.mac` input, and the embedded runtime, are
  spliced in ahead of the BSS/COMMON block instead of after it, where crt0
  zeroed them.
- Several literal values that were compared without being decoded, which
  affected designated initializers and `_Bool b = 0.0`.

## 0.5.0 and earlier

Only the 2026-04-30 sweep is recorded, from notes kept in `todo.txt` at the
time; for anything else see `git log`.

That sweep added `--int` / `--long` / `--long-long` switches so a test
making an assumption about a type's width could be compiled under it, and
then fixed what running the suites under every width uncovered:

- 64-bit `++` / `--` updated only the low 16 bits.
- A condition test on a `long long` expression ORed only the low 32 bits.
- An implicitly declared external function got no `EXTRN`, which is what had
  stuck many gcc-torture tests that call `abort()` / `exit()` without a
  prototype.
- `__builtin_memcpy` / `memset` / `abort` / `trap` / `unreachable` /
  `malloc` and the rest now rewrite to their libc equivalents.
- Source bytes that are not UTF-8 fall back to latin-1.
- `__SIZE_TYPE__` / `__PTRDIFF_TYPE__` / `__WCHAR_TYPE__` are predefined
  (they live in uc_core's `type_config` now).
- A struct-array element initialized from a compound literal --
  `struct Wrap arr[] = { (struct Wrap){fn}, fn };` -- stored the address of
  the materialized literal in the first member instead of copying the bytes,
  which showed as an infinite loop in c-testsuite 00216 under `--long=64`.
