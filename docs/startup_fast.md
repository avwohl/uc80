# Fast BSS clear in the start-up code

The CP/M start-up code (`src/uc80/lib/crt0.mac`) sets the BSS to zero before it calls `main`. The old code used one `LDIR` for all bytes. `LDIR` needs 21 T-states for each byte. A program with a large BSS lost thousands of T-states at the start. A program with a small BSS lost about 60 T-states in tests of the size.

Family J step 1 in [codegen_gap.md](codegen_gap.md) lists the change.

## The new code

The BSS holds `__exit_hook` (2 bytes) in each program. So the size is at least 2 bytes.

The compiler embeds one of three files. The choice depends on the BSS of the program itself (the `ds` bytes of the COMMON segment in the generated text, without the library modules):

- **`crt0_small.mac`**, for less than 6000 bytes (`BIG_BSS` in `src/uc80/main.py`). The code loads `BC` with the size minus 1 as a link-time expression (`__bss_end-__bss_start-1`). It stores 0 in the first byte and runs `LDIR`. This works for each size, and it is shorter than the old code. The empty program needs 135 T-states. It needed 212 T-states. Its image is 32 bytes. It was 49 bytes.
- **`crt0.mac`**, for 6000 bytes or more. The code is the same as in the other file for a BSS of up to 256 bytes. For 257 bytes or more it jumps to `__bss_big`. This code is after the exit jump. It clears `size mod 16` bytes at the start, one byte at a time. Then it sets SP to `__bss_end` and clears 16 bytes in each pass with 8 `PUSH HL` (`HL` is 0). The cost is about 9 T-states for each byte. This file is also the source of `crt0.rel`, which a separate link uses.
- **`crt0_slow.mac`**, with `--no-fast-bss-clear`. The file has the old code.

The big path writes below SP. An interrupt or an NMI pushes its return address below SP too. Such a push can only touch bytes that the loop has not cleared yet. Nevertheless the big path disables interrupts. It reads the interrupt flag with `LD A,I` before, and it executes `EI` at the end only if the flag was set.

On an NMOS Z80, an interrupt can clear the flag P/V during `LD A,I`. The code then reads the flag as off and does not execute `EI`. Only a CP/M machine that starts a program with interrupts on has this risk.

The saved flags are on the stack at `(6)-2`. This place is above the BSS.

## Size

The push fill adds 56 bytes to a program with 6000 bytes of BSS or more. It saves about 13.9 T-states for each byte of BSS and costs about 300 T-states. At 6000 bytes it saves 83000 T-states (20 ms at 4 MHz). A lower limit costs 56 bytes in many programs for a small gain.

A program with less BSS is 17 bytes smaller than before. Its clear needs 21 T-states for each byte, as before. The BSS of the program itself decides (the `ds` bytes of the COMMON segment of the generated text). The compiler does not count the BSS of the library modules. A program can have 257 bytes or more in the link and still get the short file.

## The shared automatic storage is not cleared

The area `??AUTO` holds the automatic variables of the functions that share storage. A program writes an automatic variable before it reads the variable, so the area needs no clear. In `bsd-ppt` the area has 362 bytes of 447 bytes of BSS. The clear of these bytes needed 7600 T-states, 8% of the run.

The compiler now puts `??AUTO` in its own COMMON block `/AUTO/`. The block is the first of the module. Its end has the label `??AUTOEND`. The start-up code clears the bytes from `??AUTOEND` to `__bss_end`:

```
        LD      BC,__bss_end-??AUTOEND-1
        LD      HL,??AUTOEND
        LD      (HL),0
        LD      DE,??AUTOEND+1
        LDIR
```

The compiler changes the text of the start-up file when it embeds the file (`__bss_start` becomes `??AUTOEND`). The big path of `crt0.mac` uses the same start. The choice between the two files uses the bytes of the program without `??AUTO`.

This holds only for a whole-program CP/M build with the embedded start-up code. A build for a ROM, with `--startup-lib`, with `--no-fast-bss-clear`, or without whole-program mode keeps the old layout. A build with `--asm-syntax=sdas` has the new layout too. The translator of `sdas.py` writes `__bss_start+(??AUTOEND-??AUTO)` for `??AUTOEND`, because the area `/AUTO/` is the first of the BSS.

A program that reads an automatic variable before it writes the variable has undefined behavior. The old code gave 0 in most of these cases (the first use of the area). The new code gives the bytes that the system left in memory.

`bsd-ppt`: 100,546 T-states before, 92,692 after (SDCC: 93,101).

## Switches

- `--no-skip-auto-clear` clears `??AUTO` with the rest of the BSS (the layout before step 5).

- `--no-fast-bss-clear` embeds `src/uc80/lib/crt0_slow.mac`. The file has the old code.
- A build with `--asm-syntax=sdas` computes the size with instructions at run time. The assembler `sdas` cannot subtract two symbols from the linker. `src/uc80/sdas.py` handles this in `instruction`.
- The start-up code of a ROM (`crt0_rom.mac`) did not change.

## Tests

`tests/test_famj_startup.py` fills the BSS and 16 bytes on each side with a byte pattern before the clear. It runs BSS sizes of 6, 7, 205, 255, 256, 257, 258, 305, 4100, 4102 and 20005 bytes with each start-up file. It does this for the three files, with the interrupt flag set and cleared. It checks that every BSS byte is 0, that the bytes outside are not changed, and that the interrupt flag is the same after the clear.
