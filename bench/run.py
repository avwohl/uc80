#!/usr/bin/env python3
"""Build, run and check every benchmark program with every compiler.

    python3 bench/run.py                    # all programs, all compilers
    python3 bench/run.py sieve -c uc80 -c sdcc
    python3 bench/run.py --list

For each (program, compiler, mode) the runner makes two builds:

  check  the program with its check defines (usually -DPRINTF); the output
         under z88dk-ticks must match golden/<program>.txt
  time   the program with its time defines (usually -DTIMER, no printf);
         its .com size is the size result, and z88dk-ticks counts the
         T-states between the TIMER_START and TIMER_STOP labels

Results go to bench/results/results.csv (one row per program, compiler
and mode); bench/report.py turns that file into docs/benchmarks_speed.md.
docs/bench_harness.md describes the manifest keys and the statuses.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import fcntl
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compilers import (BENCH, TOOLS, TPA_LIMIT, TPA_STACK,  # noqa: E402
                       all_compilers, size_only_status)

CACHE = BENCH / "_cache"
WORK = BENCH / "_work"
# UC80_BENCH_RESULTS: write the rows to another file (an A/B run must not
# touch the committed results.csv)
RESULTS = Path(os.environ.get("UC80_BENCH_RESULTS",
                              BENCH / "results" / "results.csv"))
FIELDS = ["program", "tier", "compiler", "mode", "status", "image", "bss",
          "com", "check_com", "cycles", "total_cycles", "timing", "version",
          "command", "code", "data", "objects"]

# Check builds that passed in this process: (base program, compiler, mode,
# check defines) -> .com size.  A variant whose check build is the same as
# one already checked (a time_only variant, for example) reuses the result.
CHECKED: dict[tuple, int] = {}


def ticks_path() -> Path:
    return Path(os.environ.get(
        "Z88DK", TOOLS / "z88dk-2.4" / "z88dk")) / "bin" / "z88dk-ticks"


def load_manifest() -> dict:
    """Read programs.toml; each variant becomes its own program entry.

    A variant adds defines (for example -DSTATIC, which the z88dk readmes
    give some compilers).  Every compiler runs every variant, so no
    compiler gets a switch the others do not.  A variant can have its own
    "about" text, and "time_only = true" when its defines only move the
    timed region (the check build then omits them).
    """
    with open(BENCH / "programs.toml", "rb") as f:
        man = tomllib.load(f)
    # One fragment per program (or group) in programs.d/, so programs can
    # be added independently.
    for frag in sorted((BENCH / "programs.d").glob("*.toml")):
        with open(frag, "rb") as f:
            extra = tomllib.load(f)
        man.setdefault("program", []).extend(extra.get("program", []))
        for k, v in extra.get("repo", {}).items():
            man.setdefault("repo", {}).setdefault(k, v)
    progs = []
    for p in man["program"]:
        progs.append(p)
        for v in p.get("variants", []):
            q = dict(p, name=f"{p['name']}+{v['name']}",
                     variant=v["defines"], base=p["name"],
                     variant_time_only=v.get("time_only", False))
            q.pop("variants")
            if "about" in v:
                q["about"] = v["about"]
            if "label" in v or "label" in p:
                q["label"] = v.get("label", f"{p.get('label')}+{v['name']}")
            progs.append(q)
    man["program"] = progs
    return man


def fetch(source: dict, repos: dict) -> Path:
    """Return a local copy of a pinned upstream file, checking sha256."""
    repo = repos[source["repo"]]
    dest = CACHE / source["repo"] / repo["commit"] / source["path"]
    if not dest.exists():
        url = repo["raw"].format(commit=repo["commit"],
                                 path=urllib.parse.quote(source["path"]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=60) as r:
            dest.write_bytes(r.read())
    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    if digest != source["sha256"]:
        raise SystemExit(f"sha256 mismatch for {source['path']}: {digest}")
    return dest


def prepare_sources(prog: dict, repos: dict, dest: Path) -> list[Path]:
    """Copy (or fetch) the program's files into `dest` and apply edits.

    A files entry is {repo, path, sha256} (pinned upstream file),
    {path} (relative to bench/programs/<program>/) or {shared} (relative
    to bench/programs/, for a file that several programs use).  Every
    file lands flat in `dest` under its base name; an upper-case .C
    suffix becomes .c.
    """
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for f in prog["files"]:
        if "repo" in f:
            src = fetch(f, repos)
            name = Path(f["path"]).name
        elif "shared" in f:
            # Relative to bench/programs/, else to bench/ (for example
            # "support/posix_shim.c").
            src = BENCH / "programs" / f["shared"]
            if not src.exists():
                src = BENCH / f["shared"]
            name = Path(f["shared"]).name
        else:
            src = BENCH / "programs" / prog.get("base", prog["name"]) / f["path"]
            name = Path(f["path"]).name
        target = dest / name
        if target.suffix == ".C":
            target = target.with_suffix(".c")
        text = src.read_text(encoding="latin-1")
        for e in prog.get("edit", []):
            if e["file"] in (name, target.name):
                if text.count(e["old"]) != 1:
                    raise SystemExit(f"{prog['name']}: edit {e['old']!r} "
                                     f"matches {text.count(e['old'])} times")
                text = text.replace(e["old"], e["new"])
        if target.suffix == ".c" and f.get("main", False):
            text = '#include "bench.h"\n' + text
        target.write_text(text, encoding="latin-1")
        if target.suffix == ".c":
            out.append(target)
    return out


def get_stdin(prog: dict) -> str:
    """The standard input of a program: stdin_file (a file in
    bench/programs/<program>/) or the stdin text, else nothing."""
    if "stdin_file" in prog:
        src = (BENCH / "programs" / prog.get("base", prog["name"])
               / prog["stdin_file"])
        return src.read_text(encoding="latin-1")
    return prog.get("stdin", "")


def normalize(text: str) -> str:
    # A TAB and the spaces up to the next tab stop are the same text: a CP/M
    # BDOS (and the uc80 library) writes the spaces, a host cc the TAB.
    lines = [l.expandtabs(8).rstrip()
             for l in text.replace("\r", "").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def run_ticks(com: Path, start: int | None = None, end: int | None = None,
              stdin: str = "", timeout: float = 3600,
              expect: str | None = None) -> tuple[str, int | None]:
    """Run a .com under z88dk-ticks; return (program output, T-states).

    The count is None when ticks times out or prints no count.  ticks runs
    in the image's directory with the bare name "prog.com": it picks CP/M
    mode by the FIRST dot in its argument (ticks_main.c), so a dot in a
    directory name (sdcc-4.4, n-body+static/...) would make it run the
    image without BDOS, and it would never stop.

    ticks prints the count straight after the last character of the
    program output.  When the output ends in a digit with no newline, the
    two run together; with `expect` (the golden text) the split that makes
    the output equal `expect` wins.
    """
    cmd = [str(ticks_path()), "-l", "100", "-pc", "100"]
    if start is not None:
        cmd += ["-start", f"{start:x}", "-end", f"{end:x}"]
    cmd += ["-counter", "999999999999", com.name]
    try:
        p = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                           timeout=timeout, errors="replace", cwd=com.parent)
    except subprocess.TimeoutExpired:
        return "", None
    if expect is not None:
        want = normalize(expect).rstrip("\n")
        got = normalize(p.stdout).rstrip("\n")
        rest = got[len(want):]
        if got.startswith(want) and rest.isdigit():
            return expect, int(rest)
    out = p.stdout.rstrip()
    m = re.search(r"(\d+)\s*$", out)
    if not m:
        return p.stdout, None
    return out[:m.start()], int(m.group(1))


@contextlib.contextmanager
def cell_lock(path: Path):
    """Hold an exclusive lock on one work directory of one cell.

    Two runs of the same (program, compiler, mode) would otherwise share
    bench/_work/<program>/<compiler>/<mode>/ and delete each other's
    files.  The second run waits, then does its own builds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"waiting: another run uses {path}", file=sys.stderr,
                  flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def golden_path(prog: dict) -> Path:
    """golden/<variant>.txt when it exists, else golden/<base>.txt."""
    own = BENCH / "golden" / f"{prog['name']}.txt"
    if "base" in prog and own.exists():
        return own
    return BENCH / "golden" / f"{prog.get('base', prog['name'])}.txt"


