"""Port devices for the runner: a scripted read feed and a YM2610 model."""

from __future__ import annotations

from typing import Callable, Iterable


class PortFeed:
    """Values that a ROM reads from input ports.

    A provider is chosen by port.  A port number up to 0FFh matches the low
    byte of the port address (the usual Z80 decode).  A larger number matches
    the full 16-bit address, so `0x0B08` answers only a read of port 08h with
    0Bh in the upper byte.  The full match wins over the low-byte match.

    Providers:
      const(port, v)                  always v
      queue(port, [v, ...], then=None)  one value per read; `then` after the
                                      list is used up (None: the last value)
      steps(port, [(t, v), ...])      v of the last pair with t <= read time
      busy(port, until_t, bit=0x80, base=0)   base|bit before until_t,
                                      base from then on (a busy flag)
      func(port, fn)                  fn(port16, t) -> byte
    """

    def __init__(self, default: int = 0xFF):
        self.default = default
        self._low: dict[int, Callable[[int, int], int]] = {}
        self._full: dict[int, Callable[[int, int], int]] = {}

    def _put(self, port: int, fn) -> None:
        (self._full if port > 0xFF else self._low)[port] = fn

    def func(self, port: int, fn: Callable[[int, int], int]) -> None:
        self._put(port, fn)

    def const(self, port: int, value: int) -> None:
        self._put(port, lambda p, t: value & 0xFF)

    def queue(self, port: int, values: Iterable[int], then: int | None = None):
        items = [v & 0xFF for v in values]
        state = {"i": 0}

        def read(p, t):
            i = state["i"]
            if i < len(items):
                state["i"] = i + 1
                return items[i]
            if then is not None:
                return then & 0xFF
            return items[-1] if items else self.default
        self._put(port, read)

    def steps(self, port: int, pairs: Iterable[tuple[int, int]]) -> None:
        table = sorted((int(t), v & 0xFF) for t, v in pairs)

        def read(p, t):
            val = self.default
            for t0, v in table:
                if t0 <= t:
                    val = v
                else:
                    break
            return val
        self._put(port, read)

    def busy(self, port: int, until_t: int, bit: int = 0x80, base: int = 0):
        self._put(port, lambda p, t: (base | bit) & 0xFF if t < until_t
                  else base & 0xFF)

    def read(self, port: int, t: int):
        """Return the value, or None when no provider covers the port."""
        fn = self._full.get(port)
        if fn is None:
            fn = self._low.get(port & 0xFF)
        return None if fn is None else fn(port, t)


class YM2610:
    """The timer and busy part of a YM2610 (OPNB) at ports base..base+3.

    The model has what a sound driver polls and waits for: the address and
    data ports of register set A and B, the busy bit, and timers A and B
    with their status flags and the IRQ line.  It makes no sound.

      port base+0 write  address latch, set A       read  status (see below)
      port base+1 write  data, set A                read  0
      port base+2 write  address latch, set B       read  ADPCM status (0)
      port base+3 write  data, set B                read  0

    Status read: bit 0 = timer A flag, bit 1 = timer B flag, bit 7 = busy.

    Timers (registers 24h to 27h of set A):
      24h  timer A high 8 bits    25h  timer A low 2 bits
      26h  timer B                27h  bit 0/1 load A/B, bit 2/3 flag enable
                                       A/B, bit 4/5 reset flag A/B
    Timer A counts (1024 - NA) steps of 72 chip clocks; timer B counts
    (256 - NB) steps of 1152 chip clocks.  A timer reloads when it expires
    while its load bit stays set.  The flag is set at expiry when its enable
    bit is set.  The IRQ line is high while a flag is set.  A write of the
    reset bit clears the flag and lowers the line.

    chip_clock_per_tstate: chip clocks for one Z80 T-state.  The Neo Geo
    has an 8 MHz YM2610 and a 4 MHz Z80, so the default is 2.
    irq_vec: the data bus byte of the IM 2 acknowledge (None: last value).
    busy_tstates: how long the busy bit stays set after a data write.  The
    value 48 is an estimate; change it when the hardware figure is known.
    """

    def __init__(self, machine, base: int = 0x04,
                 chip_clock_per_tstate: float = 2.0, busy_tstates: int = 48,
                 irq_vec: int | None = None):
        self.m = machine
        self.irq_vec = irq_vec      # IM 2 data bus byte, None: last value
        self.base = base
        self.ratio = chip_clock_per_tstate
        self.busy_tstates = busy_tstates
        self.addr = [0, 0]
        self.regs = {}
        self.busy_until = 0
        self.status = 0
        self.enable = 0
        self.timer_gen = [0, 0]        # a generation count per timer
        self.running = [False, False]
        self.na = 0
        self.nb = 0
        self.log: list[tuple[int, int, int]] = []   # (t, reg, value) set A/B
        machine.add_device(self)

    # periods in Z80 T-states
    def period_a(self) -> float:
        return (1024 - self.na) * 72 / self.ratio

    def period_b(self) -> float:
        return (256 - self.nb) * 1152 / self.ratio

    def owns(self, port: int) -> bool:
        return self.base <= (port & 0xFF) <= self.base + 3

    def read(self, port: int, t: int):
        off = (port & 0xFF) - self.base
        if off == 0:
            v = self.status & 3
            if t < self.busy_until:
                v |= 0x80
            return v
        return 0

    def write(self, port: int, value: int, t: int) -> None:
        off = (port & 0xFF) - self.base
        side = off >> 1
        if off & 1 == 0:
            self.addr[side] = value
            return
        reg = self.addr[side]
        self.busy_until = t + self.busy_tstates
        self.log.append((t, reg + (0x100 if side else 0), value))
        if side == 0:
            self._reg_a(reg, value, t)

    def _reg_a(self, reg: int, v: int, t: int) -> None:
        if reg == 0x24:
            self.na = (self.na & 3) | (v << 2)
        elif reg == 0x25:
            self.na = (self.na & ~3) | (v & 3)
        elif reg == 0x26:
            self.nb = v
        elif reg == 0x27:
            self.enable = (v >> 2) & 3
            if v & 0x10:
                self.status &= ~1
            if v & 0x20:
                self.status &= ~2
            self._irq()
            for i, bit in ((0, 1), (1, 2)):
                if v & bit and not self.running[i]:
                    self._start(i, t)
                elif not v & bit and self.running[i]:
                    self.running[i] = False
                    self.timer_gen[i] += 1

    def _start(self, i: int, t: int) -> None:
        self.running[i] = True
        self.timer_gen[i] += 1
        gen = self.timer_gen[i]
        period = self.period_a() if i == 0 else self.period_b()
        self.m.at(t + max(1, round(period)), lambda m, tt: self._expire(i, gen, tt),
                  kind="call")

    def _expire(self, i: int, gen: int, t: int) -> None:
        if gen != self.timer_gen[i] or not self.running[i]:
            return
        if self.enable & (1 << i):
            self.status |= 1 << i
        self._irq()
        # reload: the next expiry counts from the exact expiry time
        period = self.period_a() if i == 0 else self.period_b()
        self.m.at(t + max(1, round(period)), lambda m, tt: self._expire(i, gen, tt),
                  kind="call")

    def _irq(self) -> None:
        self.m.set_irq_level(bool(self.status & self.enable & 3), self,
                             self.irq_vec)

    def can_wake(self) -> bool:
        return any(self.running) and bool(self.enable)
