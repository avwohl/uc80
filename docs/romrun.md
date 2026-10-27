# romrun: the Z80 ROM test runner

`bench/romrun/` holds a test runner for raw Z80 ROM images. The runner
starts the CPU at address 0 and runs the code until a stop condition.
The runner logs every port IN and OUT with the T-state count. The runner
injects interrupts and NMIs. The runner is the test harness of Phase 1 in
[rom_target.md](rom_target.md).

The runner works for any Z80 ROM. The default memory layout is the Neo Geo
sound CPU.

## Quick start

Run a ROM image and log the ports:

```
python3 bench/romrun snd.bin --port-log ports.log --max-t 20000000
```

Run the same ROM with a periodic NMI and a YM2610 timer model:

```
python3 bench/romrun snd.bin --nmi-every 160000 --ym2610 --in 0=0x21 \
    --port-log ports.log --sym snd.sym --measure nmi_handler
```

Use the Python API in a test:

```python
import sys; sys.path.insert(0, "bench")
from romrun import Machine, YM2610

m = Machine()                       # Neo Geo layout
m.load_rom("snd.bin")
YM2610(m)                           # timers, busy bit, IRQ line
m.feed.queue(0x00, [0x21, 0x22])    # command bytes for the NMI handler
m.add_nmi_every(160_000)
res = m.run(max_tstates=20_000_000)
print(res.reason, res.t)
```

The directory `bench/romrun/examples/` has `demo.mac`, a small sound ROM
with vectors, an NMI command reader and a YM2610 timer interrupt. Build
the ROM with `um80 demo.mac` and `ul80 -p 0 -s -o demo.bin demo.rel`.

## Choice of the Z80 core

The evaluation covered three cores. The criteria were correctness first and
speed second.

- cpmemu (`qkz80`, C++). Rejected. The core counts about 5 T-states for
  each instruction (`qkz80.cc`, "approximate cycle count"). The port
  hooks take an 8-bit port number, so the bank byte in the upper address
  byte is lost. Block I/O is not implemented.
- z88dk-ticks. Rejected as the runner core. Ticks counts T-states
  exactly and has `-int` (periodic interrupt) and `-rom`. Ticks has no
  port log, no scripted port reads and no NMI. Ticks also has a few
  errors (see Validation). Ticks stays in use as the reference for
  T-state counts.
- A pure Python core, written for this runner. Chosen. The core counts
  T-states per the Zilog manual. The core logs the full 16-bit port
  address. The core models IFF1, IFF2, the EI delay, IM 0/1/2, the NMI
  and RETN.

The Python core is fast enough. `bench/romrun/z80.py` generates the
source of the core. Each register is a closure cell, so an instruction
needs no attribute lookup. The speed is 7 to 18 million T-states each
second on the benchmark programs. A kernel of 10 to 20 million T-states
runs in 1 to 2 seconds.

## Memory layout

Defaults (the Neo Geo sound CPU):

- 0000-7FFF: ROM, not writable.
- 8000-F7FF: banked windows, not writable.
- F800-FFFF: RAM, writable.

Options: `rom_size`, `ram=(lo, hi)`, `banks=[BankWindow(...)]`.
On the command line: `--rom-size`, `--ram LO-HI`, `--bank PORT:START:SIZE`
(repeatable) and `--no-banks`.

The ROM image can be larger than 64 KiB. The fixed part is the first
`rom_size` bytes. A banked window shows a part of the image.

A read of a port selects a bank. The port number is the low byte of the
address. The bank number is the upper byte of the address. The default
windows are the Neo Geo set:

- port 08h: 2 KiB at F000-F7FF
- port 09h: 4 KiB at E000-EFFF
- port 0Ah: 8 KiB at C000-DFFF
- port 0Bh: 16 KiB at 8000-BFFF

Bank n of a window shows the image bytes from n times the window size.
Bytes past the end of the image read as FFh. Before the first bank
read, each window shows the part of the image at the same address.

On the Neo Geo, port 08h is also the NMI enable port when the CPU
writes the port (see Neo Geo NMI gate).

Raw images and Intel HEX files load at address 0 (`load_rom`,
`load_hex`, or `--hex`). The HEX loader uses record types 00, 01, 02 and
04.

## RAM start state and SP

Real RAM holds random data at power-up. A ROM that skips the BSS clear,
the copy of the initial data or the set-up of SP must fail in the
runner. Two options make the failure show:

