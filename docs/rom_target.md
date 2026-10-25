# ROM and freestanding target for uc80

Status: part built. Source: five investigations and three reviews on issue avwohl/uc80#1. The user guide for the finished parts is [rom.md](rom.md).

## Status of the plan

Done:

- Phase 0, strict mode: uc80 refuses `__interrupt`, `interrupt`, `__interrupt(1)`, `[[gnu::interrupt]]`, `__attribute__((interrupt))`, `__critical __interrupt` and a bare `__fastcall` with `uc80: error: file:line: ...` and exit code 1. The uc_core frontend has the option (`strict_unsupported`). Tests: `tests/test_strict_unsupported.py`.
- Phase 1, test harness: `bench/romrun` (see [romrun.md](romrun.md)).
- Phase 1, ul80 options (`--data-origin`, `--rom-data`, `--bin`, `--fill`, `--rom-size`, `--ram-end`, `--ram-size`, `--min-stack`, `--equ-file`): done in the um80_and_friends repository.
- Phase 1, `--target=rom` and `-ffreestanding`: done, with `lib/crt0_rom.mac`, the vectors, the error for CP/M-only calls and for `main(argc, argv)`, and `--stack-top`. A user INT or NMI handler in assembly (`__int_handler`, `__nmi_handler`) replaces the default vector stub.
- Phase 1, runtime and libc state in the BSS: the zero-start variables of `rt/` and `lc/` are in a named COMMON block for each module.
- Phase 1, `libc_rom.lib`: 93 modules with no BDOS use. The split of `lc_string` and `lc_stdlib` was already done by the libc split.
- Phase 1, ROM proof: the four snd-* kernels run as ROM images with the CP/M golden output (`bench/rom/sndrom.py`, `tests/test_rom_target.py`). Figures are in [rom.md](rom.md).

- Phase 1, const data in CSEG (ROM target only): const objects, string literals and const compound literals stay in the ROM. RAM of the four snd-* kernels dropped by 33 to 230 bytes (see [rom.md](rom.md)). The CP/M output is byte-identical.
- Phase 1, dead-code roots: `--entry NAME`, `#pragma uc80 keep NAME`, a ROM program with no `main` (the crt0 calls the first `--entry`), `#pragma uc80 vector 38h NAME` and `66h NAME`, and `#pragma uc80 public NAME` (a PUBLIC label for a static name, and a kept BSS object). BSS objects of the ROM target keep their PUBLIC label.

- Phase 2, interrupt functions: `#pragma uc80 interrupt NAME [critical]` and `#pragma uc80 nmi NAME`. Register save (AF, BC, DE, HL, IY, and the alternate set when the call graph reaches an EXX user), `RETI` or `RETN`, the vector, and the errors for parameters, a return value and a direct call. `asm_dce` knows `RETI` and `RETN`. No peephole rule can match across the prologue or the epilogue (barrier regions in `main.py`). See [rom_hardware.md](rom_hardware.md).
- Phase 2, hardware access: `<z80.h>` has `z80_out`, `z80_in`, `z80_out16`, `z80_in16`, `z80_di`, `z80_ei`, `z80_irq_save`, `z80_irq_restore`, the critical-section pair, and `z80_load16`. Objects can have a fixed address (`__at`, `[[uc80::at]]`, and volatile integer objects at a constant address). The option `--isr-safe` and two warnings are new: one for work areas at fixed RAM and one for the alternate registers.
- Phase 3 (the parts that serve the Neo Geo driver): the SDCC spellings `__interrupt`, `__interrupt(n)`, `__critical __interrupt[(n)]`, `__critical { }`, `__sfr __at`, `__sfr __banked __at` and `T x __at(addr)`, with a prepass in `src/uc80/sdcc_compat.py`. `__naked`, `__sdcccall` and `__preserves_regs` stay errors.
- Phase 2 proof: `bench/rom/isrproof.py` (an NMI command queue, a YM2610 timer interrupt that drives the sequencer, a bank select with `z80_in16`).
- Phase 3, `__asm ... __endasm;` blocks (statement level and file level): the prepass turns the sdas text of a block into an `asm("...")` statement with MACRO-80 text. A directive or an operand that it cannot translate is an error with the line number. See [rom_interop.md](rom_interop.md).
- Phase 5, translator to sdas: `uc80 --asm-syntax=sdas` writes a `.s` file (`src/uc80/sdas.py`). The runtime and libc are translated and packed for `sdldz80` (`uc80 --build-sdas-libs DIR`). Measured, not estimated: the four snd-* kernels and the three interrupt proofs link with `sdldz80` to the same image bytes, the same RAM figures and the same T-states as the `ul80` build. All 105 programs of the benchmark set translate and assemble with no error. The work took more than the estimate of one small test, because of the range check of `JR` (um80 changes a far `JR` to `JP`, sdasz80 does not), the vector area, the initial data and the symbol names. See [rom_interop.md](rom_interop.md).
- Phase 5, proof with nullsound: nullsound (ngdevkit commit 5106cbd1414a) is built with `sdasz80` as its Makefile does. A table entry jumps to C functions from uc80, one C function calls `ym2610_write_port_a` through an assembly shim, and the ROM runs in romrun with an NMI that carries the command byte. The YM2610 register writes equal the expected sequence (`bench/rom/nullsound.py`, `tests/test_rom_interop.py`). The area names that ngdevkit links are `CODE` and `DATA` (not `_CODE`, `_DATA`), so the translator has the style `ngdevkit`. The register contract is verified from the nullsound sources.

