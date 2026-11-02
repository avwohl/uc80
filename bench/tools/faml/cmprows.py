#!/usr/bin/env python3
"""cmprows.py BASE.csv NEW.csv: uc80 rows field by field (image, bss, com,
cycles, status) for every program in NEW that differs from BASE."""
import csv, sys
def load(p):
    d = {}
    for r in csv.DictReader(open(p)):
        if r["compiler"] == "uc80":
            d[(r["program"], r["mode"])] = r
    return d
a, b = load(sys.argv[1]), load(sys.argv[2])
done = {l.split()[0] for l in open(sys.argv[3]) if len(l.split()) > 3 and l.split()[1] == "uc80"} if len(sys.argv) > 3 else None
bigger = slower = same = better = 0
for k in sorted(b):
    if k not in a: continue
    if done is not None and k[0] not in done: continue
    ra, rb = a[k], b[k]
    d = []
    for f in ("status", "image", "bss", "com", "cycles", "total_cycles"):
        if ra[f] != rb[f]:
            try:
                d.append("%s %s->%s (%+d)" % (f, ra[f], rb[f], int(rb[f]) - int(ra[f])))
            except ValueError:
                d.append("%s %s->%s" % (f, ra[f], rb[f]))
    if d:
        print("%-28s %s" % (k[0], "; ".join(d)))
    else:
        same += 1
print("unchanged rows:", same)
