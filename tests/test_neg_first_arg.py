"""A 16-bit negation as argument 1 must not clobber argument 2.

pr(-g, h + 1): the negation used DE as scratch, and a register call holds
argument 2 in DE while argument 1 is computed, so argument 2 became the
un-negated first operand.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401

PROG = r"""
#include <stdio.h>
int g=1000,h=7; int arr[4]={1,2,3,4}; short s=-5; unsigned char uc=200;
int fn(int x){return x*3;}
void pr(int a,int b){printf("%d %d\n",a,b);}
long pl(long a,int b){printf("%ld %d\n",a,b);return 0;}
int main(void){
  int l1=11,l2=22;
  pr(-g,h+1); pr(-g,arr[2]); pr(-g,g&3); pr(-g,fn(h)); pr(-g,-h);
  pr(-(g+1),h+1); pr(-s,h+1); pr(-uc,h+1); pr(-(char)g,h+1); pr((short)-g,h+1);
  pr(-fn(g),h+1); pr(-arr[1],h+1); pr(-l1,l2+1); pr(-g,h); pr(~g,h+1);
  pr(-0,h+1); pr(-g, -(h+1)); pr(-(-g), h+2);
  pr(-l1, l2*2); pr(-arr[3], arr[1]-arr[2]);
  return 0;}
"""
EXPECTED = '-1000 8\n-1000 3\n-1000 0\n-1000 21\n-1000 -7\n-1001 8\n5 8\n-200 8\n24 8\n-1000 8\n-3000 8\n-2 8\n-11 23\n-1000 7\n-1001 8\n0 8\n-1000 -8\n1000 9\n-11 44\n-4 -1\n'


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-inlining",)])
def test_run(tmp_path, flags):
    out = build_and_run(tmp_path, PROG, flags)
    assert out.replace("\r", "") == EXPECTED, out
