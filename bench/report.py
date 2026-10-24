#!/usr/bin/env python3
"""Turn bench/results/results.csv into docs/benchmarks_speed.md.

    python3 bench/report.py

Never edit docs/benchmarks_speed.md by hand; change this script or rerun
bench/run.py instead.
"""

from __future__ import annotations

import csv
import math
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


def geomean(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs))


def pairs(by, other, omode, field, prog_ok, delta, region=False):
    """(program, uc80 value, other value) for the programs OK on both.

    delta: subtract each compiler's empty program (the table bytes).
    region: keep only programs where both counts are TIMER-region counts.
    """
    out = []
    for prog in prog_ok:
        a, b = by.get((prog, "uc80", "size")), by.get((prog, other, omode))
        if not a or not b or a["status"] != "OK" or b["status"] != "OK":
            continue
        if region and (a["timing"] != "region" or b["timing"] != "region"):
            continue
        x, y = num(a[field]), num(b[field])
        if delta:
            ea = by.get(("empty", "uc80", "size"))
            eb = by.get(("empty", other, omode))
            if not ea or not eb:
                continue
            x, y = x - num(ea["image"]), y - num(eb["image"])
        if x and y and x > 0 and y > 0:
            out.append((prog, x, y))
    return out


def stats(pr):
    """Count, geometric mean of uc80/other, wins, losses, ties."""
    if not pr:
        return 0, None, 0, 0, 0
    r = [x / y for _, x, y in pr]
    return (len(r), geomean(r), sum(v < 1 for v in r),
            sum(v > 1 for v in r), sum(v == 1 for v in r))


def fmt(v):
    return "-" if v is None else f"{v:.2f}"


def summary(by, progs, comps):
    """Markdown lines: uc80 against each other compiler."""
    ok = [p for p in progs if p != "empty"]
    nosort = [p for p in ok if not p.startswith("sort-")]
    out = ["## uc80 against the other compilers", "",
           "A ratio is uc80 divided by the other compiler, so a ratio "
           "below 1.00 means uc80 is smaller or faster. The mean is the "
           "geometric mean over the programs that are OK on both "
           "compilers. Wins is the number of programs where uc80 is "
           "better, losses where uc80 is worse or equal. uc80 always uses "
           "its only mode. The `sort-*` programs time the library "
           "`qsort()` of each compiler, not the code that the compiler "
           "makes. The `qsort()` of SDCC is far slower than the one of "
           "uc80 on some inputs, so the T-state means are also shown "
           "without the `sort-*` programs.", "",
           "### Size", "",
           "Bytes as in the tables (minus the empty program of each "
           "compiler), and the whole image.", "",
           "| Other compiler (mode) | Programs | Mean bytes | Wins | "
           "Losses | Mean whole image |",
           "|---|---|---|---|---|---|"]
    for c in comps:
        if c == "uc80":
            continue
        n, g, w, l, t = stats(pairs(by, c, "size", "image", ok, True))
        _, g2, _, _, _ = stats(pairs(by, c, "size", "image", ok, False))
        out.append(f"| {c} (size) | {n} | {fmt(g)} | {w} | {l + t} | "
                   f"{fmt(g2)} |")
    out += ["", "### T-states", "",
            "Only programs where both counts are TIMER-region counts.", "",
            "| Other compiler (mode) | Programs | Mean | Wins | Losses | "
            "Programs without sort-* | Mean without sort-* | Wins | "
            "Losses |", "|---|---|---|---|---|---|---|---|---|"]
    for c in comps:
        if c == "uc80":
            continue
        for om in ("speed", "size"):
            a = stats(pairs(by, c, om, "cycles", ok, False, True))
            b = stats(pairs(by, c, om, "cycles", nosort, False, True))
            out.append(f"| {c} ({om}) | {a[0]} | {fmt(a[1])} | {a[2]} | "
                       f"{a[3] + a[4]} | {b[0]} | {fmt(b[1])} | {b[2]} | "
                       f"{b[3] + b[4]} |")
    out += ["", "### uc80 against SDCC 4.6.0 (size mode), by program", "",
            "| Program | uc80 bytes | sdcc bytes | Ratio | uc80 T-states "
            "| sdcc T-states | Ratio |", "|---|---|---|---|---|---|---|"]
    eu = num(by[("empty", "uc80", "size")]["image"])
    es = num(by[("empty", "sdcc", "size")]["image"])
    for p in ok:
        a, b = by.get((p, "uc80", "size")), by.get((p, "sdcc", "size"))
        if not a or not b or a["status"] != "OK" or b["status"] != "OK":
            continue
        sa, sb = num(a["image"]) - eu, num(b["image"]) - es
        ca, cb = num(a["cycles"]), num(b["cycles"])
        both = a["timing"] == "region" and b["timing"] == "region"
        tr = f"{ca / cb:.2f}" if both and ca and cb else "-"
        out.append(f"| {p} | {sa:,} | {sb:,} | {sa / sb:.2f} | {ca:,} | "
                   f"{cb:,} | {tr} |")
    bad = [p for p in ok
           if (p, "uc80", "size") in by
           and by[(p, "uc80", "size")]["status"] != "OK"]
    out += ["", "### Programs that fail on uc80", ""]
    out += [f"- {p}: {by[(p, 'uc80', 'size')]['status']}" for p in bad]
    out.append("")
    return out


