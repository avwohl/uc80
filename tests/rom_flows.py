"""Build helpers for the tests that run a ROM in both flows.

The um80 flow: uc80, um80, ul80.  The sdas flow: uc80 --asm-syntax=sdas,
sdasz80, sdldz80 (when the SDCC tools exist).  Both return the paths of the
image and of the symbol file, and the messages of the compiler.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import HAVE_SDAS

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bench"))
sys.path.insert(0, str(REPO / "bench" / "rom"))

import sndrom  # noqa: E402
import uc80  # noqa: E402
from romrun import Machine  # noqa: E402

HAVE_UM80 = bool(shutil.which("um80") and shutil.which("ul80"))
LIB = uc80.lib_dir()
INC = str(LIB / "include")
FLOWS = ["um80", "sdas"]


def uc80_cmd(*args):
    return [sys.executable, "-m", "uc80.main", *args]


def run(cmd, cwd, ok=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if ok:
        assert r.returncode == 0, (" ".join(map(str, cmd)) + "\n" + r.stdout
                                   + r.stderr)
    return r


def need_flow(flow):
    if flow == "um80" and not HAVE_UM80:
        pytest.skip("needs um80 and ul80")
    if flow == "sdas" and not HAVE_SDAS:
        pytest.skip("needs the SDCC sdas tools")


def build_flow(tmp_path, files, flow, rom_libs=None, extra=(),
               rom_size=0x8000, link_extra=()):
    """Compile the C and assembly files ({name: text}) as one program.
    Returns ((bin, sym), the stderr of uc80)."""
    need_flow(flow)
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    names = list(files)
    if flow == "sdas":
        from uc80 import sdas
        r = run(uc80_cmd("--target=rom", "--asm-syntax=sdas", "-I", INC,
                         "--rom-size", "%X" % rom_size, *extra, *names,
                         "-o", "t.s"), tmp_path)
        run(["sdasz80", "-g", "-l", "-o", "t.s"], tmp_path)
        origin = sdas.code_origin_of((tmp_path / "t.s").read_text()) or 0x68
        run(["sdldz80", *sndrom.sdas_link_flags(origin=origin), "-i", "-j",
             "t.ihx", "t.rel", *link_extra,
             "-l", str(rom_libs / "uc80_lc.lib"),
             "-l", str(rom_libs / "uc80_rt.lib")], tmp_path)
        run(["sdobjcopy", "-I", "ihex", "-O", "binary", "t.ihx", "t.bin"],
            tmp_path)
        sndrom.noi_to_sym(tmp_path / "t.noi", tmp_path / "t.sym")
        return (tmp_path / "t.bin", tmp_path / "t.sym"), r.stderr
    r = run(uc80_cmd("--target=rom", "-I", INC, *extra, *names,
                     "-o", "t.mac"), tmp_path)
    run(["um80", "t.mac", "-o", "t.rel"], tmp_path)
    run(["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
         "--rom-size", "%X" % rom_size, "--ram-size", "800",
         "--min-stack", "40", "-o", "t.bin", "-S", "t.sym", "t.rel",
         *link_extra, str(LIB / "libc_rom.lib"), str(LIB / "runtime.lib")],
        tmp_path)
    return (tmp_path / "t.bin", tmp_path / "t.sym"), r.stderr


def machine(paths, rom_size=0x8000):
    m = Machine(rom_size=rom_size, banks=None, ram_fill="random",
                sp="poison", track=True)
    m.load_rom(str(paths[0]))
    m.load_symbols(str(paths[1]))
    return m


def symbols(paths):
    out = {}
    for line in Path(paths[1]).read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1].upper()] = int(parts[0], 16)
    return out


def build_separate(tmp_path, files, flow, rom_libs=None, crt0="vec",
                   extra=(), rom_size=0x8000):
    """Compile each C file of ``files`` ({name: text}) alone with
    --no-whole-program, and link the modules with the start-up module
    crt0_rom_vec (crt0="vec") or crt0_rom (crt0="plain").  Returns
    (paths, [uc80 stderr of each file], the link result)."""
    need_flow(flow)
    errs, objs = [], []
    for name, text in files.items():
        (tmp_path / name).write_text(text)
        stem = name.rsplit(".", 1)[0]
        if flow == "sdas":
            r = run(uc80_cmd("--target=rom", "--asm-syntax=sdas",
                             "--no-whole-program", "-I", INC, *extra, name,
                             "-o", stem + ".s"), tmp_path)
            run(["sdasz80", "-g", "-l", "-o", stem + ".s"], tmp_path)
            objs.append(stem + ".rel")
        else:
            r = run(uc80_cmd("--target=rom", "--no-whole-program", "-I", INC,
                             *extra, name, "-o", stem + ".mac"), tmp_path)
            run(["um80", stem + ".mac", "-o", stem + ".rel"], tmp_path)
            objs.append(stem + ".rel")
        errs.append(r.stderr)
    paths = (tmp_path / "t.bin", tmp_path / "t.sym")
    if flow == "sdas":
        stem = "crt0_rom_vec" if crt0 == "vec" else "crt0_rom"
        crt0 = rom_libs / (stem + ".rel")
        from uc80 import sdas
        origin = sdas.code_origin_of(
            (rom_libs / (stem + ".s")).read_text()) or 0x68
        link = run(["sdldz80", *sndrom.sdas_link_flags(origin=origin), "-i",
                    "-j", "t.ihx", str(crt0), *objs,
                    "-l", str(rom_libs / "uc80_lc.lib"),
                    "-l", str(rom_libs / "uc80_rt.lib")], tmp_path, ok=False)
        if link.returncode == 0:
            run(["sdobjcopy", "-I", "ihex", "-O", "binary", "t.ihx",
                 "t.bin"], tmp_path)
            sndrom.noi_to_sym(tmp_path / "t.noi", tmp_path / "t.sym")
    else:
        crt0 = LIB / ("crt0_rom_vec.rel" if crt0 == "vec" else "crt0_rom.rel")
        link = run(["ul80", "--bin", "-p", "0", "-d", "F800", "--rom-data",
                    "--rom-size", "%X" % rom_size, "--ram-size", "800",
                    "--min-stack", "40", "-o", "t.bin", "-S", "t.sym",
                    str(crt0), *objs, str(LIB / "libc_rom.lib"),
                    str(LIB / "runtime.lib")], tmp_path, ok=False)
    return paths, errs, link
