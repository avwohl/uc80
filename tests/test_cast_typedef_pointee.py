"""A cast to a pointer to a typedef name: the pointee is resolved.

``(**(F *)p)(x)``, ``((PT *)p)->b`` and ``(*(A3 *)p)[i]`` need the function,
struct or array type behind the typedef; the cast kept an opaque name, so the
second dereference loaded code bytes and the member offset was dropped.
"""

import pytest

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

PROG = r"""
#include <stdio.h>
typedef short (*F)(short);
typedef struct {short a; short b;} Q;
struct P{short a; short b;}; typedef struct P PT; typedef PT *PP;
typedef short A3[3];
short neg(short x){return -x;}
F gfp=neg;
Q q={1,2}; PT pt[3]={{1,2},{3,4},{5,6}}; PP gpp=pt; short a3[3]={7,8,9};
int main(void){
  void *vp=&gfp; void *vq=&q; void *vt=pt; void *vpp=&gpp; void *vs=a3;
  F *pf=&gfp; void *pq=&gfp;
  printf("%d\n", (*(*(F*)vp))(9));
  printf("%d\n", (**(F*)vp)(9));
  printf("%d\n", (**((F*)pq))(9));
  printf("%d\n", ((Q*)vq)->b);
  printf("%d %d\n", ((PT*)vt)->b, (*(PT*)vt).b);
  printf("%d %d\n", ((PT*)vt+1)->b, (*((PT*)vt+1)).b);
  printf("%d\n", (*(PP*)vpp)[1].b);
  printf("%d\n", (*(A3*)vs)[1]);
  printf("%d\n", ((PP)vt)->b);
  return 0;}
"""
EXPECTED = '-9\n-9\n-9\n2\n2 2\n4 4\n4\n8\n2\n'


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-inlining",)])
def test_run(tmp_path, flags):
    out = build_and_run(tmp_path, PROG, flags)
    assert out.replace("\r", "") == EXPECTED, out
