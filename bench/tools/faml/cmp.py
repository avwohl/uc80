import csv,sys
S='/private/tmp/claude-501/-Users-wohl-src-uc80/ec4c5501-bd11-4fd4-8f7f-ed83b93def9a/scratchpad/'
fresh=list(csv.DictReader(open(S+'faml_an/fresh.csv')))
old=list(csv.DictReader(open(S+'wt/famlan/bench/results/results.csv')))
def idx(rows):
    d={}
    for r in rows: d[(r['program'],r['compiler'],r['mode'])]=r
    return d
F=idx(fresh);O=idx(old)
def num(r,k):
    try: return int(r[k])
    except: return None
progs=sorted({r['program'] for r in fresh if r['compiler']=='uc80'})
print("SIZE: uc80 image > sdcc size-mode image")
for p in progs:
    u=F[(p,'uc80','size')]
    s=F.get((p,'sdcc','size')) or O.get((p,'sdcc','size'))
    if not s or s['status'] not in('OK','FITS'): continue
    ui=num(u,'image'); si=num(s,'image')
    if ui is not None and si is not None and ui>si: print(f"{p:32s} uc80 {ui:6d} sdcc {si:6d} +{ui-si} ({100*(ui/si-1):.1f}%)")
print("SPEED: uc80 cycles > sdcc speed cycles")
for p in progs:
    u=F[(p,'uc80','size')]
    s=F.get((p,'sdcc','speed')) or O.get((p,'sdcc','speed'))
    if not s or s['status']!='OK' or u['status']!='OK': continue
    uc=num(u,'cycles'); sc=num(s,'cycles')
    if uc and sc and uc>sc*0.97: print(f"{p:32s} uc80 {uc:>14d} sdcc {sc:>14d} ratio {uc/sc:.4f}")
