#!/usr/bin/env python3
"""The gen3 family: 75 generated programs, 8 option sets each.

Usage: python3 bench/gen3_harness.py [-j N] [file.c ...]
       (default: every tests/gen3/*.c)

Each program is built with uc80 in 8 option sets and run under cpmemu.
  RLDIFF <file> <flags> | <output> | ref: <output>
      two uc80 builds give different output (a failure).  The reference is
      the --no-register-locals build.
  hostdiff(info) <file>
      the reference build differs from a host build (cc -O0 -fsigned-char).
      The host build is not the standard here (the programs use
      implementation-defined conversions), so this line is information.
The exit status is 1 when an RLDIFF or a BUILD-FAIL occurs.
The programs come from tests/gen3/generate.py.  tests/test_gen3_family.py
runs the same checks under pytest.
"""
import concurrent.futures as cf
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GEN3 = REPO / "tests" / "gen3"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"
VARS = [[], ["-O0"], ["--no-inlining"], ["--no-shared-storage"],
        ["--isr-safe"], ["--no-inlining", "--no-shared-storage"],
        ["-O0", "--no-shared-storage"], ["--no-register-locals"]]
REF = len(VARS) - 1


def _env():
    src = str(REPO / "src")
    old = os.environ.get("PYTHONPATH", "")
    return dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (src, old) if p))


def sh(cmd, cwd, timeout=120):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           errors="replace", timeout=timeout, env=_env(),
                           stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return -9, "timeout"


def lib_dir(w):
    return Path(sh([sys.executable, "-m", "uc80.main", "--print-lib-dir"], w)[1].strip())


def build_run(src, flags, work, lib):
    """Build src with uc80 and flags in the directory work, run it under cpmemu."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    steps = ([sys.executable, "-m", "uc80.main", str(src), "-o", "prog.mac"] + list(flags),
             ["um80", "prog.mac", "-o", "prog.rel"],
             ["ul80", "prog.rel", str(lib / "libc.lib"), str(lib / "runtime.lib"),
              "-o", "prog.com"])
    for cmd in steps:
        rc, out = sh(cmd, work)
        if rc:
            return "BUILD-FAIL " + out[-300:]
    try:
        p = subprocess.run([str(CPMEMU), "prog.com"], cwd=work, capture_output=True,
                           text=True, errors="replace", timeout=30,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    return p.stdout.replace("\r", "")


def host_run(src, work):
    """Build and run src with the host compiler.  None when there is no cc."""
    if not shutil.which("cc"):
        return None
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    rc, out = sh(["cc", "-O0", "-w", "-fsigned-char", "-DHOST", str(src), "-o", "h"], work)
    if rc:
        return "HOSTFAIL " + out[:200]
    return sh(["./h"], work, timeout=30)[1]


def main(argv):
    jobs = 6
    if argv[:1] == ["-j"]:
        jobs, argv = int(argv[1]), argv[2:]
    srcs = [Path(a).resolve() for a in argv] or sorted(GEN3.glob("*.c"))
    if not CPMEMU.exists():
        print("cpmemu not found:", CPMEMU)
        return 2
    base = Path(tempfile.mkdtemp(prefix="gen3_"))
    lib = lib_dir(base)
    try:
        with cf.ThreadPoolExecutor(jobs) as ex:
            futs = {(s, v): ex.submit(build_run, s, VARS[v], base / f"{s.stem}_{v}", lib)
                    for s in srcs for v in range(len(VARS))}
            hfuts = {s: ex.submit(host_run, s, base / f"{s.stem}_host") for s in srcs}
            res = {k: f.result() for k, f in futs.items()}
            hosts = {s: f.result() for s, f in hfuts.items()}
    finally:
        shutil.rmtree(base, ignore_errors=True)
    bad = 0
    for s in srcs:
        ref = res[(s, REF)].strip()
        if ref.startswith(("BUILD-FAIL", "TIMEOUT")):
            print("BUILD-FAIL", s.name, "(reference)", ref[:100].replace("\n", " / "))
            bad += 1
        for v in range(REF):
            got = res[(s, v)].strip()
            if got != ref:
                bad += 1
                print("RLDIFF", s.name, VARS[v], "|", got[:100].replace("\n", " / "),
                      "| ref:", ref[:100].replace("\n", " / "))
        if hosts[s] is not None and ref != hosts[s].strip():
            print("hostdiff(info)", s.name)
    print("done", len(srcs), "bad", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
