# Why SDCC is smaller and faster than uc80, and what it takes to fix uc80

Scope: this report excludes the ROM target issue.

Evidence rules:
- "Measured" means a hand-edited uc80 .mac file that an analyst re-assembled, re-linked, re-timed and checked against the golden output.
- Reviewers re-did three families (D, F, G) on snd-cmdq and snd-fnum. The differences are in section 2.
- "Estimate" means no measurement exists.
- Hand edits give upper bounds. A compiler pass will gain less.
- Size means the image size (`__BSS_START - 0x100`, the 6th column of `bench/results/results.csv`). The older figures in `docs/benchmarks_speed.md` (5866 B and 3926 B for snd-cmdq) come from an earlier run.

## Status on 2026-10-26: family G, step 1 (simple register residency) is done

Step 1 of family G is in the code (branch `reglocals`, see [register_locals.md](register_locals.md)). A text pass moves up to two scalar locals of a function into BC or DE. The pass runs after the body is generated, so the code generator is unchanged. It renames or wraps the scratch uses of BC and DE (`ld BC,_arr` / `add HL,BC`, calls) and a checker proves each result. The `register` keyword is a priority hint. Interrupt functions and `main` are not changed.

Numbers: `bench/run.py` (uc80, size mode) on the 109 programs of the size run, from the same tree, step 1 of G against the tree before (84cfebf). Status: no change. Image: 87 programs smaller, none bigger, total 794,019 to 782,696 bytes. T-states: 79 programs faster, 1 slower (`micro+a32`, +0.15%). Mean against SDCC 4.6.0: size 0.95 to 0.94, T-states (speed mode) 0.59 to 0.56, without `sort-*` 0.71 to 0.69.

- `snd-cmdq` 10.60M to 9.90M T-states (-6.6%) and -43 B. `snd-ymreg` -5.5% and -38 B. `snd-seq` -5.0% and -73 B. `snd-fnum` -3.4% and -36 B.
- `sieve` -17.8% and -10 B. `tak` -14.4% and -41 B. `stanford-perm` -4.0%. `stanford-towers` -7.1% and -57 B. `davidly-ttt` -1.7% and -48 B.
- `queens` gains only 0.7%, and `stanford-bubble` gains nothing. The loop counters of `queens` sit in a recursive function, and every array index uses BC and DE as scratch registers. A local in BC then pays a push and a pop for each index. The chain assignment `a[i]=b[j]=1` keeps DE busy, so the rename cannot move the scratch pair. This is the prerequisite of section G: the expression idioms must stop using BC and DE. Family H (CSE of array addresses) is the fix.
- What is not done: IY as a struct pointer, HL residency, more than two locals, and giving back the `??AUTO` slot of a local that moved. The call graph analyser still reserves the slot.

## Status on 2026-10-26: family F is integrated with the C library

The branch `regcc` is merged into the tree that has the libc rewrite in C, the float interpreter and the ROM work (branch `integrate5`). The library modules in `src/uc80/lib/lc/` come from `gen_c_modules.py` and `gen_vm_modules.py`, which compile with `--no-whole-program`. The register convention needs the whole program in view, so every library function keeps the stack convention. Running both generators again with the merged compiler gave files that are the same, byte for byte, as the files in the repository, and `--build-libs` gave the same `libc.mac`, `libc.lib` and `runtime.lib`. `tests/test_libm_vm.py` and `tests/test_sdas.py` pass.

Numbers from `bench/run.py` (uc80, size mode, 109 programs; the three games `battlestar-size`, `rogue36-size` and `empire-trefonas` are not run again). "Main" is the tree before the merge (commit c2fcd87) run again with the same harness, because the committed `results.csv` of main was older than the library work. Image bytes / T-states:

- snd-cmdq: 2797 B / 13.72M to 2635 B / 10.60M (SDCC speed mode: 4034 B / 9.03M)
- snd-ymreg: 2442 B / 21.21M to 2363 B / 17.87M (3821 B / 15.26M)
- snd-seq: 3866 B / 14.52M to 3669 B / 12.34M (5002 B / 12.99M)
- snd-fnum: 2597 B / 13.69M to 2539 B / 11.06M (3769 B / 17.65M)
- tak: 1053 B / 47.26M to 956 B / 44.16M
- stanford-perm: 528 B / 448.5M to 495 B / 356.0M
- stanford-towers: 2480 B / 501.7M to 2312 B / 404.1M
- queens: 947 B / 34.07M to 896 B / 33.94M
- davidly-ttt: 3807 B / 13.12M to 3747 B / 12.87M
- sieve: 253 B / 3.91M, no change

Of the 103 programs that are OK in both runs, 82 get smaller and none gets bigger. Two programs that fit in 64 KB but are not in this count do get bigger: `mojozork-size` (image 31,736 to 33,965 bytes) and `monop-size` (image +325 bytes, BSS -535). The cause is the call graph change for calls through a pointer (see the CHANGELOG): the functions that such a call can reach lose their `??AUTO` storage and use stack frames with `IX`. The growth shows also with `--no-register-cc` (the COM file of mojozork is 34,688 bytes with it and 34,048 without it), so the register convention does not cause it. The total image falls from 638,662 to 619,188 bytes (geometric mean of the size ratio 0.972). 56 programs get faster and 2 get slower (`embench-md5sum` +373 T-states and `embench-tarfind` +844 T-states); the geometric mean of the T-state ratio is 0.966. No program changed status.

Means against SDCC 4.6.0 (method of `bench/report.py`), main then integrated:

