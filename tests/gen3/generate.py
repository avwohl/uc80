"""Generate the 75 gen3 programs (5 value types x 3 pointer kinds x 5 bodies).

Usage: python3 tests/gen3/generate.py [outdir]   (default: this directory)
The programs h_<type>_<sp|cp|ip>_m<N>.c print three results of f().
They stress register locals: a scalar parameter in BC/DE with a pointer in IY.
"""
import sys
import pathlib
OUT = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).parent
import itertools, pathlib
P0 = {"int":("int","300"),"uint":("unsigned","40000u"),"sint":("short","-77"),"uchar":("unsigned char","201"),"schar":("signed char","-5")}
P1 = {"sp":("struct S *","&gs[%d]"),"cp":("unsigned char *","buf+%d"),"ip":("int *","ib+%d")}
cond = {"sp":"p1->u","cp":"p1[7]","ip":"p1[2]"}
bodies = {
 "m1": "if ((unsigned char)(gi ^ {c})) return (int)((long)(unsigned long)gi * -4095L - (long)p0 * (unsigned)p0); return (signed char)p0 | -4464;",
 "m2": "if ({c} > 3) return (int)((long)p0 * 70001L + gi); return (int)((long)p0 / 7 - gi);",
 "m3": "if ({c}) { long t = (long)p0 * p0; return (int)(t + gi + p0); } return p0 + 1;",
 "m4": "int r = 0; if ({c} & 1) r = (int)(((long)p0 << 5) % 1000); else r = (int)((unsigned long)p0 * gi); return r + p0;",
 "m5": "long a = (long)gi * p0; if ({c} == 0) a += p0; return (int)(a ^ (long)p0);",
}
cnt=0
for (n0,(t0,v0)),(n1,(t1,v1)) in itertools.product(P0.items(),P1.items()):
    for bn,b in bodies.items():
        body=b.replace("{c}",cond[n1])
        src=f'''#include <stdio.h>
struct S {{ signed char c; int i; long l; unsigned char u; }};
struct S gs[4]; unsigned char buf[16] = {{0,1,2,3,4,5,6,7,0,9,10,11,12,13,14,15}}; int ib[8] = {{0,1,2,3,0,5,6,7}};
int gi = 3;
int f({t0} p0, {t1} p1) {{ {body} }}
int main(void) {{ gs[1].u = 3; gs[2].u = 0; printf("%d %d %d\\n", f(({t0})({v0}), {v1.replace("%d","1") if n1!="sp" else "&gs[1]"}), f(({t0})({v0}), {"&gs[2]" if n1=="sp" else ("buf+8" if n1=="cp" else "ib+4")}), f(({t0})(5), {"&gs[1]" if n1=="sp" else ("buf+3" if n1=="cp" else "ib+1")})); return 0; }}
'''
        pathlib.Path(OUT / f"h_{n0}_{n1}_{bn}.c").write_text(src); cnt+=1
print(cnt)
