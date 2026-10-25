#!/usr/bin/env python3
"""Build a sound ROM with nullsound and C code from uc80, and run it.

Part of the uc80 benchmark harness.  SPDX-License-Identifier: GPL-3.0-or-later
See docs/rom_interop.md.

    python3 bench/rom/nullsound.py              # fetch, build, run, check
    python3 bench/rom/nullsound.py --keep DIR   # keep the build files

nullsound is the sound driver of ngdevkit (LGPL-3.0-or-later,
github.com/dciabrin/ngdevkit).  This script does not contain nullsound.
It fetches the sources at a fixed commit into bench/_cache/ngdevkit,
checks the sha256 of each file, and builds them with sdasz80 as the
Makefile of ngdevkit does.  Then the script builds bench/rom/ns_user.s and
bench/rom/ns_cmds.c (C code from uc80), links all with sdldz80 into an
image for the M1 ROM, and runs the image in bench/romrun: the host sends
the commands 02h (driver start and hook), 04h three times (C command:
key on) and 05h (C command: key off) as NMIs and the script compares the
YM2610 register writes and the replies with the expected sequence.
"""

import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH.parent / "src"))

COMMIT = "5106cbd1414a86ba02810ee93ac977be3360de4f"
RAW = ("https://raw.githubusercontent.com/dciabrin/ngdevkit/"
       "{commit}/nullsound/{path}")
CACHE = BENCH / "_cache" / "ngdevkit"

# Path in nullsound/ -> sha256.  Makefile.in lists the objects.
FILES = {
    "adpcm.s": "3c4f558895a3dd65853c364cedf89c51c992a479dade5333ad2d8438a2da57c1",
    "bios-commands.s": "25602715b21c8ad5b2787277d5ace23acfd8a60afe976fdbd6fbe17f2463317d",
    "buffers.s": "b7e37de3c45120a6a9c54ffe2fa791645f78418699ef766a3867238891c3eeae",
    "entrypoint.s": "f5099a0069b4f7dfb912c2a8d13158368e3224546cafb69edf81ca0de0f74bb7",
    "fx-arpeggio.s": "22c0b666d870d6cbf970a91d6a2e4bf3dfa549dee84a221e6e284200ac13b1db",
    "fx-legato.s": "33325f4614548bfb6c4fdd4de0c5d3c401017fea4bdd102dd26f7b0f7990da20",
    "fx-slide.s": "b19725de3b9c6ce21e1162cb994ea9d00116a323236e237d611bab17984f05a9",
    "fx-trigger.s": "b6bbb529d6db84990ec7836a93e75beacdb84b685f4a4328feb0d91f914d67da",
    "fx-vibrato.s": "2bfeece857f6422687b9b8154cd2517a290d1b12e4b90058d746d05d61225e8d",
    "memory.s": "b2c19127db62512861fcc07478252980fdce0ef2c8ba89efc70d58bb7d644a23",
    "nss-adpcm-a.s": "481badb679808bb84777ec9e3a27a7283224d94ab2b74b9f356dab2e569104a1",
    "nss-adpcm-b.s": "53379e8327c030be968ecd7efdbe542aa94ca6c4716f054ddc37bcfe9bf733e6",
    "nss-fm.s": "9c50688298d9dc0f4dc5207036a18eb0fcf694cb76ae9b799638f711420a6589",
    "nss-ssg.s": "2e56e3acebe86954980e5aa4ba022334c48352a1d68c98a7349558c3ef4adc83",
    "stream.s": "47591c6f11b63a4fa371cf599d0df5abdc1134c8cd5021a9d34ae6e257d5af0f",
    "timer.s": "835a76c0691a167327ee53331239d80f433e7eb8394d11d67fe843dfcbea1099",
    "utils.s": "8db2c5c17b13ae821c405ba6a070eeb70bece34ae69a460e7a1b138f22a85e0b",
    "volume.s": "4034210e386b1ee5b8dec58728c9f6f93c5fe9f93360dc2b9c62cc8b2b58c49e",
    "ym2610.s": "481d96f3006fe4d7ce5d8c4e316733403854b30ab678af1d83647a68d0dddc92",
    "align.inc": "c86e7184db025c8ab9a7408adf2c8469e9fc09af091e3127293b423ff9804d31",
    "helpers.inc": "bf5287f2b551a9e11bdfa9b946efd1073dfe2ce731122d559ffe52e0521c37cc",
    "pipeline.inc": "2785960fb73d23828a2e549b3e65e3fdb7c99d044cb3f9956330a5a0e14ed42b",
    "ports.inc": "86dfec5686ef8b077c09cd6193aac6dd1910f1cc851960a0a600b64269ec14e3",
    "struct-fx.inc": "20fc27bc3e1f4805dd3b296714711a82aafe8a583233f4148c47ac38407a8b33",
    "timer.inc": "36a26973f633422aaa393dbafeb88cce4041f5252e669bb2209fb6972a3c0275",
    "ym2610.inc": "50b05df2db1d1e4700559a8f5defcd2150573aa3aa2d05aa42538fe9be3c2f08",
    "check-bounds.py": "680fcf7ee66406d3e90f7354336201de6619804745412aeaeb598fb2f57b2938",
    "mvs/fm-tables.inc": "d9f197a14d659c206d42060fa5e9e2f142ac65168a0f0d9e581bc19648f4c34f",
    "mvs/ssg-tables.inc": "a9d9c73322a0974b85f3a26f3c208c3cc8de628a9e48a15843f7be64c80fdd09",
}
# The objects of nullsound.lib, in the order of nullsound/Makefile.in.
OBJS = ["memory", "bios-commands", "adpcm", "ym2610", "stream", "timer",
        "utils", "nss-fm", "nss-ssg", "nss-adpcm-a", "nss-adpcm-b",
        "fx-vibrato", "fx-slide", "fx-trigger", "fx-arpeggio", "fx-legato",
        "volume"]

