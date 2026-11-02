#!/usr/bin/env python3
"""bld.py PROG COMPILER(uc80|sdcc) [extra uc80 flags...]: build time variant into faml_an/w/PROG/COMPILER; print image, and region cycles."""
import sys, os, shutil
from pathlib import Path
WT = Path("/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan")
sys.path.insert(0, str(WT/"bench"))
import run
from compilers import Uc80, Sdcc
OUT = Path(__file__).parent/"w"
def main():
    name, comp = sys.argv[1], sys.argv[2]
    extra = sys.argv[3:]
    if extra: os.environ["UC80_BENCH_FLAGS"] = " ".join(extra)
    man = run.load_manifest()
    prog = [p for p in man["program"] if p["name"] == name][0]
    repos = man.get("repo", {})
    c = Uc80() if comp == "uc80" else Sdcc()
    mode = os.environ.get("BLD_MODE","size")
    wd = OUT/name/(comp + ("_" + mode if comp=="sdcc" and mode!="size" else "") + ("_" + "_".join(x.strip("-") for x in extra) if extra else ""))
    if wd.exists(): shutil.rmtree(wd)
    defines = run.build_defines(prog, "time")
    srcs = run.prepare_sources(prog, repos, wd)
    b = c.build(srcs, defines, [run.BENCH, wd], wd, mode)
    print(wd, b.ok, "image", b.image, "bss", b.bss)
    if not b.ok: print(b.log); return
    if "--run" in os.environ.get("BLD", ""):
        s, e = b.symbols.get("TIMER_START"), b.symbols.get("TIMER_STOP")
        out, tot = run.run_ticks(b.com, s, e, timeout=3000)
        print("region cycles", tot)
main()
