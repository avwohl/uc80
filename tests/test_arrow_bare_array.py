"""``arr->m`` with a bare array, and an inlined call with an array argument.

An array operand of ``->`` decays to a pointer to its first element.  The
address code took the struct from a pointer type only, so ``g1->s`` read the
member from the start of the element (the offset was dropped), writes went
to the wrong byte, and an inlined ``f(gs)`` for a struct-pointer parameter
read the wrong member.  Each program is run and compared with the C result.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401

PROG = r"""
#include <stdio.h>
struct P{char c; short s;};
struct S{char c; short i; long l;};
struct P g1[2]={{1,2},{3,4}};
struct P g2[2][2]={{{1,2},{3,4}},{{5,6},{7,8}}};
struct S gs[4];
struct H{struct P in[2]; int z;} gh={{{9,10},{11,12}},3};
static short f(struct S *p){return p->i;}
static long g(struct S *p){return p->l;}
int main(void){
  struct P loc[2]={{21,22},{23,24}};
  static struct P sl[2]={{31,32},{33,34}};
  int i=1;
  struct P gx[2]={{41,42},{43,44}};
  printf("%d %d\n", g1->s, (g1)->s);
  printf("%d %d\n", g2[i]->s, (*g2)->s);
  printf("%d\n", gh.in->s);
  printf("%d\n", (i?g1:gx)->s);
  printf("%d %d\n", loc->s, sl->s);
  g1->s=99; printf("%d %d\n", g1[0].c, g1[0].s);
  gs[0].c=5; gs[0].i=99; gs[0].l=100000L;
  gs->l+=3; printf("%ld\n", gs[0].l);
  (gs+0)->l-=1; printf("%ld\n", gs[0].l);
  (gs+0)->l+=1; gs->l--; printf("%ld\n", gs[0].l);
  printf("%d %ld\n", f(gs), g(gs));
  printf("%d %ld\n", f(gs+0), g(gs+0));
  printf("%d\n", ((struct P*)loc)->s);
  return 0;}
"""
EXPECTED = '2 2\n6 2\n10\n2\n22 32\n1 99\n100003\n100002\n100002\n99 100002\n99 100002\n22\n'


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-inlining",),
                                   ("--no-ast-optimize",)])
def test_arrow_on_bare_array(tmp_path, flags):
    out = build_and_run(tmp_path, PROG, flags)
    assert out.replace("\r", "") == EXPECTED, out
