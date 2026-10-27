"""Command line of the ROM test runner.  See docs/romrun.md."""

from __future__ import annotations

import argparse
import json
import sys
import time

from .devices import YM2610
from .machine import NEOGEO_BANKS, BankWindow, Machine


def num(s: str) -> int:
    """A number: decimal, 0x hex, or hex with an h suffix or a $ prefix."""
    s = s.strip()
    if s.startswith("$"):
        return int(s[1:], 16)
    if s.lower().endswith("h") and not s.lower().startswith("0x"):
        return int(s[:-1], 16)
    return int(s, 0)


def parse_in(m: Machine, spec: str) -> None:
    port_s, _, rest = spec.partition("=")
    port = num(port_s)
    kind, _, arg = rest.partition(":")
    if kind == "seq":
        vals = [num(v) for v in arg.split(",") if v]
        m.feed.queue(port, vals)
    elif kind == "steps":
        pairs = []
        for item in arg.split(","):
            t, _, v = item.partition(":")
            pairs.append((num(t), num(v)))
        m.feed.steps(port, pairs)
    elif kind == "busy":
        a = arg.split(":")
        m.feed.busy(port, num(a[0]), num(a[1]) if len(a) > 1 else 0x80)
    else:
        m.feed.const(port, num(rest))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="romrun",
        description="Run Z80 code from reset and log port I/O and T-states.")
    ap.add_argument("image", help="ROM image (raw, or Intel HEX) or CP/M .com")
    ap.add_argument("--hex", action="store_true",
                    help="image is Intel HEX (also chosen by .hex/.ihx)")
    ap.add_argument("--cpm", action="store_true",
                    help="CP/M mode: load a .com at 0100h, BDOS at 0005h")
    ap.add_argument("--stdin", metavar="FILE", help="CP/M console input")
    ap.add_argument("--rom-size", type=num, default=0x8000)
    ap.add_argument("--ram", default="F800-FFFF", metavar="LO-HI",
                    help="RAM range in hex (default F800-FFFF)")
    ap.add_argument("--bank", action="append", default=None,
                    metavar="PORT:START:SIZE",
                    help="banked window; default is the Neo Geo set")
    ap.add_argument("--no-banks", action="store_true")
    ap.add_argument("--rom-write", choices=("log", "fault", "ignore"),
                    default="log")
    ap.add_argument("--max-t", type=num, default=None,
                    help="stop after this many T-states")
    ap.add_argument("--stop-at", action="append", default=[], metavar="ADDR",
                    help="stop when PC reaches ADDR or a symbol (repeatable)")
    ap.add_argument("--no-stop-halt", action="store_true",
                    help="do not stop at HALT with interrupts disabled")
    ap.add_argument("--magic-port", action="append", default=[], type=num,
                    metavar="PORT", help="a write to PORT stops the run")
    ap.add_argument("--port-log", metavar="FILE",
                    help="write every port IN and OUT to FILE")
    ap.add_argument("--log-events", action="store_true",
                    help="add interrupt, NMI and ROM-write lines to the log")
    ap.add_argument("--im", type=int, choices=(0, 1, 2), default=None,
                    help="start in this interrupt mode (the ROM sets its own)")
    ap.add_argument("--irq-at", action="append", default=[], type=num,
                    metavar="T")
    ap.add_argument("--irq-every", type=num, default=None, metavar="N",
                    help="a maskable interrupt every N T-states")
    ap.add_argument("--irq-start", type=num, default=None)
    ap.add_argument("--irq-vec", type=num, default=None, metavar="BYTE",
                    help="the data bus byte of each maskable interrupt (IM 2: "
                    "low byte of the vector address)")
    ap.add_argument("--nmi-at", action="append", default=[], type=num,
                    metavar="T")
    ap.add_argument("--nmi-every", type=num, default=None, metavar="N")
    ap.add_argument("--nmi-start", type=num, default=None)
    ap.add_argument("--ym2610", nargs="?", const=4, type=num, default=None,
                    metavar="BASE", help="add the YM2610 timer model (ports "
                    "BASE..BASE+3, default 04h)")
    ap.add_argument("--ym-clock-ratio", type=float, default=2.0)
    ap.add_argument("--ym-busy", type=num, default=48)
    ap.add_argument("--in", dest="ins", action="append", default=[],
                    metavar="PORT=SPEC",
                    help="port read values: PORT=V, PORT=seq:V,V.. , "
                    "PORT=steps:T:V,T:V.. , PORT=busy:T[:MASK]")
    ap.add_argument("--default-in", type=num, default=0xFF)
    ap.add_argument("--sym", metavar="FILE", help="ul80 .sym file")
    ap.add_argument("--measure", action="append", default=[],
                    metavar="A:B|FUNC",
                    help="T-states from symbol A to B, or inside function FUNC")
    ap.add_argument("--ram-fill", metavar="BYTE|random", default=None,
                    help="start value of RAM (default 0)")
    ap.add_argument("--ram-seed", type=num, default=1)
    ap.add_argument("--sp", metavar="N|poison", default="0xFFFF",
                    help="start value of SP; poison is an address in ROM")
    ap.add_argument("--track", action="store_true",
                    help="report the lowest SP and the RAM written")
    ap.add_argument("--nmi-gate", action="store_true",
                    help="Neo Geo NMI gate: OUT 08h opens, OUT 18h closes")
    ap.add_argument("--dump", action="append", default=[], metavar="SYM[:N]",
                    help="print N bytes (default 1) at SYM after the run")
    ap.add_argument("--dump-ram", metavar="FILE",
                    help="write the RAM range to FILE after the run")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    ap.add_argument("--quiet", action="store_true")
    return ap


