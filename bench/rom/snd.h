/* snd.h - ROM variant of bench/programs/snd/snd.h (the snd-* kernels).
 *
 * Part of the uc80 benchmark harness.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * The same names as the CP/M header, but out () and in () are real Z80
 * OUT and IN instructions (z80io.mac), and no printf exists.  The kernel
 * output goes to the debug port 0FEh, one byte at a time, through
 * rom_printf (), which knows only literal text and %u.  snd_report ()
 * writes to the end port 0FFh; the host model (sndrom.py) adds the report
 * line that the CP/M snd_report prints: it counts the OUT and IN
 * instructions and keeps the same ring of the last 256 writes.
 *
 * Ports: 04h/05h YM2610 address/data port A, 06h/07h port B, 0Ch reply
 * to the 68k, 0FEh debug text, 0FFh end of run.
 */
#include <stdint.h>
#include <stdarg.h>

#define PORT_YM_ADDR_A 0x04
#define PORT_YM_DATA_A 0x05
#define PORT_YM_ADDR_B 0x06
#define PORT_YM_DATA_B 0x07
#define PORT_REPLY     0x0C
#define PORT_DEBUG     0xFE
#define PORT_END       0xFF

void out(uint8_t port, uint8_t value);
uint8_t in(uint8_t port);

static void rom_putu(unsigned int v)
{
    uint8_t digits[5];
    uint8_t n = 0;

    do {
        digits[n++] = (uint8_t) ('0' + v % 10);
        v /= 10;
    } while (v != 0);
    while (n != 0)
        out(PORT_DEBUG, digits[--n]);
}

void rom_printf(const char *fmt, ...)
{
    va_list ap;

    va_start(ap, fmt);
    for (; *fmt != 0; fmt++) {
        if (fmt[0] == '%' && fmt[1] == 'u') {
            rom_putu(va_arg(ap, unsigned int));
            fmt++;
        } else {
            out(PORT_DEBUG, (uint8_t) *fmt);
        }
    }
    va_end(ap);
}

#define printf rom_printf

static void snd_report(const char *name)
{
    (void) name;
    out(PORT_END, 0);
}