def build_defines(prog: dict, kind: str) -> list[str]:
    variant = prog.get("variant", [])
    if kind == "check" and prog.get("variant_time_only"):
        variant = []
    return prog.get(f"{kind}_defines", []) + variant


def bench_one(prog, comp, mode, repos, version) -> dict:
    with cell_lock(WORK / prog["name"] / comp.name / mode):
        if prog.get("kind") == "size-only":
            return _size_only_one(prog, comp, mode, repos, version)
        return _bench_one(prog, comp, mode, repos, version)


def _size_only_one(prog, comp, mode, repos, version) -> dict:
    """Build a size-only program: no run, no golden file.

    Two builds: the per-object build (comp.objects: each source is
    compiled alone, and the code size of each object is read from the
    object, the listing or the map) and the whole-program link (the
    usual comp.build).  The status is FITS when the program links and
    its image, its BSS and TPA_STACK bytes of stack end below TPA_LIMIT;
    TOO-BIG when it links and is larger, or when the linker stops
    because the program is too large; BUILD-FAIL(objects) or
    BUILD-FAIL(link) otherwise.  The per-object sizes are in the row
    even when the link fails.
    """
    row = {"program": prog["name"], "tier": prog.get("tier", 3),
           "compiler": comp.name, "mode": mode, "version": version}
    defines = build_defines(prog, "time")
    work = WORK / prog["name"] / comp.name / mode
    for kind in ("objects", "link"):
        wd = work / kind
        if wd.exists():
            shutil.rmtree(wd)
        srcs = prepare_sources(prog, repos, wd)
        if kind == "objects":
            o = comp.objects(srcs, defines, [BENCH, wd], wd, mode)
            (wd / "build.log").write_text(o.log)
            if not o.ok:
                timed_out = re.search(r"^timeout after \d+s", o.log, re.M)
                row["status"] = ("BUILD-TIMEOUT(objects)" if timed_out
                                 else "BUILD-FAIL(objects)")
                return row
            row["code"] = sum(c for _, c, _ in o.objects)
            row["data"] = sum(d for _, _, d in o.objects)
            row["objects"] = ";".join(f"{n}={c}/{d}"
                                      for n, c, d in o.objects)
            continue
        b = comp.build(srcs, defines, [BENCH, wd], wd, mode)
        (wd / "build.log").write_text(b.log)
        row["command"] = b.command
        if b.ok:
            row["com"], row["image"], row["bss"] = (b.com.stat().st_size,
                                                    b.image, b.bss)
            end = 0x100 + (b.image or 0) + (b.bss or 0)
            row["status"] = ("FITS" if end + TPA_STACK <= TPA_LIMIT
                             else "TOO-BIG")
        else:
            timed_out = re.search(r"^timeout after \d+s", b.log, re.M)
            row["status"] = ("BUILD-TIMEOUT(link)" if timed_out
                             else size_only_status(b.log))
    return row