Not done (needs `codegen.py` or other work):

- The watchdog hook of the crt0.
- The optional per-module BSS block for separate compilation.
- Phase 3: `__naked`. An assembly thunk can be a `.s` or `.mac` file, or an `__asm` block in a normal function.
- An interrupt form for the RST vectors and for IM 2. The vector pragma gives a plain `RET` function and a warning.
- The interrupt check cannot see a call through a function pointer.
- A pointer variable that holds a constant address is not changed to a direct access.
- Phase 4 and the later phases. The diagnostic for a C function with `EXX` code next to nullsound is open.
- Phase 5: the optional `sdcccall0` attribute, initialised globals next to nullsound (no start-up code copies them), and the CP/M target as a goal for the sdas output.
- An interrupt function does not save the fixed work areas of the runtime (Phase 4 option). The compiler prints a warning instead.
- A vector in separate compilation (`--no-whole-program`): the compiler prints a warning, and your own start-up code must jump to the handler.

Notes for the codegen work that follows:

- `src/uc80/rom.py` strips the empty printf dispatch tables from the assembly text. The codegen change must replace this post-processing.
- The user INT and NMI handler substitution works in whole-program mode only.
- A C object that only assembly uses is removed in whole-program mode, unless the C source names the object in `#pragma uc80 public`.
- A C function at a vector (`#pragma uc80 vector`) has no register save and no `RETI`. Use `#pragma uc80 interrupt` or `nmi` for a real interrupt function.
- The compiler puts a compound literal in ROM only when the literal is const and no code writes the literal. A local pointer that points to a compound array literal (`const int *p = (const int[]){1, 2};` in a function) stops with an internal error. This defect is older than this change.


## 1. Answer to the issue

The issue reporter writes a Neo Geo Z80 sound driver with ngdevkit and standalone SDCC, and asks if uc80 is worth the switch. The uc80 maintainer also asked what it takes to handle ROM code.

Short answer: uc80 cannot build a ROM today. A minimum viable ROM target costs about 32 engineer-days (range 28 to 38). After that work, SDCC probably still gives smaller and faster code on the four sound-driver kernels. The ROM-build ratio is not measured.

### 1.1 Measured fact: SDCC 4.6.0 beats uc80 on the snd-* kernels

Source: bench/results/results.csv. uc80 has one row for each kernel (size mode). SDCC has a size-mode row and a speed-mode row. Bytes are the image size in bytes. T-states are the run time in millions.

| Kernel | uc80 bytes | SDCC size bytes | SDCC speed bytes | uc80 T-states | SDCC size T-states | SDCC speed T-states |
|---|---|---|---|---|---|---|
| snd-cmdq | 5914 | 4038 | 4034 | 14.18M | 10.19M | 9.03M |
| snd-ymreg | 5315 | 3797 | 3821 | 22.22M | 15.32M | 15.26M |
| snd-seq | 6706 | 4985 | 5002 | 15.38M | 13.96M | 12.99M |
| snd-fnum | 5597 | 3765 | 3769 | 18.83M | 18.03M | 17.65M |

SDCC is smaller and faster than uc80 on all four kernels, in both SDCC modes.

Over all programs, the picture is different. Source: docs/benchmark_proposal.md lines 25-28. On 96 programs (size) and 78 programs (T-states), uc80 is 1.53 times bigger than SDCC. uc80 needs 0.63 of the T-states of SDCC speed mode, so uc80 is faster on the mean. uc80 loses on small integer loops and on code with many small functions. The snd-* kernels are of this kind. An older figure of "1.93 times bigger and 2.47 times slower" is not reproduced (docs/benchmarks.md:59). Do not use that figure.