- size minus the empty program: 0.9867 to 0.9585 (96 programs; uc80 smaller on 53, then on 63)
- whole image: 0.9479 to 0.9213
- T-states against the speed mode: 0.6060 to 0.5852 (78 programs); without `sort-*`: 0.7431 to 0.7148 (70 programs)
- T-states against the size mode: 0.5853 to 0.5652; without `sort-*`: 0.7150 to 0.6877

Family F status: done for the whole tree. `snd-seq` and `snd-fnum` are faster than SDCC speed mode. `snd-cmdq` and `snd-ymreg` are still 17 percent slower. The small loops (`sieve`, `queens`, `tak`, `stanford-perm`, `stanford-towers`, `davidly-ttt`) are still slower than SDCC; their cost is locals in memory (family G).

## Status on 2026-10-26: family F, steps 1 and 2 are done

Step 1 of family F is in the code (branch `regcc`). [calling_convention.md](calling_convention.md) describes the rules. A function that only direct calls reach takes its first two scalar parameters in registers (A or HL, then E or DE). The caller pushes the other parameters and does not pop the register parameters. The callee stores each parameter that the body names into a slot (`??AUTO`, or a push in the prologue of a stack frame). The pass `regcc.forward_slots` then removes the loads that a register still serves and the stores that nothing reads. A small leaf function needs no IX frame. Step 2 is described after the step 1 numbers below. Two parts of the plan are still open: a `jp` for a call that has stack arguments (the callee would have to pop its own arguments), and a third register parameter.

The numbers come from `bench/run.py` (uc80, size mode), with and without `--no-register-cc`, from the same tree. Image bytes / T-states, before and after (SDCC 4.6.0 speed mode in the last column):

- snd-cmdq: 2797 B / 13.72M, 2640 B / 10.72M, 4034 B / 9.03M
- snd-ymreg: 2442 B / 21.21M, 2363 B / 17.87M, 3821 B / 15.26M
- snd-seq: 3866 B / 14.52M, 3669 B / 12.34M, 5002 B / 12.99M
- snd-fnum: 2597 B / 13.69M, 2539 B / 11.06M, 3769 B / 17.65M
- tak: 1053 B / 47.26M, 961 B / 44.16M
- stanford-perm: 528 B / 448.5M, 499 B / 361.6M
- stanford-towers: 2480 B / 501.7M, 2323 B / 407.3M
- queens: 947 B / 34.07M, 896 B / 33.94M
- davidly-ttt: 3807 B / 13.12M, 3780 B / 12.97M
- sieve: 253 B / 3.91M, no change

Other large changes: dhrystone21 -209 B and -13.5% T-states, zx0 -130 B and -15.9%, stanford-puzzle -12.3%, coremark -715 B and -6.4%, embench-slre -508 B and -7.1%, crc -6.0%. Of the 104 uc80 rows that are OK, 82 changed and none got bigger. One got slower: `embench-md5sum` by 373 T-states (0.01%). The total image of the 104 programs falls by 19,114 bytes (630,973 to 611,859).

The means (`bench/report.py` method: uc80 divided by SDCC 4.6.0, programs that are OK on both):

- size minus the empty program: 0.9757 to 0.9484 (96 programs; uc80 smaller on 53, then on 63)
- whole image: 0.9374 to 0.9117
- T-states against SDCC speed mode: 0.6228 to 0.6020 (78 programs); without `sort-*`: 0.7662 to 0.7377 (70 programs)
- T-states against SDCC size mode: 0.6016 to 0.5815; without `sort-*`: 0.7372 to 0.7097

No program changed status. `snd-seq` is now faster than SDCC speed mode (12.34M against 12.99M).

What the analyst figures in section 1.2(a) predicted, and what step 1 gained: cmdq -3.0M T (reviewer plan 3.0M), ymreg -3.3M (analyst what-if -7.4M, which included locals in registers), seq -2.2M (-4.4M), fnum -2.6M (-3.69M). `tak` gains 3.1M, not 13M: the third parameter of `tak` is still on the stack, and the recursion keeps its frame. `queens` gains little, because the cost of `queens` is in locals in memory (family G).

Tests: `tests/test_register_cc.py` (shape, exclusions, same answers in five storage configurations), `bench/ccfuzz.py` (random programs against the host compiler; 2000 programs per step), `bench/difftest.py` and the existing suites.

## Status on 2026-10-25: families D and E are done

Families D and E are in the code (branch `speed-de`). [u8_divmod.md](u8_divmod.md) and [byte_paths.md](byte_paths.md) describe the rules. The numbers below come from `bench/run.py` (uc80, size mode: image bytes and T-states). The row "SDCC speed" is SDCC 4.6.0 in speed mode.

Program, before D and E, after D, after D and E, SDCC speed (bytes / T-states):

- snd-cmdq: 3075 B / 14.32M, 2879 B / 14.05M, 2798 B / 13.72M, 4034 B / 9.03M
- snd-ymreg: 2470 B / 22.22M, 2470 B / 22.22M, 2443 B / 21.21M, 3821 B / 15.26M
- snd-seq: 3939 B / 15.38M, 3939 B / 15.38M, 3867 B / 14.52M, 5002 B / 12.99M
- snd-fnum: 2830 B / 18.83M, 2634 B / 13.98M, 2598 B / 13.69M, 3769 B / 17.65M
- davidly-ttt: 4210 B / 14.38M, 4210 B / 14.38M, 3816 B / 13.12M, 4231 B / 7.96M
- queens: 952 B / 34.14M, 952 B / 34.14M, 949 B / 34.07M, 679 B / 23.59M
- sieve, tak, stanford-perm: no change (253 B / 3.91M, 1055 B / 47.26M, 528 B / 448.5M). Their cost is in the calling convention and in locals in memory (families F, G, H).
- stanford-towers: 2482 B / 502.89M, then 2481 B / 501.74M.

