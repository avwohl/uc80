# Interrupt functions and hardware access

Back to [rom.md](rom.md). The plan is in [rom_target.md](rom_target.md). A full Neo Geo driver example is in [neogeo_driver.md](neogeo_driver.md).

This page describes how a C program handles interrupts and reaches the hardware of a Z80 board. The text applies to `--target=rom`. The intrinsics and the `__at` objects also work in a CP/M program.

## Interrupt functions

An interrupt function is a C function that the hardware enters. The function saves the registers and ends with `RETI` or `RETN`. Mark the function with a pragma:

```
#pragma uc80 interrupt NAME             maskable interrupt, EI at the start
#pragma uc80 interrupt NAME critical    maskable interrupt, EI before RETI
#pragma uc80 nmi NAME                   non-maskable interrupt, RETN
```

The pragma goes in any C source file. Declare the function as `void NAME(void)`. You can use a `static` function.

The compiler does these steps for each interrupt function:

- It writes `EI` first (only for `interrupt NAME`), then `PUSH AF, BC, DE, HL, IY`. The frame code saves IX when the function needs IX.
- It writes `POP IY, HL, DE, BC, AF` and `RETI` at the end. The `critical` form writes `EI` before `RETI`. The `nmi` form writes `RETN` and no `EI`.
- It keeps the function in the program, whatever the call graph says, and gives the function a `PUBLIC` label.
- It sets the vector: `interrupt` sets the INT vector at 0038h, and `nmi` sets the NMI vector at 0066h. You do not need a `vector` pragma.
- It never inlines the function into a caller.

An interrupt function can call ordinary functions. Control comes back with `RETI` or `RETN` from the interrupt function only.

The compiler stops with an error in these cases:

- The function has a parameter, or returns a value.
- Other C code calls the function. Put the shared code in an ordinary function.
- The pragma names a function that the program does not define.
- Two functions want the same vector.

The compiler keeps the IX frame elision for an interrupt function. A function that does not name IX does not save IX, and no function that the interrupt function calls changes IX.

### EI at the start

The form `interrupt NAME` is the SDCC `__interrupt` form: the handler starts with `EI`. A new interrupt can then arrive in the handler. A level-triggered source (the YM2610 timer flag) that the handler has not yet cleared makes the handler run again at once, and again, until the stack is full. Use `interrupt NAME critical` when the handler clears the source of the interrupt itself. The `critical` form does not allow a nested interrupt.

An interrupt function with `EI` at the start never keeps its locals in the shared `??AUTO` area, because the function can interrupt itself.

### Alternate registers

The prologue also saves the alternate registers (`EX AF,AF'`, `EXX`) when the handler can reach code that uses them. The compiler checks the call graph of the final assembly, with the library sources for routines that are not in the program. The float runtime of the compiler does not use the alternate registers today. Hand-written assembly does. The compiler prints a warning that names the call chain:

```
uc80: warning: interrupt function 'tick' reaches code that uses the alternate
registers (tick -> _alt_user); the prologue saves and restores them
```

The check cannot see a call through a function pointer.

### Routines with fixed RAM

Some runtime and library routines keep their temporary values at fixed RAM addresses: `__tmp32`, `__divrem`, `__div32_dvd`, `__rem32`, the 64-bit work areas, the float and complex work areas, and the static variables of libc. Suppose an interrupt function and the main program both use such a routine. An interrupt that arrives in the routine then changes the temporary values of the main program.

The compiler prints a warning for each work area that an interrupt function reaches, with the call chain:

```
uc80: warning: interrupt function 'isr' reaches __div32_dvd, __rem32 (work area of
rt_arith32) at a fixed address (isr -> _divide -> __div32); main code that calls
the same routine can be corrupted by the interrupt
```

The warning is safe to ignore when the main program never uses the same routine. The simple cure is to avoid long division, 64-bit arithmetic and floating point in a handler.

### Shared automatic storage

The compiler normally puts the locals of functions that are never active together in one shared area (`??AUTO`). A function that the main program and an interrupt function both call has one copy of its locals. An interrupt that arrives in the function overwrites the locals of the main program. The compiler prints a warning:

```
uc80: warning: 'work' is called from an interrupt function and from other code,
and keeps its locals in shared storage; an interrupt inside 'work' overwrites
them. Compile with --isr-safe to keep them on the stack.
```

The option `--isr-safe` keeps the locals of all functions that an interrupt function reaches on the stack. The code is a little larger and slower. The option does not change functions that no interrupt function reaches.

## RST vectors and IM 2

The RST vectors 0008h to 0030h and the IM 2 vector table are new. Both need `--target=rom` with the start-up code that uc80 embeds (whole-program mode, no `--startup-lib`). With another start-up code, the compiler prints a warning and your code must set the vector.

