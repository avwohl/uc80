# Why SDCC is smaller and faster than uc80, and what it takes to fix uc80

Scope: this report excludes the ROM target issue.

Evidence rules:
- "Measured" means a hand-edited uc80 .mac file that an analyst re-assembled, re-linked, re-timed and checked against the golden output.
- Reviewers re-did three families (D, F, G) on snd-cmdq and snd-fnum. The differences are in section 2.
- "Estimate" means no measurement exists.
- Hand edits give upper bounds. A compiler pass will gain less.
- Size means the image size (`__BSS_START - 0x100`, the 6th column of `bench/results/results.csv`). The older figures in `docs/benchmarks_speed.md` (5866 B and 3926 B for snd-cmdq) come from an earlier run.

## Status on 2026-10-29: family I, step 2 (loop optimisation) is in the code (branch `fami`)

Step 2 has three parts. Each part has a switch. Part 2.1 moves an invariant integer expression out of a loop ([licm.md](licm.md), `--no-licm`). Part 2.2 finds counters in 0 to 32767 and uses the unsigned compare for them ([range_compare.md](range_compare.md), `--no-range-cmp`). Part 2.3 computes a 32-bit array index in 16 bits ([narrow_index.md](narrow_index.md), `--no-narrow-index`).

Differences from the plan:

- Part 2.3 is the module `src/uc80/narrow.py`, not a rule in `ast_opt.py`. The AST optimizer does not know the types of variables.
- Part 2.2 is more than a set of names. The pass finds a range for each tracked local from the ranges of its stores (so `k = i + prime` in the sieve is in range), and a counted `for` statement gives its counter a range inside the loop although the function stores to the counter elsewhere (so `y` and `x` in `tc_wr` are in range). The plan had only the set of names with constant stores.
- Part 2.1 moves an expression only when the loop evaluates the expression in each pass. The first version of the pass (every invariant expression of cost 40 or more) made `umchess` 11% slower and `embench-nettle-sha256` 503 bytes bigger. The slow part was an expression in a rare branch. The big part was the `ROUND` macro, a `do { ... } while (0)` loop that runs once.

Numbers: `bench/run.py -c uc80`, 112 rows, no status change. No program is slower. Image: `micro` and its 9 groups have 7 bytes more (one new local), 3 programs have 2 to 12 bytes more BSS, and the sum of all images falls by 1,584 bytes. Against SDCC 4.6.0 (`bench/report.py` method): size 0.9157 to 0.9128, whole image 0.8831 to 0.8805, T-states (speed mode) 0.5304 to 0.5274, without `sort-*` 0.6406 to 0.6366. uc80 is faster than SDCC on 66 of 78 programs (65 before). The ratio of the T-states of uc80 to SDCC (speed mode, whole run), step 1 to step 2: `worm` 1.255 to 0.907, `mille` 1.210 to 0.887, `canfield` 1.206 to 0.883, `coremark-unofficial` 1.182 to 0.892, `davidly-sieve` 1.259 to 1.195, `davidly-ttt` 1.575 to 1.523. The programs `bsd-ppt`, `bsd-bcd`, `queens`, `snd-ymreg`, `snd-cmdq`, `sprintf` and `davidly-tm` do not change. The geometric mean of the 13 programs falls from 1.098 to 0.992. The model of the analysis (hand edits) predicted 0.899 for all steps of the family, with the loop steps worth 0.93 for the loop-invariant part. The programs of the curses family fall by 20% to 27% (`curses-demo`, `robots`, `snake`, `invaders`, `worm`, `mille`, `canfield`).

- Still above SDCC (step 2 did not aim at these): `bsd-ppt` 1.597 and `bsd-bcd` 1.390 (console path), `davidly-ttt` 1.523 (byte compares and the minimax body), `davidly-sieve` 1.195 (the loop counters of `main` stay in memory), `queens` 1.134, `snd-cmdq` 1.033 and `snd-ymreg` 1.012.
- Not done in step 2: byte-granular allocation of byte locals (three or four of them in B, C, D and E), the load CSE of `t->reg`, and the BSS clear with a push fill. A range for a counter that is not a 16-bit `int` (the byte path has its own compare) and a range that crosses a function call are not in the pass.