These kernels run as CP/M programs with stub port I/O. They are not yet ROM builds. Nobody measured the ROM-build ratio. Phase 1 makes that measurement possible. We expect ROM support to change the ratio little, but this is an assumption.

### 1.2 What would close the codegen gap (unverified)

The size of each gain is not measured.

- Arguments go on the stack. Each 8-bit argument uses a 16-bit pushed slot (codegen.py:5304). SDCC passes the first arguments in registers (sdcccall(1)).
- The callee reads an 8-bit parameter as a 16-bit word.
- A function that uses locals or arguments builds an IX frame (codegen.py:5235). The frame is removed when the body never names IX (codegen.py:5330-5335, :5357). Leaf functions often have no frame.
- A call through a function pointer uses the helper `__callhl`.
- Inline 8-bit multiply and divide would shorten ISR paths. The ongoing speed project covers this.

A register calling convention costs 15 or more days. A cheaper step passes the first argument in A or HL for leaf functions and for static functions with no address taken. The estimate is 4 to 12 days. This estimate is a guess. Do a one-function hand-edit experiment first, to measure the gain. Do not promise size parity.

Recommendation: tell the reporter that SDCC is the better choice for a pure size and speed goal today. Offer uc80 for other reasons: C23, one toolchain, and one compiler for other Z80 ROM projects.

## 2. Current state (claims re-checked)

| Claim | Result | Evidence |
|---|---|---|
| ul80 has no data origin option | True. The fields exist. No option sets them. | um80_and_friends/um80/ul80.py:137-138, :1701-1727 |
| Raw output exists | `-p` with the default .com writer gives a raw image. The writer pads to 128-byte records with 00. `-x` writes hex. | ul80.py:1519-1535, :1539-1564 |
| Const data goes to DSEG | True. Every initialized global, string and const table is in DSEG. | codegen.py around :3786-3870 (exact emit site not re-cited) |
| BSS placement | Whole-program: `common //`. Separate compile: DSEG. Then BSS is in the image and `__BSS_START` equals `__BSS_END`. | codegen.py:3872-3920 |
| No ROM to RAM copy | True. The linker has one address per byte. Only `__END__`, `__BSS_START`, `__BSS_END` exist. | ul80.py:118, :803, :963-972 |
| No ISR support | True. No RETI or RETN emission. | codegen.py:5344 emits only `ret`; codegen.py:519 lists the names only |
| Bare `__interrupt` is erased without a message | True. Also `[[gnu::interrupt]]` and `__attribute__((interrupt))`. The compiler gives no diagnostic. The investigators saw a plain RET only with `--no-dead-elimination`. | uc_core/frontend.py:56-86, :159-180, :903-947 |
| `__interrupt(1)`, `__critical`, `__naked`, `__sfr`, `__at`, `__asm` | Loud errors. | experiments i1 to i11 |
| Bare `__fastcall` is erased without a message | True. The call compiles as a normal stack call. | frontend.py:62-79 |
| No port I/O | True. Inline asm takes no operands. | codegen.py:7399-7430 |
| Dead-function elimination drops ISR and startup functions | True in whole-program mode. Roots are `main`, address-taken functions and functions named in file-scope asm. | codegen.py:1902-1925, :1286-1291 |
| `__sret_buf` costs 256 bytes of RAM | Mostly out of date. Struct results use per-call-site slots in the caller frame. `rt_sret.mac:14` still has `DS 256` as a fallback. | codegen.py:6189-6245 |
| Runtime helpers use fixed memory | True. See Phase 2. | rt_*.mac |
| Calling conventions | uc80 matches sdcccall(0) for int, long and pointer arguments only. A char argument takes 2 bytes in uc80. sdcccall(0) uses 1 byte. uc80 returns char in L with H extended. SDCC 4.4.0 and 4.6.0 both default to sdcccall(1). | codegen.py:5304; interop experiments |
| sdas .rel versus M80 .REL | Different formats. sdldz80 cannot read uc80 objects. | relformat.py:612 |
| asm-DCE knows RETI and RETN | False. Only the exact string `RET` ends a block. | asm_dce.py:484, :514-515 |

Two new defects:

1. The AST optimizer (`Uc80ASTOptimizer`, main.py:610) removes earlier stores to a global `volatile` variable. The sequence `ymaddr=0x28; ymaddr=0x28; ymaddr=1;` compiles to one store. The option `-O0` does not help. The option `--no-ast-optimize` keeps all three stores. A store through a volatile pointer is kept. A volatile global read in a loop is kept. SDCC keeps every store. This breaks write-twice registers such as the YM2610 ports.
2. `lc_string` pulls in `malloc` through `strdup`. Every `strlen` user pays about 0.8 KB.

## 3. Hardware and toolchain facts for the reporter

- Neo Geo Z80: 4 MHz. ROM at 0000-7FFF. Four banked windows from 8000 to F7FF. RAM at F800-FFFF (2 KB).
- Windows: port 0Bh selects 8000-BFFF (16 KB unit). Port 0Ah selects C000-DFFF (8 KB unit). Port 09h selects E000-EFFF (4 KB unit). Port 08h selects F000-F7FF (2 KB unit).
- Bank select is a port READ. Use `in a,(c)` with B as the bank number, or `in a,(n)` with the bank in A.
- Port 08h has two meanings. A read selects bank window 0. A write enables NMI. Port 18h (write) disables NMI. nullsound writes port 08h at init. So the port API needs separate in and out forms.
- INT vector at 0038h (IM 1, YM2610 timers). NMI vector at 0066h. nullsound puts its command handler at 0066h. This handler is not a plain RETN. It uses `ex af,af'` and `exx`.
- ngdevkit links nullsound with `sdldz80 -b DATA=0xf800`. The area is named `DATA`. SDCC C code uses `_DATA`, `_INITIALIZED` and `_INITIALIZER`. The ngdevkit tree has no Z80 C build. So the area mapping for C is unverified.
- The 128 KB M1 ROM comes from `sdobjcopy --pad-to 131072`. ngdevkit builds SDCC 4.4.0. Its asm output has no `sdcccall(1)` marker, but the convention is the same as 4.6.0.
- nullsound is all assembly. It uses IX, IY and the alternate registers. A C caller needs an asm shim for each nullsound routine, with any compiler.
- ngdevkit ships no C example for the Z80. The reporter's driver is private.
- Unverified: YM2610 busy flag bit, a Z80-side watchdog, and whether ADPCM data avoids the Z80.

## 4. Phased plan

Effort is in engineer-days. Estimates are engineering judgement. A 30 percent buffer is in the totals of section 6, not in each row.

### Phase 0: stop silent miscompiles (2.5 days, do first)

| Item | Repo | Days | Risk | Test |
|---|---|---|---|---|
| Add an opt-in strict mode to the uc_core frontend. In strict mode, the erasure of `__interrupt`, `[[...interrupt...]]`, `__attribute__((interrupt))` and bare `__fastcall` is an error. uc80 sets strict mode. uc386 keeps the DOS tolerance. | uc_core (frontend.py:56-74), uc80 | 1 | Medium. The erasure is on purpose for DOS code. Tests: uc_core/tests/test_dos_tolerance.py:51-52, :127 and test_dos_pragmas.py. | Compile each spelling with uc80. Expect an error. Run the uc_core and uc386 tests. |
| Keep stores to volatile globals in the AST optimizer. Audit other volatile paths: struct member, array element, typedef, 16-bit access, and the HL cache (codegen.py:15205). | uc80 (main.py:610) | 1.5 | Medium | Three-store sequence gives three stores. A `while(flag)` loop re-reads. A volatile struct field and an array element keep every store. |

Open point: check that the external peephole (upeepz80, another repo) does not delete code around inline asm. main.py:46-78 already fences inline-asm regions. New IN and OUT intrinsics need the same fence.

### Phase 1: generic freestanding ROM target (16.75 days, plus 1.5 optional)

This phase works for any Z80 ROM. The linker work has a prototype. The prototype is commit f188ef6 on the local branch `rom-target-prototype` of the um80_and_friends repository (not pushed). The um80_and_friends tests pass with the patched copy. A run under cpmemu showed that the data copy and the BSS clear work. The run used a relocated CP/M variant, not a ROM at address 0.