The mean covers the programs that are OK on both compilers (`bench/report.py` method, uc80 divided by SDCC 4.6.0). We measured it on the merge of `speed-de` and `bugfix-float`. The size ratio falls from 0.992 to 0.982 (96 programs). The T-state ratio against SDCC speed mode falls from 0.629 to 0.623 (78 programs). Without `sort-*` it falls from 0.775 to 0.766.

No program that was OK changed status. The largest changes are the snd kernels and `davidly-ttt`. The `bugfix-float` changes make 16 programs smaller and none bigger. These changes are the float-aware rewrites and the power-of-two multiply in the code generator. They change no T-state count of D and E.

The row `empire-trefonas` went from HANG to WRONG. It failed before and still fails. The row `battlestar-size` went from BUILD-TIMEOUT to TOO-BIG.

What the re-check of the claims in sections 1.2(b), 2.D and 2.E found:

- D: the fuse step is necessary. With `__dv8n` and the fuse, `snd-fnum` falls by 3.5M T, not by 4.5M T. The reciprocal multiply falls by 4.86M T and costs 23 B more than `__dv8n` (the report said 5.50M T and 23 B). `snd-cmdq` is 196 B smaller (the report said 170 B). The dead helpers are not linked when no 16-bit division remains: a test checks this.
- D: the fuse works on two adjacent block items, not on two parts of one expression. `x / K + x % K` makes two divisions.
- E: the byte path for `_gen_expr_a` and the byte switch existed. The switch did not accept an int value that is known to be a byte (`cmd >> 4`). `snd-cmdq` gains 10 B and 156K T from that rule, as the report said (11 B and 172K T).
- E: the dead `ld H,0` before a call is only dead when uc80 compiles the callee. The rule uses this condition. For `snd-cmdq` the rule gains 69 B and 174K T together with `ld L,K` (the report said 26 B and 39K T for the `ld H,0` alone).
- E: `cp (HL)` gains 480K T and 3 B in `snd-ymreg` (the report said 694K T and 6 B for `ym_write`).
- E: the three rules of `peep_local` (dead register write, byte to the high half, `call` and `ret` as `jp`) gain 212K T and 5 B in `snd-ymreg`, 149K T and 8 B in `snd-seq`, and only 2 B in `snd-cmdq`. The report said 93K T for `call` and `ret` in `snd-cmdq`. The caller of a function with arguments pops them after the `call`, so a `call` with arguments is never next to a `ret`. Only a call without arguments becomes `jp`. `tak` and `ack`-style recursion gain nothing for the same reason: the gain waits for the register calling convention (family F).
- E: int locals that hold bytes: `davidly-ttt` gains 374 B and 917K T (the report said 564 B and 1.56M T for char compares through int locals). The loop counter `p` of `MinMax` is not a byte holder, because the compiler has no range analysis.
- The line numbers in section 1.2(b) are out of date.

## Status on 2026-10-25: families A, B and C are done

Families A, B and C are in the code (branches `printf-puts` and `libc-split`, merged). The measured result is in [benchmarks_speed.md](benchmarks_speed.md), from the size-mode run of `bench/run.py`.

- A: `_prt_float` is in `lib/lc/lc_prt_float.mac`. Only `%f`, `%e` and `%g` link the module.
- B: `printf("text\n")` and `printf("%s\n", s)` become `puts`, and `printf("text")` becomes `cputs`. The rewrite needs an unused result and no other `printf`-family call. [compiler_options.md](compiler_options.md) has the rules.
- C: libc has one module for each public function (176 modules in `libc.mac`). `bsearch`, `vprintf` and `vsprintf` are separate, and `rand` links no runtime multiply.
- Result against SDCC 4.6.0 (96 programs that are OK on both): the mean size ratio fell from 1.53 to 0.99. uc80 is smaller on 52 programs and bigger on 44 (53 and 43 after D, E and the float fixes). The staged estimate in section 1.1 was 1.014, so the measured result is a little better than the estimate.
- Snd kernels (image bytes): snd-cmdq 5866 to 3027, snd-ymreg 5267 to 2422, snd-seq 6658 to 3891, snd-fnum 5549 to 2782. All four are now smaller than SDCC (3926, 3685, 4873 and 3653).
- T-states against SDCC 4.6.0 (speed mode) did not change: the mean is 0.63 (0.77 without `sort-*`). The families D to H are open. The speed gap on small integer loops is still there.
- The programs `micro*` grew by 6 bytes (8601 to 8607) because the code generator now keeps the access to a volatile object (the `volatile-fix` branch).

## 1. Why

### 1.1 The size gap is mostly library packaging

The size gap has two parts. The library part is large and is a packaging error. The user-code part is small and is a codegen problem.

Library part (measured):

- `lc_printf_core.mac` holds the float printer `_prt_float` (526 B). Its EXTRNs pull in the soft-float runtime (about 1.9 to 2.1 KB). Every program that calls `printf` links about 2.5 to 2.85 KB of float code, even when the program never prints a float.
- One split of that module removes more bytes than the whole size gap in all four snd kernels. A reviewer confirmed the mechanism in the link map (about 2.6 to 2.7 KB). Treat the exact savings as correct to about 10%.
- After the split, uc80 is smaller than SDCC on all four kernels. The remaining uc80 printf is about 1.4 KB smaller than the SDCC `printf_large`.

