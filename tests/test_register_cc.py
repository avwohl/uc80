"""The register calling convention (docs/calling_convention.md).

A function that only direct calls reach takes its first two scalar
parameters in registers: A or HL for parameter 0, E or DE for parameter 1.
Every other function keeps the stack convention.  The tests check three
things:

  - which functions get the register convention (the shape of the code);
  - that the two conventions give the same answers, in every storage mode,
    for the cases that go wrong first: recursion, mutual recursion, a
    parameter that lives across a call, function pointers, variadic
    callees, struct returns, assembly callers;
  - that the functions the rules exclude really keep the stack convention.

bench/ccfuzz.py runs thousands of random programs against the host
compiler; these tests pin the cases by name.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from .test_rom_target import _ul80_has_rom_options, compile_rom, link_rom
from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO, _toolchain_available

needs_run = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")


def compile_mac(tmp_path, source, *flags, name="t", files=()):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(source)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), *map(str, files),
         *flags, "-o", str(mac)],
        capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return mac.read_text(), mac


def run_mac(tmp_path, mac):
    rel, com = tmp_path / "t.rel", tmp_path / "t.com"
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True, text=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                         timeout=120)
    return run.stdout.decode("latin-1").replace("\r", "")


def build_run(tmp_path, source, *flags, files=()):
    _, mac = compile_mac(tmp_path, source, *flags, files=files)
    return run_mac(tmp_path, mac)


def func(mac, name):
    """The text of function ``name``, from its label to its return label."""
    start = mac.index(f"\n_{name}:")
    ends = [mac.find(m, start + 1) for m in (f"@{name}_ret:", "\n_")]
    end = min(e for e in ends if e >= 0)
    return mac[start:end]


def ops(mac, name):
    return [re.sub(r"\s+", " ", l.split(";")[0]).strip()
            for l in func(mac, name).splitlines()[2:]
            if l.strip() and not l.strip().startswith(";")]


# ------------------------------------------------------------ the shape

OUT = """
unsigned char a;
void out(unsigned char port, unsigned char v) { a = port + v; }
int add(int x, int y) { return x + y; }
int main(void) { out(3, 4); return add(1, 2) + a; }
"""


class TestShape:
    def test_leaf_with_two_bytes_has_no_frame_and_no_store(self, tmp_path):
        mac, _ = compile_mac(tmp_path, OUT, "--no-inlining")
        body = ops(mac, "out")
        assert not any("IX" in o for o in body)
        assert not any("??AUTO" in o for o in body)
        assert "add A,E" in body

    def test_two_words_are_added_in_registers(self, tmp_path):
        mac, _ = compile_mac(tmp_path, OUT, "--no-inlining")
        assert ops(mac, "add") == ["add HL,DE"]

    def test_call_site_loads_registers_and_pushes_nothing(self, tmp_path):
        mac, _ = compile_mac(tmp_path, OUT, "--no-inlining")
        main = ops(mac, "main")
        i = main.index("call _out")
        assert main[i - 2:i] == ["ld A,3", "ld E,4"]
        assert "push HL" not in main[:i]
        # the caller pops nothing either: after the call, the next call
        # follows with no pop in between
        j = main.index("call _add")
        assert "pop DE" not in main[i:j]

    def test_third_parameter_stays_on_the_stack(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int f(int a, int b, int c) { return a + b + c; }
            int main(void) { return f(1, 2, 3); }""", "--no-inlining")
        main = ops(mac, "main")
        i = main.index("call _f")
        assert main[:i] == ["ld HL,3", "push HL", "ld HL,1", "ld DE,2"]
        assert "(IX+4)" in " ".join(ops(mac, "f"))
        assert main[i + 1] == "pop DE"

    def test_long_first_parameter_is_pushed_and_second_is_in_de(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            long f(long a, int b) { return a + b; }
            int main(void) { return (int) f(100000L, 7); }""", "--no-inlining")
        main = ops(mac, "main")
        i = main.index("call _f")
        assert "ld DE,7" in main[:i]
        assert main.count("push DE") >= 1 and main.count("push HL") >= 1

    def test_unused_parameter_is_not_stored(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int pick(int unused, int y) { return y; }
            int main(void) { return pick(1, 2); }""", "--no-inlining")
        assert not any("??AUTO" in o or "IX" in o for o in ops(mac, "pick"))

    def test_stack_frame_stores_parameters_by_push(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int f(int n) { if (n < 2) return 1; return n * f(n - 1); }
            int main(void) { return f(5); }""", "--no-inlining")
        body = ops(mac, "f")
        assert body[:4] == ["push IX", "ld IX,0", "add IX,SP", "push HL"]

    def test_tail_call_becomes_a_jump(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int g(int x) { return x + 1; }
            int f(int x) { return g(x); }
            int main(void) { return f(1); }""", "--no-inlining")
        assert "jp\t_g" in mac or "jp _g" in mac


