# optnone: no optimization for one function

The code generator writes a function under optnone as the source says. No pass changes the function. A debugger can then stop at each statement, and each local variable stays in its stack slot.

The design is in [debugger_plan.md](https://github.com/avwohl/cpmemu/blob/main/docs/debugger_plan.md) of cpmemu (D6). The marker format is in `docs/debug_info.md` of um80_and_friends. [debug_info_g.md](debug_info_g.md) describes the markers that `-g` writes.

## Use

Mark the function in the source. Use one of these forms before the function:

```c
#pragma uc80 optnone
int work(int n) { ... }

[[uc80::optnone]] int work(int n) { ... }

__attribute__((optimize("O0"))) int work(int n) { ... }
```

The pragma applies to the next function, with or without a body. The pragma `#pragma uc80 optnone work sink` names the functions instead. The attribute forms `[[gnu::optimize("O0")]]`, `__attribute__((optnone))` and `[[clang::optnone]]` are the same as the three forms above. An attribute on a prototype applies to the function with a body of the same name.

Or name the functions on the command line:

```
uc80 -g prog.c -o prog.mac --optnone=work,sink
uc80 -g prog.c -o prog.mac --optnone=all
```

`--optnone=all` puts every function of the program under optnone. The runtime and the library are not part of the program, so the runtime and the library stay optimized. `uc80` writes a warning for a name that is not a function with a body. A static function that the merge of two files renamed (`h__s0`) has the C name `h` on the command line. The option then applies to each function of that name. A pragma applies to the function of its own file.

`-g` and optnone are independent. The `.com` file of a program is the same with and without `-g`, also when the program has functions under optnone.

## What uc80 does for a function under optnone

- The function is not inlined. The function gets no inlined call.
- The AST optimizer does not touch the body. The body gets no AST folding of constants, common subexpression elimination, copy propagation, dead store removal, dead code removal or loop unrolling.
- The passes of the code generator skip the function. These are dead store removal, address reuse, chained stores, index narrowing, division by a range, loop invariant motion, the divide pair and the byte holders. The other switches that a `--no-...` option stops are off too (`_OPTNONE_OFF` in `codegen.py`).
- The parameters stay on the stack. The function uses the stack convention. No local variable lives in a register or in the shared `??AUTO` area. Each local variable has a slot at an `IX` offset.
- The function keeps its IX frame (`push IX`, `ld IX,0`, `add IX,SP`) and its `;#func` record says `frame=ix`.
- A call that ends the function stays `call X` followed by the epilogue. The function never uses `jp X`.
- A call to an empty function stays.
- With `-g`, each statement has a `;#line` marker, and the marker is a step point.
- The text of the function stands between `;#noopt begin` and `;#noopt end`. No text pass changes the text between the two markers (see below).

A function under optnone is still a normal function for the other functions. `uc80` optimizes a caller that is not under optnone as before. The caller pushes the arguments on the stack for the function, because the function has no register convention.

## What uc80 does not stop

- The rewrites of the whole program that do not depend on one function still apply to the body: pointers that hold one address, names at an absolute address, and `printf("...\n")` to `puts`. These rewrites do not move a statement.
- `uc80` removes a function that no code calls, also under optnone.
- A function that you name in `--optnone` and that has the attribute `naked` stays as it is: the body is assembly.

## How the text passes leave the function alone

A marker is a comment, so the assembler writes no byte for the marker. The two markers are:

```
_work:
	;#noopt begin
	; optnone _work
	...
	;#noopt end
```

The comment line after `;#noopt begin` names the function. `um80 --debug-info` records the region as `noopt` in `prog.dbg`.

The passes of `uc80` use one of three ways:

- `asm_dce` treats the blocks of a region as one unit. When one block is live, all blocks of the region are live. `asm_dce` removes a function that no code calls, with the region.
- `upeepz80` reads the markers. No pattern and no pass changes a line of a region. The liveness analysis treats a region as code that reads every register and flag. `uc80` also protects the region with the next way.
- The other text passes (`peep_local`, `peep_flow`, `looprot`, `reglocals` and `iyptr`) run on the whole text. `optnone.guard` then puts the text of each region back as the text was before the pass. Each pass sees the callers and the callees of the region as they are. If a pass loses or moves a marker, `optnone.guard` drops the work of the pass and prints `uc80: warning: optnone: a text pass lost a ;#noopt marker; the pass is dropped`.

## Choose the functions: the size report

A function under optnone is bigger than the same function without optnone. A program can grow past 64K. Use the size report of `udb80` to see the size of each function, and choose the functions for `--optnone`:

```
uc80 -g prog.c -o prog.mac
um80 --debug-info prog.mac -o prog.rel
ul80 --debug-info prog.rel $LIB/libc.lib $LIB/runtime.lib -o prog.com
udb80 sizes prog.dbg
```

The report lists the bytes of each function, the biggest first, and the total. See `docs/udb80.md` of um80_and_friends. Without a `udb80` command, run `python3 -m um80.udb80 sizes prog.dbg`.

## Measurements

These numbers come from `um80` ("Code segment" of the program module) and from `ul80`. `uc80` was run with the default options and with `--optnone=all`.

- The 36 programs of `examples/`: the code of the program modules grows from 12,175 to 15,514 bytes (27.4 percent). One program grows 7.2 percent, and the program that grows most doubles (`hello3`: 59 to 118 bytes).
- A program of 400 functions of about 115 bytes each. Each function has a loop, an array update and a call. Run `bench/optnone_big.py 400` to make the program. With `-g` and the default options, the module code is 52,117 bytes and the `.com` file is 53,632 bytes. The program prints `-23226`. With `--optnone=all` the module code is 80,529 bytes, and `ul80` writes ten warnings "a reference to ... leads outside the bytes the module loaded". The `.com` file is 80,640 bytes. `cpmemu` loads 64,512 bytes, and the program prints no line. With `--optnone=f0,...,f10,main` (the 11 functions and `main`, 12 of the 402 functions), the module code is 54,055 bytes, the `.com` file is 55,552 bytes, and the program prints `-23226`.
- `main` is the biggest function of the program (6,020 bytes in the default build, in the `udb80 sizes` list). Take `main` out of the list when the program is close to the limit.

`uc80` has no separate switch for the `tail_call` pattern of `upeepz80` or for rule 3 of `peep_local`. The region replaces both switches: no pattern and no rule changes a line of a region, so a `call` in an optnone function stays a `call`. A `call` that you write in a `.mac` file outside a region is still changed. `PeepholeOptimizer(disable={"tail_call"})` of `upeepz80` stays available for other tools.

An attribute inside braces (a local variable or a member) marks no function. `uc80` prints `optnone attribute is not before a function`.

## Tests

`tests/test_optnone.py` has these tests:

- the three forms, the option and the names of static functions
- the IX frame, the calls, the missing inlining and the missing loop unrolling
- the missing dead store removal and the missing register locals
- the `.com` file with and without `-g`, and the rows of `prog.dbg`
- the output of programs under cpmemu, the guard and `asm_dce`

The upeepz80 tests are in `tests/test_noopt.py` of upeepz80.
