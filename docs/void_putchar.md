# putchar as a statement

A call `putchar(c);` whose value is not used now calls `__putchar_v`. The old code pushed the character, called `_putchar` (the stack convention), and dropped the argument with `pop`. `_putchar` called `__conout`, which saves BC, DE and HL. A character cost about 254 T-states. It costs about 103 T-states now.

Family J step 1 in [codegen_gap.md](codegen_gap.md) lists the change.

## The code

The code generator emits this for `putchar(c);` and for `(void) putchar(c);`:

```
	ld	E,<c>
	call	__putchar_v
```

`__putchar_v` is in `src/uc80/lib/lc/lc_conout.mac`. It takes the character in E. It destroys all registers and returns no value. The routine applies the same rules as `__conout`: LF becomes CR LF, unless the last character was CR, and `___crlf_mode` can switch the change off. `__conout` is now a short wrapper. It saves BC, DE and HL and calls `__putchar_v`. The library has still two `LD C,CONOUT` instructions (the test in `tests/test_crlf.py` counts them).

The register-locals pass treats `__putchar_v` as a call that changes all registers. A local in BC or DE gets the same wrap as for any other call.

## When the change applies

The compiler keeps the call of `_putchar` in these cases:

- The program uses the value of the call (`r = putchar(c)`, `if (putchar(c) == c)`).
- The program defines its own `putchar`.
- The target is a ROM (`--target=rom`) or the output is sdas text (`--asm-syntax=sdas`). These targets have their own console.
- The argument is a float, a `long long` or a `_Bool`.
- The option `--no-void-putchar` is on.

A cast to void now discards the value of any call. `(void) f();` gives the same code as `f();`.

## Tests

`tests/test_famj_startup.py` runs one program with LF, CR LF, only CR and a LF after a CR. It compares the bytes with the expected console output, with the options `--no-crlf`, `--no-void-putchar`, `-O0`, `--int=32` and others. `tests/test_crlf.py` is not changed.
