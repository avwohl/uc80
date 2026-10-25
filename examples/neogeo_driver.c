/* Neo Geo sound driver skeleton: an NMI command handler, a YM2610
   timer IRQ (IM 1), a YM busy wait and a bank select.
   Build and run: see docs/neogeo_driver.md. */
#include <z80.h>
#include <stdint.h>

#pragma uc80 nmi on_nmi
#pragma uc80 interrupt on_tick critical

volatile uint8_t cmd, got, ticks;

/* Write YM2610 register r (port 4 = address, port 5 = data).
   Bit 7 of port 4 is the busy flag. */
static void ym_a(uint8_t r, uint8_t v)
{
    while (z80_in(4) & 0x80)
        ;
    z80_out(4, r);
    while (z80_in(4) & 0x80)
        ;
    z80_out(5, v);
}

/* NMI: read the command from port 0, answer on port 0Ch. */
void on_nmi(void)
{
    cmd = z80_in(0);
    got = 1;
    z80_out(0x0C, cmd);
}

/* Timer A IRQ: count a tick, reset the timer flag. */
void on_tick(void)
{
    ticks++;
    ym_a(0x27, 0x15);
}

void main(void)
{
    z80_out(0x18, 0);               /* close the NMI gate */
    ym_a(0x24, 0x10);               /* timer A high byte */
    ym_a(0x25, 0);                  /* timer A low bits */
    ym_a(0x27, 0x15);               /* load and start timer A, enable IRQ */
    z80_out(0x08, 0);               /* open the NMI gate */
    z80_ei();
    for (;;) {
        if (got) {
            got = 0;
            /* IN with the bank number in B: bank = cmd, window 8000h-BFFFh */
            z80_in16(((uint16_t)cmd << 8) | 0x0B);
            ym_a(0x10, cmd);
        }
    }
}
