"""The ROM machine: memory map, port log, interrupts, stops, symbols.

    m = Machine()                       # Neo Geo sound CPU layout
    m.load_rom("snd.bin")
    m.add_nmi_at(100000)
    res = m.run(max_tstates=20_000_000)
    print(res.reason, res.t)

See docs/romrun.md for the full description.
"""

from __future__ import annotations

import heapq
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable, TextIO

from .devices import PortFeed
from .z80 import Z80

BIG = 1 << 62


@dataclass
class BankWindow:
    """A banked window: IN on `port` with bank n in the upper address byte
    maps image bytes n*size .. n*size+size-1 at `start`."""
    port: int
    start: int
    size: int


NEOGEO_BANKS = [BankWindow(0x08, 0xF000, 0x0800),
                BankWindow(0x09, 0xE000, 0x1000),
                BankWindow(0x0A, 0xC000, 0x2000),
                BankWindow(0x0B, 0x8000, 0x4000)]


@dataclass
class RunResult:
    reason: str           # halt, idle, budget, address:XXXX, port:XX, rom-write, exit
    t: int                # total T-states
    pc: int
    info: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"stop: {self.reason}  t={self.t}  pc={self.pc:04X}"


def load_symbols(path: str) -> dict[str, int]:
    """Read a ul80 .sym file (`ADDR NAME` per line)."""
    syms: dict[str, int] = {}
    with open(path, encoding="latin-1") as f:
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                try:
                    syms[parts[1].upper()] = int(parts[0], 16)
                except ValueError:
                    pass
    return syms


def parse_ihex(text: str) -> dict[int, int]:
    """Intel HEX text to {address: byte}.  Records 00, 01, 02, 04 are used."""
    out: dict[int, int] = {}
    base = 0
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        if line[0] != ":":
            raise ValueError(f"hex line {n}: no colon")
        raw = bytes.fromhex(line[1:])
        if len(raw) < 5 or raw[0] + 5 != len(raw):
            raise ValueError(f"hex line {n}: bad length")
        if sum(raw) & 255:
            raise ValueError(f"hex line {n}: bad checksum")
        count, addr, typ = raw[0], (raw[1] << 8) | raw[2], raw[3]
        data = raw[4:4 + count]
        if typ == 0:
            for i, b in enumerate(data):
                out[base + addr + i] = b
        elif typ == 1:
            break
        elif typ == 2:
            base = ((data[0] << 8) | data[1]) << 4
        elif typ == 4:
            base = ((data[0] << 8) | data[1]) << 16
    return out


