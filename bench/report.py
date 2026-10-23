#!/usr/bin/env python3
"""Turn bench/results/results.csv into docs/benchmarks_speed.md.

    python3 bench/report.py

Never edit docs/benchmarks_speed.md by hand; change this script or rerun
bench/run.py instead.
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run import RESULTS, load_manifest  # noqa: E402

DOC = Path(__file__).resolve().parent.parent / "docs" / "benchmarks_speed.md"
ORDER = ["uc80", "sdcc", "sdcc-4.4", "z88dk-zsdcc", "z88dk-sccz80",
         "hitech", "dcc", "llvm-z80"]
TIERS = {0: "Baseline", 1: "Tier 1: classic benchmarks",
         2: "Tier 2: kernels", 3: "Tier 3: games and real programs"}


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def cell(r, base):
    if r is None:
        return "-"
    if r["status"] != "OK":
        return f"**{r['status']}**"
    img, cyc = num(r["image"]), num(r["cycles"])
    delta = img - base if img is not None and base is not None else None
    size = f"{delta:,}" if delta is not None else (f"{img:,}" if img else "?")
    if cyc is None:
        return size
    # A "total" count is the whole run, not the TIMER region: mark it so
    # that nobody compares it with the region counts of other compilers.
    mark = "†" if r.get("timing") == "total" else ""
    return f"{size} / {cyc:,}{mark}"


def label(p: dict, name: str) -> str:
    """The name the tables show: the manifest 'label', else the name."""
    return p.get("label", name)


def main() -> int:
    rows = list(csv.DictReader(open(RESULTS)))
    man = {p["name"]: p for p in load_manifest()["program"]}
    comps = sorted({r["compiler"] for r in rows},
                   key=lambda c: (ORDER.index(c) if c in ORDER else 99, c))
    by = {(r["program"], r["compiler"], r["mode"]): r for r in rows}
    versions = {}
    for r in rows:
        versions.setdefault(r["compiler"], r["version"])
    # Size of each compiler's empty program, per mode.
    base = {(r["compiler"], r["mode"]): num(r["image"])
            for r in rows if r["program"] == "empty" and r["status"] == "OK"}

    out = ["# Speed and Size Benchmarks", "",
           "Back to [benchmarks.md](benchmarks.md). This file is generated "
           "by `bench/report.py` from `bench/results/results.csv`. Do not "
           "edit this file by hand. The plan behind this file is in "
           "[benchmark_proposal.md](benchmark_proposal.md). How the "
           "harness builds, runs and measures each program is in "
           "[bench_harness.md](bench_harness.md).", "",
           "To make all of the numbers again:", "",
           "```bash", "python3 bench/run.py && python3 bench/report.py",
           "```", "",
           "## How to read the tables", "",
           "- Each cell is **bytes / T-states**.",
           "- Bytes: code and initialized data, minus the same compiler's "
           "empty program (the startup code and the library it always "
           "links). The BSS is not counted.",
           "- T-states: Z80 clock cycles from `z88dk-ticks`, between the "
           "`TIMER_START` and `TIMER_STOP` labels of the program.",
           "- \u2020 after the T-states: the count is for the whole run "
           "(start-up, printing and exit included), because the build has "
           "no TIMER labels. Do not compare such a count with the region "
           "counts of other compilers.",
           "- A run counts only if its output matches the golden file in "
           "`bench/golden/`. **WRONG** means wrong output, **HANG** means no "
           "exit, **BUILD-FAIL** means the compiler, assembler or linker "
           "failed, and **BUILD-TIMEOUT** means a build tool hit its time "
           "limit (1800 s; 600 s for the hitech and dcc tools). "
           "(check) or (time) tells which build failed. "
           "**WRONG(time)**: the time build has the same defines as the "
           "check build, and its output is wrong. **HANG(region)**: the "
           "program did not get from `TIMER_START` to `TIMER_STOP`.",
           "- Every compiler compiles the same source with the same "
           "defines. A program variant such as `sieve+static` adds a define "
           "for every compiler.",
           "- Each compiler has a \"size\" mode and a \"speed\" mode. uc80 "
           "has one mode only.", ""]

    for mode in ("size", "speed"):
        heads = comps
        out += [f"## {mode.capitalize()} mode", ""]
        if mode == "speed":
            out += ["uc80 has no speed mode, so its column repeats the size "
                    "mode result.", ""]
        tiers = defaultdict(list)
        for prog in dict.fromkeys(r["program"] for r in rows):
            tiers[int(man.get(prog, {}).get("tier", 1))].append(prog)
        for tier in sorted(tiers):
            out += [f"### {TIERS.get(tier, f'Tier {tier}')}", "",
                    "| Program | " + " | ".join(heads) + " |",
                    "|---|" + "---|" * len(heads)]
            for prog in sorted(tiers[tier]):
                cells = []
                for c in heads:
                    m = mode if (prog, c, mode) in by else "size"
                    r = by.get((prog, c, m))
                    b = base.get((c, m)) if prog != "empty" else None
                    cells.append(cell(r, b))
                name = label(man.get(prog, {}), prog)
                out.append(f"| {name} | " + " | ".join(cells) + " |")
            out.append("")

    out += ["## Programs", "", "| Program | Tier | What it measures |",
            "|---|---|---|"]
    for name, p in man.items():
        out.append(f"| {label(p, name)} | {p.get('tier', 1)} | "
                   f"{p.get('about', '')} |")
    out += ["", "## Compiler versions", ""]
    for c in comps:
        out.append(f"- **{c}**: {versions[c]}")
    out.append("")
    DOC.write_text("\n".join(out))
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