### RST vectors

```
#pragma uc80 rst 10h NAME                       software call: a plain function
#pragma uc80 rst 18h NAME interrupt             interrupt function: EI, saves, RETI
#pragma uc80 rst 18h NAME interrupt critical    interrupt function: EI before RETI
#pragma uc80 vector 10h NAME                    the same as rst 10h NAME
```

The address is 08h, 10h, 18h, 20h, 28h or 30h. A decimal number and `0x10` also work. The start-up code changes the `RET` at the vector to `JP NAME`. A vector that has no pragma keeps `RET`.

- Software call. The function is a plain C function with the form `void NAME(void)`. An assembly caller runs `RST 10h`, and the function ends with `RET`. The function saves no registers beyond the normal C rules, so the caller must expect that any register changes. The C convention for the stack and for the registers still applies. A naked function is also a valid target: it gets the stack as the `RST` left it.
- Interrupt function. Use this form when a device puts an RST opcode on the data bus in IM 0. The function has the same prologue and epilogue as `#pragma uc80 interrupt NAME`: `EI` first (not with `critical`), `PUSH AF, BC, DE, HL, IY`, and `RETI`. The function must not take a parameter or return a value. No C code can call the function.

The compiler stops with an error in these cases:

- The address is 00h (the reset vector). Use `#pragma uc80 interrupt NAME` for 38h.
- The address is not a multiple of 8 from 08h to 30h.
- Two pragmas set the same RST vector.
- The function is not defined.

RST 38h and 66h keep their own pragmas. The SDCC form `__interrupt(n)` makes no vector, and n has no effect. Add the pragma for the vector.

### IM 2

```
#pragma uc80 im2 SLOT NAME              bind NAME to a slot of the vector table
#pragma uc80 im2 SLOT NAME critical     the same, with EI before RETI
#pragma uc80 im2table ADDR              set the address of the table
```

The device puts one byte on the data bus. The CPU makes the address of a vector from the register I (high byte) and the data bus byte (low byte). The CPU reads a 16-bit handler address at this place. A device puts an even byte on the bus, so one table has 128 slots of two bytes. The bus byte is two times SLOT, and SLOT is 0 to 127.

An `im2` pragma, or an `im2table` pragma, selects IM 2 for the whole program:

- The reset vector at 0000h does `DI`, `IM 2` (in place of `IM 1`).
- `__start` loads I with the high byte of the table address, before `main`. The program must run `EI` itself, as for IM 1.
- The compiler writes the table of 256 bytes at the address of `im2table`. The default address is 0100h. The address must be a multiple of 100h from 0100h to FF00h, because the start-up code must fit below the table. The PUBLIC label of the table is `__im2_table`.
- A slot with no pragma points to `__im2_default` (PUBLIC), which does `EI` and `RETI`. Then a bus byte for an unbound slot does no harm.
- The program code starts after the table (at 0200h for the default). The ROM has a gap between the end of the start-up code and the table. The start-up code with every option (watchdog, user INT handler, user NMI handler) must stay below the table. A test checks this limit. The cost of IM 2 is 256 bytes for the table and the gap. A program with no IM 2 pragma has the same image as before.
- A handler is an interrupt function as for `#pragma uc80 interrupt NAME`: `EI` first (not with `critical`), five pushes, and `RETI`. One function can serve two slots when both pragmas use the same form. The vector at 0038h is not used in IM 2.

The compiler stops with an error in these cases:

- A slot is not in 0 to 127.
- Two pragmas bind the same slot.
- An `im2table` address is not a multiple of 100h, or is below 0100h.
- Two `im2table` pragmas give different addresses.
- The function is not defined, takes a parameter, returns a value, or has a caller in C.
- The function has only a prototype in C, and no assembly input file defines its PUBLIC label.

A handler can be a function in an assembly input file. Declare the function in C (`extern void ih(void);`) and name the function in the `im2` pragma, or in the `rst ... interrupt` pragma. The compiler does not add code to the function. The assembly code must save the registers it changes, and must end with `EI` and `RETI` (`RETI` alone for a critical handler).

Example, with a device that puts 06h (slot 3) or 14h (slot 10) on the bus:

```
#pragma uc80 im2 3 on_a
#pragma uc80 im2 10 on_b
void on_a(void) { count_a++; }
void on_b(void) { count_b++; }
```

The 257-byte table (every byte equal, so that any bus byte gives the same handler) for a device that puts an odd or floating byte on the bus is not supported. Use an assembly start-up code for such a device. The runner `bench/romrun` does not force bit 0 of the bus byte to 0, as the real CPU does not.