class Machine:
    """A Z80 with a ROM, a RAM range, banked windows and logged ports.

    Defaults are the Neo Geo sound CPU: ROM 0000-7FFF (not writable),
    banked windows 8000-F7FF (not writable), RAM F800-FFFF.

    mode "rom" is the default.  mode "cpm" makes all of memory RAM, loads a
    .com file at 0100h and answers BDOS calls at 0005h (see load_com).
    """

    def __init__(self, *, rom_size: int = 0x8000,
                 ram: tuple[int, int] = (0xF800, 0xFFFF),
                 banks: Iterable[BankWindow] | None = NEOGEO_BANKS,
                 rom_write: str = "log", mode: str = "rom",
                 default_in: int = 0xFF, im: int = 1):
        self.cpu = Z80()
        self.mode = mode
        self.rom_size = rom_size
        self.ram = ram
        self.banks = list(banks or [])
        self.rom_write = rom_write            # log, fault or ignore
        self.image = bytearray()               # the whole ROM image
        self.feed = PortFeed(default_in)
        self.devices: list = []
        self.syms: dict[str, int] = {}
        # logs
        self.port_log: list[tuple[int, str, int, int]] = []   # t, IN/OUT, port, value
        self.keep_log = True
        self.log_file: TextIO | None = None
        self.log_events = False
        self.rom_writes: list[tuple[int, int, int, int]] = []  # t, addr, value, pc
        self.accepted: list[tuple[str, int]] = []              # (irq|nmi, t)
        self.bank_state: dict[int, int] = {}
        # events
        self._heap: list = []
        self._seq = 0
        self._nmi_sources = 0
        self._irq_sources = 0
        self._irq_levels: dict[object, bool] = {}
        self.magic_ports: set[int] = set()
        self.magic_value: int | None = None
        self.stop_on_halt = True
        # breakpoints and spans
        self._bps: dict[int, list[Callable]] = {}
        self.spans: dict[str, "Span"] = {}
        # cp/m
        self.console = bytearray()
        self.stdin = b""
        self._stdin_pos = 0
        self.echo_console = False

        cpu = self.cpu
        if mode == "cpm":
            self.rom_size = 0
            self.ram = (0, 0xFFFF)
        else:
            cpu.wok[:] = bytes(65536)
            lo, hi = self.ram
            cpu.wok[lo:hi + 1] = b"\x01" * (hi - lo + 1)
        cpu.set_hooks(inh=self._in, outh=self._out, wbad=self._wbad,
                      fire=self._fire, wake=self._wake, bphit=self._bphit,
                      onacc=self._onacc)
        cpu.set(SP=0xFFFF if mode == "rom" else 0, IM=0)
        self._started = False

    # --- loading ------------------------------------------------------------

    def load_rom(self, data: bytes | str, addr: int = 0) -> None:
        """Load a raw image (bytes or a path) at `addr` of the ROM image."""
        if isinstance(data, str):
            with open(data, "rb") as f:
                data = f.read()
        need = addr + len(data)
        if len(self.image) < need:
            self.image.extend(b"\xff" * (need - len(self.image)))
        self.image[addr:addr + len(data)] = data
        self._map_rom()

    def load_hex(self, text_or_path: str) -> None:
        """Load Intel HEX text, or a path to a hex file."""
        text = text_or_path
        if "\n" not in text and ":" not in text[:1] and os.path.exists(text):
            with open(text, encoding="latin-1") as f:
                text = f.read()
        recs = parse_ihex(text)
        if not recs:
            return
        top = max(recs) + 1
        if len(self.image) < top:
            self.image.extend(b"\xff" * (top - len(self.image)))
        for a, b in recs.items():
            self.image[a] = b
        self._map_rom()

    def _map_rom(self) -> None:
        """Copy the fixed ROM and the initial bank windows into memory."""
        mem = self.cpu.mem
        n = min(self.rom_size, len(self.image))
        mem[0:n] = self.image[0:n]
        for w in self.banks:
            if w.port not in self.bank_state:
                self.bank_state[w.port] = w.start // w.size
            self._switch(w, self.bank_state[w.port])

    def _switch(self, w: BankWindow, bank: int) -> None:
        self.bank_state[w.port] = bank
        off = bank * w.size
        chunk = bytes(self.image[off:off + w.size])
        if len(chunk) < w.size:
            chunk += b"\xff" * (w.size - len(chunk))
        self.cpu.mem[w.start:w.start + w.size] = chunk

    def load_com(self, data: bytes | str, stdin: bytes = b"") -> None:
        """CP/M mode: load a .com at 0100h, set up page zero and BDOS."""
        if isinstance(data, str):
            with open(data, "rb") as f:
                data = f.read()
        cpu = self.cpu
        cpu.mem[0x100:0x100 + len(data)] = data
        cpu.mem[0] = 0xC3              # JP 0 (warm boot, caught by a stop)
        cpu.mem[5] = 0xC9              # RET (BDOS, caught by a hook)
        cpu.mem[6:8] = (0x00, 0xFF)    # BDOS base for programs that read it
        cpu.mem[0xFEFE:0xFF00] = b"\x00\x00"
        cpu.set(PC=0x100, SP=0xFEFE)
        self.stdin = stdin
        self._stdin_pos = 0
        self._add_bp(0, lambda m, pc: "exit")
        self._add_bp(5, self._bdos)

    def load_symbols(self, path: str) -> None:
        self.syms.update(load_symbols(path))

    def addr(self, spec: int | str) -> int:
        """An address given as a number, a hex string or a symbol."""
        if isinstance(spec, int):
            return spec
        s = spec.strip()
        if s.upper() in self.syms:
            return self.syms[s.upper()]
        if ("_" + s).upper() in self.syms:
            return self.syms[("_" + s).upper()]
        try:
            return int(s, 16) if not s.lower().startswith("0x") else int(s, 16)
        except ValueError:
            raise KeyError(f"unknown symbol or address: {spec}") from None

    # --- ports --------------------------------------------------------------

    def add_device(self, dev) -> None:
        self.devices.append(dev)

    def _emit(self, t: int, kind: str, port: int, value: int) -> None:
        if self.keep_log:
            self.port_log.append((t, kind, port, value))
        if self.log_file is not None:
            self.log_file.write(f"{t} {kind} {port:04X} {value:02X}\n")

    def _in(self, port: int, t: int) -> int:
        v = self.feed.read(port, t)
        if v is None:
            for d in self.devices:
                if d.owns(port):
                    v = d.read(port, t)
                    break
        if v is None:
            v = self.feed.default
        for w in self.banks:
            if (port & 0xFF) == w.port:
                self._switch(w, port >> 8)
        self._emit(t, "IN", port, v)
        return v

    def _out(self, port: int, value: int, t: int) -> None:
        self._emit(t, "OUT", port, value)
        for d in self.devices:
            if d.owns(port):
                d.write(port, value, t)
        if (port & 0xFF) in self.magic_ports:
            self.magic_value = value
            self.cpu.poke(stop=f"port:{port & 0xFF:02X}")

    def _wbad(self, addr: int, value: int) -> None:
        s = self.cpu.get()
        self.rom_writes.append((s["T"], addr, value, s["IPC"]))
        if self.log_file is not None:
            self.log_file.write(f"{s['T']} ROMWRITE {addr:04X} {value:02X} "
                                f"pc={s['IPC']:04X}\n")
        if self.rom_write == "fault":
            self.cpu.poke(stop="rom-write")

    # --- events -------------------------------------------------------------

    def at(self, t: int, fn: Callable, kind: str = "call") -> None:
        """Run fn(machine, t) when the CPU clock reaches t (at the next
        instruction boundary); `t` passed to fn is the scheduled time."""
        self._seq += 1
        heapq.heappush(self._heap, (int(t), self._seq, kind, fn, None))
        self.cpu.poke()

    def add_nmi_at(self, t: int) -> None:
        self._seq += 1
        self._nmi_sources += 1
        heapq.heappush(self._heap, (int(t), self._seq, "nmi", None, None))
        self.cpu.poke()

    def add_irq_at(self, t: int) -> None:
        self._seq += 1
        self._irq_sources += 1
        heapq.heappush(self._heap, (int(t), self._seq, "irq", None, None))
        self.cpu.poke()

    def add_nmi_every(self, period: int, start: int | None = None,
                      count: int | None = None) -> None:
        self._periodic("nmi", period, start, count)

    def add_irq_every(self, period: int, start: int | None = None,
                      count: int | None = None) -> None:
        self._periodic("irq", period, start, count)

    def _periodic(self, kind, period, start, count) -> None:
        if period <= 0:
            raise ValueError("period must be positive")
        if kind == "nmi":
            self._nmi_sources += 1
        else:
            self._irq_sources += 1
        self._seq += 1
        heapq.heappush(self._heap, (int(period if start is None else start),
                                    self._seq, kind, None, (period, count)))
        self.cpu.poke()

    def set_irq_level(self, level: bool, source: object) -> None:
        self._irq_levels[source] = level
        self.cpu.poke(irq_level=any(self._irq_levels.values()))

    def _fire(self, t: int):
        h = self._heap
        while h and h[0][0] <= t:
            when, _, kind, fn, per = heapq.heappop(h)
            if self.log_events and self.log_file is not None:
                self.log_file.write(f"{when} EVENT {kind}\n")
            if kind == "nmi":
                self.cpu.poke(nmi=True)
            elif kind == "irq":
                self.cpu.poke(irq=True)
            else:
                fn(self, when)
            if per is not None:
                period, count = per
                if count is not None:
                    count -= 1
                if count is None or count > 0:
                    self._seq += 1
                    heapq.heappush(h, (when + period, self._seq, kind, fn,
                                       (period, count)))
                elif kind == "nmi":
                    self._nmi_sources -= 1
                else:
                    self._irq_sources -= 1
            elif kind == "nmi":
                self._nmi_sources -= 1
            elif kind == "irq":
                self._irq_sources -= 1
        return h[0][0] if h else None

    def _wake(self, iff1: int) -> bool:
        """Can a future event end a HALT?"""
        if not self.stop_on_halt:
            return True
        if self._nmi_sources > 0:
            return True
        if iff1 and (self._irq_sources > 0 or any(
                getattr(d, "can_wake", lambda: False)() for d in self.devices)):
            return True
        return bool(iff1) and any(self._irq_levels.values())

    def _onacc(self, kind: str, t: int) -> None:
        self.accepted.append((kind, t))
        if self.log_file is not None and self.log_events:
            self.log_file.write(f"{t} ACCEPT {kind}\n")

    # --- breakpoints, spans -------------------------------------------------

    def _add_bp(self, addr: int, fn: Callable) -> None:
        self._bps.setdefault(addr, []).append(fn)
        self.cpu.bp[addr] = 1

    def _bphit(self, pc: int):
        for fn in self._bps.get(pc, ()):
            r = fn(self, pc)
            if r:
                return r
        return None

    def stop_at(self, addr: int | str) -> int:
        a = self.addr(addr)
        self._add_bp(a, lambda m, pc: f"address:{pc:04X}")
        return a

    def measure(self, name: str, start: int | str, end: int | str | None = None
                ) -> "Span":
        """Count T-states from each arrival at `start` to the next arrival at
        `end`.  With no `end`, from the arrival at `start` (a function) to
        the matching return."""
        sp = Span(name, self.addr(start), None if end is None else self.addr(end))
        self.spans[name] = sp
        self._add_bp(sp.start, lambda m, pc, sp=sp: sp.on_start(m))
        if sp.end is not None:
            self._add_bp(sp.end, lambda m, pc, sp=sp: sp.on_end(m))
        return sp

    # --- cp/m ---------------------------------------------------------------

    def _bdos(self, m, pc):
        cpu = self.cpu
        s = cpu.get()
        fn = s["C"]
        de = (s["D"] << 8) | s["E"]
        mem = cpu.mem
        a = 0
        hl = None
        if fn == 0:
            return "exit"
        if fn == 1:
            # like z88dk-ticks: no echo, 0FFh at end of input
            a = self._getc() if self._stdin_pos < len(self.stdin) else 0xFF
        elif fn == 2:
            if s["E"] != 12:
                self._putc(s["E"])
        elif fn == 6:
            if s["E"] == 0xFF:
                # like z88dk-ticks: 0 at end of input, LF and CR swapped
                a = self._getc() if self._stdin_pos < len(self.stdin) else 0
                a = 13 if a == 10 else 10 if a == 13 else a
            else:
                self._putc(s["E"])
        elif fn == 9:
            i = de
            while mem[i] != 0x24:
                self._putc(mem[i])
                i = (i + 1) & 0xFFFF
        elif fn == 10:
            size = mem[de]
            line = bytearray()
            while self._stdin_pos < len(self.stdin):
                ch = self.stdin[self._stdin_pos]
                self._stdin_pos += 1
                if ch == 10:
                    break
                if ch != 13 and len(line) < size:
                    line.append(ch)
            mem[(de + 1) & 0xFFFF] = len(line)
            mem[(de + 2) & 0xFFFF:(de + 2 + len(line)) & 0xFFFF or None] = line
        elif fn == 11:
            a = 0xFF if self._stdin_pos < len(self.stdin) else 0
        elif fn == 12:
            hl = 0x0022
            a = 0x22
        elif fn in (0x0E, 0x19):
            a = 0 if s["E"] == 0 else 0xFF
        # return to the caller as the RET at 0005h would (10 T-states)
        sp = s["SP"]
        ret = mem[sp] | (mem[(sp + 1) & 0xFFFF] << 8)
        upd = dict(PC=ret, SP=(sp + 2) & 0xFFFF, A=a, T=s["T"] + 10)
        if hl is not None:
            upd["H"], upd["L"] = hl >> 8, hl & 255
        cpu.set(**upd)
        return None

    def _putc(self, ch: int) -> None:
        self.console.append(ch & 0xFF)
        if self.echo_console:
            sys.stdout.write(chr(ch & 0xFF))

    def _getc(self) -> int:
        if self._stdin_pos < len(self.stdin):
            ch = self.stdin[self._stdin_pos]
            self._stdin_pos += 1
            return ch
        return 0x1A

    # --- running ------------------------------------------------------------

    def reset(self) -> None:
        self.cpu.set(PC=0, IFF1=0, IFF2=0, IM=0, I=0, R=0, HALTED=False,
                     EIDELAY=False)

    @property
    def t(self) -> int:
        return self.cpu.get()["T"]

    def run(self, max_tstates: int | None = None, *, until_t: int | None = None
            ) -> RunResult:
        """Run until a stop condition.  `max_tstates` counts from now;
        `until_t` is an absolute T-state count."""
        cpu = self.cpu
        s = cpu.get()
        if until_t is None and max_tstates is not None:
            until_t = s["T"] + max_tstates
        cpu.poke(budget=BIG if until_t is None else until_t)
        skip = self._started and bool(cpu.bp[s["PC"]])
        self._started = True
        reason = cpu.run(skip)
        s = cpu.get()
        res = RunResult(reason, s["T"], s["PC"])
        if self.magic_value is not None and reason.startswith("port:"):
            res.info["value"] = self.magic_value
        if reason == "exit" or reason.startswith("address:"):
            pass
        return res

    def regs(self) -> dict:
        return self.cpu.get()

    def output_text(self) -> str:
        return self.console.decode("latin-1")