| Item | Repo | Days | Risk | Test |
|---|---|---|---|---|
| Test harness first. Use a raw ROM runner at address 0. Reuse the Z80 core of cpmemu (qkz80) if it fits. Add a port-I/O log, IRQ and NMI injection (IM 1 at 38h, EI delay, RETN restores IFF), cycle counts, a YM2610 model with timers and busy flag, and bank-port reads. All later items use it. | cpmemu or a new Python runner | 5 | Medium. Reuse of the core is unverified. | Run the Phase 1 prototype ROM. |
| `-d/--data-origin` option (and optional `--common-origin`) | ul80 | 0.5 | Low. Default unchanged. | Link with `-p 0 -d F800`. Check `.sym`. |
| `--rom-data`: store the DSEG bytes after the code. Resolve symbols at the RAM address. Define `__DATA_ROM`, `__DATA_RAM`, `__DATA_LEN`, `__DATA_END`. | ul80 | 1.5 | Medium. PRL and SPR modes. COMMON blocks with initial bytes. `.sym` lists only the run address. | A ROM crt0 copies the data. Check values in the harness. |
| `--bin`, `--fill`, `--rom-size`. Use the existing raw writer as the base. Bypass the 128-byte padding of `save_com`. Make hex output skip gaps. | ul80 | 1 | Low | A 32768-byte image with fill 0FFh. |
| Overflow checks: `--rom-size` and `--ram-end`. Print a ROM and RAM summary line. Add a link-time check of data, BSS and a minimum stack against RAM size. | ul80 | 0.5 | Low. The check does not count a deep stack or the heap. | Link with too small a limit. Expect an error. |
| Put const objects, string literals and constant compound literals in CSEG. A `const char *p` pointer stays in DSEG. Add a diagnostic or test for a write through a cast to a const object. | uc80 | 1.75 | Medium. Mixed tables such as `const char *tbl[]`. | Link. Check `.sym` addresses. Write to a const object in the harness. |
| `--target=rom` (alias `-ffreestanding`). Select `lib/crt0_rom.mac`. No BDOS. No printf table. Whole-program default. Diagnose `exit`, `abort`, `printf`, file I/O and `main(argc, argv)`. See the crt0 specification below. | uc80 | 3 | Low | The existing `--startup-lib` flow is the base. |
| Move runtime and libc state (`__tmp32`, `__divrem`, `__div32_dvd`, `__rem32`, 64-bit, float, complex, libc statics) from DSEG `DW 0` to BSS. The files are rt_arith16.mac:142, rt_arith32.mac:178-221, rt_arith64.mac, rt_float.mac, rt_complex.mac and the libc modules. | uc80 lib | 1.25 | Low to medium. Hand-written asm. Retest float and 64-bit. | `uc80 --build-libs`. The image has no copy for these. |
| Split `lc_string` (move `strdup` out) and `lc_stdlib` (move `exit`, `abort` out). Build `libc_rom.lib`. | uc80 lib | 1.25 | Low | A ROM link of `strlen` has no `_malloc`, `_exit` or BDOS reference. |
| Dead-code roots: `--entry NAME`, `#pragma uc80 keep f`. A missing `main` is legal in the ROM target. Apply the same roots in the asm-DCE pass (main.py:785-790). | uc80 | 1 | Low | A file with no `main` keeps `run` and `nmi`. |
| Optional: BSS in a named COMMON block per module for separate compile. Default stays whole-program. | uc80 | 1.5 | Medium. Needs named COMMON in ul80 (ul80.py:125, :956-959). | Two-module ROM. `__BSS_START` is below `__BSS_END`. |

crt0_rom specification:

1. Vectors: 0000h is `di; im 1; jp start`. 0038h is a default INT stub (`ei; reti`) or a jump to the user ISR. 0066h is a default NMI stub (`retn`) or a jump to the user NMI. RST 08h to 30h are `ret`.
2. Allow `#pragma uc80 vector 38h f` to place a jump to function f.
3. The crt0 sets SP from a link symbol. The default is the top of RAM.
4. Order at start: clear BSS, copy the initial data, call `main`, then loop on `halt`.
5. A hook for a watchdog kick is empty by default. A Z80-side watchdog is unverified.
6. The vectors need ASEG code. ul80 needs the ASEG module first, or a code origin above 68h (`-p 100`). Otherwise ul80 stops with an overlap error (ul80.py:927-936).

Phase 1 test plan: rebuild the four snd-* kernels as freestanding ROM kernels. Run each kernel in the harness. Compare the output with the CP/M run. Phase 1 gives the first measured cycle count for a ROM build.

### Phase 2: hardware access and ISR safety (10 days)

