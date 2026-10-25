#!/usr/bin/env python3
"""Build the snd-* kernels as Z80 ROM images and run them in romrun.

Part of the uc80 benchmark harness.  SPDX-License-Identifier: GPL-3.0-or-later
See docs/rom.md.

    python3 bench/rom/sndrom.py                 # all four kernels, dev layout
    python3 bench/rom/sndrom.py --layout neogeo # RAM F800-FFFF (2 KB)
    python3 bench/rom/sndrom.py snd-cmdq --keep DIR

A kernel is compiled with `uc80 --target=rom` (bench/rom/snd.h replaces the
CP/M snd.h, bench/rom/z80io.mac gives out () and in ()), assembled with um80
and linked with `ul80 --bin --rom-data`.  The run starts at address 0 with
random RAM and a poisoned SP.  The host model SndPorts catches the ports: it
keeps the ring of the last 256 writes and the busy count of the CP/M snd.h,
so the decoded output is the CP/M golden output (bench/golden/snd-*.txt).
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
sys.path.insert(0, str(BENCH))

from romrun import Machine  # noqa: E402

KERNELS = {
    "snd-cmdq": "cmdq.c",
    "snd-ymreg": "ymreg.c",
    "snd-seq": "seq.c",
    "snd-fnum": "fnum.c",
}

# name: (data origin, RAM size, RAM range for the model, min stack)
LAYOUTS = {
    # ROM 0000-7FFF, RAM 8000-FFFF: room for the const tables, which are
    # still in DSEG (copied to RAM) until the codegen part of Phase 1.
    "dev": dict(data=0x8000, ram=(0x8000, 0xFFFF), rom_size=0x8000),
    # The Neo Geo sound CPU: ROM 0000-7FFF, RAM F800-FFFF (2 KB).
    "neogeo": dict(data=0xF800, ram=(0xF800, 0xFFFF), rom_size=0x8000),
}


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


class SndPorts:
    """The host side of bench/programs/snd/snd.h: what out () and in ()
    of the CP/M kernel keep in RAM, kept here for the ROM kernel."""

    def __init__(self):
        self.log = bytearray(512)
        self.writes = 0
        self.polls = 0
        self.busy = 0
        self.text = bytearray()

    def owns(self, port):
        return True

    def write(self, port, value, t):
        p = port & 0xFF
        if p == 0xFE:
            self.text.append(value)
            return
        if p == 0xFF:
            return
        i = (self.writes & 255) * 2
        self.log[i] = p
        self.log[i + 1] = value
        self.writes += 1
        if (p & 0xF8) == 0:
            self.busy = 2

    def read(self, port, t):
        self.polls += 1
        if (port & 0xFF) == 0x04 and self.busy:
            self.busy -= 1
            return 0x80
        return 0x00

    def report(self, name):
        return "%s: %u writes, %u polls, log CRC %u\n" % (
            name, self.writes & 0xFFFF, self.polls & 0xFFFF,
            crc16_ccitt_false(bytes(self.log)))


def tools_missing():
    return [t for t in ("uc80", "um80", "ul80") if not shutil.which(t)]


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def build(kernel, work: Path, layout="dev", min_stack=0x100):
    """Compile, assemble and link one kernel in `work`.  Returns the paths
    and the ROM and RAM figures of the ul80 summary line."""
    src_dir = BENCH / "programs" / kernel
    c_name = KERNELS[kernel]
    text = (src_dir / c_name).read_text()
    (work / c_name).write_text('#include "bench.h"\n' + text)
    shutil.copy(HERE / "z80io.mac", work / "z80io.mac")
    lay = LAYOUTS[layout]
    lib = Path(run(["uc80", "--print-lib-dir"], work)[1].strip())
    steps = [
        ["uc80", "--target=rom", "-I", str(HERE), "-I", str(BENCH),
         c_name, "z80io.mac", "-o", "k.mac"],
        ["um80", "k.mac", "-o", "k.rel"],
        ["ul80", "--bin", "-p", "0", "-d", "%X" % lay["data"], "--rom-data",
         "--rom-size", "%X" % lay["rom_size"],
         "--ram-size", "%X" % (0x10000 - lay["data"]),
         "--min-stack", "%X" % min_stack, "--verbose",
         "-o", "k.bin", "-S", "k.sym", "k.rel",
         str(lib / "libc_rom.lib"), str(lib / "runtime.lib")],
    ]
    log = []
    for cmd in steps:
        rc, out = run(cmd, work)
        log.append(out)
        if rc != 0:
            return dict(ok=False, log="\n".join(log))
    m = re.search(r"ROM (\d+) of (\d+) bytes.*?init data (\d+).*?"
                  r"RAM (\d+) of (\d+) bytes.*?data (\d+), bss (\d+)",
                  "\n".join(log))
    info = dict(ok=True, log="\n".join(log), bin=work / "k.bin",
                sym=work / "k.sym", layout=layout)
    if m:
        (info["rom"], _, info["init_data"], info["ram"], info["ram_size"],
         info["data"], info["bss"]) = map(int, m.groups())
    return info


def execute(info, name, max_t=200_000_000):
    """Run the image; return the decoded text, T-states of the timed
    region, and the stack depth."""
    lay = LAYOUTS[info["layout"]]
    m = Machine(rom_size=lay["rom_size"], ram=lay["ram"], banks=None,
                ram_fill="random", sp="poison", track=True)
    m.keep_log = False
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    ports = SndPorts()
    m.add_device(ports)
    m.magic_ports.add(0xFF)
    span = m.measure("TIMER_START", "TIMER_START", "TIMER_STOP")
    res = m.run(max_tstates=max_t)
    out = bytes(ports.text).decode("ascii", "replace") + ports.report(name.split("-", 1)[1])
    return dict(reason=res.reason, total_t=res.t, text=out,
                timed_t=span.total, stack=m.stack_depth(top=0x10000),
                rom_writes=len(m.rom_writes), writes=ports.writes,
                polls=ports.polls)


def golden(kernel) -> str:
    return (BENCH / "golden" / f"{kernel}.txt").read_text()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("kernel", nargs="*", help="snd-cmdq ... (default: all)")
    ap.add_argument("--layout", choices=sorted(LAYOUTS), default="dev")
    ap.add_argument("--keep", metavar="DIR", help="keep the build files here")
    args = ap.parse_args(argv)
    missing = tools_missing()
    if missing:
        print("missing tools: " + " ".join(missing), file=sys.stderr)
        return 2
    names = args.kernel or list(KERNELS)
    bad = 0
    for k in names:
        work = Path(args.keep or tempfile.mkdtemp(prefix="sndrom-")) / k
        work.mkdir(parents=True, exist_ok=True)
        info = build(k, work, args.layout)
        if not info["ok"]:
            print(f"{k}: BUILD FAILED\n{info['log']}")
            bad += 1
            continue
        r = execute(info, k)
        same = r["text"] == golden(k)
        print("%s: %s  layout %s  ROM %d  RAM %d (data %d, bss %d) + stack %s"
              "  %d T-states  [%s]" % (
                  k, "OK" if same else "OUTPUT DIFFERS", args.layout,
                  info.get("rom", 0), info.get("ram", 0), info.get("data", 0),
                  info.get("bss", 0), r["stack"], r["timed_t"], r["reason"]))
        if not same:
            bad += 1
            print("--- ROM\n" + r["text"] + "--- golden\n" + golden(k))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