## Status on 2026-10-28: family I, step 1 (the sound-driver bundle) is in the code (branch `fami`)

Family I is the set of changes that the analysis of the 13 programs above SDCC speed named (the programs where uc80 is slower than SDCC in speed mode, among them the two Neo Geo sound kernels `snd-ymreg` and `snd-cmdq`). Step 1 has four parts. Part 1.0 is the library: `_prt_div16` and `_div32_10` use 16 and 32 shift and subtract steps, and `putchar` and `__conout` have a short path for a character that is not LF. Part 1.1 is the discarded comma and the pair peephole rules ([peephole_pairs.md](peephole_pairs.md)). Part 1.2 is slot folding, and part 1.3 is the flow graph that knows calls that do not return ([register_locals.md](register_locals.md)).

Numbers: `bench/run.py -c uc80`, 109 programs, no status change. Against SDCC 4.6.0 (`bench/report.py` method): size 0.9162 to 0.9157, whole image 0.8837 to 0.8831, T-states (speed mode) 0.5456 to 0.5304, without `sort-*` 0.6611 to 0.6406. uc80 is faster than SDCC on 65 of 78 programs (63 before). The ratio of the T-states of uc80 to SDCC (speed mode, whole run) for the programs of family I: `snd-ymreg` 1.142 to 1.012, `snd-cmdq` 1.129 to 1.033, `sprintf` 1.371 to 0.311, `davidly-tm` 1.234 to 0.957, `bsd-bcd` 1.581 to 1.396, `bsd-ppt` 1.743 to 1.597. The programs `davidly-ttt`, `davidly-sieve`, `worm`, `mille`, `canfield`, `coremark-unofficial` and `queens` change by less than 0.5%. The geometric mean of the 13 programs falls from 1.297 to 1.098. The analysis predicted 1.026 and 1.040 for the two sound kernels with the same steps, so the figures are close to the model.

- Not done in step 1: the BSS clear in `crt0.mac` keeps `LDIR`. A push fill needs `DI` and a read of IFF2 with `LD A,I`, which has a known error on early Z80 chips. An unrolled fill saves only 2.7k T-states of 162k in `bsd-ppt` and adds bytes to every program.
- Not done in step 1 (steps 2 and later): byte-granular allocation of byte locals (three or four of them in B, C, D and E), the load CSE of `t->reg`, loop-invariant code motion, the unsigned compare of a counted loop index, and the narrowing of 32-bit index arithmetic.

## Status on 2026-10-28: family H is integrated with the ROM work (branch `integrate8`)

The three passes of family H (address CSE and chained constant stores, dead locals and overwritten stores, inline block operations) and the register-locals change for names under `&` are merged with `main` (register locals, naked functions, RST and IM 2 vectors, ROM wave 4). The merge needed no change of a pass. `tests/test_famh_rom_integration.py` runs the interactions in the ROM runner, in the um80 flow and in the sdas flow: the ROM pass for constant-address pointers (`ptrabs.py`) runs first, and the three passes keep every volatile access, every store through a constant-address volatile pointer, the low-byte-first order of 16-bit volatile stores (also for a structure copy to a volatile target), the work areas of an interrupt function (`--isr-save-workareas`), naked functions, and separate compilation with a BSS block for each module.

Numbers: `bench/run.py -c uc80`, 109 programs, no status change. Against SDCC 4.6.0: size 0.9162, whole image 0.8837, T-states (speed mode) 0.5456, without `sort-*` 0.6611. uc80 is faster than SDCC on 63 of 78 programs. `main` alone: 0.9353, 0.9000, 0.5628, 0.6844, 60 of 78. `famh` alone: 0.9164, 0.8838, 0.5459, 0.6615, 63 of 78. See the entry of `integrate8` in the [CHANGELOG](../CHANGELOG.md).