# The Neo Geo sound CPU ports that the expected sequence uses
PORT_YM_A_ADDR, PORT_YM_A_VALUE = 0x04, 0x05
PORT_TO_68K = 0x0C
NOTE_FNUM = [0x26A, 0x28F, 0x2B6, 0x2DF, 0x30B, 0x339,
             0x36A, 0x39E, 0x3D5, 0x410, 0x44E, 0x48F]


class Unavailable(Exception):
    """A tool or a source file is missing (no network, no cache)."""


def tools_missing():
    return [t for t in ("uc80", "sdasz80", "sdldz80", "sdobjcopy")
            if not shutil.which(t)]


def fetch(cache: Path = CACHE) -> Path:
    """Return the directory with the nullsound sources.  Each file is
    checked against FILES; a file that is missing is fetched."""
    root = cache / COMMIT / "nullsound"
    for rel, want in FILES.items():
        dest = root / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = RAW.format(commit=COMMIT, path=rel)
            try:
                with urllib.request.urlopen(url, timeout=60) as r:
                    dest.write_bytes(r.read())
            except OSError as e:
                raise Unavailable(f"cannot fetch {url}: {e}") from None
        got = hashlib.sha256(dest.read_bytes()).hexdigest()
        if got != want:
            dest.unlink()
            raise Unavailable(f"sha256 of {rel} is {got}, not {want}")
    return root


def run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    out = r.stdout + r.stderr
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd))}\n{out}")
    return out


def build_nullsound(src: Path, work: Path) -> Path:
    """Assemble nullsound as nullsound/Makefile.in does (sdasz80 -a -g -l
    -p -s -u, the MVS tables) and make nullsound.lib, a plain
    concatenation of the .rel files.  Returns the path of the library."""
    work.mkdir(parents=True, exist_ok=True)
    rels = []
    jobs = [("entrypoint", "entrypoint-mvs", ["-I" + str(src / "mvs")])]
    jobs += [(o, o, []) for o in OBJS]
    for stem, out, extra in jobs:
        run(["sdasz80", "-a", "-g", "-l", "-p", "-s", "-u", *extra,
             "-I" + str(src), "-o", str(work / (out + ".rel")),
             str(src / (stem + ".s"))], work)
        rels.append(work / (out + ".rel"))
    lib = work / "nullsound.lib"
    lib.write_bytes(b"".join(p.read_bytes() for p in rels))
    return lib


def build_user(src: Path, work: Path):
    """uc80 compiles ns_cmds.c to sdas text; sdasz80 assembles that and
    ns_user.s."""
    shutil.copy(HERE / "ns_cmds.c", work / "ns_cmds.c")
    shutil.copy(HERE / "ns_user.s", work / "ns_user.s")
    run(["uc80", "--target=rom", "--asm-syntax=sdas",
         "--sdas-style=ngdevkit", "--no-embed-startup",
         "--entry", "cmd_init", "--entry", "cmd_key_on",
         "--entry", "cmd_key_off", "ns_cmds.c", "-o", "ns_cmds.s"], work)
    run(["sdasz80", "-g", "-l", "-o", "ns_cmds.s"], work)
    run(["sdasz80", "-g", "-l", "-I" + str(src), "-o", "ns_user.s"], work)


def link(lib: Path, work: Path):
    """The link command of nullsound/Makefile.in (nullbios): DATA at
    F800h, nullsound first.  Then the check of nullsound for the RAM
    ranges, and the binary image."""
    out = run(["sdldz80", "-n", "-b", "DATA=0xf800", "-i", "-j", "-m",
               "-w", "ns.ihx", str(lib), "ns_user.rel", "ns_cmds.rel"], work)
    run(["sdobjcopy", "-I", "ihex", "-O", "binary", "ns.ihx", "ns.bin"], work)
    noi = (work / "ns.noi").read_text().splitlines()
    syms = ["%04X %s" % (int(p[2], 16) & 0xFFFF, p[1])
            for p in (ln.split() for ln in noi)
            if len(p) == 3 and p[0] == "DEF"]
    (work / "ns.sym").write_text("\n".join(syms) + "\n")
    return out


