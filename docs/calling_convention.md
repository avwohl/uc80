# Calling convention

uc80 has two calling conventions: the stack convention and the register convention. The compiler chooses one convention for each function. It decides before it generates the first call. The plan for the full register convention is section F of [codegen_gap.md](codegen_gap.md). This page describes what the compiler does today (step 1 of that plan).

## The two conventions

The stack convention is the original one. The caller pushes every argument as a 16-bit word, from the last argument to the first. The caller pops the arguments after the call. The callee builds a frame with IX and reads each parameter as `(IX+d)`. A function returns a value in HL (DEHL for a long or a float).

The register convention applies to one function at a time. The callee takes its first two scalar parameters in registers. The caller pushes the other parameters, as before. The caller does not pop the register parameters, because they were never pushed. The return value is the same as in the stack convention.

## Which registers

A scalar parameter is a char, a short, an int, an enum, a `_Bool` or a pointer: one or two bytes. Only parameter 0 and parameter 1 can travel in registers. A parameter of another type stays on the stack. The other parameter can still use a register.

- Parameter 0: a byte goes in A, a word goes in HL.
- Parameter 1: a byte goes in E, a word goes in DE.

The two parameters never share a register. Parameter 1 uses E and DE (not L), so the code of parameter 0 can use HL freely. A byte argument is the low byte of the converted argument. The caller does not extend the byte. The callee extends the byte when the body needs a word.

Examples:

```
int  f(int a, int b)            a: HL   b: DE
void out(unsigned char p, unsigned char v)    p: A    v: E
int  g(long a, int b)           a: stack (4 bytes)   b: DE
int  h(int a, int b, int c)     a: HL   b: DE   c: stack
int  k(char *s, char c)         s: HL   c: E
```

## Which functions qualify

The compiler uses the register convention for a function when all of these rules are true. The compiler tests the rules on the complete program, in the prepass `CodeGenerator._plan_register_cc`.

- The compiler sees the whole program (the default). `--no-whole-program` keeps the stack convention for every function.
- The function has a body in the program.
- The name of the function occurs only as the callee of a direct call. A function with an address that the program takes (`&f`, `f` as a value, a table of function pointers, a global initializer) keeps the stack convention. The same is true for an indirect call.
- No assembly mentions the function: not an `asm` statement in any function, not a file-scope `asm`, not an appended `.mac` file. Basic asm is opaque text, so the compiler scans each template for the name. A function that contains an `asm` statement also keeps the stack convention, because the asm text can read the parameters at `(IX+d)`.
- No library routine mentions the name. The compiler reads the `PUBLIC` and `EXTRN` lines of the library sources. A program function with the name of a library function (`abs`, `putchar`) keeps the stack convention, because library code can call the function.
- The function is not an entry point: not `main`, not named by `--entry` or `#pragma uc80 keep`, `public` or `vector`, not an interrupt function.
- An interrupt function does not reach the function. This keeps the ISR safe rules (section "Storage of the parameters").
- The call graph includes calls through pointers. A call through a pointer, a call of a name that is not a declared function, and a call of a library routine that takes a callback (`qsort`, `bsearch`, `atexit`, `signal`) or an argument that names a function, can reach every function with an address that the program takes. So `f` calls `fp`, `fp` points to `h`, and `h` calls `f`: this is a cycle, and `f` is recursive. An interrupt function that calls a pointer reaches the same functions.
- The function is not variadic. A function that returns a struct wider than HL (`sret`) keeps the stack convention.
- Every call has as many arguments as the definition has parameters.
- `--int=32` is not set. The options `--embed-lib`, `--runtime-lib` and `--startup-lib` turn the register convention off, because the extra assembly is not scanned.

`--no-register-cc` turns the register convention off for the whole program.

The compiler cannot see code that the linker adds after the compile: a separate `.rel` or `.lib` file on the `ul80` command line. If such code calls a C function by name with the stack convention, name the function with `#pragma uc80 keep NAME` (or `--entry NAME`), or use `--no-register-cc`.

## Calls

The caller does these steps in this order:

1. Push the arguments that are not in registers, from the last to the first.
2. Load the register arguments.
3. `call` the function.
4. Pop the pushed arguments (none, when every parameter is in a register).

A call that ends a function and has no pushed arguments becomes `jp` (the `peep_local` rule `call` then `ret`). In the stack convention that rule almost never applies.

Step 2 must not destroy one register argument while the compiler computes the other. The compiler uses these rules:

