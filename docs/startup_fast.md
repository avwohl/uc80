# Fast BSS clear in the start-up code

The CP/M start-up code (`src/uc80/lib/crt0.mac`) sets the BSS to zero before it calls `main`. The old code used one `LDIR` for all bytes. `LDIR` needs 21 T-states for each byte. A program with a large BSS lost thousands of T-states at the start. A program with a small BSS lost about 60 T-states in tests of the size.

Family J step 1 in [codegen_gap.md](codegen_gap.md) lists the change.

## The new code

The BSS holds `__exit_hook` (2 bytes) in each program. So the size is at least 2 bytes.

- **Up to 256 bytes.** The code loads `BC` with the size minus 1 as a link-time expression (`__bss_end-__bss_start-1`). It stores 0 in the first byte and runs `LDIR`. The empty program needs 150 T-states. It needed 212 T-states.
- **257 bytes or more.** The code jumps to `__bss_big`. This code is after the exit jump. It clears `size mod 16` bytes at the start, one byte at a time. Then it sets SP to `__bss_end` and clears 16 bytes in each pass with 8 `PUSH HL` (`HL` is 0). The cost is about 9 T-states for each byte.

The big path writes below SP. An interrupt or an NMI pushes its return address below SP too. Such a push can only touch bytes that the loop has not cleared yet. Nevertheless the big path disables interrupts. It reads the interrupt flag with `LD A,I` before, and it executes `EI` at the end only if the flag was set.

The saved flags are on the stack at `(6)-2`. This place is above the BSS.

## Size

The big path adds about 60 bytes to each program. The small path is 4 bytes longer than the old code.

## Switches

- `--no-fast-bss-clear` embeds `src/uc80/lib/crt0_slow.mac`. The file has the old code.
- A build with `--asm-syntax=sdas` computes the size with instructions at run time. The assembler `sdas` cannot subtract two symbols from the linker. `src/uc80/sdas.py` handles this in `instruction`.
- The start-up code of a ROM (`crt0_rom.mac`) did not change.

## Tests

`tests/test_famj_startup.py` fills the BSS and 16 bytes on each side with a byte pattern before the clear. It runs BSS sizes of 2, 6, 205, 255, 256, 257, 258, 305, 4100, 4102 and 20005 bytes, with the interrupt flag set and cleared. It checks that every BSS byte is 0, that the bytes outside are not changed, and that the interrupt flag is the same after the clear.