- Not done in family H (unchanged): value CSE, a flow graph for the dead-store part, inline `memset` and `memcpy` when the program uses the result.

## Status on 2026-10-28: family H, part 3 (inline block operations) is done

Part 3 of family H is in the code (branch `famh`, [blockops.md](blockops.md)). The pass is `src/uc80/blockops.py`. A call of `memset` or `memcpy` with a constant size and an unused result becomes `ldi` (up to 4 bytes) or `ld bc,N` and `ldir`. A fill stores the first byte and copies the byte with `ldir`. A constant address goes straight into HL or DE. `memmove`, a volatile argument, a size that is not constant and a used result keep the call. The same copy serves a structure assignment and a structure initialisation, and a zero initialiser of a local array in shared storage is one `ldir` fill. `--no-blockops` turns the pass off. The register-locals pass got two small changes (`ldi` does not read BC, and wrap intervals that touch join) so that a loop counter in BC survives an unrolled copy.

Numbers: `bench/run.py` (uc80), the 109 programs of the size run, against the tree of part 2 (6cc473b). Status: no change. Image: 35 programs smaller, 12 bigger (the largest `monop-size` +21 B), total 774,824 to 774,053 bytes. T-states: 28 programs faster, 3 slower (`embench-md5sum` +0.03%, which is a code-address effect, `mille` and `fasta` +0.001%). Against SDCC 4.6.0 (`bench/report.py` method): size 0.9216 to 0.9164, whole image 0.8879 to 0.8838, T-states (speed mode) 0.5466 to 0.5459, without `sort-*` 0.6624 to 0.6615. uc80 is faster than SDCC on 63 of 78 programs (62 before).

- The gain is small because the benchmark programs have few constant-size block operations. The hot memset or memcpy sites that exist are in `sieve` (-33 B), `embench-statemate` (-3.17% T-states), `zx0` (-1.14%) and `embench-huffbench` (-104 B).
- What is not done in family H: value CSE, inline `memset` and `memcpy` when the program uses the result, a fill with a size over 4 that unrolls word stores for speed, and a flow graph for the dead-store part.

## Status on 2026-10-28: family H, part 2 (dead locals and overwritten stores) is done

Part 2 of family H is in the code (branch `famh`, [dead_store.md](dead_store.md)). The pass is `src/uc80/deadstore.py`. It works on the AST of each function, before the address CSE pass. A local that no expression reads is removed with all its stores. A store that a later store of the same statement list overwrites is removed too. The rest of the right side stays as a statement when it has an effect (a call, `++`, a store, a volatile access). The pass repeats until nothing changes, so `count -= !flags[k]` goes first and then everything that only fed it. A global, a static, a volatile, a name under `&`, a parameter and a function with inline assembly, `setjmp` or a statement expression are never changed. `--no-dead-store` turns the pass off.

Numbers: `bench/run.py` (uc80, size mode), 109 programs, against the tree with `--no-dead-store` (the tree of part 1). Status: no change. Image: 26 programs smaller, none bigger, total 775,324 to 774,814 bytes. T-states: 17 programs faster, 1 slower by 2 T-states (`embench-huffbench`, from a BSS that is 2 bytes smaller). Against SDCC 4.6.0 (`bench/report.py` method): size 0.9276 to 0.9216, whole image 0.8928 to 0.8879, T-states (speed mode) 0.5515 to 0.5466, without `sort-*` 0.6691 to 0.6624. uc80 is faster than SDCC on 62 of 78 programs (no change). Against the tree before part 1 (main, e28deae): size 0.9353, whole image 0.9000, T-states 0.5628, without `sort-*` 0.6844, 60 of 78.