| Kernel | uc80 now | SDCC (size) | Gap | Float split saves | uc80 after split |
|---|---|---|---|---|---|
| snd-cmdq | 5914 B | 4038 B | 1876 B | 2850 B | 3064 B |
| snd-ymreg | 5315 B | 3797 B | 1518 B | 2816 B | 2499 B |
| snd-seq | 6706 B | 4985 B | 1721 B | 2519 B | 4187 B |
| snd-fnum | 5597 B | 3765 B | 1832 B | 2660 B | 2937 B |

The analyst for snd-ymreg reported the 128-byte-rounded .com size (5376 B and 2560 B). The table uses the exact image size.

Wide result over 96 programs (measured, staged):

| Stage | Geomean size vs SDCC |
|---|---|
| As is | 1.527 |
| Fix the dead `printf("lit\n")` to `puts` rewrite | 1.219 |
| Plus float printer split | 1.112 |
| Plus string/mem split (strdup EXTRN `_malloc` drags malloc) | 1.055 |
| Plus stdlib/atoi/ctype/wchar/malloc/file split | 1.014 |

The four library fixes save 206,712 B in total. All 95 re-linked programs match the golden output.

The `printf` to `puts` rewrite never fires. `_rewrite_printf_to_puts` (`codegen.py:4797-4850`) tests `isinstance(expr.args[0], ast.StringLiteral)`. The AST now wraps the literal in a list (the list shape is handled at `codegen.py:4666`). The call site (`codegen.py:3659-3662`) runs the rewrite only in whole-program mode. The saving of 84,400 B over 24 programs holds for whole-program mode only.

User-code part (the real codegen gap, about 170 to 400 B per kernel):

- snd-cmdq: 1278 B against 883 B.
- snd-ymreg: 719 B against about 549 B.
- snd-seq: 1944 B against about 1550 B.
- snd-fnum: 821 B against 569 B.
- Over all 96 programs, summed user code is nearly equal (397 KB against 400 KB). The per-program geomean is 1.12x. The worst cases are small integer kernels at 1.5x to 2.3x (sieve, tak, queens, stanford-bubble, stanford-quick).

Caveats:
- The split between user code and library comes from per-module sizes. No analyst gave a per-kernel table that adds up to the image size for both compilers. The user-code figures are estimates of about 10% accuracy.
- The SDCC "empty" image already holds putchar and getchar (46 B). uc80 charges its console routines (57 to 70 B) to each printing program. The bias is about 60 B per program.
- uc80 is already leaner than SDCC in some places: its integer printf, its crt0 and its multiply helpers. Its divide helpers are larger (see 2.D).

### 1.2 The speed gap is the calling convention first, local storage second

Profiles came from `z88dk-ticks -trace` with a per-instruction T-state table. The tables matched ticks to within 0.1% (uc80) and 0.5 to 4% (SDCC).

| Kernel | uc80 T | SDCC T | Gap | Main sources |
|---|---|---|---|---|
| snd-cmdq | 14.18M | 9.03M | 5.15M | out() and its callers, memory locals |
| snd-ymreg | 22.22M | 15.26M | 6.94M | frame_update loop, in/out/ym_wait call overhead |
| snd-seq | 15.38M | 12.99M | 2.39M | reg_set, out, sine8, note_base |
| snd-fnum | 18.83M | 17.65M | 1.18M | out() (+2.63M), offset by faster divide (-2.70M) and multiply (-0.57M) |

#### (a) What SDCC's design does that uc80 does not

1. Register calling convention (sdcccall(1)). SDCC passes the first byte in A, a second byte in L, a word in HL or DE, and returns bytes in A. A leaf function needs no frame.
   - uc80 widens every argument to a 16-bit word and pushes it (`codegen.py:11171-11400`).
   - The caller pops the arguments (`codegen.py:11460-11501`).
   - Each function builds an IX frame (`push IX / ld IX,0 / add IX,SP`, `codegen.py:5233-5237`). Each argument read is `(IX+d)` at 19 T.
   - `_frame_is_unused` (`codegen.py:5357-5373`) drops the frame only when no `ix` text occurs in the body (`_IX_USE`, `codegen.py:5354`). A parameter read always names IX. The epilogue that deletes the prologue is at `codegen.py:5330-5345`.
   - One `out(port,val)` call costs 304 T in uc80 and 167 T in SDCC. The call site costs 69 T and 10 B in uc80, against 24 T and 5 B in SDCC.
   - Analyst what-if, by kernel: cmdq -3.67M T and -174 B. ymreg about -7.4M T (this includes some local-variable gain) and -145 B. seq -4.4M T and -261 B. fnum -3.69M T and -93 B.
   - Reviewer re-measure on cmdq: -2.35M T and -155 B for out() alone, and -3.03M T and -166 B with nmi_cmd and dispatch. Plan with about 3.0M T for cmdq.
   - Small-loop programs (analyst): tak -13.07M T (-27.7%), stanford-perm -82.6M T, stanford-towers -43.4M T.
2. Registers for locals. SDCC keeps loop counters, pointers and temps in BC, DE and HL. uc80 puts every named local in static `??AUTO` memory or in the IX frame (`codegen.py:5208-5322`, `1849-1900`). Shared storage can be switched off (`--no-shared-storage`, `main.py:370`). Then locals use the IX frame at 19 T per access. With shared storage on, an access costs 13 to 16 T. A register move costs 4 T. The analyst profiles used the default mode. The `RegisterAllocator` at `codegen.py:1010-1040` holds expression temporaries only.
   - Analyst what-if: cmdq -1.53M T and -149 B. ymreg -2.48M T and -31 B. sieve (with the count update still live) -360K T and -42 B. queens -11.0M T (-32%), which closes the whole gap to SDCC. ttt (a `register int p` in BC) -2.51M T.
   - Reviewer re-measure on cmdq: -1.72M T and -109 B. The bytes are 27% below the analyst figure.