# ------------------------------------------------------- who is excluded

def reads_stack(mac, name):
    return any("(IX+4)" in o for o in ops(mac, name))


class TestStackConventionKept:
    def test_function_whose_address_is_taken(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int twice(int x) { return x * 2; }
            int (*fp)(int) = twice;
            int main(void) { return fp(4) + twice(3); }""", "--no-inlining")
        assert reads_stack(mac, "twice")
        assert "push\tHL" in mac[mac.index("\n_main:"):]

    def test_variadic_function(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            #include <stdarg.h>
            int sum(int n, ...) { va_list ap; int s = 0; va_start(ap, n);
              while (n--) s += va_arg(ap, int); va_end(ap); return s; }
            int main(void) { return sum(2, 3, 4); }""", "--no-inlining")
        assert reads_stack(mac, "sum")

    def test_main(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int main(int argc, char **argv) { return argc; }""")
        assert reads_stack(mac, "main")

    def test_separate_compilation(self, tmp_path):
        mac, _ = compile_mac(tmp_path, OUT, "--no-inlining",
                             "--no-whole-program")
        assert reads_stack(mac, "add")
        assert "push\tHL" in mac[mac.index("\n_main:"):]

    def test_no_register_cc_option(self, tmp_path):
        mac, _ = compile_mac(tmp_path, OUT, "--no-inlining", "--no-register-cc")
        assert reads_stack(mac, "add")

    def test_keep_pragma(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            #pragma uc80 keep helper
            int helper(int x, int y) { return x - y; }
            int main(void) { return helper(5, 3); }""", "--no-inlining")
        assert reads_stack(mac, "helper")

    def test_entry_option(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int helper(int x, int y) { return x - y; }
            int main(void) { return helper(5, 3); }""", "--no-inlining",
                             "--entry", "helper")
        assert reads_stack(mac, "helper")

    def test_library_name(self, tmp_path):
        # The library exports abs; a program function of that name may be
        # called from library code.
        mac, _ = compile_mac(tmp_path, """
            int abs(int x) { return x < 0 ? -x : x; }
            int main(void) { return abs(-3); }""", "--no-inlining")
        assert reads_stack(mac, "abs")

    def test_inline_asm_in_function(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int f(int x, int y) { __asm__("\\tnop"); return x + y; }
            int main(void) { return f(1, 2); }""", "--no-inlining")
        assert reads_stack(mac, "f")

    def test_function_named_by_asm(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int cb(int x, int y) { return x + y; }
            void caller(void) { __asm__("\\tcall _cb"); }
            int main(void) { caller(); return cb(1, 2); }""", "--no-inlining")
        assert reads_stack(mac, "cb")

    def test_call_with_the_wrong_argument_count(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            int f();
            int g(int a, int b) { return a + b; }
            int main(void) { return g(1, 2); }""", "--no-inlining")
        assert "add HL,DE" in ops(mac, "g")

    def test_struct_return(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            struct S { int a, b, c; };
            struct S mk(int x, int y) { struct S s = { x, y, x + y }; return s; }
            int main(void) { struct S s = mk(1, 2); return s.c; }""",
                             "--no-inlining")
        assert "(IX+6)" in " ".join(ops(mac, "mk"))

    def test_interrupt_function_and_what_it_reaches(self, tmp_path):
        if not _ul80_has_rom_options():
            pytest.skip("needs a ul80 with ROM options")
        r = compile_rom(tmp_path, """
            #include <stdint.h>
            volatile uint8_t ticks;
            static uint8_t bump(uint8_t v, uint8_t w) { return v + w; }
            #pragma uc80 interrupt tick
            void tick(void) { ticks = bump(ticks, 1); }
            int main(void) { uint8_t i; for (i = 0; i < 9; i++) ticks = bump(ticks, i);
              return 0; }""", "--no-inlining")
        assert r.returncode == 0, r.stderr
        mac = (tmp_path / "t.mac").read_text()
        assert reads_stack(mac, "bump")


# ------------------------------------------------ the answers are the same

PROGRAMS = {
    "recursion": r"""
#include <stdio.h>
int fact(int n) { if (n < 2) return 1; return n * fact(n - 1); }
int fib(int n) { if (n < 2) return n; return fib(n - 1) + fib(n - 2); }
int ack(int m, int n) {
    if (m == 0) return n + 1;
    if (n == 0) return ack(m - 1, 1);
    return ack(m - 1, ack(m, n - 1));
}
int tak(int x, int y, int z) {
    if (y >= x) return z;
    return tak(tak(x - 1, y, z), tak(y - 1, z, x), tak(z - 1, x, y));
}
/* the parameters live across the recursive call */
int sumdown(int n, int acc) {
    int r;
    if (n == 0) return acc;
    r = sumdown(n - 1, acc + n);
    return r + n * 2 + acc;
}
int main(void) {
    printf("%d %d %d %d %d\n", fact(7), fib(15), ack(2, 3), tak(18, 12, 6),
           sumdown(10, 1));
    return 0;
}
""",
    "mutual": r"""
#include <stdio.h>
int odd(int n, int tag);
int even(int n, int tag) { if (n == 0) return tag; return odd(n - 1, tag + 1) + n; }
int odd(int n, int tag) { if (n == 0) return tag * 2; return even(n - 1, tag - 1) - n; }
int main(void) { printf("%d %d\n", even(9, 5), odd(8, 3)); return 0; }
""",
    "bytes": r"""
#include <stdio.h>
unsigned char u8(unsigned char a, unsigned char b) { return a + b; }
signed char s8(signed char a, signed char b) { return a - b; }
int widen(signed char a, unsigned char b) { return a * 256 + b; }
char first(char c, int n) { return c + n; }
int cmp(unsigned char a, signed char b) { return a < b; }
int main(void) {
    unsigned char x = 200;
    signed char y = -100;
    printf("%d %d %d %d %d\n", u8(x, 100), s8(y, 100), widen(y, x),
           first('A', 2), cmp(x, y));
    printf("%d %d\n", widen(-1, 255), widen(127, 128));
    return 0;
}
""",
    "mixed": r"""
#include <stdio.h>
struct P { int x, y; };
long lsum(long a, int b) { return a + b; }
int third(int a, int b, int c) { return a * 100 + b * 10 + c; }
int fourth(int a, int b, int c, int d) { return a * 1000 + b * 100 + c * 10 + d; }
int bystruct(int a, struct P p, int b) { return a + p.x * 10 + p.y * 100 + b * 1000; }
int ptrs(int *p, char *q) { return p[1] + q[2]; }
long mix(int a, long b, int c) { return a + b * 10 + c * 100; }
int main(void) {
    struct P p = { 2, 3 };
    int arr[3] = { 1, 2, 3 };
    char s[] = "abc";
    printf("%ld %d %d %d %d %ld\n", lsum(70000L, 5), third(1, 2, 3),
           fourth(4, 3, 2, 1), bystruct(1, p, 4), ptrs(arr, s),
           mix(1, 2L, 3));
    return 0;
}
""",
    "pointers": r"""
#include <stdio.h>
int twice(int x) { return x * 2; }
int thrice(int x) { return x * 3; }
int apply(int (*f)(int), int v) { return f(v); }
int direct(int x, int y) { return twice(x) + thrice(y); }
int (*table[2])(int) = { twice, thrice };
int main(void) {
    printf("%d %d %d %d\n", apply(twice, 4), apply(thrice, 4), direct(3, 5),
           table[1](table[0](2)));
    return 0;
}
""",
    "variadic": r"""
#include <stdio.h>
#include <stdarg.h>
int sum(int n, ...) { va_list ap; int s = 0; va_start(ap, n);
    while (n--) s += va_arg(ap, int); va_end(ap); return s; }
int sq(int x, int y) { return x * x + y; }
int main(void) {
    printf("%d %d\n", sum(3, sq(1, 2), sq(2, 3), sq(3, 4)), sum(1, sq(5, 5)));
    return 0;
}
""",
    "nested": r"""
#include <stdio.h>
int a(int x, int y) { return x + y; }
int b(int x, int y) { return x * y; }
int c(int x) { return x - 1; }
char d(char x, char y) { return x ^ y; }
int main(void) {
    int v = 5;
    printf("%d %d %d\n", a(b(v, c(v)), a(c(v), b(2, c(c(v))))),
           b(a(1, 2), a(3, 4)), d(d(1, 2), d(3, 4)));
    if (a(v, c(v)) > b(2, 3) && c(a(1, 1)) == 1)
        printf("cond\n");
    for (v = 0; v < a(1, 2); v = a(v, 1))
        printf("%d", b(v, v));
    printf("\n");
    return 0;
}
""",
    "structret": r"""
#include <stdio.h>
struct S { int a, b, c; };
struct S mk(int x, int y) { struct S s; s.a = x; s.b = y; s.c = x + y; return s; }
int use(int x, int y) { struct S s = mk(x, y); return s.a + s.b * 10 + s.c * 100; }
int main(void) { printf("%d\n", use(1, 2)); return 0; }
""",
    "asm_between": r"""
#include <stdio.h>
unsigned char a;
void out(unsigned char port, unsigned char v) { a = port + v; }
int add(int x, int y) { return x + y; }
int main(void) {
    out(3, 4);
    __asm__("\tnop");
    printf("%d %d\n", a, add(5, 6));
    __asm__("\tnop");
    out(a, a);
    printf("%d\n", a);
    return 0;
}
""",
    "printf_in_callee": r"""
#include <stdio.h>
void show(int a, int b, int c, int r) { printf("%d %d %d %d\n", a, b, c, r); }
void run(int n) {
    int i, r = n;
    if (n) run(n - 1);
    for (i = 0; i < 2; i++) show(i, n, 0, r);
}
int main(void) { run(2); return 0; }
""",
    "ptr_cycle": r"""
#include <stdio.h>
static int (*fp)(int, int);
static int f(int a, int b) { int t; if (a <= 0) return b; t = fp(a - 1, b + 1); return t * 2 + a + b; }
static int h(int a, int b) { int t = f(a, b + 3); return t + a; }
int main(void) { fp = h; printf("%d\n", f(5, 1)); return 0; }
""",
    "ptr_cycle_apply": r"""
#include <stdio.h>
typedef int (*cb)(int);
static int apply(cb c, int x) { return c(x); }
static int f(int a, int b);
static int h(int x) { return f(x - 1, x); }
static int f(int a, int b) { int t; if (a <= 0) return b + 100; t = apply(h, a); return t + a * b; }
int main(void) { printf("%d\n", f(6, 2)); return 0; }
""",
    "char_result": r"""
#include <stdio.h>
typedef unsigned char u8;
static char sneg(int x) { return (char)(x * 3); }
static u8 uadd(u8 a, u8 b) { return a + b; }
static signed char sdiv(int a) { return (signed char)(a / 2); }
static char five(void) { return 5; }
static u8 chain(int a) { return uadd((u8)a, (u8)a); }
static char bounce(char c) { return sneg(c); }
static int seen;
static void note(int a, char c) { seen += a + c; }
int main(void) {
    char c = sneg(100);
    u8 u = uadd(200, 100);
    int i = sneg(100) + 1;
    int j = uadd(250, 10) * 2;
    int k = sdiv(-5);
    printf("%d %d %d %d %d %d\n", c, u, i, j, k, five());
    printf("%d %d\n", chain(130), bounce(-100));
    if (sneg(90)) printf("t1\n");
    if (!uadd(128, 128)) printf("t2\n");
    if (sneg(10) < sneg(50)) printf("t3\n");
    if (uadd(100, 100) > 150) printf("t4\n");
    sneg(3);
    note(sneg(70), uadd(1, 2));
    printf("%d\n", seen);
    return 0;
}
""",
    "tail_and_keep": r"""
#include <stdio.h>
static int g_sum(int *p, int n) { int s = 0; while (n--) s += *p++; return s; }
static int tail_arr(int a, int b) { int arr[3]; arr[0] = a; arr[1] = b; arr[2] = a + b; return g_sum(arr, 3); }
static int tail_local(int a) { int v = a * 2; return g_sum(&v, 1); }
static int leaf(int a, int b) { return a * 10 + b; }
static int tail_ok(int a, int b) { return leaf(b, a); }
static int tail_deep(int a, int b) { if (a) return tail_ok(a, b); return tail_local(b); }
static int keep_two(int a, int b) { int t = leaf(1, 2); return t + a * 100 + b; }
static int keep_one(int a, int b) { int t = leaf(a, 2); return t + b; }
static int keep_swap(int a, int b) { int t = leaf(3, 4); int u = leaf(a, b); return t + u; }
int main(void) {
    printf("%d %d %d %d\n", tail_arr(1, 2), tail_local(21), tail_ok(3, 4), tail_deep(0, 5));
    printf("%d %d %d\n", keep_two(2, 3), keep_one(4, 5), keep_swap(6, 7));
    return 0;
}
""",
    "assign_param": r"""
#include <stdio.h>
int count(int n, char c) { int k = 0; while (n--) { c++; k += c; } return k; }
int clobber(int a, int b) { a = a + 1; b = b + a; a += b; return a * 100 + b; }
int addr(int a, int b) { int *p = &a; *p += b; return a; }
int main(void) { printf("%d %d %d\n", count(3, 'a'), clobber(1, 2), addr(5, 6)); return 0; }
""",
}

# What a host compiler prints for each program (int is 16 bits here, but no
# program depends on it).
KNOWN = {'char_result': '44 44 45 8 -2 5\n4 -44\nt1\nt2\nt4\n-43\n',
    'tail_and_keep': '6 42 43 10\n215 47 101\n',
    'ptr_cycle': '1204\n',
    'ptr_cycle_apply': '183\n',
    'asm_between': '7 11\n14\n',
    'assign_param': '297 604 11\n',
    'bytes': '44 56 -25400 67 0\n-1 32640\n',
    'mixed': '70005 123 4321 4321 101 321\n',
    'mutual': '17 2\n',
    'nested': '30 21 4\ncond\n014\n',
    'pointers': '8 12 21 12\n',
    'printf_in_callee': '0 0 0 0\n1 0 0 0\n0 1 0 1\n1 1 0 1\n0 2 0 2\n1 2 0 2\n',
    'recursion': '5040 610 9 7 506\n',
    'structret': '321\n',
    'variadic': '23 30\n'}


CONFIGS = [
    pytest.param((), id="default"),
    pytest.param(("--no-shared-storage",), id="stack-frames"),
    pytest.param(("--no-inlining", "--no-const-propagation"), id="no-inlining"),
    pytest.param(("-O0",), id="no-peephole"),
    pytest.param(("--isr-safe",), id="isr-safe"),
]


@needs_run
@pytest.mark.parametrize("flags", CONFIGS)
@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_register_and_stack_convention_agree(tmp_path, name, flags):
    src = PROGRAMS[name]
    reg = build_run(tmp_path, src, *flags)
    (tmp_path / "ref").mkdir()
    ref = build_run(tmp_path / "ref", src, *flags, "--no-register-cc")
    assert reg == ref
    assert reg == KNOWN[name]


@needs_run
def test_assembly_caller_uses_the_stack_convention(tmp_path):
    asm = tmp_path / "caller.mac"
    asm.write_text("""\t.z80
\tCSEG
\tPUBLIC\t_acall
\tEXTRN\t_cb
_acall:
\tLD\tHL,5
\tPUSH\tHL
\tLD\tHL,7
\tPUSH\tHL
\tCALL\t_cb
\tPOP\tDE
\tPOP\tDE
\tRET
\tEND
""")
    src = """
#include <stdio.h>
int cb(int x, int y) { return x * 10 + y; }
extern int acall(void);
int main(void) { printf("%d %d\\n", acall(), cb(1, 2)); return 0; }
"""
    out = build_run(tmp_path, src, "--no-inlining", files=[asm])
    assert out == "75 12\n"


@needs_run
def test_function_pointer_and_direct_call_of_the_same_function(tmp_path):
    out = build_run(tmp_path, PROGRAMS["pointers"], "--no-inlining")
    assert out == "8 12 21 12\n"


@needs_run
def test_isr_and_main_share_a_helper(tmp_path):
    """A helper that an interrupt function reaches keeps the stack
    convention, so an interrupt in the middle of main's call cannot
    overwrite main's parameters."""
    if not _ul80_has_rom_options():
        pytest.skip("needs a ul80 with ROM options")
    from .test_rom_target import machine
    r = compile_rom(tmp_path, """
        #include <stdint.h>
        volatile uint8_t ticks;
        volatile uint8_t total;
        static uint8_t bump(uint8_t v, uint8_t w) { return (uint8_t) (v + w); }
        #pragma uc80 interrupt tick
        void tick(void) { ticks = bump(ticks, 1); }
        int main(void) {
            __asm__("\\tei");
            while (ticks < 5)
                total = bump(total, 2);
            __asm__("\\tdi");
            return 0;
        }""", "--no-inlining")
    assert r.returncode == 0, r.stderr
    link_rom(tmp_path)
    m = machine(tmp_path)
    m.add_irq_every(2500, start=2500)
    assert m.run(max_tstates=400_000).reason == "halt"
    assert m.peek8("_ticks") == 5


@needs_run
def test_recursion_through_a_pointer_keeps_parameters_per_activation(tmp_path):
    """f is named only in direct calls, but f -> fp -> h -> f is a cycle.
    The call graph must see the indirect edge, or the parameters of f sit
    in one static slot and the inner activation overwrites them."""
    assert build_run(tmp_path, PROGRAMS["ptr_cycle"], "--no-inlining") == KNOWN["ptr_cycle"]


@needs_run
def test_isr_reaches_helper_through_a_hook_pointer(tmp_path):
    """An interrupt function calls hook(); hook points to h, and h calls f
    by name.  f is reachable from the interrupt function, so f keeps the
    stack convention and its parameters are safe from the interrupt."""
    if not _ul80_has_rom_options():
        pytest.skip("needs a ul80 with ROM options")
    r = compile_rom(tmp_path, """
        #include <stdint.h>
        volatile uint16_t cnt;
        static void (*hook)(void);
        static int f2(int a, int b) { a = a + b; b = b + 3; a = a * 2; b = b * 5; a = a - b; b = b + a; return a + b; }
        static void h(void) { cnt = cnt + (f2(cnt, 3) & 1); }
        #pragma uc80 interrupt isr
        void isr(void) { hook(); }
        volatile int sink;
        int main(void) {
            int i;
            hook = h;
            __asm__("\tei");
            for (i = 0; i < 300; i++) sink = f2(i, i + 1) + f2(f2(i, 1), f2(2, i));
            __asm__("\tdi");
            return 0;
        }""", "--no-inlining")
    assert r.returncode == 0, r.stderr
    link_rom(tmp_path)
    from .test_rom_target import machine, symbols
    sink = symbols(tmp_path)["_SINK"]

    def result(period):
        m = machine(tmp_path)
        if period:
            m.add_irq_every(period, start=period)
        res = m.run(max_tstates=20_000_000)
        assert res.reason == "halt", (period, res.reason)
        return m.peek8(sink), m.peek8(sink + 1)

    want = result(0)
    for period in (1511, 3001, 5003):
        assert result(period) == want


# ------------------------------------------------ results in A, tail calls


class TestStep2:
    def test_char_result_comes_back_in_a_and_the_caller_tests_a(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            static char lo(int x) { return x & 127; }
            int main(void) { if (lo(5)) return 1; return 0; }""",
                             "--no-inlining")
        assert ops(mac, "lo") == ["ld A,L", "and 127"]
        main = ops(mac, "main")
        i = main.index("call _lo")
        assert main[i + 1] == "or A"

    def test_char_result_is_widened_only_when_used_as_int(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            unsigned char g;
            static unsigned char inc(unsigned char x) { return x + 1; }
            int use(void) { return inc(4) + 1; }
            void store(void) { g = inc(9); }
            int main(void) { store(); return use(); }""", "--no-inlining")
        store = ops(mac, "store")
        i = store.index("call _inc")
        assert store[i + 1] == "ld (_g),A"
        use = ops(mac, "use")
        assert "ld H,0" in use

    def test_return_of_a_call_is_a_jump(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            static int leaf(int a, int b) { return a - b; }
            static int w(int a, int b) { return leaf(b, a); }
            int main(void) { return w(5, 3); }""", "--no-inlining")
        body = ops(mac, "w")
        assert not any(o.startswith("call") for o in body)
        assert body[-1] in ("jp _leaf", "jr _leaf")

    def test_parameter_across_a_call_is_pushed(self, tmp_path):
        mac, _ = compile_mac(tmp_path, """
            extern int putchar(int);
            static int w(int a, int b) { putchar('x'); return a + b; }
            int main(void) { return w(5, 3); }""", "--no-inlining")
        body = ops(mac, "w")
        assert body[0] == "push DE" and "pop DE" in body
        assert not any("??AUTO" in o for o in body)
