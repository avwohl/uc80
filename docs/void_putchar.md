# putchar as a statement

A call `putchar(c);` whose value is not used now calls `__putchar_v`. The old code pushed the character, called `_putchar` (the stack convention), and dropped the argument with `pop`. `_putchar` called `__conout`, which saves BC, DE and HL. A character cost about 254 T-states. It costs about 103 T-states now.

Family J step 1 in [codegen_gap.md](codegen_gap.md) lists the change.

## The code

The code generator emits this for `putchar(c);` and for `(void) putchar(c);`:

```
	ld	E,<c>
	call	__putchar_v
```

`__putchar_v` is in `src/uc80/lib/lc/lc_putchar_v.mac`. It takes the character in E. It destroys all registers and returns no value.

The routine applies the same rules as `__conout`. LF becomes CR LF, unless the last character was CR. `___crlf_mode` can switch the change off.

`__conout` (`lc_conout.mac`) has the old code again: it saves BC, DE and HL, and it calls BDOS itself. A call of `__conout` costs the same as before. The two bytes `___crlf_mode` and `__conout_last` are in the module `lc_conout_d.mac`. So a program that links only `__putchar_v`, or only `__conout`, does not link the code of the other routine. The library has four `LD C,CONOUT` instructions, two in each routine (the test in `tests/test_crlf.py` counts them).

The register-locals pass knows that `__putchar_v` reads only E (`reglocals.helper_reads`) and changes all registers. A local in BC or DE gets the same wrap as for any other call. Without this knowledge the pass takes an unknown `__` routine for a reader of all registers. The curses programs were 4% to 9% slower then.

## When the change applies

The compiler keeps the call of `_putchar` in these cases:

- The program uses the value of the call (`r = putchar(c)`, `if (putchar(c) == c)`).
- The program defines its own `putchar` in C.
- The target is a ROM (`--target=rom`) or the output is sdas text (`--asm-syntax=sdas`). These targets have their own console.
- The argument is a float, a `long long` or a `_Bool`.
- The option `--no-void-putchar` is on.
- An assembly file (`.mac`) is on the command line. The file can define `_putchar`, and the compiler cannot see all of its text.
- The build has `--no-whole-program`. Another unit can define `_putchar`.

A cast to void now discards the value of any call. `(void) f();` gives the same code as `f();`.

## Tests

`tests/test_famj_startup.py` runs one program with LF, CR LF, only CR and a LF after a CR. It compares the bytes with the expected console output, with the options `--no-crlf`, `--no-void-putchar`, `-O0`, `--int=32` and others. `tests/test_crlf.py` follows the new module names. A test passes an assembly file that defines `_putchar` and checks that the output comes from that routine.