def check_bounds(src: Path, work: Path) -> str:
    """nullsound/check-bounds.py: no state range of nullsound crosses a
    256 byte boundary.  The C code adds its data after the nullsound data,
    so the ranges stay where they are."""
    return run([sys.executable, "-I", str(src / "check-bounds.py"),
                "ns.map"], work)


def expected_writes(count_before: int, n_keyon: int):
    """The (port, value) sequence of the key-on commands that the C code
    writes to the YM2610 port A, and the replies to the 68k."""
    seq = []
    count = count_before
    for _ in range(n_keyon):
        f = NOTE_FNUM[count % 12]
        for reg, val in ((0xA4, (4 << 3) | (f >> 8)), (0xA0, f & 0xFF),
                         (0x28, 0xF1)):
            seq += [(PORT_YM_A_ADDR, reg), (PORT_YM_A_VALUE, val)]
        count += 1
        seq.append((PORT_TO_68K, 0x40 | (count & 0x3F)))
    return seq


def execute(work: Path, ram_fill="random", seed=1):
    """Run ns.bin in romrun.  Sends the commands as NMIs with the 68k
    command byte on port 0.  Returns a dict with the port log and the
    machine."""
    from romrun import Machine
    m = Machine(rom_size=0x8000, ram=(0xF800, 0xFFFF), nmi_gate=True,
                ram_fill=ram_fill, ram_seed=seed, sp="poison", track=True)
    m.load_rom(str(work / "ns.bin"))
    m.load_symbols(str(work / "ns.sym"))
    # (time, command): the read of port 0 returns the last command whose
    # time has come.  An NMI at each time.
    plan = [(200_000, 0x02),        # start the driver, run the C hook
            (3_000_000, 0x04), (3_600_000, 0x04), (4_200_000, 0x04),
            (4_800_000, 0x05)]
    m.feed.steps(0x00, plan)
    for t, _cmd in plan:
        m.add_nmi_at(t)
    res = m.run(max_tstates=6_000_000)
    return dict(machine=m, result=res, plan=plan)


def check(run_info) -> list:
    """Compare the log with the expected sequence.  Returns the list of
    problems (empty when all is right)."""
    m, plan = run_info["machine"], run_info["plan"]
    log = m.port_log
    bad = []
    # 1. the hook (command 02h) answered 20h after the start of the driver
    t_hook = [t for (t, k, p, v) in log if k == "OUT" and (p & 0xFF) ==
              PORT_TO_68K and v == 0x20]
    if len(t_hook) != 1:
        bad.append(f"the hook reply 20h came {len(t_hook)} times, not once")
    # 2. the sequence of the C commands, from the first key-on NMI on
    t_first = plan[1][0]
    seq = []
    for (t, k, p, v) in log:
        p &= 0xFF
        if (k == "OUT" and t >= t_first
                and p in (PORT_YM_A_ADDR, PORT_YM_A_VALUE, PORT_TO_68K)):
            seq.append((p, v))
    # The main loop of nullsound writes register 1Ch (ADPCM-A flags) all
    # the time, and nullsound writes 84h or 85h to the 68k port when it
    # takes the command.  Take these away: they are not from the C code.
    pairs, i = [], 0
    while i < len(seq):
        if seq[i][0] == PORT_YM_A_ADDR and i + 1 < len(seq):
            if seq[i][1] != 0x1C:
                pairs += seq[i:i + 2]
            i += 2
        else:
            if not (seq[i][0] == PORT_TO_68K and seq[i][1] in (0x84, 0x85)):
                pairs.append(seq[i])
            i += 1
    seq = pairs
    want = expected_writes(0, 3)
    want += [(PORT_YM_A_ADDR, 0x28), (PORT_YM_A_VALUE, 0x01),
             (PORT_TO_68K, 0x80 | 2)]
    if seq != want:
        bad.append("port sequence differs:\n  got  %r\n  want %r" % (seq, want))
    if run_info["result"].reason not in ("budget", "idle"):
        bad.append("stop reason " + run_info["result"].reason)
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--keep", metavar="DIR", help="keep the build files here")
    ap.add_argument("--cache", metavar="DIR", default=str(CACHE),
                    help="where the nullsound sources are kept")
    args = ap.parse_args(argv)
    missing = tools_missing()
    if missing:
        print("missing tools: " + " ".join(missing), file=sys.stderr)
        return 2
    work = Path(args.keep or tempfile.mkdtemp(prefix="nullsound-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        src = fetch(Path(args.cache))
    except Unavailable as e:
        print(f"nullsound sources not available: {e}", file=sys.stderr)
        return 2
    lib = build_nullsound(src, work)
    build_user(src, work)
    print(link(lib, work).strip())
    print(check_bounds(src, work).strip() or "check-bounds.py: ok")
    info = execute(work)
    bad = check(info)
    m = info["machine"]
    print("nullsound %s: %d port events, stop %s, image %d bytes"
          % (COMMIT[:12], len(m.port_log), info["result"].reason,
             (work / "ns.bin").stat().st_size))
    for b in bad:
        print("FAIL " + b)
    if not bad:
        print("OK: the C commands wrote the expected YM2610 sequence")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