3. Struct pointer in a register. SDCC keeps a struct pointer in BC. uc80 rebuilds `ld HL,(ptr) / ld DE,off / add HL,DE` for each field (44 T). With IY as the pointer, `ld A,(IY+11)` costs 19 T. Measured on snd-seq on top of register arguments: tick -1.41M T and -112 B, fetch -0.205M T and -138 B.
4. Peephole rules. SDCC removes redundant moves, merges `call` and `ret` into `jp`, narrows byte compares to `cp (HL)` and keeps a byte in A. No analyst measured the total of these rules. The per-rule figures in 1.2(b) are the only data.

#### (b) Fixable local inefficiencies in uc80 (not design differences)

- Dead printf rewrite (see 1.1).
- Unsigned 8-bit `/` and `%` by a constant use the signed 16-bit chain `__smod16 / __div16 / __dv8w / __dv8n`. The compiler chooses the 16-bit helper at `codegen.py:8619-8646` (binary operators) and `codegen.py:10040-10043` (compound assignment). The name `__dv8n` is not in `codegen.py`. The helper `__div16` reaches `__dv8n` inside `lib/rt`. The cost is 628 T per operation in fnum and about 200 T in cmdq.
- Byte values widened to 16 bits and narrowed again:
  - the switch key is computed in 16 bits (`_SWITCH_CHAR_FORMS`, `codegen.py:7754-7820`): -11 B and -172K T in cmdq.
  - `ld HL,imm` where `ld L,imm` is enough.
  - a dead `ld H,0` before each call: -26 B and -39K T.
  - `(int8_t)` to `(uint8_t)` double extension chains.
  - On the ttt program, char compares through int-typed locals cost 564 B and 1.56M T.
- `push AF / ld E,(HL) / pop AF / cp E` where `cp (HL)` works (`codegen.py:17682-17686`, 15742, 14720). In ymreg `ym_write` alone this saves 694K T and 6 B.
- `call` then `ret` is not turned into `jp` (93K T in cmdq, 393K T in the ack test). This pays only after the register convention exists.
- No CSE of repeated array element addresses. Hand CSE saves 89.2M T (-17.7%) on towers and 204.6M T (-42.4%) on bubble sort (`codegen.py:11847-11895`, 13515). These are small-loop programs. No snd kernel has a measured CSE gain.
- No dead-store elimination. In sieve, the unused `count` costs 1.87M T (-48%). SDCC also removes this variable, so the gain is partly a benchmark effect.
- `memset` and `memcpy` with a constant size are not inlined as LDIR (`codegen.py:11064`, 11123-11130).
- Chained assignment `a[i]=b[j]=1` goes through HL and DE (queens: 555K T).

### 1.3 The sums exceed the gaps

The what-if savings add up to more than each gap (cmdq: 5.78M saved against a 5.15M gap. ymreg: 10.5M against 6.94M). Two reasons:
- uc80 is already ahead on multiply (`__mul8x16`) and on divide for fnum, and on `switch` jump tables for seq. Analysts measured these leads on fnum and seq only.
- The steps overlap. The ymreg figure for the calling convention already includes some local-variable gain, and the local-register step counts it again. Part of the excess is double counting.

For this reason the per-cause figures are marginal numbers against the uc80 baseline. Do not add them across families.

## 2. What it takes

Families are in order of gain per effort. Effort figures include the reviewer corrections. The peephole and AST passes need more work than the analysts assumed (see E and H).

| # | Family | Type | Effort (days) | Gain | Measured |
|---|---|---|---|---|---|
| A | Move `_prt_float` and its data out of the printf core | Packaging | 0.5 | -2.5 to -2.85 KB per printf program | Yes |
| B | Fix the `printf` to `puts` rewrite | Bug fix | 0.5 to 1 | -84,400 B over 24 programs (whole-program mode) | Yes |
| C | Split string/mem, stdlib, atoi, ctype, wchar, malloc, file into one function per module | Packaging | 2 to 3 | -29,922 B (string/mem) and -34,747 B (rest) | Yes |
| D | Narrow u8 divide and modulo by a constant, and drop dead helpers | Selection | 1.5 to 2 | cmdq -170 B and -146K T; fnum -4.5M T and -223 B | Yes (reviewer confirmed). Done: cmdq -196 B and -272K T; fnum -196 B and -4.86M T |
| E | Byte-width and peephole clean-up | Peephole, new pass or many codegen edits | 5 to 7 | cmdq -55 B and -339K T; ymreg ym_write -694K T; ttt -564 B | Yes, low confidence on effort. Done: cmdq -81 B and -330K T; ymreg -27 B and -1.01M T; seq -72 B and -852K T; ttt -394 B and -1.26M T |
| F | Register calling convention for internal functions | Calling convention | 12 to 20 | cmdq about -3.0M T; ymreg about -7.4M T; seq -4.4M T; fnum -3.69M T; tak -27.7% | Yes (upper bounds) |
| G | Register residency for locals (BC, DE, IY) | Register allocation | 8 to 10 (simple), 15 to 20 (full) | cmdq about -1.7M T and -110 B; ymreg -2.48M T; seq about -1.6M T and -250 B (IY); queens -32% | Yes (upper bounds) |
| H | Block-local CSE, dead-store elimination, inline LDIR | New typed pass | 8 to 12 | towers -17.7%; bubble -42%; sieve DSE -48% (small-loop programs only) | Yes, no snd kernel |

### A. Float printer split (0.5 day, near zero risk)