- `ram_fill` (`--ram-fill`): the start value of the RAM range. The value
  is a byte, or `random` (`--ram-seed N` selects the sequence). The
  default is zero. The ROM area is not changed.
- `sp` (`--sp`): the start value of SP. The default is FFFFh. The value
  `poison` is 4000h, an address in ROM. A push before the ROM sets SP
  then is a ROM write, which the `rom_write` policy logs or faults.

## Inspection after a run

- `m.peek8(x)`, `m.peek16(x)` (little endian) and `m.peek(x, n)` read
  memory. `x` is an address, a hex string or a symbol from the `.sym`
  file. `m.dump_ram()` returns the RAM range.
- `--dump SYM[:N]` (repeatable) prints N bytes (default 1) as hex after
  the run. `--dump-ram FILE` writes the RAM range to a file. The
  `--json` result has a `dump` object.
- A ROM kernel can report a result in three ways: a write to a magic
  port (`--magic-port`, stops the run, value in `info["value"]`), a
  write to any port (the port log has the value), or a store in RAM that
  the test reads with `peek`.

## Stack depth and RAM use

`Machine(track=True)` (`--track`) records the lowest SP at a stack write
and each RAM address that the ROM writes. The cost is small (about 5%).

- `m.min_sp`: the lowest SP after a push, CALL, RST, EX (SP),HL or an
  interrupt, or `None` if there was none.
- `m.stack_depth(top=None)`: `top` (default the start SP) minus
  `min_sp`. Interrupt pushes count.
- `m.written()` and `m.written_ranges()`: the written addresses, and
  the same as `(first, last)` ranges. A write to ROM does not count.
- `RunResult.info` has `min_sp` and `ram_written` (a byte count). The
  command line prints both and `--json` has both.

A test can compare `written_ranges()` with the RAM budget, or check
that `min_sp` stays above the end of the data.

## Neo Geo NMI gate

On the Neo Geo sound CPU, a write to port 08h enables the NMI and a
write to port 18h disables the NMI. A read of port 08h selects a bank
(see Memory layout), so port 08h has two uses: a read selects a bank
and a write enables the NMI.

`Machine(nmi_gate=True)` (`--nmi-gate`) models the gate. The options
`nmi_enable_port` (08h) and `nmi_disable_port` (18h) set the ports. The
runner matches the low byte of the port address.

- The gate starts closed at reset.
- While the gate stays closed, an NMI request waits. The runner holds
  one request.
- An OUT to the enable port opens the gate and delivers the held NMI at
  the next instruction boundary.
- An OUT to the disable port closes the gate.
- A held NMI does not end a HALT, so a ROM that halts with a closed
  gate stops with the reason `halt`.

The default is off, which is a generic Z80: each NMI goes to 0066h at
once. Use `nmi_gate=True` for a Neo Geo ROM that depends on the gate.

## Writes to ROM

A write to an address that is not in the RAM range does not change
memory. The runner records the write (T-state, address, value, PC of the
instruction). The policy `rom_write` selects the next step:

- `log` (default): record the write and go on.
- `fault`: record the write and stop with the reason `rom-write`.
- `ignore`: drop the write. The runner still records the write.

The command line prints the exit status 2 after a `fault` stop.

## Stop conditions

`Machine.run(max_tstates=None)` returns a `RunResult` with the fields
`reason`, `t` (the total T-states), `pc` and `info`. The reasons are:

- `halt`: HALT with IFF1 clear, and no NMI source can wake the CPU.
  `pc` is the address after the HALT.
- `idle`: HALT with interrupts enabled, and no event can wake the CPU.
- `budget`: the T-state limit.
- `address:XXXX`: `stop_at(addr_or_symbol)`, `--stop-at`. The CPU stops
  before the instruction at the address.
- `port:XX`: a write to a magic port (`magic_ports`, `--magic-port`).
  The value is in `info["value"]`.
- `rom-write`: see above.
- `exit`: CP/M mode only (BDOS function 0, or a jump to 0000h).

A ROM that loops for ever never stops without `max_tstates` (`--max-t`).
A second call of `run` continues from the stop. `stop_on_halt = False`
makes HALT wait for the next event, and `max_tstates` must be set.

## Port log

Every IN and OUT goes in the log, in order. The line format is:

```
<T-state> <IN|OUT> <port, 4 hex digits> <value, 2 hex digits>
```