def size_only_cell(r, which):
    """A cell of a size-only table: "fit" (status and linked bytes) or
    "obj" (per-object code bytes / code plus data bytes)."""
    if r is None:
        return "-"
    code, data = num(r.get("code")), num(r.get("data"))
    if which == "obj":
        if code is None:
            return f"**{r['status']}**"
        return f"{code:,} / {code + data:,}"
    st = r["status"] if r["status"] in ("FITS", "TOO-BIG") \
        else f"**{r['status']}**"
    img, bss = num(r.get("image")), num(r.get("bss"))
    if img is None:
        return st
    return f"{st} ({img + bss:,})"


def size_only_tables(by, man, comps, names):
    """Markdown lines: one fit table and one per-object table for each
    mode, for the programs with kind = "size-only"."""
    out = ["## Size-only programs", "",
           "These programs are built only. They are not run, and they have "
           "no golden file. Each compiler builds them in two ways. The "
           "per-object build compiles each source file alone and adds the "
           "sizes of the objects. The whole-program link builds the "
           "program as usual and tells whether it fits in the TPA of a "
           "64 KB CP/M 2.2 system (addresses 0100h to E3FFh, minus 512 "
           "bytes of stack). The method for each compiler is in "
           "[bench_harness.md](bench_harness.md#size-only-programs).", ""]
    for mode in ("size", "speed"):
        if not any((p, c, mode) in by for p in names for c in comps):
            continue
        out += [f"### Fit in the TPA ({mode} mode)", "",
                "**FITS**: the program links, and its image and BSS fit. "
                "**TOO-BIG**: the program is larger (or the linker says "
                "so). **BUILD-FAIL(objects)** or **BUILD-FAIL(link)**: a "
                "compiler, assembler or linker step failed. A number is "
                "the bytes of image and BSS of the linked program, "
                "including start-up code and library.", "",
                "| Program | " + " | ".join(comps) + " |",
                "|---|" + "---|" * len(comps)]
        for prog in names:
            cells = [size_only_cell(by.get((prog, c, mode if (prog, c, mode)
                                                  in by else "size")), "fit")
                     for c in comps]
            out.append(f"| {label(man.get(prog, {}), prog)} | "
                       + " | ".join(cells) + " |")
        out += ["", f"### Size of the objects ({mode} mode)", "",
                "Each cell is **code / code plus data**, in bytes, summed "
                "over the objects of the program, without start-up code "
                "and library. The compilers differ in where strings and "
                "constant tables count, so compare the second number. "
                "The sizes of each object are in the `objects` column of "
                "`results.csv`.", "",
                "| Program | " + " | ".join(comps) + " |",
                "|---|" + "---|" * len(comps)]
        for prog in names:
            cells = [size_only_cell(by.get((prog, c, mode if (prog, c, mode)
                                                  in by else "size")), "obj")
                     for c in comps]
            out.append(f"| {label(man.get(prog, {}), prog)} | "
                       + " | ".join(cells) + " |")
        out.append("")
    return out


def label(p: dict, name: str) -> str:
    """The name the tables show: the manifest 'label', else the name."""
    return p.get("label", name)


def main() -> int:
    rows = list(csv.DictReader(open(RESULTS)))
    man = {p["name"]: p for p in load_manifest()["program"]}
    comps = sorted({r["compiler"] for r in rows},
                   key=lambda c: (ORDER.index(c) if c in ORDER else 99, c))
    by = {(r["program"], r["compiler"], r["mode"]): r for r in rows}
    # Size-only programs have their own tables (size_only_tables).
    sizeonly = [n for n, p in man.items() if p.get("kind") == "size-only"
                and any(r["program"] == n for r in rows)]
    run_rows = [r for r in rows if r["program"] not in sizeonly]
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
           "has one mode only.",
           "- Games (tier 3) read a script from standard input and have no "
           "TIMER labels (`timing = total` in the manifest). Their "
           "T-states are the count of the whole run, so they all have the "
           "\u2020 mark. Their random numbers and clock come from "
           "`bench/support/posix_shim.c`, so each run is the same on every "
           "compiler.",
           "- Programs with `kind = \"size-only\"` are not in these "
           "tables. They have their own tables in the section "
           "\"Size-only programs\".", ""]

    for mode in ("size", "speed"):
        heads = comps
        out += [f"## {mode.capitalize()} mode", ""]
        if mode == "speed":
            out += ["uc80 has no speed mode, so its column repeats the size "
                    "mode result.", ""]
        tiers = defaultdict(list)
        for prog in dict.fromkeys(r["program"] for r in run_rows):
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

    out += summary(by, list(dict.fromkeys(r["program"] for r in run_rows)),
                   comps)
    if sizeonly:
        out += size_only_tables(by, man, comps, sizeonly)
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