def _bench_one(prog, comp, mode, repos, version) -> dict:
    row = {"program": prog["name"], "tier": prog.get("tier", 1),
           "compiler": comp.name, "mode": mode, "version": version}
    golden = golden_path(prog).read_text()
    stdin = get_stdin(prog)
    limit = prog.get("timeout", 600)
    same = build_defines(prog, "check") == build_defines(prog, "time")
    for kind in ("check", "time"):
        defines = build_defines(prog, kind)
        key = (prog.get("base", prog["name"]), comp.name, mode,
               tuple(defines), golden_path(prog).name)
        work = WORK / prog["name"] / comp.name / mode / kind
        if kind == "check" and key in CHECKED:
            row["check_com"] = CHECKED[key]
            if work.exists():
                shutil.rmtree(work)
            work.mkdir(parents=True)
            (work / "build.log").write_text(
                "same check build as an earlier program in this run; "
                "not built again\n")
            continue
        if work.exists():
            shutil.rmtree(work)
        srcs = prepare_sources(prog, repos, work)
        b = comp.build(srcs, defines, [BENCH, work], work, mode)
        (work / "build.log").write_text(b.log)
        if kind == "time":
            row["command"] = b.command
        if not b.ok:
            timed_out = re.search(r"^timeout after \d+s", b.log, re.M)
            row["status"] = (f"BUILD-TIMEOUT({kind})" if timed_out
                             else f"BUILD-FAIL({kind})")
            return row
        size = b.com.stat().st_size
        if kind == "check":
            row["check_com"] = size
            out, total = run_ticks(b.com, stdin=stdin, timeout=limit,
                                   expect=golden)
            (work / "output.txt").write_text(out)
            if normalize(out) != normalize(golden):
                row["status"] = "WRONG" if total is not None else "HANG"
                return row
            CHECKED[key] = size
            continue
        row["com"], row["image"], row["bss"] = size, b.image, b.bss
        t0 = time.monotonic()
        out, total = run_ticks(b.com, stdin=stdin, timeout=limit,
                               expect=golden if same else None)
        elapsed = time.monotonic() - t0
        (work / "output.txt").write_text(out)
        row["total_cycles"] = total
        if total is None:
            row["status"] = "HANG(time)"
            return row
        # The time build has the check build's defines, so its output
        # must match the golden file too.
        if same and normalize(out) != normalize(golden):
            row["status"] = "WRONG(time)"
            return row
        s, e = b.symbols.get("TIMER_START"), b.symbols.get("TIMER_STOP")
        # timing = "total" (games): the count of the whole run, even when
        # the program has TIMER labels.
        if (s is not None and e is not None
                and prog.get("timing") != "total"):
            # ticks with -start/-end runs until the end label; when the
            # program never reaches it, ticks never stops.  The region is
            # part of the whole run, so a few times the time of the whole
            # run is enough.
            region_limit = min(limit, max(60.0, 5 * elapsed))
            _, cyc = run_ticks(b.com, s, e, stdin=stdin,
                               timeout=region_limit)
            if cyc is None:
                row["status"] = "HANG(region)"
                return row
            row["cycles"], row["timing"] = cyc, "region"
        else:
            row["cycles"], row["timing"] = total, "total"
    row["status"] = "OK"
    return row