Tests: `tests/test_rom_vectors.py` runs these cases: two IM 2 handlers on two slots, an unbound slot, the RST software call, the RST interrupt function in IM 0, and IM 1 with no IM 2. Each test runs with `ul80` and with `--asm-syntax=sdas` (`sdasz80`, `sdldz80`). The sdas flow links the code at the origin after the table.

## Port input and output

`<z80.h>` has the port functions. Each function is a compiler intrinsic. The compiler writes the instruction in line. No optimizer pass moves, merges or removes the instruction, as for a volatile access. Two calls with the same port and value are two `OUT` instructions.

```
z80_out (port, value)      8-bit port: OUT (n),A, or LD C,port / OUT (C),A
z80_in (port)              8-bit port: IN A,(n), or LD C,port / IN A,(C)
z80_out16 (port, value)    16-bit port: LD BC,port / OUT (C),A
z80_in16 (port)            16-bit port: LD BC,port / IN A,(C)
```

- The value of `z80_in` and `z80_in16` is an `unsigned char`. The value of `z80_out` is the byte that the call wrote.
- For `z80_in16` and `z80_out16`, BC is the whole port address. The upper byte (B) is the bank number on the Neo Geo: `z80_in16 ((bank << 8) | 0x0B)` selects a bank of the window at 8000h. A bank select is a port read, so there is an `in` form and an `out` form, and they are separate.
- A call in an expression, such as `z80_in (4) & 0x80`, reads the port in A with no extra moves.

## Interrupt control

```
z80_di ()                   DI
z80_ei ()                   EI
s = z80_irq_save ()         LD A,I / DI. s is not zero when interrupts were on.
z80_irq_restore (s)         EI only when s is not zero
z80_critical_enter ()       LD A,I / DI / PUSH AF
z80_critical_leave ()       POP AF / JP PO,L / EI / L:
z80_load16 (p)              read a 16-bit object that an interrupt function writes
```

- `z80_irq_save` and `z80_irq_restore` can be in different functions, and they can nest. Interrupts that were off stay off.
- `z80_critical_enter` and `z80_critical_leave` are the SDCC form of a critical section. Both calls must be in the same function, and no `return`, `goto`, `break` or `continue` can leave the section. The SDCC form `__critical { ... }` makes the same code, and the compiler checks the jumps.
- `z80_load16 (&counter)` is one `LD HL,(nn)`. The CPU takes an interrupt only between instructions, so the read cannot be torn. For any other pointer, the code turns the interrupts off for the two byte reads and restores the old state.

## Objects at a fixed address

An object at a fixed address has a name and no storage. The name is an `EQU`. Write the address with `__at` or with the C23 attribute:

```
volatile unsigned char reg __at(0xF900);
unsigned char table[4] __at(0xF910);
__at(0xF920) unsigned int word;
[[uc80::at(0xF930)]] unsigned char flag;
```

- The object is always volatile: every access happens as written.
- The object cannot have an initial value, and it cannot be a local object.
- The ROM image does not contain the object, and the BSS clear does not touch the object.

The compiler also treats a volatile integer object at a constant address like this:

```
#define REG8 (*(volatile unsigned char *) 0xF900)
REG8 |= 4;           /* LD A,(F900h) / OR 4 / LD (F900h),A */
```

The same holds for `((volatile T *) K)[i]` with a constant `i`. Only integer types change. A pointer variable that holds the address (`volatile unsigned char *const p = (...) 0xF900`) is a normal pointer.

## SDCC spellings

A prepass reads these SDCC spellings and makes the same code as the uc80 forms:

```
void f(void) __interrupt { }               interrupt f
void f(void) __interrupt(n) { }            interrupt f (n is not used)
void f(void) __critical __interrupt { }    nmi f
void f(void) __critical __interrupt(n) { } interrupt f critical
__sfr __at(0x10) PORT;                     8-bit port, IN and OUT
__sfr __banked __at(0x1234) PORT;          16-bit port (BC), IN and OUT
T name __at(0xF800);                       object at a fixed address
__critical { ... }                         critical section
__asm ... __endasm;                        inline assembly in sdas syntax
```

- A read of a port name becomes `IN`. A write becomes `OUT`. `PORT |= x` becomes `IN`, `OR`, `OUT`. `PORT++` becomes `IN`, `INC`, `OUT`.
- The address of a port is an error.
- `__interrupt(n)` does not make a vector. The compiler sets the vector as for the pragma.
- An `__asm` block becomes an `asm("...")` statement with MACRO-80 text. [rom_interop.md](rom_interop.md) lists the forms.

The section "Naked functions" describes `__naked`. Every other SDCC form stays an error: `__banked` on a function, `__sdcccall`, `__preserves_regs`, `__using`, a bare `__critical`, `[[gnu::interrupt]]`, `__attribute__((interrupt))`, `__interrupt` before the declarator, and a bare `__fastcall`. The compiler never drops one of them without a message.

