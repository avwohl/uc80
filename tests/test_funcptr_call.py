"""A call written ``(*f)(...)``, ``(**f)(...)`` or ``(&g)(...)``.

``*f`` of a function or of a pointer to a function is a function
designator, which decays to the pointer again.  The second ``*`` of
``(**f)(3)`` was a load of two bytes from the code of the function: the type
of ``*g`` for a function ``g`` was unknown, so the dereference read memory.
Every form is run and compared with the value the C rules give.
"""

import pytest

from tests.test_struct_return_abi import build_and_run  # noqa: F401
from tests.test_struct_return_abi import pytestmark  # noqa: F401

FORMS = r"""#include <stdio.h>
int g(int x){ return x*2+1; }
int h(int x){ return x+100; }
struct S { int (*fp)(int); int k; };
int (*tab[2])(int) = { g, h };
int main(void){
  int (*f)(int) = g;
  struct S s; s.fp = h; s.k = 5;
  struct S *ps = &s;
  printf("a %d\n", f(3));
  printf("b %d\n", (*f)(3));
  printf("c %d\n", (**f)(3));
  printf("d %d\n", (&g)(3));
  printf("e %d\n", (***f)(3));
  printf("f %d\n", s.fp(3));
  printf("g %d\n", (*s.fp)(3));
  printf("h %d\n", ps->fp(3));
  printf("i %d\n", (*ps->fp)(3));
  printf("j %d\n", tab[1](3));
  printf("k %d\n", (*tab[1])(3));
  printf("l %d\n", (*tab)(3));
  printf("m %d\n", (**tab)(3));
  printf("n %d\n", (*&g)(3));
  printf("o %d\n", (*g)(3));
  printf("p %d\n", (**g)(3));
  printf("q %d\n", (&*f)(3));
  printf("r %d\n", (f)(3));
  printf("s %d\n", (1?g:h)(3));
  printf("t %d\n", (*(0?g:h))(3));
  return 0;
}
"""

FORMS_EXPECTED = """a 7
b 7
c 7
d 7
e 7
f 103
g 103
h 103
i 103
j 103
k 103
l 7
m 7
n 7
o 7
p 7
q 7
r 7
s 7
t 103
"""

MORE = r"""#include <stdio.h>
typedef int (*fn_t)(int, int);
typedef void vfn(void);
int cnt;
int add(int a, int b){ return a+b; }
int sub(int a, int b){ return a-b; }
long lmul(long a, long b){ return a*b; }
char ch(char c){ return c+1; }
void bump(void){ cnt++; }
int zero(void){ return 42; }
struct R { int a, b, c; };
struct R mk(int x){ struct R r; r.a=x; r.b=x+1; r.c=x+2; return r; }
int (*pick(int w))(int, int){ return w ? add : sub; }
struct Ops { fn_t op; int (*z)(void); vfn *v; char (*c)(char); long (*lm)(long,long); } ops = { sub, zero, bump, ch, lmul };
struct Ops arr[2] = { { add, zero, bump, ch, lmul }, { sub, zero, bump, ch, lmul } };
int (*gtab[3])(int,int) = { add, sub, add };
int apply(int (*f)(int,int), int x, int y){ return (*f)(x,y) + f(x,y) + (**f)(x,y); }
int applyl(fn_t f, int x){ int r = 0; r += (*f)(x, 1); r += (*f)(r, 2); return r; }
int main(void){
  fn_t f = add;
  volatile int k = 5;
  int r;
  printf("1 %d\n", (*f)(k, 3));
  r = (*f)(k, 4); printf("2 %d\n", r);
  printf("3 %d\n", apply(sub, 10, 3));
  printf("4 %d\n", applyl(add, 7));
  (*ops.v)(); ops.v(); (*arr[1].v)(); arr[0].v(); (**arr[0].v)(); (*&bump)();
  printf("5 %d\n", cnt);
  printf("6 %d %d %d\n", (*ops.z)(), ops.z(), (*arr[1].z)());
  printf("7 %d %d\n", (*ops.op)(9,k), ops.op(9,k));
  printf("8 %d\n", (*arr[k-4].op)(9,k));
  printf("9 %ld\n", (*ops.lm)(100000L, 3L));
  printf("10 %d\n", (*ops.c)('a'));
  printf("11 %d\n", (*gtab[k-4])(20, k));
  printf("12 %d\n", (**gtab)(20, k));
  printf("13 %d\n", (*pick(1))(20, k));
  printf("14 %d\n", pick(0)(20, k));
  printf("15 %d\n", (*(*pick)(1))(1, 2));
  { struct R q = (*mk)(4); printf("16 %d %d %d\n", q.a, q.b, q.c); }
  { struct R q = (&mk)(5); printf("17 %d %d %d\n", q.a, q.b, q.c); }
  { struct R (*mp)(int) = mk; struct R q = (*mp)(6); printf("18 %d\n", q.c); }
  { struct Ops *p = &arr[1]; printf("19 %d %d\n", (*p->op)(8,k), (*(*p).op)(8,k)); }
  { fn_t *pp = &f; printf("20 %d\n", (**pp)(2,k)); }
  { fn_t *pp = gtab; printf("21 %d\n", (*pp[1])(2,k)); }
  { int (*(*ppf)(int))(int,int) = pick; printf("22 %d\n", (*(*ppf)(0))(2,k)); }
  { void (*vp)(void) = bump; (*vp)(); vp(); (**vp)(); printf("23 %d\n", cnt); }
  { int (*zp)(void) = zero; printf("24 %d %d %d\n", (*zp)(), (**zp)(), zp()); }
  return 0;
}
"""

MORE_EXPECTED = '1 8\n2 9\n3 21\n4 18\n5 6\n6 42 42 42\n7 4 4\n8 4\n9 300000\n10 98\n11 15\n12 25\n13 25\n14 15\n15 3\n16 4 5 6\n17 5 6 7\n18 8\n19 3 3\n20 7\n21 -3\n22 -3\n23 9\n24 42 42 42\n'


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-register-cc",),
                                   ("--no-inlining",)])
def test_every_spelling_of_a_function_pointer_call(tmp_path, flags):
    out = build_and_run(tmp_path, FORMS, flags)
    assert out.replace("\r", "") == FORMS_EXPECTED, out


@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-register-cc",)])
def test_calls_through_members_arrays_and_returned_pointers(tmp_path, flags):
    out = build_and_run(tmp_path, MORE, flags)
    assert out.replace("\r", "") == MORE_EXPECTED, out