- A constant, or a variable of the size of the parameter, loads straight into the register (`ld DE,5`, `ld DE,(x)`, `ld E,(IX-3)`). The compiler loads such an argument last. It uses no other register than its own (a byte in a global variable also uses A).
- Otherwise the compiler computes parameter 1 first. Then it computes parameter 0. If the code of parameter 0 can change D or E (it has a call, a jump or a push), the compiler saves DE with `push DE` before that code and `pop DE` after.
- The register scan uses `upeepz80.z80.effect`. A run of code that has only plain register instructions and does not write E or D needs no save.

## The callee

The callee stores each register parameter that the body names. The callee does not store a parameter that the body never names. The store goes to a slot of the function:

- In shared storage (`??AUTO`), the slot is in the region of the function. The store is one instruction: `ld (??AUTO+n),A`, `ld (??AUTO+n),HL` or `ld (??AUTO+n),DE`. The store of a byte in E also stores D, because A can hold parameter 0.
- In a stack frame, the prologue pushes the register: `push AF` (A is the high byte of the slot), `push HL` or `push DE`. The pushes come before the instructions that reserve the rest of the frame, because those instructions destroy HL. A slot is always one word in a stack frame.

The slot is a local variable of the function. The function can assign the parameter and take its address.

After the compiler generates the body, the pass `regcc.forward_slots` removes the loads of the slots that a register still holds. The pass deletes `ld HL,(??AUTO+0)`, or replaces the instruction with register moves, as long as no label, call or write to the register comes between. It also handles `ld r,(IX+d)` and the ALU forms `add A,(IX+d)` and `ld HL,??AUTO+n` then `add A,(HL)`. When no read of a slot remains, the pass removes the stores to the slot (shared storage only). A function without stack locals and without stored parameters needs no frame. The existing rule `_frame_is_unused` removes `push IX / ld IX,0 / add IX,SP` and the epilogue.

`out(port, v)` is an example:

```
_out:
        add     A,E
        ld      (_a),A
        ret
```

A loop of 1000 calls of `out(3, k)` costs 199 T-states for each call in the stack convention and 50 T-states in the register convention. Both numbers include the call site and the store to `a`. The cost of the loop is not in the numbers.

## Storage of the parameters

The slot of a parameter has the same storage rules as every other local variable.

- A recursive function does not use shared storage. The slots are in the stack frame, one for each activation. The prologue therefore saves a register parameter that is live across the recursive call. Two activations never use one static slot.
- `--no-shared-storage` puts all slots in the stack frame.
- `--isr-safe` already keeps the functions that an interrupt function reaches on the stack. Without `--isr-safe`, the compiler would put the slot of a parameter in `??AUTO`. An interrupt could then overwrite the parameter of an interrupted call. In the stack convention a parameter was safe, because it was on the stack. Therefore the compiler keeps the stack convention for every function that an interrupt function reaches, with or without `--isr-safe`.
- The call-graph analyser sees the slot as part of the local storage of the function (`func_storage`). Two functions that can be active together never share a slot.

## Optimizer passes

The passes that run after the code generator need no special rule for the register convention.

- `upeepz80` follows a `call` into the routine and reads the registers that the routine reads before it writes them. A load of A, HL or DE before a `call` is therefore live. A `call` to a label outside the text reads every register.
- `peep_local` looks at neighbouring instructions only, and a label ends each rule.
- `asm_dce` follows blocks that the program reaches. It does not track registers.

The test `asm_between` in `tests/test_register_cc.py` puts inline asm between calls. The optimizer then works on separate pieces of text, and the test checks that the arguments survive.

## Options

- `--no-register-cc`: use the stack convention for every function. Use this option to compare the two conventions on a program.

## Tests

- `tests/test_register_cc.py` checks three things. It checks the shape of the code. It checks the functions that keep the stack convention. It checks that both conventions give the same output for recursion, byte parameters, function pointers, variadic callees, struct returns, assembly callers and the storage options.
- `bench/ccfuzz.py`: random programs with many small functions, compared with the host compiler. See the header of the file for the kinds of calls.
- `bench/difftest.py --focus calls`: random programs against SDCC.

## Later steps

Step 1 is the subject of this page. The next steps of the plan in [codegen_gap.md](codegen_gap.md) are:

- a non-leaf callee that keeps its parameters live across a call without a store
- a byte result in A
- the tail call (`jp`) for calls that have stack arguments
