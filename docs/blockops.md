# Inline block operations

The compiler changes some calls of `memset` and `memcpy` into inline code. It also uses a shorter code sequence for small structure copies. The code is in `src/uc80/blockops.py`.

## What changes

A call changes when all of these are true:

- The name is `memset` or `memcpy`. The call has three arguments.
- The size is a constant from 1 to 65535.
- The program does not use the result of the call.
- No argument names a volatile object, a volatile pointer or a cast to a volatile type.
- The call is not in a function that has the name of the routine.

Other calls stay calls. `memmove` never changes, because its areas can overlap and `LDIR` copies forward. A call with a size that is not constant stays. A call with the result in use (`p = memset(a, 0, 8)`) stays.

## The code

A copy of N bytes puts the source in HL and the destination in DE. Then:

- N of 4 or less: N times `LDI`. Each `LDI` is 2 bytes and 16 T-states.
- N of more than 4: `LD BC,N` and `LDIR`.

A fill of N bytes with the value V:

- N of more than 4: store V in the first byte, set DE to the address plus 1, then `LD BC,N-1` and `LDIR` (`ld (hl),v / ld de,hl+1 / ldir`).
- N of 4 or less, constant address, constant V: word stores `LD (addr),HL` and one byte store with `LD (addr),A`.
- N of 4 or less, other cases: N times `LD (HL),V` with `INC HL` between them.

An argument that is a constant address (`buf`, `&g`, a string) goes straight into HL or DE with one `LD`. The code does not use `PUSH`, `POP` or `EX` for such an argument.

A fill or a copy of more than 4 bytes is never larger than the call. The inline fill is 13 bytes. The call is about 15 bytes. The library routine stays in the image only if a call still uses the routine. Therefore the compiler has no size limit for a constant size.

A copy of 3 or 4 bytes is 6 or 8 bytes of `LDI`. A `LD BC,N` and `LDIR` copy is 5 bytes. The `LDI` copy is larger by 1 or 3 bytes but faster.

A struct assignment of 3 or 4 bytes can make a program bigger (embench-slre: +6 bytes).

uc80 has one code selection. The options `--opt-code-size` and `--opt-code-speed` do not exist in uc80. The unroll limit of 4 is a compromise: an unrolled copy is at most 3 bytes longer than `LDIR`. `UC80_BLOCKOP_UNROLL=n` changes the limit, for a test. The smallest value is 1.

## Other places that use the code

- A structure assignment `a = b` and a structure initialisation from another object use the same copy. A destination with a constant address no longer needs `PUSH HL` and `POP HL`.
- A local array that has a zero initialiser in shared storage (`char a[50] = {0}`) was 50 stores of the form `xor a / ld (addr),a`. It is now a fill with `LDIR` when the size is more than 4.

## Interrupts and ROM programs

`LDIR` and `LDI` use BC, DE and HL, and no memory. An interrupt can happen between two bytes of an `LDIR`, and the interrupt function returns to the same place. The library routine has the same property. The inline code uses no memory, so the code needs no special case for `--isr-safe`. The longest time between two interrupt checks is one `LDIR` step of 21 T-states.

The register-locals pass treats `LDIR` and `LDI` as instructions that change BC, DE and HL. It wraps or renames a local in BC or DE as before.

## Switches

- `--no-blockops` keeps the library calls and the old copy code, including the PUSH and POP around a constant destination. The register-locals change (LDI does not read BC, runs of wraps join) has no switch. The join also changes programs that have no block operation.
- A program that defines a function named `memset` or `memcpy` (static or not) keeps all calls of that name.
- The order of evaluation of the arguments changes for an inline call. The code evaluates the source first for `memcpy`, then the destination. For `memset` the code evaluates the destination first, then the value. The C standard leaves the order unspecified. The library call evaluated the arguments from right to left.
- `UC80_NO_BLOCKOPS=1` does the same, for a script.
- `UC80_BLOCKOP_ONLY=f,g` limits the change of calls to the named functions, for a bisect.

## Constant addresses, volatile and the work areas

- A call of `memcpy` or of `memset` with a size that is not constant stays a call. A `memcpy` with a pointer to a volatile object stays a call. A `memset` to a volatile object or to a constant address is a device fill (see below).
- A structure copy to a volatile target uses `LDI` or `LDIR`. Both copy from the low address to the high address. A 16-bit member gets the low byte first, as the rule for volatile stores needs.
- `--isr-save-workareas`: an inline copy in an interrupt function reaches no work area of the runtime. The runtime `memcpy` and `memset` have no work area, so the plan is the same as with `--no-blockops`, and the plan never grows. The handler has no call of `_memcpy` or `_memset`.
- A fill of device memory never reads the destination. The destination is device memory in two cases. First, the argument, as the program wrote it (before the conversion to `void *`), points to a volatile object. Second, the argument is a constant address: an integer constant, or a cast of one, such as `(void *)0x4300`. The compiler then emits stores only. A fill of up to 6 bytes is a run of stores. A longer fill with a constant size is a loop of `LD (HL),A`, `INC HL` and `DJNZ`.
- A `memset` to device memory with a size that is not constant, or with a used result, calls `__memset_dev` of the library. This routine also only stores. A pointer that the program cast to `void *` shows no volatile object. Only a constant address makes such a fill a device fill. A `memcpy` has no change, because a copy reads the source and writes the destination. The test `tests/test_memset_device.py` runs these cases in the ROM runner.
- A fill of RAM keeps `LDIR`: it stores the first byte and copies the byte along. The library routine `memset` does the same. Take a pointer that shows no volatile object and no constant address, for example a `void *` parameter. A fill of a device through this pointer still uses `LDIR`. There the programmer must use a loop of volatile stores.
- The body of a naked function is assembly text only. It has no call of `memset` or `memcpy`, and the pass does not change it.
- `--no-whole-program`: each module makes its own inline code, and the result is the same as in the whole program.
- `tests/test_famh_rom_integration.py` runs these cases in the ROM runner, in the um80 flow and in the sdas flow.

## Tests

`tests/test_blockops.py` runs one program in eleven modes against the host compiler. The program has fills and copies of 1 to 400 bytes at global, pointer, local and string addresses. It also has side effects in the arguments, a used result and volatile objects. It has `memmove` with areas that overlap, and pointers that point to the same array. The shape tests check which calls stay: a variable size, `memmove`, a volatile object, a size of 0 and a used result.
