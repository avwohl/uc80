famm (family M analysis) tools.  Run `. patches/env.sh` first (edit the paths in it).
ab.py PROG --tag T [--edit patch.py] [--flags=...]   build check+time kinds, golden check, image, region T-states
  patches p_*.py: edit(kind, text) for ab.py (hand edits of the generated prog.mac); s_*.py: fix(workdir) source edits (AB_SRC_EDIT)
romab.py snd-cmdq PATCH  (ROMSRC=dm selects rom/uc/dm/k.mac): ROM build of a patched k.mac, run in romrun
lmods.py / sdmods.py / fn2.py / gapsum.py / table.py   per-module and per-function byte attribution, uc80 vs SDCC
combo.py OUT p1.py p2.py ...  compose patches
