# Calling convention

uc80 has two calling conventions: the stack convention and the register convention. The compiler chooses one convention for each function. It decides before it generates the first call. The plan for the full register convention is section F of [codegen_gap.md](codegen_gap.md). This page describes what the compiler does today (steps 1 and 2 of that plan).

## The two conventions

The stack convention is the original one. The caller pushes every argument as a 16-bit word, from the last argument to the first. The caller pops the arguments after the call. The callee builds a frame with IX and reads each parameter as `(IX+d)`. A function returns a value in HL (DEHL for a long or a float).

The register convention applies to one function at a time. The callee takes its first two scalar parameters in registers. The caller pushes the other parameters, as before. The caller does not pop the register parameters, because they were never pushed. A result that is not a char is returned as in the stack convention. A char result is returned in A (section "Results in A").

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
- A function with no register parameter also qualifies when it returns a char (`char f(void)`), because the result goes in A.
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

## Results in A

A function of the register convention that returns `char`, `signed char` or `unsigned char` (a typedef of these too, but not `_Bool`) returns the byte in A. The callee does not set H and does not set L. The callee does not extend the byte.

- In the callee, `return e;` evaluates `e` with the byte paths of the compiler (`_gen_expr_a`), so `return a + b;` for two bytes is `add A,E`.
- In the caller, the code asks for A when the value is used as a byte: a store to a char, a `return` of a char, a byte argument, a test (`if (f(x))` is `call` and `or A`), and a call whose value is not used. In these cases the caller does not widen the byte.
- In every other case the caller widens the byte to an int in HL after the call: `ld L,A` then `ld H,0` (unsigned) or the sign extension (signed).

The compiler picks A only when it pays. The prepass counts the calls of the function in two groups, from the syntax of the call. A call is a byte use when it is a whole statement, a condition, an operand of `&&`, `||`, `!` or a comparison, the operand of a cast to a char type, the value stored to a char object (assignment or initializer), or the operand of a `return` in a char function. Every other call is an int use. A function that has more int uses than byte uses returns its char in HL, as in step 1: each int use would pay for the widening that the callee saves once.

The widening moves from the callee to the caller. A call that needs HL costs the same as before. A call that needs A saves the widening: 8 T-states and 2 bytes for each byte result that stays a byte.

## A parameter across a call

The callee stores a parameter that the body names in a slot (section "The callee"). In the shared storage (`??AUTO`) the store and the loads are `ld (??AUTO+n),HL` and `ld HL,(??AUTO+n)`: 16 T-states and 3 bytes each (20 T-states and 4 bytes for DE). The pass `regcc.push_pop_slots` (after `forward_slots`) uses the stack instead when all of these are true:

- The slot has one store and one load left. The store and the load are word instructions for HL or DE (the load may use HL, DE or BC).
- Only straight-line code lies between them: no label, no jump, no return. Calls and balanced `push` and `pop` pairs are allowed. Any use of SP stops the pass.
- The stack is level at the load: the load is not in the middle of a `push` of arguments.

The store becomes `push rr` and the load becomes `pop rr`: 11 and 10 T-states, 1 byte each. When two slots qualify, the stores are put in the order that makes the pops match (the word that is loaded last is pushed first). If the conditions are not true, the compiler spills once at entry, as in step 1. A stack frame keeps the prologue `push`, because the frame layout is fixed.

## Tail calls

`return g(...);` in a function with no stack local and no stack argument for `g` ends with `call _g` then `jp @f_ret`. The compiler replaces the two instructions with `pop IX` and `jp _g` (only `jp _g` when the function has no frame). The function `g` returns to the caller of this function. A call to a function in the register convention has no cleanup. The call and the `ret` are therefore next to each other in many more places than in step 1.

The compiler does not do this when:

- the function has a stack local (a local in `??AUTO` is fine), a stack-frame spill of a parameter, or a statement expression. The callee could receive a pointer to the frame, and the frame is gone after the jump.
- the call has stack arguments, a hidden pointer for a struct result, or any code after it (a cast, or the widening of a char result).
- the callee is a runtime routine (a name that starts with two underscores) or `setjmp`.
- the function is `main` or an interrupt function.

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
- `bench/ccfuzz.py`: random programs with many small functions, compared with the host compiler. See the header of the file for the kinds of calls. It also makes tail calls and returns of an expression of another type.
- `bench/difftest.py --focus calls`: random programs against SDCC.

## Later steps

Steps 1 and 2 are the subject of this page. Two items of the plan in [codegen_gap.md](codegen_gap.md) are still open:

- a `jp` for a call that has stack arguments (the callee would have to pop its own arguments).
- a third register parameter (a byte in L, as SDCC does).
