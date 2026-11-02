#!/usr/bin/env python3
"""ab.py PROG [--obj x.mac]... [--mac prog_edit.mac] [--flags '...']
Build uc80 for check+time kinds; optionally replace program .mac text via --mac (file with full replacement for time kind only),
link extra modules (assembled with um80) before libc.lib; verify check output vs golden; print image + region cycles."""
import sys, os, shutil, subprocess, argparse
from pathlib import Path
WT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(WT/"bench"))
import run
OUT = Path(os.environ.get("AB_OUT", "/tmp/ab"))
ap = argparse.ArgumentParser()
ap.add_argument("prog"); ap.add_argument("--obj", action="append", default=[])
ap.add_argument("--flags", default=""); ap.add_argument("--tag", default="x")
ap.add_argument("--edit", default=None, help="python file with function edit(kind, text)->text applied to prog.mac")
ap.add_argument("--nocheck", action="store_true")
ap.add_argument("--odef", action="append", default=[])
a = ap.parse_args()
man = run.load_manifest()
prog = [p for p in man["program"] if p["name"] == a.prog][0]
repos = man.get("repo", {})
lib = Path(subprocess.run(["uc80","--print-lib-dir"],capture_output=True,text=True).stdout.strip())
gp = run.golden_path(prog)
golden = gp.read_text() if gp.exists() else ""
SIZEONLY = not gp.exists()
stdin = run.get_stdin(prog)
res = {}
for kind in ("check","time"):
    if kind=="check" and (a.nocheck or SIZEONLY): continue
    wd = OUT/a.prog/f"{a.tag}_{kind}"
    if wd.exists(): shutil.rmtree(wd)
    srcs = run.prepare_sources(prog, repos, wd)
    defines = run.build_defines(prog, kind)
    cc = ["uc80"]+[str(s) for s in srcs]+[f"-I{run.BENCH}",f"-I{wd}"]+[f"-D{d}" for d in defines]+a.flags.split()+["-o","prog.mac"]
    def sh(cmd):
        r = subprocess.run(cmd,cwd=wd,capture_output=True,text=True)
        if r.returncode: print("FAIL",cmd,r.stdout,r.stderr); sys.exit(1)
    sh(cc)
    if a.edit:
        import importlib.util
        spec = importlib.util.spec_from_file_location("ed", a.edit); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        t = (wd/"prog.mac").read_text(); (wd/"prog.mac").write_text(m.edit(kind,t))
    sh(["um80","prog.mac","-o","prog.rel"])
    objs=[]
    for o in a.obj:
        o=Path(o).resolve(); r=wd/(o.stem+".rel"); sh(["um80"]+sum([["-D",d] for d in a.odef],[])+[str(o),"-o",str(r)]); objs.append(str(r))
    sh(["ul80","prog.rel",*objs,str(lib/"libc.lib"),str(lib/"runtime.lib"),"-o","prog.com","-S","prog.sym"])
    syms={}
    for l in (wd/"prog.sym").read_text().splitlines():
        p=l.split()
        if len(p)==2: syms[p[1].upper()]=int(p[0],16)
    image = syms["__BSS_START"]-0x100
    com = wd/"prog.com"
    if kind=="check":
        out,tot = run.run_ticks(com, stdin=stdin, timeout=3000, expect=golden)
        ok = run.normalize(out)==run.normalize(golden)
        print(a.prog,a.tag,"check", "OK" if ok else "WRONG", "image",image)
    else:
        s,e = syms.get("TIMER_START"),syms.get("TIMER_STOP")
        _,cyc = run.run_ticks(com,s,e,stdin=stdin,timeout=3000)
        print(a.prog,a.tag,"time image",image,"cycles",cyc)
