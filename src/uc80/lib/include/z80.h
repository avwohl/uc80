/* z80.h - port I/O, interrupt control and critical sections for uc80.
 *
 * Each name below is a compiler intrinsic: the compiler writes the Z80
 * instruction in line, and no optimizer pass moves, merges or deletes it.
 * Two OUT calls with the same port and value are two OUT instructions.
 * See docs/rom.md ("Hardware access").
 *
 * Port numbers
 *   z80_in (port) and z80_out (port, value) take an 8-bit port.  With a
 *   constant port the code is IN A,(n) or OUT (n),A.  With a variable
 *   port the code is LD C,port / IN A,(C) or OUT (C),A.
 *   z80_in16 (port) and z80_out16 (port, value) take a 16-bit port: BC
 *   is the whole port address.  The high byte (register B) is the bank
 *   number on the Neo Geo: z80_in16 ((bank << 8) | 0x0B) selects a bank
 *   of the window at 8000h.  A read selects a bank, so use the read form
 *   (z80_in16) for that, and z80_out16 only for ports that are written.
 */
#ifndef _Z80_H
#define _Z80_H

#include <stdint.h>

unsigned char __builtin_z80_in (unsigned int port);
unsigned char __builtin_z80_out (unsigned int port, unsigned char value);
unsigned char __builtin_z80_in16 (unsigned int port);
unsigned char __builtin_z80_out16 (unsigned int port, unsigned char value);
void __builtin_z80_di (void);
void __builtin_z80_ei (void);
unsigned char __builtin_z80_irq_save (void);
void __builtin_z80_irq_restore (unsigned char state);
void __builtin_z80_crit_enter (void);
void __builtin_z80_crit_leave (void);
unsigned int __builtin_z80_load16 (const volatile unsigned int *p);

#define z80_in(port)             __builtin_z80_in (port)
#define z80_out(port, value)     __builtin_z80_out ((port), (value))
#define z80_in16(port)           __builtin_z80_in16 (port)
#define z80_out16(port, value)   __builtin_z80_out16 ((port), (value))

/* DI and EI. */
#define z80_di()                 __builtin_z80_di ()
#define z80_ei()                 __builtin_z80_ei ()

/* Turn the interrupts off and return the old state (not zero: they were on);
 * z80_irq_restore (state) turns them on again only if they were on.  The
 * pair can nest, and can be in different functions. */
#define z80_irq_save()           __builtin_z80_irq_save ()
#define z80_irq_restore(state)   __builtin_z80_irq_restore (state)

/* The SDCC critical section, as two statements: LD A,I / DI / PUSH AF, and
 * POP AF / JP PO,L / EI / L:.  The two must be in the same function, and
 * no return, goto, break or continue may leave the section.  The SDCC
 * spelling __critical { ... } checks that and is easier to read. */
#define z80_critical_enter()     __builtin_z80_crit_enter ()
#define z80_critical_leave()     __builtin_z80_crit_leave ()

/* Read a 16-bit object that an interrupt function writes, with no torn
 * value.  For a known address (z80_load16 (&counter)) the code is one
 * LD HL,(nn), which an interrupt cannot split.  For any other pointer the
 * code turns the interrupts off around the two byte reads. */
#define z80_load16(p)            __builtin_z80_load16 (p)

#endif