- Target of the section: `sieve` -46.13% T-states and -49 B (the dead `count`; the report estimated 48%). The ratio of its T-states to SDCC (speed mode) goes from 2.51 to 1.35.
- Other programs: `fannkuch` -2.79% and -67 B, `dhrystone21` -0.55%, `embench-tarfind` -0.51% and -22 B, `whetstone` -0.26% and -43 B, `binary-trees` -54 B, `monop-size` -41 B, `coremark-unofficial` -34 B.
- No change: `sieve+static` (its locals are `static`, and a store to a static stays), `queens`, `stanford-*`, `tak`, `snd-*`.
- What is not done in family H: value CSE. The dead-store part has no flow graph: a store that is dead on some paths only stays. Inline LDIR for `memset` and `memcpy` is done, see the status of part 3.

## Status on 2026-10-27: family H, part 1 (address CSE and chained constant stores) is done

Part 1 of family H is in the code (branch `famh`, [addr_cse.md](addr_cse.md)). The pass is `src/uc80/addrcse.py`. It works on the AST before the frames are sized. A repeated element address (`a[i]`, `a[i + 1]`, `p[i]`, `s.arr[i]`, `p->arr[i]`, `a[i][j]`) becomes one new pointer local. The register-locals pass can move the pointer to IY, BC or DE. A key can hold local variables, loads from named arrays and global scalars. A run of statements ends at an assignment to a variable of the key, a label, and (for a key with a load) a call, a store through a pointer, a store into the array of the load and a volatile access. A program with an interrupt function gets no key with a load. The statement `a[i] = b[j] = K` with a constant K that fits each target becomes one store for each target when a target is a byte. `--no-addr-cse` and `--no-chain-store` turn the parts off. The change of the register-locals pass for the names under `&` ([register_locals.md](register_locals.md)) comes with this part.

Numbers: `bench/run.py` (uc80, size mode), the 109 programs of the size run, against the tree before (e28deae). Status: no change. Image: 56 programs smaller, 1 bigger (`bsd-bcd` +2 B), total of 107 programs 677,727 to 672,357 bytes. T-states: 48 programs faster, 2 slower (`snd-ymreg` +0.42%, `v7-arithmetic` +0.002%). Against SDCC 4.6.0 (`bench/report.py` method): size 0.9353 to 0.9276, whole image 0.9000 to 0.8928, T-states in speed mode 0.5628 to 0.5515, without `sort-*`: 0.6844 to 0.6691, in size mode 0.5437 to 0.5327 (without `sort-*`: 0.6585 to 0.6438). uc80 is faster than SDCC (speed mode) on 62 of 78 programs (60 before) and faster without `sort-*` on 54 of 70 (52 before).

- Targets of the section: `stanford-towers` -12.89% T-states and -47 B (the hand CSE gave 17.7%), `stanford-bubble` -12.45% and -40 B (hand CSE 42.4%), `queens` -20.57% and -24 B (the chain `col[c] = diag1[d1] = diag2[d2] = 1` is now three stores of the constant), `stanford-perm` -5.17% and -12 B (from the names under `&`, not from the CSE or the chain).
- Other programs: `coremark-unofficial` -21.55% and -628 B, `embench-matmult-int` -18.57%, `embench-ud` -5.95%, `fannkuch` -4.96%, `stanford-quick` -2.64%, `stanford-tree` -2.31%, `invaders` -2.22%, `dhrystone21` -2.01%, `curses-demo` -1.91% and -323 B.
- No change: `sieve`, `tak`, `snd-cmdq`, `snd-seq` and `snd-fnum`. `sieve` and `tak` have no repeated address, and the `snd-*` kernels no chain. `snd-ymreg` gets 0.42% slower and 3 bytes smaller from the change of the names under `&`: the cost model of the register-locals pass chooses a worse register for `ym_write`.
- The gain of `stanford-bubble` is smaller than the hand CSE because the loads of the long values stay: the hand version also keeps each value in a register. Value CSE is not done (see [addr_cse.md](addr_cse.md), "Not done").
- What is not done in family H: value CSE, dead-store and dead-local removal (the `count` of `sieve`), and inline LDIR for `memset` and `memcpy` with a constant size.