def main(argv=None) -> int:
    try:
        return _main(argv)
    except (KeyError, FileNotFoundError, ValueError) as e:
        print(f"romrun: error: {e.args[0] if e.args else e}", file=sys.stderr)
        return 1


def _main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    lo, _, hi = a.ram.partition("-")
    banks = None
    if not a.no_banks:
        if a.bank:
            banks = []
            for b in a.bank:
                p, s, z = b.split(":")
                banks.append(BankWindow(num(p), num(s), num(z)))
        elif not a.cpm:
            banks = list(NEOGEO_BANKS)
    m = Machine(rom_size=a.rom_size, ram=(int(lo, 16), int(hi, 16)),
                banks=banks, rom_write=a.rom_write,
                mode="cpm" if a.cpm else "rom", default_in=a.default_in,
                ram_fill=(None if a.ram_fill is None else
                          "random" if a.ram_fill == "random"
                          else num(a.ram_fill)),
                ram_seed=a.ram_seed,
                sp="poison" if a.sp == "poison" else num(a.sp),
                track=a.track, nmi_gate=a.nmi_gate)
    m.log_events = a.log_events
    m.stop_on_halt = not a.no_stop_halt
    if a.port_log:
        m.log_file = open(a.port_log, "w")
        m.keep_log = False
    if a.sym:
        m.load_symbols(a.sym)
    if a.cpm:
        stdin = open(a.stdin, "rb").read() if a.stdin else b""
        m.load_com(a.image, stdin)
    elif a.hex or a.image.lower().endswith((".hex", ".ihx")):
        m.load_hex(a.image)
    else:
        m.load_rom(a.image)
    if a.im is not None:
        m.cpu.set(IM=a.im)
    for t in a.irq_at:
        m.add_irq_at(t, a.irq_vec)
    for t in a.nmi_at:
        m.add_nmi_at(t)
    if a.irq_every:
        m.add_irq_every(a.irq_every, a.irq_start, vec=a.irq_vec)
    if a.nmi_every:
        m.add_nmi_every(a.nmi_every, a.nmi_start)
    if a.ym2610 is not None:
        YM2610(m, a.ym2610, a.ym_clock_ratio, a.ym_busy)
    for spec in a.ins:
        parse_in(m, spec)
    m.magic_ports.update(a.magic_port)
    for s in a.stop_at:
        m.stop_at(s)
    for spec in a.measure:
        if ":" in spec:
            x, y = spec.split(":", 1)
            m.measure(spec, x, y)
        else:
            m.measure(spec, spec)
    t0 = time.time()
    res = m.run(a.max_t)
    dt = time.time() - t0
    if m.log_file:
        m.log_file.close()
    if a.cpm and m.console:
        sys.stdout.write(m.output_text())
        sys.stdout.flush()
    dumps = {}
    for spec in a.dump:
        sym, _, n = spec.partition(":")
        dumps[spec] = m.peek(sym, num(n) if n else 1).hex()
    if a.dump_ram:
        with open(a.dump_ram, "wb") as f:
            f.write(m.dump_ram())
    out = {"reason": res.reason, "t": res.t, "pc": res.pc,
           "seconds": round(dt, 2), "rom_writes": len(m.rom_writes),
           "spans": {k: {"count": v.count, "total": v.total, "min": v.min,
                         "max": v.max} for k, v in m.spans.items()}}
    out.update(res.info)
    if dumps:
        out["dump"] = dumps
    if a.json:
        print(json.dumps(out))
    elif not a.quiet:
        print(f"{res}  ({dt:.1f}s)", file=sys.stderr)
        if m.rom_writes:
            print(f"rom writes: {len(m.rom_writes)}  first at "
                  f"{m.rom_writes[0][1]:04X} (pc {m.rom_writes[0][3]:04X})",
                  file=sys.stderr)
        for spec, hx in dumps.items():
            print(f"{spec}: {hx}", file=sys.stderr)
        if a.track:
            ms = m.min_sp
            print("lowest sp: " + ("none" if ms is None else f"{ms:04X}")
                  + f"  ram written: {len(m.written())} bytes",
                  file=sys.stderr)
        for sp in m.spans.values():
            print(sp.summary(), file=sys.stderr)
    # exit status: 0 for a normal stop, 2 for a ROM write fault
    return 2 if res.reason == "rom-write" else 0