- Change: put `_prt_float` and its data (`_prt_float_prec`, `__ftmp`, `__fint`, `__fwork`, `_pbig_m`, `_prt_big`) in `lib/lc/lc_prt_float.mac`. Only `lc_printf_f.mac`, `lc_printf_e.mac` and `lc_printf_g.mac` reference the new module.
- Where: `src/uc80/lib/lc/lc_printf_core.mac:680-1060` (EXTRNs at 685-691, data at about 1150-1163) and `lib/build_libs.py`.
- Source of truth: `lib/split_libc.py` is a one-shot script. It reads `libc.mac` and writes `lib/lc/` with fixed line ranges. Its header says printf stays monolithic. Edit `lib/lc/*.mac` directly. Do not run `split_libc.py` again, because it could overwrite the edits.
- Test: a link-size test (`printf("%d")` stays under about 1500 B), a `%f` program with identical output, `run_ctests.py` (218/220 baseline), and the bench verify run.
- Risk: data labels that two modules share must be PUBLIC in one module and EXTRN in the other.

### B. Printf rewrite fix (0.5 to 1 day, low risk)

The analyst described the fault as a list-wrapped literal. A reviewer found more defects in `_rewrite_printf_to_puts` (`codegen.py:4797-4850`). The function reads `.value` as a plain string, but other code reads `arg.value.text`. The raw literal holds escape sequences, not a real newline. The function builds `StringLiteral` and `Identifier` nodes with plain strings, but later passes read `.name.text` and `.value.text`.

- Change: accept a list of `StringLiteral` nodes. Decode the literal with the same decoder as `decoded_format` (`codegen.py:4666`). Build token-compatible nodes with `dataclasses.replace`. Handle `printf("%s\n", s)` and the no-newline case through a new format path.
- Limit: the rewrite runs only in whole-program mode (`codegen.py:3659-3662`). Separate-compilation builds get no saving.
- Test: `printf("hi\n")` must not link `LC_PRINTF_CORE` (the image drops from 3852 B to 171 B). The other 79 .mac files must stay byte-identical.
- Risk: `printf` returns a count and `puts` returns a different value. Rewrite only when the result is unused.

### C. Libc module split (2 to 3 days, low risk)

- Change: a splitter that cuts each `lc_*.mac` at the PUBLIC boundaries. It copies EQU constants into each part and makes shared data PUBLIC in one module. Keep `bsearch` in its own module. Keep fall-through aliases (for example `_div32` and `_rem32`) together. Leave `lc_qsort` for later. Edit `lib/lc/*.mac` and `lib/build_libs.py`.
- Test: bench verify run against the golden files on all programs (95 of 95 matched in the experiment).
- Risk: a hidden fall-through or shared static between two functions.

### D. u8 divide and modulo (1.5 to 2 days, low to medium risk)

- Change: when the left operand is a zero-extended u8 and the divisor is a constant from 1 to 255, call `__dv8n` directly (quotient in E, remainder in A). The compiler promotes a u8 to int, so the test needs a value-range check in the style of `_byte_kind`, not a type test. Fuse `a / k` and `a % k` of the same operand into one call.
- Where: `codegen.py:8619-8646`, `10040-10043`, `_is_promoted_unsigned` at `codegen.py:16691`, `lib/rt/rt_div8.mac`.
- Dead helpers: check that `runtime.py` and `asm_dce.py` drop `__dv8w`, `__sdiv16`, `__smod16` and `__div16` when no caller is left. The byte gain (199 B in fnum, 172 B in cmdq) depends on this.
- Reviewer data: the fnum gain of -4.5M T needs the fuse step. Without the fuse step the gain is about -2.5M T. A reciprocal multiply gives -5.50M T but adds 23 B.
- Test: pytest for exhaustive u8 by k results (0..255 by 1..255 is cheap), `difftest.py`, bench snd-cmdq and snd-fnum.
- Risk: the range test must be correct. A signed operand must stay on the signed path.

### E. Byte-width and peephole clean-up (5 to 7 days, medium risk)

`asm_dce.py` (629 lines) is a block-level dead-code pass. It is not a peephole engine. Peephole code lives in `codegen.py` (19.7k lines). The `Uc80ASTOptimizer` in `ast_opt.py` (270 lines) knows no variable types. So E is either a new peephole pass or many edits in `codegen.py`.

- Change:
  - route promoted-u8 expressions that fit a byte through the existing `_gen_expr_a` and byte-switch paths (`codegen.py:7754-7820`, 14608).
  - drop the dead `ld H,0` before calls and use `ld L,imm`.
  - use `cp (HL)` for `*p` compares (`codegen.py:17682`, 15742, 14720).
  - delete `ld H,0 / ld H,l` pairs and sign-extend then zero-extend pairs.
  - turn `(int8_t)x >= 0` into a bit-7 test.
  - turn `call x / ret` into `jp x` where no cleanup is between them.
  - extend `_byte_kind` to int locals that come from chars (ttt: -552 B).
- Test: pytest, `run_ctests.py`, `difftest.py`, the bench harness.
- Risk: flag-state assumptions in later rules. Add each rule alone and run the full tests after each one.

### Step 2 of family F (measured on 2026-10-26)

Step 2 is in the code (branch `regcc`, see [calling_convention.md](calling_convention.md)). It has three parts. All numbers are `bench/run.py` (uc80, size mode) on the 104 programs that are OK, from the same tree, step 2 against step 1.

