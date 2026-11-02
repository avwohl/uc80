"""L3: a call of a function with an empty body is deleted when every
argument is free of side effects (docs/family_l.md).  The tests compare the
output of uc80 under cpmemu with the host compiler."""

from ._faml import needs_toolchain, run_both

pytestmark = needs_toolchain

SRC = r"""
#include <stdio.h>
#include <stdarg.h>
static int counter;
volatile int vport;
static void dbg(const char *fmt, ...)
{
}
static void nop(void) { }
static void two(int a, int b) { (void) a; }
static int bump(void) { return ++counter; }
static void hook(int x) { }
typedef void (*fn_t)(int);
static fn_t table[2] = { hook, hook };
static void shadowed(int v) { }
static void use_shadow(void (*shadowed)(int))
{
    shadowed(1);
}
static void report(int v) { printf("report %d\n", v); }
int main(void)
{
    int i, x = 4;
    dbg("a %d %s\n", x, "text");
    nop();
    two(x, x + 1);
    two(bump(), 2);
    two(counter++, 2);
    two(x = 9, 2);
    two(vport, 2);
    dbg("v %d\n", vport);
    if (x > 3)
        dbg("big %d\n", x);
    else
        x++;
    for (i = 0; i < 3; i++)
        two(i, x * i);
    table[1](5);
    use_shadow(report);
    (void) bump();
    printf("%d %d %d\n", x, counter, i);
    return 0;
}
"""


def test_matches_host(tmp_path):
    want, got, text = run_both(tmp_path, SRC)
    assert got == want
    assert "report 1" in want


def test_calls_are_deleted(tmp_path):
    want, got, text = run_both(tmp_path, SRC)
    assert text.count("call\t_dbg") == 1         # the volatile argument
    assert "call\t_nop" not in text
    assert "call\t_hook" not in text and "_hook" in text     # address taken
    assert text.count("call\t_two") == 4          # four calls with effects


def test_option_keeps_the_calls(tmp_path):
    want, got, text = run_both(tmp_path, SRC, "--no-empty-call")
    assert got == want
    assert "call\t_dbg" in text
    assert "call\t_nop" in text


def test_function_with_code_is_kept(tmp_path):
    src = r"""
#include <stdio.h>
static int n;
static void f(int a) { n += a; }
static void g(int a) { if (0) n += a; }
int main(void)
{
    f(2); g(3); f(4);
    printf("%d\n", n);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "6"
    assert "call\t_f" in text
