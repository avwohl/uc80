# Neo Geo sound driver example

Back to the [README](../README.md). This page shows one C file for the Neo Geo sound CPU. The file has an NMI command handler, a YM2610 timer interrupt, a YM busy wait and a bank select. See [rom_hardware.md](rom_hardware.md) for the features and [romrun.md](romrun.md) for the runner.

## Z80 ports

These are the ports that the example uses.

- Port 00h, read: the command byte from the 68000. An NMI is the signal that a command is ready.
- Port 0Ch, write: the reply byte to the 68000.
- Port 08h, write: open the NMI gate (enable the NMI).
- Port 18h, write: close the NMI gate (disable the NMI).
- Port 04h: YM2610 address port A. A read gives the status. Bit 7 of the status is the busy flag.
- Port 05h: YM2610 data port A.
- Ports 06h and 07h: YM2610 address and data port B.
- Ports 08h to 0Bh, read: select a bank. The high byte of the port address (B) is the bank number. Port 0Bh selects a 16 KiB window at 8000h to BFFFh. Use `z80_in16`, because the bank number must be in B.

## The file

The file is [examples/neogeo_driver.c](../examples/neogeo_driver.c).

```c
#include <z80.h>
#include <stdint.h>

#pragma uc80 nmi on_nmi
#pragma uc80 interrupt on_tick critical

volatile uint8_t cmd, got, ticks;

static void ym_a(uint8_t r, uint8_t v)
{
    while (z80_in(4) & 0x80) ;
    z80_out(4, r);
    while (z80_in(4) & 0x80) ;
    z80_out(5, v);
}

void on_nmi(void)  { cmd = z80_in(0); got = 1; z80_out(0x0C, cmd); }
void on_tick(void) { ticks++; ym_a(0x27, 0x15); }

void main(void)
{
    z80_out(0x18, 0);
    ym_a(0x24, 0x10); ym_a(0x25, 0); ym_a(0x27, 0x15);
    z80_out(0x08, 0);
    z80_ei();
    for (;;) {
        if (got) {
            got = 0;
            z80_in16(((uint16_t)cmd << 8) | 0x0B);
            ym_a(0x10, cmd);
        }
    }
}
```

The compiler sets the vector at 0066h for `on_nmi` and the vector at 0038h for `on_tick`. The form `interrupt on_tick critical` does not execute `EI` at the start, so the timer interrupt cannot interrupt itself.

## Build

```
uc80 --target=rom examples/neogeo_driver.c -o d.mac
um80 d.mac -o d.rel
ul80 --bin -p 0 -d F800 --rom-data --rom-size 8000 --ram-size 800 \
     -o d.bin -S d.sym d.rel $(uc80 --print-lib-dir)/libc_rom.lib \
     $(uc80 --print-lib-dir)/runtime.lib
```

Or run `uc80 --target=rom -v` and copy the `ul80` line that the compiler prints.

## Run

```
python3 bench/romrun d.bin --nmi-gate --ym2610 --in 0=0x21 \
    --nmi-every 160000 --sym d.sym --port-log ports.log --max-t 2000000
```

The port log shows `OUT 18` (close), the YM setup and `OUT 08` (open). Each NMI then gives an `IN 00`, an `OUT 0C` with the command, an `IN 210B` (bank select with B = 21h) and the YM writes. The timer interrupt comes about every 34500 T-states. The test `test_neogeo_driver_example` in `tests/test_rom_isr_proof.py` builds and runs this file.

## Assembly sound libraries

uc80 has no `__naked`, no `__asm ... __endasm` and no `__sdcccall`. To use a routine of an assembly library (for example nullsound), write a small thunk in a `.mac` file. Give the file to the compiler as a second input file (see "Your own interrupt handlers" in [rom.md](rom.md)). The thunk moves the arguments to the registers that the routine needs. The C file declares the thunk as `extern`.