| Item | Repo | Days | Risk | Test |
|---|---|---|---|---|
| ISR codegen. Flag per function. Prologue: optional EI, then PUSH AF, BC, DE, HL, IY. IX is already saved (codegen.py:5235). Add the alternate set (EX AF,AF' and EXX) only when the call tree reaches an EXX user (float48.mac). Epilogue: POP, then RETI (maskable) or RETN (NMI). The epilogue replaces `ret` at codegen.py:5344 on both paths (frame elided and not elided; see :5330-5346). Exclude ISRs from inlining, frame elision (`_frame_is_unused`, :5357) and tail-jump peepholes. Reject parameters, return values and normal calls to an ISR. | uc80 | 2.5 | Medium | The harness injects IRQ and NMI. Check registers and the return opcode. |
| Teach asm_dce and the peephole passes about RETI and RETN. asm_dce treats only `RET` as a block end (asm_dce.py:514-515). Audit peephole matches on `RET`. Decide if upeepz80 needs a change. | uc80, maybe upeepz80 | 0.5 | Medium | A function that ends in RETI is not merged into the next function. Unused code after it is removed. |
| `#pragma uc80 interrupt f` and `#pragma uc80 nmi f`. The preprocessor already routes `#pragma` (preprocessor.py:495, :1607-1664). | uc_core, uc80 | 0.5 | Low | The pragma form builds the same ISR. |
| Port intrinsics: `__builtin_z80_out(port, v)`, `__builtin_z80_in(port)`, and 16-bit forms with BC as the full port (bank select). The statement form drops the result. Separate in and out forms. Ship `<z80.h>`. Builtin hook: codegen.py:10818-10905. The intrinsic is a peephole and DCE barrier. | uc80 | 2 | Low | The port-I/O log shows exact IN and OUT sequences. |
| `__builtin_z80_di()`, `__builtin_z80_ei()`, and a critical-section helper (`ld a,i; di; push af ... pop af; jp po,L; ei; L:`). Add an atomic read for 16-bit variables shared with an ISR. | uc80 | 1 | Low | The harness injects IRQ between the two bytes of a 16-bit read. |
| `__at(addr)` variables as absolute symbols (EQU, no storage). Direct `ld A,(addr)` for constant volatile pointers. Absolute const tables through ASEG (unverified in ul80). | uc_core, uc80 | 1.5 | Low to medium | `.sym` and generated code. Link a const table at a fixed address. |
| `--isr-safe`: turn off shared storage for functions reachable from an ISR. The reachability set exists (codegen.py:1787-1803). | uc80 | 1 | Low | The harness interrupts a shared function and calls it. |
| Diagnostic when an ISR reaches a helper with fixed memory or an EXX user. Print the call chain. | uc80 | 1 | Low | An ISR that calls long division gives the diagnostic. |

SDCC reference for the ISR forms (SDCC 4.4.0 and 4.6.0 agree):

- `__interrupt` and `__interrupt(n)`: EI first, save AF, HL, BC, DE, IY, RETI. The number n only checks for duplicates. It makes no vector.
- `__critical __interrupt` (no number): no EI, RETN.
- `__critical __interrupt(n)`: no EI at entry, then EI and RETI at exit.

Do not key RETN on `__critical` alone.

Fixed runtime memory today:

- `__tmp32`: 4 bytes (rt_tmp32.mac:8).
- `__divrem`: 2 bytes (rt_arith16.mac:141).
- `__div32_dvd` and `__rem32`: 8 bytes (rt_arith32.mac:878).
- Six 64-bit areas of 8 bytes each (rt_arith64.mac:19-20, :576-577, :790-791).
- Float temporaries: about 30 bytes (rt_float.mac:1448+).
- Complex temporaries: 40 bytes (rt_complex.mac:272).
- libc statics.

 A linked test with int, long and long long arithmetic used 52 bytes of fixed RAM plus 50 bytes of `??AUTO`. Pure 8-bit and 16-bit code uses 0 to 2 bytes. Float code uses `EXX` (88 lines in float48.mac).

Shared locals (`??AUTO`) are safe for an ISR that has its own call tree. One case stays unsafe: a function that both main and the ISR call. `--isr-safe` fixes this case.

SDCC has no static temporaries in its arithmetic helpers, so its code is ISR-safe by design. uc80 does not match this today. The diagnostic is the minimum. An optional ISR save of `__tmp32`, `__divrem`, `__div32_dvd` and `__rem32` costs about 100 T-states per interrupt and 1 more day. This option is not in the MVP.

### Phase 3: SDCC source compatibility (3.5 days)

Rule: every spelling is either supported or a loud error. No spelling is erased.

| Item | Repo | Days | Risk | Test |
|---|---|---|---|---|
| Source prepass in frontend.py, like the existing packed pattern (frontend.py:113-130, :903-980). Record `__interrupt[(n)]`, `__critical __interrupt`, `__naked`, `[[gnu::interrupt]]`, `__attribute__((interrupt))` in a side table. Then erase the text. `__naked` skips prologue and epilogue. | uc_core, uc80 | 2 | Medium. A regex can change user identifiers (frontend.py:67-86). | Each spelling gives the same code as the Phase 2 pragma. |
| `__sfr __at(n)` and `__sfr __banked __at(n)`. Reads and writes become IN and OUT. `P \|= x` becomes IN, OR, OUT. | uc_core, uc80 | 1 | Low | Port-I/O log. SDCC gives IN, OR, OUT. |
| `__critical { }` and `__asm ... __endasm;` rewrite | uc_core | 0.5 to 1 | Medium. Needs brace matching. | Compare with SDCC output (`ld a,i; di; push af; ... pop af; jp po,L; ei; L:`). |
| Accept `_Noreturn`. Make `__sdcccall(0)`, `__sdcccall(1)` and `__preserves_regs(...)` loud errors. Accept `__sdcccall(0)` only after an sdcccall0 mode exists (Phase 5). Reason: ignoring them changes the ABI without a message. | uc_core | in the first item | Low | Each spelling gives the expected result. |

Not needed: `__code`, `__data`, `__xdata`. SDCC for Z80 rejects them. A grammar change in uplox (about 5 days) is not recommended.

### Phase 4: extra ISR safety (1.5 days, optional)

| Item | Repo | Days | Risk | Test |
|---|---|---|---|---|
| Optional ISR save of `__tmp32`, `__divrem`, `__div32_dvd`, `__rem32` | uc80 | 1 | Medium. Cost at 4 MHz and 60 Hz. | Cycle count in the harness. |
| Document that no C code may call the EXX-using float runtime next to nullsound. nullsound uses AF', BC', DE', HL' in its NMI path at any time. Add a diagnostic for a link with nullsound. | uc80, docs | 0.5 | Low | A float call in a nullsound build gives the diagnostic. |

### Phase 5: interop with SDCC and nullsound (5.5 days core)

Goal: the reporter keeps nullsound and replaces only the reporter's own C code.

| Item | Repo | Days | Risk | Test |
|---|---|---|---|---|
| Translator pass from M80 text to sdas syntax at main.py:1094. A 98-line prototype made a byte-identical 281-byte image against um80 and ul80. Four large outputs assemble with 0 sdasz80 errors. They were not linked or run. Map dseg to `_INITIALIZED` and `_INITIALIZER`. Use `s__BSS` and `l__BSS` in the crt0. The estimate rests on one small test. | uc80 | 3.5 | Medium to high. Area ordering, `.globl` for every symbol and inline asm text need work. | Link a bench kernel with sdldz80 and run it in the harness. Compare with the um80 image. |
| Asm thunks for calling conventions. Avoid char arguments and char returns at the boundary. | docs, examples | 1 | Low. Not run on an emulator. | Harness. |
| Initialized-data split in the translator | uc80 | 1 | Low | Harness. |

Do not hard-code `_INITIALIZED` or `s__BSS` before you inspect a real ngdevkit C build. ngdevkit links `DATA`, and SDCC C uses `_DATA`. Ask the reporter for the link command.

Cheaper stopgap: a fixed-address blob linked with ul80 and included in the sdas project as `.db` lines plus `.equ` symbols. Cost 2 to 3 days. The build is fragile because every change moves the symbols. Use this only for a demo.

Optional: an `sdcccall0` attribute (char arguments in 1-byte slots, H extension after extern char calls). 3 to 4 days. Build this only if C-to-C calls cross the boundary often.

Not recommended: ul80 reading sdas `.rel` (5 to 8 days), um80 reading sdas syntax (8 to 12 days), a second sdas backend in codegen (15 days).

Rules for C next to nullsound:

- nullsound owns the vectors, init, stack and NMI. The NMI handler is at 0066h.
- `cmd_jmptable` has 128 entries of 3 bytes (`jp`). The dispatcher calls an entry with `push bc; push hl; ret` and saves only HL. A C command needs a jp shim that handles A, BC and DE.
- The post-init hook is a 16-bit word at FFFE-FFFF. The `jr`-to-self idle loop also uses this word. It is not free RAM.
- C code must never touch AF', BC', DE', HL'. So C code must not call the float runtime.
- The shim must keep IX and IY safe across the call, because uc80 keeps its frame pointer in IX.
- The stack top must stay at or below FFFD. Check page-constant DATA ranges (nullsound check-bounds.py).
- Unverified: the real nullsound register contract.

### Phase 6: banking

| Level | What | Days |
|---|---|---|
| 0 | Select banks with the 16-bit IN intrinsic. Read banked data through a `const unsigned char *` into the window. No extra work after Phase 2. | 0 |
| 1a | Script path: link each bank with its own `ul80 -p` run at the origin of its window (8000, C000, E000 or F000). Each window has its own size and bank unit. Join the images. Export symbols as EQU files. | 1 |
| 1b | `__banked` functions with trampolines, plus bank areas in ul80 and um80 | 8 (3 uc80, 5 ul80 and um80) |
| 2 | Far 24-bit pointers | 5 or more |

Unverified: all banking estimates. Recommend level 0 for now. Neo Geo banking is a port read, so the SDCC `__banked` function model does not apply.

## 5. Minimum milestone

Goal: the reporter can build a C sound driver with uc80, with or without nullsound.

Scope: Phase 0, Phase 1 (without the optional per-module BSS item), and Phase 2. Phase 2 includes the ISR-safety diagnostic. SDCC spelling support (Phase 3) is not in the MVP. The reporter ports the driver code by editing a few lines.

| Part | Days |
|---|---|
| Phase 0 | 2.5 |
| Phase 1 without the optional item (harness 5) | 16.75 |
| Phase 2 | 10 |
| Docs, packaging (libc_rom.lib, `--build-libs`, docs/*.md, CHANGELOG) | 2 |
| uc386 regression cycle for the uc_core changes | 1 |
| Total MVP, own crt0 and vectors | about 32 |

Range: 28 to 38 days. The range depends on the harness (reuse of an existing Z80 core), and on the risk in the peephole, DCE and volatile passes.

Add for nullsound coexistence: about 3 days for the blob path, or 5.5 days for the translator path.

MVP result: freestanding ROM, ISR and NMI, port I/O, `__at`, di and ei intrinsics, reentrancy diagnostics. The result is probably not smaller or faster than SDCC on the snd-* kernels.

## 6. Totals

| Block | Days |
|---|---|
| Phase 0 | 2.5 |
| Phase 1 (without optional item) | 16.75 |
| Phase 2 | 10 |
| Docs, packaging, uc386 regression | 3 |
| MVP subtotal | 32.25 |
| Phase 3 | 3.5 |
| Phase 4 | 1.5 |
| Phase 5 (core) | 5.5 |
| Phase 6 (level 1a) | 1 |
| Optional per-module BSS | 1.5 |
| Core total | about 45 |
| Optional: sdcccall0 attribute, banking level 1b, register calling convention (4 to 12 for the cheap step, 15 or more for full) | 15 to 35 |

Range for core work: 38 to 55 days. Linker numbers rest on a working prototype. Calling convention, banking, translator and nullsound numbers are unverified.

## 7. Ask the reporter

1. Does the driver link nullsound, or does it have its own crt0?
2. What is the link command, and which area names does the C code use?
3. Which SDCC spellings does the code use: `__sfr`, `__at`, `__interrupt`, `__critical`, `__naked`, `__asm`, `__banked`?
4. Which SDCC version does the driver use? ngdevkit builds 4.4.0. The benchmark used 4.6.0 (4.4.0 rows exist in results.csv).
5. Do any functions run from both main and an ISR?
6. Does the code use 16-bit variables shared with an ISR, or float?
7. Can the reporter share one hot function or the build files? Real code is the best test.

## 8. Unverified items

- Peephole handling of a POP and RETI epilogue (asm-DCE needs a change).
- Whether the external upeepz80 package needs a change.
- Behavior of `.PHASE` images in um80 and ul80.
- Native run of a ROM at address 0 (done only through a relocated CP/M variant).
- COMMON blocks with initial bytes under `--rom-data`.
- Reuse of the cpmemu Z80 core for the harness.
- uc386 test impact of uc_core changes.
- Per-ISR cycle cost of saving runtime temporaries.
- Area names in the ngdevkit C build of the reporter.
- The nullsound register contract.
- YM2610 busy flag, Z80-side watchdog, ADPCM bus.
- Other Z80 platforms (SMS, ColecoVision, MSX, ZX Spectrum). The generic design in Phase 1 should serve them. Notes are from general knowledge.
