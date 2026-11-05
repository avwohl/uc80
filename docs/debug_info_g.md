# Debug markers (-g)

`uc80 -g` writes debug markers into the assembler text. A debugger uses the markers to find the C source line of each instruction. The design is in [debugger_plan.md](https://github.com/avwohl/cpmemu/blob/main/docs/debugger_plan.md) of cpmemu. The marker format is in `docs/debug_info.md` of um80_and_friends.

## Use

`uc80` writes the `.mac` file only. You run `um80` and `ul80` yourself. Give both programs the option `--debug-info`.

```
uc80 -g prog.c -o prog.mac
um80 --debug-info prog.mac -o prog.rel
ul80 --debug-info prog.rel libc.lib runtime.lib -o prog.com
```

`um80` writes `prog.dbm` next to `prog.rel`. `ul80` writes `prog.dbg` next to `prog.com`. The file `prog.dbg` lists the source line of each address, the routines, and the labels. A GDB converter or `udb80` reads the file.

The file names in the markers are relative to the directory where you run `uc80`. Run `uc80` in the directory of the source file, or give the debugger a source path.

## The code does not change

The `.com` file is the same, byte for byte, with and without `-g`. A marker is a whole-line comment. The assembler writes no byte for a comment. The tests compare the `.com` files of all the programs in `examples/`, `tests/c-testsuite-z80/` and `tests/gen3/`, and of the same program under five option sets.

Two rules keep the code the same:

- Each text pass of uc80 skips marker lines and keeps them in order. This applies to `peep_local`, `peep_flow`, `reglocals`, `iyptr`, `looprot` and `asm_dce`. The module `markers.py` tells a marker from another comment.
- A pass inside the code generator sees the text as it is without `-g`. `dbg_clean` takes the markers out before the pass and puts them back after the pass. A marker goes back in front of the line that it was in front of. When the pass replaced that line, the marker goes in front of the new text, and the line number can be wrong. The code is the same.

The upeepz80 package keeps each marker too. A marker of code that upeepz80 deleted goes in front of the next marker.

## What uc80 writes

- `;#file N "PATH"` in the header, one for each source file. An `#include` file and a `#line` name get their own number. The file name is the real name, not the name of the preprocessed text.
- `;#func NAME F L frame=KIND pops=0` after the label of each function, and `;#endfunc NAME` after the last live line of the function. A function that ends in an endless loop (`for (;;)`) has no `ret`. Its `;#endfunc` stands after the last jump. KIND has the value `ix` when the function keeps the IX frame. KIND has the value `static` when the function has no frame and keeps its locals in the shared area `??AUTO`. KIND has the value `none` for another function with no frame. `uc80` reads KIND from the final text, after the passes, because a pass can delete the frame. The caller removes the arguments, so `pops` is always 0.
- `;#line F L` in front of each statement. F and L are the file and line of the first token of the statement. The line of a token comes from `srcmap`, not from the preprocessed text.
- Three markers for a `for` header: the first part in front of the init code, the second part after the loop label, the third part after the update label. A `while` and a `do` loop have one marker for the condition, after the loop label.
- The line of the function declaration, in front of the prologue. The line of the `}` that ends the function, in front of the epilogue.
- `;#inline F L NAME` and `;#endinline` around the code of an inlined function. The line inside is the line of the `return` of the inlined function.
- `;#line F L nostmt` in front of code that loop invariant motion moved out of a loop. L is the line of the loop. The debugger does not stop there when it steps.

## IX range

A debugger finds the return address of an IX frame at IX+2, but only between `add IX,SP` of the prologue and `pop IX` of the epilogue. Outside the range IX is the IX of the caller. `uc80 -g` writes the range on the marker of each function with `frame=ix`:

```
	;#func _sum 1 2 frame=ix pops=0 ixbegin=??IXB1 ixend=??IXE1
	push	IX
	ld	IX,0
	add	IX,SP
??IXB1:
	...
	ld	SP,IX
??IXE1:
	pop	IX
	ret
```

`ixbegin` is the address after `add IX,SP`. `ixend` is the address of the last `pop IX` in front of a `ret`. The module `ixrange.py` writes the two labels as the last step on the text, after `looprot` and after the removal of the unused `EXTRN` lines. A label stops the peephole patterns, so a label inside the text of a pass would change the code. `uplm80` does the same in `add_ix_range_labels`.

The range needs the plain prologue: `push IX`, `ld IX,0` and `add IX,SP` are the first three instructions of the function. A function that saves registers in front of `push IX` has no range. 

A `push IX ... pop IX` pair in the body lends IX to the code as a pointer. The pair is not the epilogue. The `pop IX` of an epilogue is the one with `ret` after the `pop IX`. A function with an early exit has more than one epilogue. The range ends at the last one, and the debugger reads the return address at SP at each `ret`.

Measured on 357 programs (`examples/`, `tests/c-testsuite-z80/`, `tests/gen3/`, `tests/wide_int/`, `bench/programs/micro/` and the 220 programs of the c-testsuite): 114 functions have `frame=ix`, and all 114 have a range. 560 functions have no frame (219 `static` and 341 `none`). The `.com` of the 115 programs of the D2 test is the same with and without `-g`.

## Variables

`uc80 -g` writes a `;#var` marker for each global, static, parameter and local variable. It also writes a `;#type` marker for each struct, union, enum and typedef that a variable uses. The format is in `docs/debug_info.md` of um80_and_friends (Variables). `udb80` and GDB read the records: see `docs/udb80.md` and `docs/gdb.md` there.

```
;#type struct:pt size=4 members=x:int:2@0,y:int:2@2
	;#var g int:2 kind=global at=_g decl=1:4
	;#func _work 1 33 frame=static pops=0
	;#var arr [3]int:2 kind=local at=??AUTO+4 scope=1:33-44 decl=1:34
	;#var i int:2 kind=local reg=DE range=??VR3:??VR4 range=??VR5:??VR6 scope=1:33-44 decl=1:36
```

The type markers and the global markers stand in the header after the `;#file` lines. The markers of a routine stand after its `;#func` marker. A removed function takes its markers with it, and an unused global keeps its marker without a label (the location is then not available).

### Where each kind of variable lives

- **Global and static.** `at=_name` for a global, `at=_name` for a file `static` (kind `static`), and `at=__f_S0` for a `static` local of the function `f`.
- **Shared area.** A function that is not recursive keeps its locals in `??AUTO`. The record is `at=??AUTO+N`. The shared area belongs to the function only while the function is active, so a debugger uses the record in the frames of that function.
- **IX frame.** A function with `frame=ix` keeps its locals at IX-N and its stack parameters at IX+4 and up: `ix=N`. If the final text has no frame (`frame=none`), the record says `none` and `why=no-frame`.
- **Register.** The pass `reglocals` and `iyptr` move a local into B, C, D, E, BC, DE or IY. The record is `reg=R` with `range=` pairs. See below.
- **Parameter in a register.** With the register convention a parameter arrives in A, HL, E or DE. The callee stores the parameter in a slot when the body names it. The register pass and the forwarding pass can remove the slot. The record then has `none`, or `reg=` with ranges when `reglocals` kept the value in a register.

### Register ranges and "not available"

`varinfo.py` writes the ranges after the last text pass, so no pass sees a label (D2). A register local is valid from the first statement marker of the function to the closing brace marker. One stretch is not valid: between a `push` of its register pair and the matching `pop`. A call that must keep the local has such a pair. The range ends too at a `pop` of the pair with no `push` before it, and at a `push` that no `pop` closes. In these places the text loads the register with another value.

The record says `none` and gives a reason when it cannot say where the variable lives:

- `no-storage`: a variable that `reglocals` could move (a scalar, no address taken) has no instruction that names its slot. The optimizer removed the variable or moved the value into a register that the pass does not record.
- `no-frame`: the function lost its IX frame.
- `save-not-paired`: nothing is left of the range.

`uc80` cannot see a write that reuses a register after the last use of the local with no `pop`. A debugger can then show the value of the other use. Under `--optnone` the function keeps all of its locals in slots, so the values are exact.

An AST pass can remove a variable before the code generator sees the variable: a loop that the optimizer unrolled loses its counter, and a constant that the optimizer folded loses its local. These variables have no record. A debugger then says that the name does not exist. Use `--optnone=NAME` for the function that you debug.

### Scope

`scope=FILE:FIRST-LAST` is the block of the declaration. For a variable in the init clause of a `for` the block is the `for` statement. A parameter has the block of the function body. `decl=` is the line of the declaration. A debugger hides a variable on the lines above `decl=`. The file and the lines come from the source map, so they are the lines after a `#line` directive.

## The outer level

A `#line` directive in the C source changes the file name and the line number of the lines that follow. mbasic writes one directive for each BASIC line. The `;#line` markers then name the BASIC file and the BASIC line, and a debugger can show the BASIC program.

Take a main source file that has at least one `#line` directive, compiled with `-g`. `uc80` then writes the position in the C file as `outer=FILE:LINE` on each `;#line` marker that a directive moved:

```
	;#line 1 20 outer=2:6
```

`uc80` finds the C position with a second run of the preprocessor. The second run replaces each `#line` directive by `#undef`, which writes no line and moves no line number. The output lines of the two runs match one to one, and the second map gives the physical position. If the two maps differ in length, `uc80` writes no `outer=`. A marker whose position is the same in both maps has no `outer=`. A program with no `#line` directive has no `outer=` and no second run. Measured: the `.com` of a program with `#line` directives is the same with and without `-g`.

um80, ul80 and `prog.dbg` carry the value ([debug_info.md](https://github.com/avwohl/um80_and_friends/blob/main/docs/debug_info.md)). `udb80` shows it with `info line`, `set level outer` and `addr2line`.

## Limits

- The keywords `if`, `while` and `for` have no token in the AST. The line of the condition stands for the line of the keyword. The two lines can differ when the condition starts on a later line.
- A declaration with no initializer has no marker. A `return;` and a `break;` take the line from the position of the statement in the preprocessed text.
- Whole-program mode copies the runtime and the start-up code into the `.mac` file. These lines have no markers. `um80` gives each of them a line of the `.mac` file (assembler level only).
- A jump table of a `switch` stands inside its function. The table has no source line.
- A function with a frame in IX has `ixbegin=` and `ixend=` on its `;#func` marker, and the two labels `??IXBn` and `??IXEn`. See [IX range](#ix-range). A function with `push IX` in front of its frame (an interrupt handler) has no range, and the debugger uses the stack depth.
- The if/else merge of `peep_local` moves the common first and last instructions of both arms. The instructions keep the line of the arm where the merge put them.
- uc80 writes `;#inline` for the inlined functions that survive the later AST passes. A later pass can rebuild the call, and the call then loses its marker. The code is the same.
- The markers work with `--asm-syntax=sdas` and `--target=rom` as comments. Only `um80` and `ul80` read them.
- `dbg2elf` writes a `DW_TAG_inlined_subroutine` for each inlined call of `prog.dbg`. Measured with GDB 18.1 on a `uc80 -g` program with an inlined call: `step` enters the inlined function, `bt` shows the inline frame, and `finish` returns to the caller. See `docs/gdb.md` in um80_and_friends.
- The first draft of the variable records is for `uc80` only. `uplm80` and `ucow` write no `;#var` marker.

## Measurements

These results come from 390 programs: `examples/`, `tests/c-testsuite-z80/`, `tests/gen3/`, `tests/wide_int/`, the 220 programs of the c-testsuite and `bench/programs/micro`. 363 of them built and linked. Each program was built with `-g`, `um80 --debug-info` and `ul80 --debug-info`.

- 105,500 instruction bytes lie in the functions of the programs, outside the runtime. A function goes from its `;#func` to its `;#endfunc`, and a function with no `;#endfunc` goes to the next function. Each of the 105,500 bytes has a source line in `prog.dbg`. The share is 100.0 percent for the set and for each program. No function of the 363 programs has no end.
- 160 more bytes in these functions are the jump tables of `switch` statements. These bytes have no source line.
- The functions are 58 percent of the instruction bytes of the program module. The runtime and the start-up code are the rest.
- 14 programs have inlined code. Their `lines` rows have an inline stack.
- The `.mac` text of 362 programs is the same with and without `-g`, when you remove the marker lines and the blank lines. The text of two other programs differs in a label name (`@SG269_1` and `@SG291_1`, from the line number of the line in the text) and in the `__TIME__` string. The `.com` of the first program is the same.
- The tests compare the `.com` of 115 programs, and the `.com` of one program under five option sets. All are the same.
- Variables: 138 programs (139 less one with `__TIME__`) give 1,070 `;#var` records with `-g`: 463 global, 11 static, 248 parameter and 348 local. 474 are at a label (333 in `??AUTO`), 34 in the IX frame, 136 in a register, and 93 have no place (85 `no-storage`, 8 `no-frame`). With `--optnone=all` 137 of them build and give 1,155 records, 684 in the IX frame, and none without a place.

See [loop_rotation.md](loop_rotation.md) for the pass `looprot`. It copies the markers of the loop test.

See [optnone.md](optnone.md) for a function that no pass changes (`;#noopt` regions), so that a debugger can step through each statement.
