#!/usr/bin/env python3
"""Build the snd-* kernels as Z80 ROM images and run them in romrun.

Part of the uc80 benchmark harness.  SPDX-License-Identifier: GPL-3.0-or-later
See docs/rom.md.

    python3 bench/rom/sndrom.py                 # all four kernels, dev layout
    python3 bench/rom/sndrom.py --layout neogeo # RAM F800-FFFF (2 KB)
    python3 bench/rom/sndrom.py snd-cmdq --keep DIR
    python3 bench/rom/sndrom.py --sdas          # sdasz80 and sdldz80 build

A kernel is compiled with `uc80 --target=rom` (bench/rom/snd.h replaces the
CP/M snd.h, bench/rom/z80io.mac gives out () and in ()), assembled with um80
and linked with `ul80 --bin --rom-data`.  The run starts at address 0 with
random RAM and a poisoned SP.  The host model SndPorts catches the ports: it
keeps the ring of the last 256 writes and the busy count of the CP/M snd.h,
so the decoded output is the CP/M golden output (bench/golden/snd-*.txt).

With --sdas the kernel goes through `uc80 --asm-syntax=sdas`, sdasz80 and
sdldz80 (docs/rom_interop.md) instead of um80 and ul80.  The run and the
checks are the same.
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
    # ROM 0000-7FFF, RAM 8000-FFFF: a roomy layout for any program.
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


UC80_FLAGS = []         # more uc80 options (--uc80-flag)


def tools_missing():
    return [t for t in ("uc80", "um80", "ul80") if not shutil.which(t)]


def run(cmd, cwd):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def sdas_tools_missing():
    return [t for t in ("uc80", "sdasz80", "sdldz80", "sdar", "sdobjcopy")
            if not shutil.which(t)]


def sdas_libs(libdir: Path, style="sdcc") -> Path:
    """Build the sdas runtime and libc archives in libdir once."""
    if not (libdir / "uc80_lc.lib").exists():
        sys.path.insert(0, str(BENCH.parent / "src"))
        from uc80 import sdas
        libdir.mkdir(parents=True, exist_ok=True)
        res = sdas.build_library(libdir, style)
        if res["failed"]:
            raise RuntimeError("sdas library build failed: %r"
                               % res["failed"][:3])
    return libdir


def noi_to_sym(noi: Path, sym: Path):
    """Write a ul80 style .sym file ("ADDR NAME") from an sdld .noi."""
    out = []
    for line in noi.read_text().splitlines():
        p = line.split()
        if len(p) == 3 and p[0] == "DEF":
            out.append("%04X %s" % (int(p[2], 16) & 0xFFFF, p[1]))
    sym.write_text("\n".join(out) + "\n")


def sdas_link_flags(style="sdcc", layout="neogeo", origin=0x68):
    """The sdldz80 flags for a ROM of the layout (see uc80.sdas)."""
    sys.path.insert(0, str(BENCH.parent / "src"))
    from uc80 import sdas
    return sdas.link_flags(style, origin, LAYOUTS[layout]["data"], 0)


def sdas_compile_link(work: Path, c_name: str, layout="dev", libs=None,
                      style="sdcc", extra=()):
    """uc80 --asm-syntax=sdas, sdasz80, sdldz80 and sdobjcopy for c_name
    and z80io.mac, which are in `work`.  Like build(), returns a dict."""
    from uc80 import sdas  # noqa: E402 (the path is set by sdas_libs)
    lay = LAYOUTS[layout]
    libs = Path(libs)
    log = []
    rc, out = run(["uc80", "--target=rom", "--asm-syntax=sdas",
                   "--sdas-style", style, "-I", str(HERE), "-I", str(BENCH),
                   *extra, c_name, "z80io.mac", "-o", "k.s"], work)
    log.append(out)
    if rc:
        return dict(ok=False, log="\n".join(log))
    origin = sdas.code_origin_of((work / "k.s").read_text()) or 0x68
    flags = sdas.link_flags(style, origin, lay["data"], 0)
    steps = [
        ["sdasz80", "-g", "-l", "-o", "k.s"],
        ["sdldz80", *flags, "-i", "-j", "-m", "k.ihx", "k.rel",
         "-l", str(libs / "uc80_lc.lib"), "-l", str(libs / "uc80_rt.lib")],
        ["sdobjcopy", "-I", "ihex", "-O", "binary", "k.ihx", "k.bin"],
    ]
    for cmd in steps:
        rc, out = run(cmd, work)
        log.append(out)
        if rc != 0:
            return dict(ok=False, log="\n".join(log))
    noi_to_sym(work / "k.noi", work / "k.sym")
    syms = {}
    for line in (work / "k.sym").read_text().split("\n"):
        p = line.split()
        if len(p) == 2:
            syms[p[1]] = int(p[0], 16)
    a = sdas.area_names(style)
    info = dict(ok=True, log="\n".join(log), bin=work / "k.bin",
                sym=work / "k.sym", layout=layout, compiler=log[0])
    info["rom"] = syms.get("s_" + a.rom_init, 0) + syms.get(
        "l_" + a.rom_init, 0)
    info["init_data"] = syms.get("l_" + a.rom_init, 0)
    info["data"] = syms.get("l_" + a.ram_init, 0)
    info["bss"] = syms.get("l_" + a.bss, 0)
    info["ram"] = info["data"] + info["bss"]
    return info


def build_sdas(kernel, work: Path, layout="dev", libs=None, style="sdcc"):
    """Like build(), with uc80 --asm-syntax=sdas, sdasz80 and sdldz80."""
    src_dir = BENCH / "programs" / kernel
    c_name = KERNELS[kernel]
    text = (src_dir / c_name).read_text()
    (work / c_name).write_text('#include "bench.h"\n' + text)
    shutil.copy(HERE / "z80io.mac", work / "z80io.mac")
    return sdas_compile_link(work, c_name, layout, libs, style,
                             extra=UC80_FLAGS)


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
         *UC80_FLAGS, c_name, "z80io.mac", "-o", "k.mac"],
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
    ap.add_argument("--sdas", action="store_true",
                    help="build with sdasz80 and sdldz80 (docs/rom_interop.md)")
    ap.add_argument("--sdas-style", default="sdcc",
                    choices=["sdcc", "ngdevkit"])
    ap.add_argument("--uc80-flag", action="append", default=[],
                    metavar="FLAG", help="one more uc80 option (repeatable, "
                    "for example --no-direct-pointers)")
    args = ap.parse_args(argv)
    UC80_FLAGS.extend(args.uc80_flag)
    missing = sdas_tools_missing() if args.sdas else tools_missing()
    if missing:
        print("missing tools: " + " ".join(missing), file=sys.stderr)
        return 2
    names = args.kernel or list(KERNELS)
    bad = 0
    libs = None
    if args.sdas:
        libs = sdas_libs(Path(args.keep or tempfile.mkdtemp(
            prefix="sdaslib-")) / "sdaslib", args.sdas_style)
    for k in names:
        work = Path(args.keep or tempfile.mkdtemp(prefix="sndrom-")) / k
        work.mkdir(parents=True, exist_ok=True)
        info = (build_sdas(k, work, args.layout, libs, args.sdas_style)
                if args.sdas else build(k, work, args.layout))
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
