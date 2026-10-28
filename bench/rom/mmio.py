#!/usr/bin/env python3
"""Stage B proof: a memory-mapped sound chip driver with and without the
direct access of a pointer that holds a constant address.

Part of the uc80 benchmark harness.  SPDX-License-Identifier: GPL-3.0-or-later
See docs/rom_hardware.md ("Pointers to hardware").

    python3 bench/rom/mmio.py            # ul80 flow
    python3 bench/rom/mmio.py --sdas     # uc80 --asm-syntax=sdas, sdldz80

The program mmio_ym.c writes a table of YM2610 registers through pointers
at 4000h (a ROM address in the runner, so each write is in the log of the
runner).  The script builds the program twice (the second time with
--no-direct-pointers), runs both images, checks that the logs of the writes
are equal, and prints the ROM bytes and the T-states.
"""

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from romrun import Machine  # noqa: E402
from sndrom import (run, sdas_compile_link, sdas_libs,  # noqa: E402
                    sdas_tools_missing, tools_missing)


def build(work, extra, sdas_libdir):
    work.mkdir(parents=True, exist_ok=True)
    (work / "mmio_ym.c").write_text((HERE / "mmio_ym.c").read_text())
    if sdas_libdir:
        (work / "z80io.mac").write_text((HERE / "z80io.mac").read_text())
        return sdas_compile_link(work, "mmio_ym.c", "neogeo", sdas_libdir,
                                 extra=extra)
    lib = Path(run(["uc80", "--print-lib-dir"], work)[1].strip())
    steps = [
        ["uc80", "--target=rom", *extra, "mmio_ym.c", "-o", "k.mac"],
        ["um80", "k.mac", "-o", "k.rel"],
        ["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
         "--rom-size", "8000", "--ram-size", "800", "--min-stack", "40",
         "--verbose", "-o", "k.bin", "-S", "k.sym", "k.rel",
         str(lib / "libc_rom.lib"), str(lib / "runtime.lib")],
    ]
    for cmd in steps:
        rc, out = run(cmd, work)
        if rc:
            return dict(ok=False, log=out)
    return dict(ok=True, bin=work / "k.bin", sym=work / "k.sym")


def execute(info):
    m = Machine(rom_size=0x8000, ram=(0xF800, 0xFFFF), banks=None,
                ram_fill="random", sp="poison", track=True)
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    m.keep_log = False
    for a in range(0x4000, 0x4030):      # the chip: not busy, no reply
        m.cpu.mem[a] = 0
    res = m.run(max_tstates=50_000_000)
    try:                      # ul80: the image ends after the initial data
        size = m.addr("__DATA_ROM") + m.addr("__DATA_LEN")
    except Exception:         # sdas: the image has no padding
        size = len((info["bin"]).read_bytes())
    return res, [(a, v) for (_t, a, v, _pc) in m.rom_writes], size, m


def main(argv=None):
    args = list(argv if argv is not None else sys.argv[1:])
    sdas = "--sdas" in args
    missing = sdas_tools_missing() if sdas else tools_missing()
    if missing:
        print("missing tools: " + " ".join(missing), file=sys.stderr)
        return 2
    work = Path(tempfile.mkdtemp(prefix="mmio-"))
    libs = sdas_libs(work / "sdaslib") if sdas else None
    rows = {}
    for tag, extra in (("direct", []), ("pointer", ["--no-direct-pointers"])):
        info = build(work / tag, extra, libs)
        if not info["ok"]:
            print("BUILD FAILED (%s)\n%s" % (tag, info["log"]))
            return 1
        rows[tag] = execute(info)
        if rows[tag][0].reason != "halt":
            print("%s: the run ended with %s" % (tag, rows[tag][0].reason))
            return 1
    same = rows["direct"][1] == rows["pointer"][1]
    print("writes: %d, equal: %s" % (len(rows["direct"][1]), same))
    for tag in ("pointer", "direct"):
        res, _w, size, _m = rows[tag]
        print("%-8s image %5d bytes  %9d T-states" % (tag, size, res.t))
    d, p = rows["direct"], rows["pointer"]
    print("change: %+d bytes, %+d T-states (%.1f%%)" % (
        d[2] - p[2], d[0].t - p[0].t, 100.0 * (d[0].t - p[0].t) / p[0].t))
    return 0 if same else 1


if __name__ == "__main__":
    sys.exit(main())