## Status on 2026-10-27: family G is done and merged with the bug fixes

Family G (steps 1 and 2) and the fixes of `bugfix2` are in one tree (branch `integrate6`). Family G is done: up to two scalar locals in BC and DE, and one pointer local in IY. The merge costs a little: the programs that use float get 56 to 71 bytes bigger (the NaN test in `__fcmp`). Means against SDCC 4.6.0, `bench/report.py` method, 96 programs for size and 78 for T-states:

- size minus the empty program: 0.958 (84cfebf), 0.934 (G alone), 0.935 (merged)
- whole image: 0.900 (merged)
- T-states against the speed mode: 0.585, 0.563, 0.563; without `sort-*`: 0.715, 0.684, 0.684
- T-states against the size mode: 0.5437 (without `sort-*`: 0.6585)
- uc80 is faster than SDCC (speed mode) on 60 of 78 programs and smaller on 66 of 96

The details of step 2 are in the next section. [register_locals.md](register_locals.md) says how the pass meets the code generator fixes.

## Status on 2026-10-27: family G, step 2 (a pointer in IY, tuning) is done

Step 2 adds one pointer local in IY (`src/uc80/iyptr.py`, [register_locals.md](register_locals.md)). A field access `p->m` becomes `ld A,(IY+d)` (19 T-states) and needs no HL, DE or BC. `p++` is `inc IY`. A call that can change IY, while the pointer is live, gets `push IY` and `pop IY` (the tag `; RYn`). `prune_iy` removes the wraps whose callees never change IY, and a runtime helper never changes IY. An interrupt function saves IY already. The proof `iy` of `bench/rom/isrproof.py` runs a function with IY, a callee with IY and an NMI function under a real NMI.

Numbers: `bench/run.py` (uc80, size mode), the 109 programs of the size run, step 2 against step 1 (7f20482). Status: no change. Image: 38 programs smaller, 1 bigger (`embench-huffbench` +3 bytes), total 601,579 to 600,061 bytes for the 104 programs with a result in every run. T-states: 38 programs faster, none slower. Against SDCC 4.6.0 (geometric mean, `bench/report.py`): size 0.936 to 0.934, whole image 0.901 to 0.899, T-states in speed mode 0.565 to 0.563, without `sort-*` 0.687 to 0.684. Against the tree before step 1 (84cfebf) the means are 0.959, 0.921, 0.585 and 0.715.

- `snd-seq` 12.92M to 12.02M T-states (-6.95%) and -118 B (the sequencer `tick` and `fetch` walk a `struct chan` pointer). `stanford-queens` -7.5% and -9 B. `stanford-tree` -2.7% and -35 B. `dhrystone21` -0.72% and -10 B. `embench-slre` -175 B, `mille` -365 B, `monop-size` -320 B, `hamurabi` -110 B, `robots` -110 B.
- `snd-cmdq`, `snd-ymreg`, `snd-fnum`, `sieve`, `queens`, `tak`, `stanford-perm`, `stanford-towers`, `stanford-bubble` and `davidly-ttt` do not change: they have no pointer that pays in IY. Their gains stay as in step 1 (against 84cfebf: cmdq -7.1%, ymreg -5.8%, fnum -4.1%, sieve -13.0%, tak -14.4%, perm -4.0%, towers -7.1%, ttt -1.7%, queens -0.75%).
- The IY gain over BC is small for a pointer that has few fields. `ld H,B / ld L,C` costs 8 T-states, and `ld A,(IY+0)` costs 19 against 15 for `ld A,(HL)` after the copy. IY wins when the pointer has many field accesses, because BC and DE stay free for two other locals, and a field at offset 4 or more costs 19 T-states against 28 with BC.
- Tuning (part b): the constants of step 1 are near the best value on a set of 32 programs. One change is better in every column: a local needs a weight of 1 (not 2) to be a candidate (-8 B, six programs smaller and six faster, none worse). The table of every constant is in [register_locals.md](register_locals.md). Each part of the pass has a measured gain on the same set: the rename of scratch values 1.7% of the T-states, the IY pass 0.8%, the second local in BC or DE 0.24%, the prune of the wraps 0.04% (BC and DE) and 0.09% (IY).
- What is not done: HL residency, more than two locals in BC and DE, a second pointer (IX is the frame pointer in a frame function, but free in a shared-storage function), a pointer parameter on the stack, and giving back the `??AUTO` slot of a local that moved.

