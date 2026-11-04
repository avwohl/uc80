#!/usr/bin/env python3
"""romab.py KERNEL PATCHFILE|- : take uc80 ROM k.mac of KERNEL (rom/uc/KERNEL), apply PATCHFILE.edit(text)->text and
 optionally replace z80io, link as sndrom.build does, run in romrun; print ROM bytes and T."""
import sys,subprocess,shutil,re,importlib.util
from pathlib import Path
WT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(WT/"bench/rom")); sys.path.insert(0,str(WT/"bench"))
import sndrom
H=Path(__file__).parent/"rom"
k=sys.argv[1]; patch=sys.argv[2]; io=sys.argv[3] if len(sys.argv)>3 else None
src=H/"uc"/(__import__("os").environ.get("ROMSRC") or k); import os; work=Path(os.environ["A"])/"rab"/(k+"_"+Path(patch).stem); work.parent.mkdir(exist_ok=True)
if work.exists(): shutil.rmtree(work)
shutil.copytree(src,work)
text=(work/"k.mac").read_text()
if patch!="-":
    spec=importlib.util.spec_from_file_location("p",patch); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    text=m.edit(text)
(work/"k.mac").write_text(text)
lib=Path(subprocess.run(["uc80","--print-lib-dir"],capture_output=True,text=True).stdout.strip())
def sh(c):
    r=subprocess.run(c,cwd=work,capture_output=True,text=True)
    if r.returncode: print("FAIL",c,r.stdout[-1500:],r.stderr[-1500:]); sys.exit(1)
    return r.stdout+r.stderr
lay=sndrom.LAYOUTS["neogeo"]
objs=["k.rel"]
sh(["um80","k.mac","-o","k.rel"])
if io:
    shutil.copy(io,work/"io.mac"); sh(["um80","io.mac","-o","io.rel"]); objs.append("io.rel")
    # z80io.mac must not be linked twice: k.mac was built from c only? (sndrom linked z80io into k.mac)
out=sh(["ul80","--bin","-p","0","-d","%X"%lay["data"],"--rom-data","--rom-size","%X"%lay["rom_size"],"--ram-size","%X"%(0x10000-lay["data"]),"--min-stack","100","--verbose","-o","k.bin","-S","k.sym",*objs,str(lib/"libc_rom.lib"),str(lib/"runtime.lib")])
mm=re.search(r"ROM (\d+) of",out)
info=dict(ok=True,bin=work/"k.bin",sym=work/"k.sym",layout="neogeo")
res=sndrom.execute(info,k)
gold=(WT/"bench/golden"/f"{k}.txt").read_text()
print(k,Path(patch).stem,"ROM",mm.group(1) if mm else "?", "T",res["total_t"],"timed",res["timed_t"],"golden-match",res["text"].strip()==gold.strip())