- Byte results in A. A function that returns a char returns the byte in A when its calls use the byte as a byte at least as often as they need an int (a guess from the syntax). The callee does not widen. A call that is a statement, a condition, a comparison, a store to a char or a `return` in a char function does not widen either. Gain: 110 bytes in the 104 programs (`davidly-ttt` -33 B and -0.8% T-states, `coremark-unofficial` -15 B). A first version without the guess made `snd-seq` 5 bytes bigger and 14K T-states slower, because two calls needed an int and paid for the widening that one callee saved.
- A parameter across a call by push and pop. When a register parameter has one store and one load left in straight-line code, the store is `push` and the load is `pop` (section "A parameter across a call"). Gain: 250 bytes. `stanford-perm` 361.6M to 356.0M T-states (-1.5%), `binary-trees` -1.05%, `snd-cmdq` 10.72M to 10.60M (-1.05%), `stanford-towers` -0.8%, `davidly-ttt` -0.8%.
- Tail calls. `return g(...)` in a function with no stack local becomes `pop IX` and `jp _g`. Gain: 50 bytes (`tak` -5 B, `coremark-unofficial` -9 B). The time effect is small, because few hot paths end with a call.

Totals for step 2 against step 1: the image of the 104 programs falls by 408 bytes (611,907 to 611,499). 41 programs get smaller, none gets bigger. 21 programs get faster and one gets slower (`embench-tarfind`, +1,269 T-states, 0.04%). Geometric means against SDCC 4.6.0, with the method of `bench/report.py`:

- Size, minus the empty program: 0.9757 (no register convention), 0.9484 (step 1), 0.9476 (step 2)
- Size, whole image: 0.9374, 0.9117, 0.9110
- T-states against the speed mode, 78 programs: 0.6228, 0.6020, 0.6015
- T-states without the `sort-*` programs, 70 programs: 0.7662, 0.7377, 0.7370

Step 2 gains much less than step 1 (-0.0008 on the size mean and -0.0005 on the T-state mean, against -0.0273 and -0.0208). The kernels that motivated family F have few char results and few calls at the end of a function. The remaining distance to SDCC on `snd-cmdq` (10.60M against 9.03M) is not in the calling convention any more. It is in locals in memory (family G) and in the code of the loops (families D, H).

### F. Register calling convention (12 to 20 days, medium to high risk)

- Change: a second convention for functions that the compiler can prove are internal. First byte in A (or first word in HL). Second byte in L (or word in DE). Third byte in E. Byte results in A, word results in HL. The caller does not pop. A leaf with no spills gets no IX frame.
- Which functions qualify: in whole-program mode, every function that no library, asm or function-pointer reference can reach. A `static` keyword is not needed. In the snd kernels `out()` and `in()` are not `static` and the .mac declares `PUBLIC _out`. The rule must still cover them, or most of the gain is lost. Test this first. If the rule cannot cover them, emit a register entry point plus a stack-convention wrapper, and count the wrapper cost.
- How parameters are held: the callee spills a parameter to its `??AUTO` slot when the parameter lives across a call. Analyst edits differed here. The ymreg ym_write edit and the fnum edit stored arguments to `??AUTO` on entry. The cmdq `out()` edit used registers inside the body. A spill costs 13 T per access, the same as a memory local today. Part of the F gain therefore depends on G. Measure F with spill on entry before you commit to the gain.
- Convention-selection prepass: it must decide "internal and address not taken" before any call is generated. This includes forward calls and both whole-program and separate-compilation modes.
- Interactions in the same call code: struct returns (`__sret_buf`, `codegen.py:2081`, 6256), builtins (`codegen.py:11062`) and varargs.
- Where:
  - prologue and parameter loads: `codegen.py:5233-5300`, 5330-5345, 5357-5373, 970-985, 17162-17176.
  - call sites: `codegen.py:11171-11400`, 11460-11501.
  - returns: `gen_return` at `codegen.py:7445`.
  - tail call: epilogue at `codegen.py:5330-5345`.
- Phased plan (step 1 is done, see the status above): (1) leaf functions with 1 or 2 scalar parameters (out, in, ym_wait, sine8). The reviewer measured -2.35M T for `out()` alone on cmdq. (2) non-leaf callees with a spill. (3) byte returns in A. (4) `jp` tail call.
- What a convention change breaks:
  - Hand-written `lib/rt` and `lib/lc` assembly uses the stack convention. Keep the stack convention for every exported function, every address-taken function and every library function.
  - Indirect calls and function pointers need the stack convention.
  - Varargs (`printf`) stay on the stack.
  - Existing asm interop and `extern` declarations stay on the stack form.
  - Recursion: a register parameter that lives across the recursive call needs a stack spill (push/pop), not a static `??AUTO` slot.
  - ISR and reentrancy: static `??AUTO` slots are already non-reentrant. Do not add register arguments to an ISR or to a function that an ISR also calls.
  - `asm_dce.py` and the peephole rules must know that A, L and E carry live values at function entry.
- Test: pytest, `run_ctests.py` (218/220), `difftest.py`, then every snd kernel and the small-loop programs (tak, queens, perm, towers, ttt). Add a test with a recursive function, one with a function pointer, one with `printf`, one with a struct return and one with an asm callee.

### G. Register residency for locals (8 to 10 days simple, 15 to 20 days full, low confidence)

The `RegisterAllocator` class (`codegen.py:1010`) has bc and de descriptors with auto-spill, but analysts found it tracks expression temporaries only. `_hl_word_known` (`codegen.py:15204`) is a per-expression HL cache. It is not a model for resident locals. IY is unused by the code generator today.

