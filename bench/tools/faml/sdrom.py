#!/usr/bin/env python3
"""sdrom.py KERNEL MODE(speed|size): build a snd-* kernel as a ROM with standalone SDCC, run in romrun, print ROM size and T."""
import sys,subprocess,shutil
from pathlib import Path
WT=Path("/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/wt/famlan")
sys.path.insert(0,str(WT/"bench/rom")); sys.path.insert(0,str(WT/"bench"))
import sndrom
H=Path(__file__).parent/"rom"
kernel=sys.argv[1]; mode=sys.argv[2] if len(sys.argv)>2 else "speed"
work=H/f"{kernel}_{mode}"
if work.exists(): shutil.rmtree(work)
work.mkdir(parents=True)
c_name=sndrom.KERNELS[kernel]
src=(WT/"bench/programs"/kernel/c_name).read_text()
(work/c_name).write_text('#include "bench.h"\n'+src)
def sh(cmd):
    r=subprocess.run(cmd,cwd=work,capture_output=True,text=True)
    if r.returncode: print("FAIL",cmd,r.stdout[-1500:],r.stderr[-1500:]); sys.exit(1)
    return r.stdout+r.stderr
opt="--opt-code-speed" if mode=="speed" else "--opt-code-size"
sh(["sdasz80","-g","-o","crt0_rom.rel",str(H/"crt0_rom.s")])
sh(["sdasz80","-g","-o","z80io.rel",str(H/"z80io_sdcc.s")])
sh(["sdcc","-mz80",opt,"--max-allocs-per-node","200000","-I",str(WT/"bench/rom"),"-I",str(WT/"bench"),"-c",c_name,"-o","k.rel"])
sh(["sdcc","-mz80","--no-std-crt0","--code-loc","0x68","--data-loc","0xF800","crt0_rom.rel","z80io.rel","k.rel","-o","k.ihx"])
sh(["sdobjcopy","-I","ihex","-O","binary","k.ihx","k.bin"])
sndrom.noi_to_sym(work/"k.noi",work/"k.sym")
size=(work/"k.bin").stat().st_size
info=dict(ok=True,bin=work/"k.bin",sym=work/"k.sym",layout="neogeo")
res=sndrom.execute(info,kernel)
print(kernel,mode,"ROM bytes",size,{k:v for k,v in res.items() if k!="text"})
gold=(WT/"bench/golden"/f"{kernel}.txt").read_text()
print("output matches golden:", sndrom.golden.__name__ and res["text"].strip()==gold.strip())