@dataclass
class Span:
    name: str
    start: int
    end: int | None
    count: int = 0
    total: int = 0
    min: int = 0
    max: int = 0
    t0: int | None = None
    sp0: int | None = None
    ret: int | None = None
    rets: set = field(default_factory=set)

    def _add(self, dt: int) -> None:
        self.count += 1
        self.total += dt
        self.min = dt if self.count == 1 else min(self.min, dt)
        self.max = max(self.max, dt)

    def on_start(self, m: Machine):
        s = m.cpu.get()
        if self.end is not None:
            if self.t0 is None:
                self.t0 = s["T"]
            return None
        # function mode: remember the return address and stack level; a
        # recursive entry is counted once (the outermost call)
        if self.t0 is None:
            sp = s["SP"]
            self.t0 = s["T"]
            self.sp0 = sp
            self.ret = m.cpu.mem[sp] | (m.cpu.mem[(sp + 1) & 0xFFFF] << 8)
            if self.ret not in self.rets:
                self.rets.add(self.ret)
                m._add_bp(self.ret, lambda mm, pc, sp=self: sp.on_ret(mm))
        return None

    def on_ret(self, m: Machine):
        s = m.cpu.get()
        if self.t0 is not None and s["SP"] == ((self.sp0 + 2) & 0xFFFF) \
                and s["PC"] == self.ret:
            self._add(s["T"] - self.t0)
            self.t0 = None
        return None

    def on_end(self, m: Machine):
        if self.t0 is not None:
            self._add(m.cpu.get()["T"] - self.t0)
            self.t0 = None
        return None

    def summary(self) -> str:
        if not self.count:
            return f"{self.name}: never completed"
        return (f"{self.name}: count={self.count} total={self.total} "
                f"min={self.min} max={self.max} "
                f"mean={self.total / self.count:.1f}")