The port is the full 16-bit address. The upper byte is A for `IN A,(n)`
and `OUT (n),A`. The upper byte is B for `IN r,(C)`, `OUT (C),r` and the
block I/O instructions. The bank select of the Neo Geo is a
read, so the log has both reads and writes.

The T-state stamp is the T-state where the I/O machine cycle starts. The
offset from the start of the instruction is 7 for `IN A,(n)` and
`OUT (n),A`, 8 for `IN r,(C)` and `OUT (C),r`, and 9 for the block I/O
instructions.

`Machine.port_log` holds the tuples `(t, kind, port, value)`. The option
`--port-log FILE` (or `m.log_file`) writes the text form. With
`--log-events` the file also has the lines `<t> ACCEPT irq|nmi`,
`<t> EVENT <kind>` and `<t> ROMWRITE <addr> <value> pc=<pc>`.

## Port reads

Resolution order of a read:

1. `m.feed` (class `PortFeed`), full 16-bit match first, then low-byte
   match.
2. A device (the YM2610 model).
3. `default_in` (default FFh).

`PortFeed` providers:

- `const(port, v)`
- `queue(port, [v, ...], then=None)`: one value per read.
- `steps(port, [(t, v), ...])`: the value of the last pair with `t` at or
  before the read time.
- `busy(port, until_t, bit=0x80, base=0)`: a busy flag that clears at
  `until_t`.
- `func(port, fn)`: `fn(port16, t)` returns the byte.

A port number above FFh matches the full 16-bit address. A smaller number
matches the low byte.

Command line form of `--in`: `PORT=V`, `PORT=seq:V,V,...`,
`PORT=steps:T:V,T:V,...`, `PORT=busy:T[:MASK]`.

## Interrupts

Maskable interrupt (INT):

- `add_irq_at(t, vec=None)`, `add_irq_every(period, start=None, count=None, vec=None)`. `vec` is the byte of the data bus (`--irq-vec`): the low byte of the vector in IM 2, the opcode in IM 0. The CPU does not force bit 0 of the byte to 0. Without `vec`, the last value stays (FFh at reset).
- `set_irq_level(level, source, vec=None)` and `YM2610(..., irq_vec=None)` give the same data bus byte to a level-triggered source. The `--irq-vec` option sets `irq_vec` of the YM2610 model.
- The request stays until the CPU takes the request. The CPU takes the
  request when IFF1 is set and one instruction has run after EI.
- IM 0 uses the RST opcode in `IVEC` (default FFh, RST 38h). IM 1 jumps
  to 0038h and takes 13 T-states. IM 2 reads the vector at `(I << 8) | IVEC` (the `vec` byte)
  and takes 19 T-states.
- A device can hold the line high with `set_irq_level`. The line stays
  high until the device lowers the line.

NMI:

- `add_nmi_at(t)`, `add_nmi_every(period, start=None, count=None)`.
- The CPU takes the NMI after the current instruction, also with
  interrupts disabled. The CPU copies IFF1 to IFF2, clears IFF1, pushes
  PC and jumps to 0066h. The NMI takes 11 T-states.
- RETN and RETI set IFF1 from IFF2.

The CPU takes an event for T-state `t` at the first instruction boundary
at or after `t`. `Machine.accepted` lists `(kind, t)` for each interrupt the
CPU took.

HALT idles in steps of 4 T-states. The runner jumps the clock to the next
event. An interrupt that ends a HALT pushes the address after the HALT.

## YM2610 timer model

`YM2610(machine, base=0x04, chip_clock_per_tstate=2.0, busy_tstates=48)`
models what a sound driver polls and waits for. The model makes no sound.

- Ports `base` to `base+3`: address and data of register set A and B.
- Status read at `base`: bit 0 timer A flag, bit 1 timer B flag, bit 7
  busy.
- Timer A: registers 24h and 25h. Timer A counts `(1024 - NA) * 72` chip
  clocks. Timer B: register 26h. Timer B counts `(256 - NB) * 1152` chip
  clocks.
- Register 27h: bits 0 and 1 load the timers, bits 2 and 3 enable the
  flags, bits 4 and 5 reset the flags.
- A timer reloads when the timer expires and the load bit stays set. The
  flag sets at expiry when the enable bit is set. The INT line is high
  while a flag is set. A reset bit lowers the flag and the line.
- The Neo Geo YM2610 clock is 8 MHz and the Z80 clock is 4 MHz, so the
  default is 2 chip clocks for each T-state.

