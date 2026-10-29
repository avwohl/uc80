"""Register locals and calls that never return (docs/register_locals.md).

A call to exit, abort, a function marked ``_Noreturn``, ``[[noreturn]]`` or
``__attribute__((noreturn))`` has no successor in the flow graph of the
register-locals pass.  No local is live after such a call, so an error path
such as ``printf(...); exit(1);`` needs no push and pop of BC and DE, and the
instructions after the call (the cleanup of the arguments) are removed.  A
function that is not marked is a function that returns.  The programs run in
cpmemu with the option on, off (--no-slot-fold) and with
--no-register-locals.
"""

import re

import pytest

from .test_register_cc import build_run, compile_mac, func, needs_run

VARIANTS = [(), ("--no-slot-fold",), ("--no-register-locals",), ("-O0",)]

CHECK = r"""
#include <stdio.h>
#include <stdlib.h>
static unsigned char data[40];
static int flag;
static void chk(char *p, int v, unsigned c)
{
    unsigned char *pc = (unsigned char *) p;
    unsigned char val = (unsigned char) (v & 0xff);
    unsigned i;
    if (0 == p) { printf("null pointer\n"); exit(1); }
    for (i = 0; i < c; i++) {
        if (*pc != val) {
            printf("bad byte at %u: v %d, c %u, *pc %d, off %d\n",
                   i, v, c, *pc, (int)(pc - (unsigned char *) p));
            exit(3);
        }
        pc++;
    }
    printf("ok %u\n", c);
}
int main(void)
{
    int i;
    for (i = 0; i < 40; i++) data[i] = 7;
    chk((char *) data, 7, 40);
    chk((char *) data, 7, 10);
    if (flag == 0) data[33] = 9;
    chk((char *) data, 7, 33);
    chk((char *) data, 7, 34);
    printf("not reached\n");
    return 0;
}
"""

MARKED = r"""
#include <stdio.h>
#include <stdlib.h>
_Noreturn void die_a(int code, const char *m);
[[noreturn]] void die_b(int code, const char *m);
void die_c(int code, const char *m) __attribute__((noreturn));
void die_a(int code, const char *m) { printf("A %s\n", m); exit(code); }
void die_b(int code, const char *m) { printf("B %s\n", m); exit(code); }
void die_c(int code, const char *m) { printf("C %s\n", m); exit(code); }
static int total;
static int scan(int which, int limit)
{
    unsigned char a = 0, b = 1;
    int i;
    for (i = 0; i < 50; i++) {
        a += 3;
        b ^= a;
        total += b;
        if (i == limit) {
            if (which == 0) die_a(a, "stop");
            if (which == 1) die_b(b, "stop");
            if (which == 2) die_c(i, "stop");
        }
    }
    return a + b;
}
int main(void)
{
    printf("%d\n", scan(0, 99));
    printf("%d\n", scan(3, 20));
    printf("%d\n", scan(2, 99) + total);
    printf("%d\n", scan(1, 30));
    printf("never\n");
    return 0;
}
"""

RETURNS = r"""
#include <stdio.h>
static int total;
static void note(int x) { total += x; }
static void fake_die(int code) { total -= code; }
static int scan(int limit)
{
    unsigned char a = 0, b = 1;
    int i;
    for (i = 0; i < 50; i++) {
        a += 3;
        b ^= a;
        note(b);
        if (i == limit) { printf("limit %d\n", i); fake_die(a); }
    }
    return a + b + total;
}
int main(void)
{
    printf("%d\n", scan(20));
    printf("%d\n", scan(99));
    return 0;
}
"""

PROGRAMS = {"check": CHECK, "marked": MARKED, "returns": RETURNS}


@needs_run
@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_every_option_set_gives_the_same_output(tmp_path, name):
    outs = []
    for i, flags in enumerate(VARIANTS):
        d = tmp_path / f"v{i}"
        d.mkdir()
        outs.append(build_run(d, PROGRAMS[name], "--no-inlining", *flags))
    assert outs[0].strip(), "no output"
    assert all(o == outs[0] for o in outs), list(zip(VARIANTS, outs))


@needs_run
def test_the_error_path_is_taken(tmp_path):
    out = build_run(tmp_path, CHECK, "--no-inlining")
    assert "ok 40" in out and "ok 10" in out and "ok 33" in out
    assert "bad byte at 33" in out
    assert "not reached" not in out


@needs_run
def test_marked_functions_end_the_program(tmp_path):
    out = build_run(tmp_path, MARKED, "--no-inlining")
    assert "never" not in out
    lines = out.split()
    assert lines[0].isdigit() or lines[0].lstrip("-").isdigit()


def test_the_cleanup_after_exit_is_gone(tmp_path):
    on, _ = compile_mac(tmp_path, CHECK, "--no-inlining")
    off, _ = compile_mac(tmp_path, CHECK, "--no-inlining", "--no-slot-fold")
    a, b = func(on, "chk"), func(off, "chk")
    assert not re.search(r"call\s+_exit\s*\n\s*pop", a)
    assert re.search(r"call\s+_exit\s*\n\s*pop", b)
    assert len(a) < len(b)


def test_the_programs_name_which_functions_do_not_return(tmp_path):
    """A function that is not marked returns: the loop code after the call
    stays."""
    mac, _ = compile_mac(tmp_path, RETURNS, "--no-inlining")
    text = func(mac, "scan")
    assert "call\t_fake_die" in text
    assert "@FORUPD" in text.split("call\t_fake_die", 1)[1]


def test_the_text_scan_finds_the_attribute_spellings():
    from uc80 import sdcc_compat as sc
    found = sc.Found()
    sc._noreturn_pass(
        "#define NR __attribute__((noreturn))\n"
        "int g(void);\n"
        "[[noreturn]] void a(int x);\n"
        "void b(void) __attribute__((noreturn));\n"
        "__attribute__((__noreturn__)) static void c(void) { for (;;); }\n"
        "void d(void) __attribute__((noreturn, cold));\n"
        "[[gnu::cold, noreturn]] void e(void);\n"
        "typedef void (*fp)(void) __attribute__((noreturn));\n"
        "void (*h)(void) __attribute__((noreturn));\n"
        "void k(void);\n", found)
    assert found.noreturn == {"a", "b", "c", "d", "e"}


def test_a_define_does_not_mark_the_next_declaration():
    from uc80 import sdcc_compat as sc
    found = sc.Found()
    sc._noreturn_pass("#define NR __attribute__((noreturn)) \\\n  extra\n"
                      "int g(void);\nNR void h(void);\n", found)
    assert found.noreturn == set()
