#!/usr/bin/env python3
"""mergerows.py OUT.csv PART.csv...: join the CSV files of parallel bench/run.py runs."""
import csv, sys
rows = []; hdr = None
for f in sys.argv[2:]:
    r = list(csv.DictReader(open(f)))
    if r:
        hdr = hdr or list(r[0].keys())
        rows += r
w = csv.DictWriter(open(sys.argv[1], "w"), fieldnames=hdr)
w.writeheader(); w.writerows(rows)
print(len(rows), "rows")
