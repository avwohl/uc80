#!/usr/bin/env python3
"""gaps.py UC80.csv [SDCC.csv]: programs bigger than SDCC 4.6.0 (size mode,
image) or slower than SDCC (speed mode, T), field by field.  The sdcc rows
come from SDCC.csv (default bench/results/results.csv)."""
import csv, sys
from pathlib import Path
uc = {}
for r in csv.DictReader(open(sys.argv[1])):
    if r["compiler"] == "uc80":
        uc[r["program"]] = r
sd = {}
src = sys.argv[2] if len(sys.argv) > 2 else Path(__file__).resolve().parents[2] / "results" / "results.csv"
for r in csv.DictReader(open(src)):
    if r["compiler"] == "sdcc":
        sd[(r["program"], r["mode"])] = r
def n(v):
    try: return int(v)
    except Exception: return None
big = []; slow = []
for p, r in sorted(uc.items()):
    if r["status"] not in ("OK", "FITS"):
        continue
    s = sd.get((p, "size")); f = sd.get((p, "speed"))
    if s and s["status"] == "OK" and n(r["image"]) and n(s["image"]) and n(r["image"]) > n(s["image"]):
        big.append((p, n(r["image"]), n(s["image"])))
    if f and f["status"] == "OK" and n(r["cycles"]) and n(f["cycles"]) and n(r["cycles"]) > n(f["cycles"]):
        slow.append((p, n(r["cycles"]), n(f["cycles"])))
print("bigger than SDCC size mode:")
for b in big: print("  %-28s %7d %7d  %+d" % (b[0], b[1], b[2], b[1] - b[2]))
print("slower than SDCC speed mode:")
for b in slow: print("  %-28s %10d %10d  %.3f" % (b[0], b[1], b[2], b[1] / b[2]))