## Naked functions

A naked function has no frame code. The compiler writes the label and the body only. It writes no `push IX`, no stack space for locals, no code for the parameters and no `RET`. The body is the assembly of the programmer.

```
int add2(int a, int b) __naked
{
    __asm
        ld hl,#2
        add hl,sp
        ld e,(hl)
        inc hl
        ld d,(hl)
        inc hl
        ld a,(hl)
        inc hl
        ld h,(hl)
        ld l,a
        add hl,de
        ret
    __endasm;
}

[[uc80::naked]] void f(void) { asm("\tret"); }
```

Spellings: `__naked` after the parameter list (also with `__interrupt` and `__critical`), and the C23 attribute `[[uc80::naked]]` before the declaration or after the parameter list. A `__naked` on a prototype has no effect.

Rules:

- The body holds only `asm("...")` statements, `__asm ... __endasm;` blocks and empty statements. Any other statement or declaration is an error with the line number. SDCC allows C code in the body, but the result is not defined. So uc80 refuses it.
- A body with no asm statement gives a warning: the function has no code and runs into the next function.
- You must write the `RET` (or `RETI`, `RETN`, or a `JP`). The compiler never adds one.
- No pass changes the asm text. The peephole optimizer and the assembly dead-code pass keep each asm block as it is. The compiler never inlines a naked function and never propagates a constant into one.
- A naked function uses the stack convention, never the register convention. At entry, `(SP)` is the return address. The caller pushed each argument as a 16-bit word, the last argument first. So the first parameter is at `(SP+2)`, the second at `(SP+4)`, and so on. A `long` or `float` takes two words (low word at the lower address). Do not change `IX`: the C caller needs it.
- The result goes in HL (DEHL for a `long` or `float`). A `char` result goes in HL too.
- The caller removes the arguments. The asm must not remove them.
- A C function can call a naked function. Only assembly can also reference one. The compiler reads each `_name` in the asm text as a reference. So a function that only asm calls stays in the program. A naked function that no C code and no asm text names is dead code, and the compiler removes it. Use `#pragma uc80 keep NAME` to keep a function that other assembly files use.
- The interrupt check treats the body as opaque text. It follows each `_name` in the asm text as a call. So a C function that a naked handler calls counts as reached by the interrupt: `--isr-safe` and the shared-storage warning apply to the function. The compiler saves no registers for a naked handler, so the note about the alternate registers does not apply.

A naked function as an interrupt function (`#pragma uc80 interrupt NAME [critical]`, `#pragma uc80 nmi NAME`, `__interrupt`): the vector jump goes to the label. The compiler writes no `EI`, no register saves and no `RETI` or `RETN`.

The asm must save every register that it changes, and must end with `RETI` or `RETN` (and `EI` before `RETI` if the handler must enable interrupts). The kind of the pragma sets only the vector (38h or 66h). The function must still return `void` and take no parameters. No C code can call the function.

## Proof

`bench/rom/isrproof.py` builds three ROMs and runs them in `bench/romrun` with real interrupts. The test `tests/test_rom_isr_proof.py` runs the same code.

```
python3 bench/rom/isrproof.py            # nmi, seq, bank
```

- `nmi`: the snd-cmdq kernel with a real NMI. The handler (`#pragma uc80 nmi`) reads the command byte from port 0 with `z80_in` and queues it. The main loop dispatches 300 random commands. The port log has the same 1039 writes as a Python model of the dispatch switch. The handler takes 667 T-states from its first instruction to the end of `RETN`. The NMI acknowledge adds 11.
- `seq`: the snd-seq kernel driven by the YM2610 timer A (IM 1). The handler (`interrupt NAME critical`) reads the status with `z80_in`, resets the flag, and runs one sequencer tick. After 500 interrupts the YM write log has the same 7934 writes, in the same order, as the plain loop version of the kernel. A tick takes 19589 to 50300 T-states. The longest timer period is 36864 T-states, so a long tick makes the next interrupt come at once after `RETI`. The compiler prints no warning for this ROM, because the tick reaches no routine with fixed RAM.
- `bank`: `z80_in16` with the bank number in B selects banks 2 to 7 of the window at 8000h. The port log has the 16-bit port addresses 020Bh to 070Bh, and the bytes read through the window are the bytes of the image.

The save and the restore of an interrupt function cost 131 T-states: `EI` at the start (4), five pushes (59), five pops (54) and `RETI` (14). The acknowledge of the interrupt adds 13 T-states in IM 1 and 11 for an NMI. The function body comes on top. See `tests/test_rom_isr.py::test_isr_cycle_cost`.