The busy time is an estimate. Change `busy_tstates` when you know the
hardware figure. The ADPCM, SSG and FM sections are not modelled. The
register writes are available in `YM2610.log`.

## Cycle spans

`m.measure(name, start, end=None)` measures T-states. A symbol name
(`_` prefix optional), a hex string or a number gives each address. The
`.sym` file of ul80 gives the symbols (`m.load_symbols`, `--sym`).

- With `end`: the T-states from each arrival at `start` to the next
  arrival at `end`.
- Without `end`: the T-states of a call to the function at `start`, from
  the entry to the return that belongs to the call. A recursive call counts once, at the
  outermost level.

Each span reports `count`, `total`, `min` and `max`. Command line form:
`--measure A:B` or `--measure FUNC`. Interrupts that run inside a
span count in the span.

## CP/M mode

`Machine(mode="cpm")` and `load_com(path)` (or `--cpm`) load a `.com` at
0100h. All memory is RAM. The BDOS entry at 0005h is a hook. The hook
supports function 0 (exit), 1, 2, 6, 9, 10, 11, 12, 14 and 25. A jump
to 0000h also stops the run. The console output is in
`Machine.output_text()`.

The BDOS behaviour follows z88dk-ticks, so the output and the T-state
count agree with the benchmark harness. Function 1 does not echo, and
the function returns FFh at the end of input. The hook adds the 10
T-states of the RET at 0005h, as ticks does.

## Validation

The core has these checks.

1. Per-instruction timing sweep against z88dk-ticks: the base, CB,
   ED, DD, FD and DDCB/FDCB opcodes, each after one of two flag preambles. The
   file `bench/romrun/golden/ticks_sweep.txt` records the results: the
   instruction, the preamble, the T-states of ticks and the T-states of
   the core. The sweep leaves out RET, RST, JP (HL), HALT, RETN, RETI and
   the block instructions that repeat. `python3 -m romrun.sweep --check`
   (from `bench/`) runs the core on each case and compares the result
   with the file. It needs no ticks. `--write FILE` makes the file again
   and needs ticks. The cases where ticks and the core differ are all
   errors of ticks (see the `ticks differs` lines of `--check`): ADC A,H
   (8Ch) takes 7 T-states in ticks and 4 on the Z80, and ED 8Bh.
   `tests/test_romrun.py` checks the core against the file.
2. Random instruction streams (`python3 -m romrun.difftest`, from
   `bench/`). The registers before each instruction, the final memory
   and the final T-state count must agree with ticks. 40 of 40 streams
   of 200 instructions agree. The generator leaves out the
   instructions where ticks differs from the Zilog manual:
   ADC A,H, LD IXH,IXL, the Z flag of a CB shift of (HL) with a zero
   result, and BIT n,(HL) (the X and Y flags come from the hidden WZ
   register, which the core does not model).
3. Benchmark programs in CP/M mode against ticks. The output and the
   T-state count are the same on 14 programs. Two programs differ by
   exactly 3 T-states for each ADC A,H they run (the error of ticks).
   Examples: davidly-sieve 53 856 222 T-states, coremark 42 437 558,
   embench-nettle-sha256 6 696 872, binary-trees 195 885 861.
4. `zexdoc` under the CP/M mode. Two tests finished and passed:
   `<adc,sbc> hl,<bc,de,hl,sp>` and `aluop a,nn`. The full run is about
   46 billion T-states, which takes more than an hour on the core. The
   full run of `zexdoc` and `zexall` has not finished. The first run of
   all of the tests is an open item.
5. `tests/test_romrun.py`: tests for the T-state table, port log,
   interrupt model, ROM write faults, banks, feeds, YM2610, HEX loader,
   spans and the command line.

### Known limits

- No MEMPTR (WZ) register. The X and Y flags of BIT n,(HL) come from H.
- No wait states, no refresh timing, no bus contention.
- The R register counts one for each opcode fetch. LD A,R and LD R,A
  work. The R count is not exact for interrupts that end a HALT.
- A stop `halt` leaves PC at the address after the HALT. A real Z80
  keeps PC on the HALT.
- The LD A,I and LD A,R flag quirk with an interrupt that arrives in the
  instruction is not modelled.
- IM 0 supports only RST opcodes on the data bus.
- The memory is 64 KiB. Writes to the banked windows count as ROM writes.
