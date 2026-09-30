# Inline Assembly

Back to the [README](../README.md).

uc80 supports basic `asm("...")`, spelled `asm` or `__asm__`, with or without
`volatile`. The text of the template goes into the output assembly unchanged,
at the point where it is written. Write it in MACRO-80 syntax, because um80
assembles it.

```c
int marker;

/* File scope: hand-written data and code. */
asm("\tPUBLIC\t_table\n"
    "_table:\tdw\t11,22,33\n");

void store(void) {
    /* Inside a function. */
    asm("ld hl,1234\n\tld (_marker),hl");
}
```

Rules for the assembly text:

- A C object is reached through its assembler symbol. A global `x` is `_x`.
- IX is the frame pointer. Preserve it. SP is free if you balance it. Every
  other register is free, because uc80 holds no value in a register across a
  statement.
- An inline assembly block is a barrier. The peephole optimizer and the
  assembly dead-code eliminator do not change, move or delete it, and no
  optimization crosses it.
- uc80 emits the block in CSEG. A block that changes the segment should change
  it back, because the compiler-generated code after it expects CSEG.
- The C grammar makes `asm` a block item, not a statement, so an unbraced
  `if (c) asm("nop");` is a syntax error. Write `if (c) { asm("nop"); }`.

Extended asm, which has an operand or clobber list, for example
`asm("ld hl,%0" : : "r"(x))`, is not supported. `asm goto` is not supported.
Both stop the compilation with an error message. Pass values through global
variables instead. `#asm`/`#endasm` and `__naked` are also not supported.