def make_golden(prog: dict, repos: dict) -> None:
    """Build the check variant with the host cc and save its output.

    A variant gets its own golden/<variant>.txt only when its output
    differs from the golden file of its base program.
    """
    work = WORK / prog["name"] / "host"
    with cell_lock(work):
        if work.exists():
            shutil.rmtree(work)
        srcs = prepare_sources(prog, repos, work)
        cmd = (["cc", "-w", "-O1", f"-I{BENCH}", f"-I{work}"]
               + [f"-D{d}" for d in build_defines(prog, "check")
                  + prog.get("defines", {}).get("host", [])]
               + [str(s) for s in srcs] + ["-o", str(work / "host"), "-lm"])
        subprocess.run(cmd, check=True)
        out = subprocess.run([str(work / "host")], input=get_stdin(prog),
                             capture_output=True, text=True,
                             check=False).stdout
    (BENCH / "golden").mkdir(exist_ok=True)
    dest = BENCH / "golden" / f"{prog['name']}.txt"
    if "base" in prog:
        base = BENCH / "golden" / f"{prog['base']}.txt"
        if base.exists() and normalize(base.read_text()) == normalize(out):
            if dest.exists():
                dest.unlink()
            print(f"golden/{prog['name']}.txt: same as golden/{base.name}")
            return
    dest.write_text(normalize(out))
    print(f"golden/{prog['name']}.txt: {len(out.splitlines())} lines")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("programs", nargs="*")
    ap.add_argument("-c", "--compiler", action="append")
    ap.add_argument("-m", "--mode", action="append",
                    choices=["size", "speed"])
    ap.add_argument("--tier", type=int, action="append")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--golden", action="store_true",
                    help="write golden/<program>.txt from a host cc build")
    args = ap.parse_args()
    # A SIGTERM ends the run like Ctrl-C: subprocess.run then kills the
    # compiler or z88dk-ticks child, so no child keeps running.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

    man = load_manifest()
    known = {p["name"] for p in man["program"]}
    for name in args.programs:
        if name not in known:
            print(f"warning: no program named {name!r} (a list of names in "
                  "one argument? zsh does not split $VAR into words)",
                  file=sys.stderr)
    progs = [p for p in man["program"]
             if (not args.programs or p["name"] in args.programs)
             and (not args.tier or p.get("tier", 1) in args.tier)]
    allc = all_compilers()
    for name in args.compiler or []:
        if name not in {c.name for c in allc}:
            print(f"warning: no compiler named {name!r}", file=sys.stderr)
    comps = [c for c in allc if not args.compiler or c.name in args.compiler]
    if args.list:
        for p in man["program"]:
            print(f"{p['name']}\ttier {p.get('tier', 1)}"
                  f"\t{p.get('kind', 'run')}\t{p.get('about', '')}")
        for c in allc:
            print(f"{c.name}\t{'available' if c.available() else 'missing'}")
        return 0
    if not progs or not comps:
        print("nothing to run: the filters match no program or no compiler",
              file=sys.stderr)
        return 2
    if args.golden:
        for p in progs:
            if p.get("kind") == "size-only":
                print(f"{p['name']}: size-only, no golden file")
                continue
            make_golden(p, man.get("repo", {}))
        return 0
    if not ticks_path().exists():
        raise SystemExit(f"z88dk-ticks not found at {ticks_path()}")

    for comp in comps:
        if not comp.available():
            print(f"skip {comp.name}: not installed", file=sys.stderr)
            continue
        version = comp.version()
        modes = [m for m in comp.modes if not args.mode or m in args.mode]
        for prog in progs:
            for mode in modes:
                row = bench_one(prog, comp, mode, man.get("repo", {}),
                                version)
                save_rows({(row["program"], row["compiler"], row["mode"]): row})
                print(f"{row['program']:<16} {comp.name:<14} {mode:<6} "
                      f"{row['status']:<18} image={row.get('image', '-')} "
                      f"cycles={row.get('cycles', '-')}", flush=True)
    return 0


def save_rows(rows: dict) -> None:
    """Merge rows into results.csv under a lock (runs may be parallel)."""
    RESULTS.parent.mkdir(exist_ok=True)
    with open(RESULTS.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        old = {}
        if RESULTS.exists():
            with open(RESULTS) as f:
                for r in csv.DictReader(f):
                    old[(r["program"], r["compiler"], r["mode"])] = r
        old.update(rows)
        tmp = RESULTS.with_suffix(".tmp")
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
            w.writeheader()
            for key in sorted(old):
                w.writerow(old[key])
        tmp.replace(RESULTS)


if __name__ == "__main__":
    sys.exit(main())