## Status on 2026-10-26: family G, step 1 (simple register residency) is done

Step 1 of family G is in the code (branch `reglocals`, see [register_locals.md](register_locals.md)). A text pass moves up to two scalar locals of a function into BC or DE. The pass runs after the body is generated, so the code generator is unchanged. It renames or wraps the scratch uses of BC and DE (`ld BC,_arr` / `add HL,BC`, calls) and a checker proves each result. The `register` keyword is a priority hint. Interrupt functions and `main` are not changed.

Numbers: `bench/run.py` (uc80, size mode) on the 109 programs of the size run, from the same tree, step 1 of G against the tree before (84cfebf). Status: no change. Image: 87 programs smaller, none bigger, total 794,019 to 782,696 bytes. T-states: 79 programs faster, 1 slower (`micro+a32`, +0.15%). Mean against SDCC 4.6.0: size 0.95 to 0.94, T-states (speed mode) 0.59 to 0.56, without `sort-*` 0.71 to 0.69.

- `snd-cmdq` 10.60M to 9.90M T-states (-6.6%) and -43 B. `snd-ymreg` -5.5% and -38 B. `snd-seq` -5.0% and -73 B. `snd-fnum` -3.4% and -36 B.
- `sieve` -17.8% and -10 B. `tak` -14.4% and -41 B. `stanford-perm` -4.0%. `stanford-towers` -7.1% and -57 B. `davidly-ttt` -1.7% and -48 B.
- `queens` gains only 0.7%, and `stanford-bubble` gains nothing. The loop counters of `queens` sit in a recursive function, and every array index uses BC and DE as scratch registers. A local in BC then pays a push and a pop for each index. The chain assignment `a[i]=b[j]=1` keeps DE busy, so the rename cannot move the scratch pair. This is the prerequisite of section G: the expression idioms must stop using BC and DE. Family H (CSE of array addresses) is the fix.
- What was not done in step 1: IY as a struct pointer (done in step 2), HL residency, more than two locals, and giving back the `??AUTO` slot of a local that moved. The call graph analyser still reserves the slot.

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
- IY as a struct pointer base (about 2 days within this family): `ld A,(IY+d)` at 19 T. Save and restore IY at call boundaries. Done in step 2 (see the status section).
- Full version: a liveness-based allocator with call-clobber handling and spill code.
- Where: `codegen.py:1010-1040`, 5208-5322, 5862-5973, 7599.
- Test: the same as family F, plus the sieve, queens, tak and ttt timings.
- Risk: the highest in the plan.

### H. CSE, dead-store elimination and inline LDIR (8 to 12 days, medium risk)

`ast_opt.py` is a thin subclass of the external `uc_core` optimizer. It has no types, no alias data and no dead-store logic. H therefore needs a new typed pass in `codegen.py`, or a change to `uc_core`. All measured gains are on small-loop programs. No snd kernel has a measured H gain. Put H after G in priority.

- Change (part 1 is done, see the status of 2026-10-27 at the top):
  - block-local CSE of pure address expressions. Invalidate on a write to the index variable or on a call. Done.
  - a dead-store and dead-local pass (iterate, because removing `count -= !flags[k]` makes `!flags[k]` dead). Done, see the status of 2026-10-28.
  - inline `memset` and `memcpy` with a constant size as LDIR (`codegen.py:11064`, 11123-11130). Done, see the status of part 3.
  - store a constant directly into each lvalue of a chain `a=b=K`. Done.
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