- Prerequisite (inside the effort figure): remove every BC and DE overwrite from expression idioms. Many array paths use `ld BC,_arr / add HL,BC` (`_gen_address`, `gen_index`). The first cmdq edit of the reviewer gave a wrong CRC (38711 instead of 17781) because `dispatch` clobbered the resident register. Callees such as `out`, `cmd_pop` and `nmi_cmd` also needed a rewrite or a push/pop. The 34 T inline pattern worked in queens.
- Simple version: leaf functions and counted-loop variables. Pick the 1 to 2 scalars with the highest use count weighted by loop depth. They must not be address-taken or volatile. Assign them to BC and DE (a byte variable to C, E, B or D). Honor `register` as a priority hint (`decl_storage_class`, `codegen.py:371`). Save and restore BC or DE around calls with push and pop (21 T and 2 B per call). Also handle shared-storage `??AUTO` allocation and `CallGraphAnalyzer` (`codegen.py:1235`), which assume locals are memory.
- IY as a struct pointer base (about 2 days within this family): `ld A,(IY+d)` at 19 T. Save and restore IY at call boundaries.
- Full version: a liveness-based allocator with call-clobber handling and spill code.
- Where: `codegen.py:1010-1040`, 5208-5322, 5862-5973, 7599.
- Test: the same as family F, plus the sieve, queens, tak and ttt timings.
- Risk: the highest in the plan.

### H. CSE, dead-store elimination and inline LDIR (8 to 12 days, medium risk)

`ast_opt.py` is a thin subclass of the external `uc_core` optimizer. It has no types, no alias data and no dead-store logic. H therefore needs a new typed pass in `codegen.py`, or a change to `uc_core`. All measured gains are on small-loop programs. No snd kernel has a measured H gain. Put H after G in priority.

- Change:
  - block-local CSE of pure address expressions. Invalidate on a write to the index variable or on a call.
  - a dead-store and dead-local pass (iterate, because removing `count -= !flags[k]` makes `!flags[k]` dead).
  - inline `memset` and `memcpy` with a constant size as LDIR (`codegen.py:11064`, 11123-11130).
  - store a constant directly into each lvalue of a chain `a=b=K`.
- Test: pytest, `difftest.py`, bench towers, bubble, sieve.
- Risk: aliasing. Restrict CSE to global arrays that are indexed by a local that is not written in between.

### What needs what

- Packaging and bug fixes: A, B, C (about 3 to 4.5 days).
- Local selection or peephole work: D, E, H (about 15 to 21 days).
- A calling-convention change: F.
- A register allocator (a simple one is enough for the snd kernels): G.

## 3. Realistic projection

Size (measured for A, B and C, estimate for the rest):

| Stage | snd kernels vs SDCC (size mode) | Geomean over 96 programs |
|---|---|---|
| Now | 1.35x to 1.47x bigger | 1.53x |
| After A to C (about 3 to 4.5 days) | smaller than SDCC on all four (cmdq 3064 B against 4038 B; ymreg 2499 against 3797; seq 4187 against 4985; fnum 2937 against 3765) | 1.014 (measured) |
| After D to H | about 0.6x to 0.8x (cmdq analyst chain 2513 B against 4038 B) | estimate only, about 0.9x to 1.0x. Not measured. The cost of push/pop around calls in G is not counted. |

Speed (T-states). Each row is cumulative, except where the text says it is stand-alone.

| Stage | snd-cmdq | snd-ymreg | snd-seq | snd-fnum |
|---|---|---|---|---|
| SDCC speed mode | 9.03M | 15.26M | 12.99M | 17.65M |
| uc80 now | 14.18M | 22.22M | 15.38M | 18.83M |
| After A to C | 14.18M | 22.22M | 15.38M | 18.83M |
| After D and E (measured) | 13.72M | 21.21M | 14.52M | 13.69M |
| After F on top of D and E (estimate) | about 10.5M | about 14.9M | about 11M | about 10.6M |
| Analyst chain F, G, H, D, E (not independently reproduced) | 8.40M | 11.68M | 7.85M | 9.03M |

Notes on the table:
- The fnum chain uses the reciprocal multiply (-5.50M T), not `__dv8n` (-4.53M T). The fnum win over SDCC depends mainly on D.
- The 9.03M figure for fnum is a separate measurement. It is not a copy of the SDCC cmdq time. The ymreg edit also counted one step with overlapping F and G gains.
- A reviewer added the independent single-family results for cmdq: 3.03M (F) + 1.72M (G) + 0.15M (D) = 4.90M T. This gives about 9.3M T, which is still above SDCC (9.03M). The analyst chain reaches 8.40M with E and H steps (about 0.8M T) that the reviewer did not re-measure. Expect between 8.4M and 9.3M T.

Honest conclusion:

- The size gap closes in about 3 to 4.5 days by packaging fixes alone. Do these first. No register allocator is needed.
- The speed gap does not close with peephole work. Family F is the largest item. It removes more than half of the cmdq gap and most of the seq, ymreg and fnum gaps. Part of the F gain needs parameters to stay in registers, so F and a simple G are best planned together.
- With F plus a simple G, the hand-edited snd kernels reach about SDCC speed on cmdq and pass SDCC on ymreg and seq. On fnum, the win comes mostly from D.
- A full register allocator is needed to match SDCC on small recursive integer code (tak, ttt, stanford-towers, davidly-ttt). After the hand edits, 14% to 37% of the gap stays open on towers and ttt.
- The speed mean over all 96 programs is not measured. uc80 is faster overall because of its library, but no figure supports the size of that lead.
- Total cost with the corrected efforts: about 6 days for A to D, about 50 to 70 days for all families. These are estimates.

The hand-edited .mac files of the analysts and reviewers were in a temporary folder and are not kept. Each family section gives enough detail to redo its edit.
