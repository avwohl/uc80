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
- `;#func NAME F L frame=KIND pops=0` after the label of each function, and `;#endfunc NAME` after the last live line of the function. A function that ends in an endless loop (`for (;;)`) has no `ret`. Its `;#endfunc` stands after the last jump. KIND has the value `ix` when the function keeps the IX frame. KIND has the value `none` when the function has no frame. The caller removes the arguments, so `pops` is always 0.
- `;#line F L` in front of each statement. F and L are the file and line of the first token of the statement. The line of a token comes from `srcmap`, not from the preprocessed text.
- Three markers for a `for` header: the first part in front of the init code, the second part after the loop label, the third part after the update label. A `while` and a `do` loop have one marker for the condition, after the loop label.
- The line of the function declaration, in front of the prologue. The line of the `}` that ends the function, in front of the epilogue.
- `;#inline F L NAME` and `;#endinline` around the code of an inlined function. The line inside is the line of the `return` of the inlined function.
- `;#line F L nostmt` in front of code that loop invariant motion moved out of a loop. L is the line of the loop. The debugger does not stop there when it steps.

## Limits

- The keywords `if`, `while` and `for` have no token in the AST. The line of the condition stands for the line of the keyword. The two lines can differ when the condition starts on a later line.
- A declaration with no initializer has no marker. A `return;` and a `break;` take the line from the position of the statement in the preprocessed text.
- Whole-program mode copies the runtime and the start-up code into the `.mac` file. These lines have no markers. `um80` gives each of them a line of the `.mac` file (assembler level only).
- A jump table of a `switch` stands inside its function. The table has no source line.
- uc80 does not write `ixbegin` and `ixend`. A label would stop the text passes, and a label changes the code. The debugger uses the stack offset at the address instead.
- The if/else merge of `peep_local` moves the common first and last instructions of both arms. The instructions keep the line of the arm where the merge put them.
- uc80 writes `;#inline` for the inlined functions that survive the later AST passes. A later pass can rebuild the call, and the call then loses its marker. The code is the same.
- The markers work with `--asm-syntax=sdas` and `--target=rom` as comments. Only `um80` and `ul80` read them.
- `dbg2elf` writes a `DW_TAG_inlined_subroutine` for each inlined call of `prog.dbg`. Measured with GDB 18.1 on a `uc80 -g` program with an inlined call: `step` enters the inlined function, `bt` shows the inline frame, and `finish` returns to the caller. See `docs/gdb.md` in um80_and_friends.
- The `outer` field and the third level (BASIC to C) are not written.

## Measurements

These results come from 390 programs: `examples/`, `tests/c-testsuite-z80/`, `tests/gen3/`, `tests/wide_int/`, the 220 programs of the c-testsuite and `bench/programs/micro`. 363 of them built and linked. Each program was built with `-g`, `um80 --debug-info` and `ul80 --debug-info`.

- 105,500 instruction bytes lie in the functions of the programs, outside the runtime. A function goes from its `;#func` to its `;#endfunc`, and a function with no `;#endfunc` goes to the next function. Each of the 105,500 bytes has a source line in `prog.dbg`. The share is 100.0 percent for the set and for each program. No function of the 363 programs has no end.
- 160 more bytes in these functions are the jump tables of `switch` statements. These bytes have no source line.
- The functions are 58 percent of the instruction bytes of the program module. The runtime and the start-up code are the rest.
- 14 programs have inlined code. Their `lines` rows have an inline stack.
- The `.mac` text of 362 programs is the same with and without `-g`, when you remove the marker lines and the blank lines. The text of two other programs differs in a label name (`@SG269_1` and `@SG291_1`, from the line number of the line in the text) and in the `__TIME__` string. The `.com` of the first program is the same.
- The tests compare the `.com` of 115 programs, and the `.com` of one program under five option sets. All are the same.

See [loop_rotation.md](loop_rotation.md) for the pass `looprot`. It copies the markers of the loop test.

See [optnone.md](optnone.md) for a function that no pass changes (`;#noopt` regions), so that a debugger can step through each statement.
