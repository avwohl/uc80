"""Interrupt safety of the runtime work areas.  See docs/rom_hardware.md
("Work areas of the runtime in an interrupt function").

A handler that does 32-bit arithmetic uses __tmp32, __div32_dvd and
__rem32, as the main code does.  An interrupt inside a division of the main
code corrupts the temporaries.  --isr-save-workareas (and the pragma
isr_save_workareas) save and restore the areas that the call graph of the
handler reaches.  The programs run in bench/romrun with frequent interrupts,
in the um80 flow and in the sdas flow.
"""

import pytest

from .rom_flows import (FLOWS, build_flow, machine, run, uc80_cmd, REPO,
                        need_flow)
from .conftest import HAVE_SDAS

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS or __import__("shutil").which("um80")),
    reason="needs um80 and ul80, or sdas")


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


MATH = r"""
#include <stdint.h>
volatile uint32_t sum_out;
volatile uint32_t isr_acc;
volatile uint16_t isr_n;
%s
void isr(void)
{
    uint32_t v = isr_acc + 0x01234567UL;
    uint32_t d = (isr_n & 15) + 3;
    isr_acc = v * 3 + v / d + v %% 13;
    isr_n++;
}
int main(void)
{
    uint32_t s = 0;
    __asm__("\tei");
    for (uint16_t i = 1; i <= 120; i++) {
        volatile uint32_t x = 1000003UL * i;
        uint32_t d = i + 77;
        s += (x / d) * 3 + x %% d;
    }
    __asm__("\tdi");
    sum_out = s;
    return 0;
}
"""


def expected_sum():
    s = 0
    for i in range(1, 121):
        x = (1000003 * i) & 0xFFFFFFFF
        d = i + 77
        s = (s + ((x // d) * 3 & 0xFFFFFFFF) + x % d) & 0xFFFFFFFF
    return s


KINDS = {
    "im2": ("#pragma uc80 im2 3 isr", "irq", 6),
    "nmi": ("#pragma uc80 nmi isr", "nmi", None),
    "int": ("#pragma uc80 interrupt isr", "irq", None),
}


def run_math(paths, kind):
    m = machine(paths)
    _p, source, vec = KINDS[kind]
    if source == "nmi":
        m.add_nmi_every(9973, start=7000)
        m.run(max_tstates=1_800_000)    # the NMI also wakes the final HALT
    else:
        m.add_irq_every(9973, start=7000, vec=vec)
        assert m.run(max_tstates=20_000_000).reason == "halt"
    got = int.from_bytes(m.peek("_sum_out", 4), "little")
    return m, got


@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("flow", FLOWS)
def test_the_work_areas_are_corrupted_with_the_opt_out(
        kind, flow, tmp_path, libs):
    """--no-isr-save-workareas: the result of the main loop is wrong, and
    the compiler warned."""
    paths, err = build_flow(tmp_path, {"t.c": MATH % KINDS[kind][0]}, flow,
                            libs, extra=("--no-isr-save-workareas",))
    assert "work area of rt_rem32" in err and "work area of rt_tmp32" in err
    m, got = run_math(paths, kind)
    assert m.peek16("_isr_n") > 20          # many interrupts
    assert got != expected_sum()


@pytest.mark.parametrize("how", ["default", "option", "pragma"])
@pytest.mark.parametrize("kind", sorted(KINDS))
@pytest.mark.parametrize("flow", FLOWS)
def test_the_work_areas_survive_with_the_option(
        how, kind, flow, tmp_path, libs):
    pragma = KINDS[kind][0]
    extra = ()
    if how == "option":
        extra = ("--isr-save-workareas",)
    elif how == "pragma":
        pragma += "\n#pragma uc80 isr_save_workareas isr"
    paths, err = build_flow(tmp_path, {"t.c": MATH % pragma}, flow, libs,
                            extra=extra)
    assert "saves and restores the work areas of rt_rem32, rt_tmp32" in err
    assert "warning" not in err
    m, got = run_math(paths, kind)
    assert m.peek16("_isr_n") > 20
    assert got == expected_sum()
    assert m.rom_writes == []


@pytest.mark.parametrize("flow", FLOWS)
def test_a_handler_that_reaches_no_work_area_gets_no_save(
        flow, tmp_path, libs):
    """A handler that only counts reaches no work area: the option adds
    no save code and prints no note."""
    src = """
#include <stdint.h>
volatile uint32_t a = 70000, b = 3, r;
volatile uint8_t n;
#pragma uc80 interrupt isr
void isr(void) { n++; }
int main(void) { return 0; }
"""
    _paths, err = build_flow(tmp_path, {"t.c": src}, flow, libs,
                             extra=("--isr-save-workareas",))
    assert "saves and restores" not in err      # reaches nothing


def compile_text(tmp_path, src, *extra):
    (tmp_path / "t.c").write_text(src)
    return run(uc80_cmd("--target=rom", "t.c", "-o", "t.mac", *extra),
               tmp_path, ok=False)


def test_a_state_block_is_warned_not_saved(tmp_path):
    """strtol writes errno: a state variable that no save may revert."""
    r = compile_text(tmp_path, """
#include <stdlib.h>
volatile long r;
volatile unsigned long u = 5;
volatile char buf[8] = "123";
#pragma uc80 interrupt isr
void isr(void) { r = strtol((const char *) buf, 0, 10); u = u * 3 / 7; }
int main(void) { return 0; }
""", "--isr-save-workareas")
    assert r.returncode == 0, r.stderr
    assert "lc_errno" in r.stderr and "warning" in r.stderr
    assert "saves and restores the work areas" in r.stderr


def test_the_pragma_names_an_interrupt_function(tmp_path):
    r = compile_text(tmp_path, """
#pragma uc80 isr_save_workareas f
void f(void) { }
int main(void) { return 0; }
""")
    assert r.returncode == 1
    assert "'f' is not an interrupt function" in r.stderr


def test_the_pragma_wants_a_name(tmp_path):
    r = compile_text(tmp_path, "#pragma uc80 isr_save_workareas\n"
                     "int main(void) { return 0; }\n")
    assert r.returncode == 1
    assert "expected one or more names" in r.stderr


def test_a_naked_handler_with_the_pragma_is_an_error(tmp_path):
    r = compile_text(tmp_path, """
#pragma uc80 interrupt isr
#pragma uc80 isr_save_workareas isr
__attribute__((naked)) void isr(void) { __asm__("\\treti"); }
int main(void) { return 0; }
""")
    assert r.returncode == 1, r.stderr
    assert "naked" in r.stderr


def test_the_option_leaves_a_plain_handler_alone(tmp_path):
    a = compile_text(tmp_path, """
volatile unsigned char t;
#pragma uc80 interrupt isr
void isr(void) { t++; }
int main(void) { return 0; }
""", "--isr-save-workareas")
    assert a.returncode == 0 and a.stderr == ""


@pytest.mark.parametrize("flow", FLOWS)
def test_the_cost_of_the_save(flow, tmp_path, libs):
    """The bytes the compiler reports are the bytes that the image gains."""
    import re
    sizes = []
    for extra in (("--no-isr-save-workareas",), ("--isr-save-workareas",)):
        d = tmp_path / ("a" if "--no-isr-save-workareas" in extra else "b")
        d.mkdir()
        paths, err = build_flow(d, {"t.c": MATH % KINDS["int"][0]}, flow,
                                libs, extra=extra)
        sizes.append((len((paths[0]).read_bytes().rstrip(b"\xff")), err))
    gain = sizes[1][0] - sizes[0][0]
    m = re.search(r"(\d+) bytes of code, (\d+) T-states", sizes[1][1])
    assert m and int(m.group(1)) == gain


# ---- a call through a function pointer -------------------------------------

POINTER = r"""
#include <stdint.h>
volatile uint32_t sum_out;
volatile uint32_t isr_acc;
volatile uint16_t isr_n;
static void step(void)
{
    uint32_t v = isr_acc + 0x01234567UL;
    uint32_t d = (isr_n & 15) + 3;
    isr_acc = v * 3 + v / d + v %% 13;
    isr_n++;
}
void (*volatile hook)(void) = step;
%s
void isr(void) { hook(); }
int main(void)
{
    uint32_t s = 0;
    __asm__("\tei");
    for (uint16_t i = 1; i <= 120; i++) {
        volatile uint32_t x = 1000003UL * i;
        uint32_t d = i + 77;
        s += (x / d) * 3 + x %% d;
    }
    __asm__("\tdi");
    sum_out = s;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_a_handler_that_calls_a_pointer_reaches_every_taken_function(
        flow, tmp_path, libs):
    """step is reached only through the pointer hook.  The note names the
    call, and the work areas of step are reported (and corrupt the main
    loop) although no direct call leads to step."""
    paths, err = build_flow(
        tmp_path, {"t.c": POINTER % "#pragma uc80 interrupt isr"}, flow, libs,
        extra=("--no-isr-save-workareas",))
    assert "uc80: note: interrupt function 'isr' calls through a function " \
           "pointer" in err
    assert "step" in err                         # the reached function
    assert "work area of rt_rem32" in err        # reported through step
    m, got = run_math(paths, "int")
    assert m.peek16("_isr_n") > 20
    assert got != expected_sum()


@pytest.mark.parametrize("flow", FLOWS)
def test_the_work_area_save_covers_a_function_reached_by_a_pointer(
        flow, tmp_path, libs):
    paths, err = build_flow(
        tmp_path, {"t.c": POINTER % "#pragma uc80 interrupt isr"}, flow,
        libs, extra=("--isr-save-workareas",))
    assert "calls through a function pointer" in err
    assert "saves and restores the work areas of rt_rem32, rt_tmp32" in err
    assert "reaches" not in err                  # no work-area warning
    m, got = run_math(paths, "int")
    assert got == expected_sum()


@pytest.mark.parametrize("flow", FLOWS)
def test_a_plain_handler_has_no_pointer_note(flow, tmp_path, libs):
    _paths, err = build_flow(tmp_path, {"t.c": MATH % KINDS["int"][0]}, flow,
                             libs)
    assert "function pointer" not in err


SHARED = r"""
#include <stdint.h>
volatile uint16_t ticks;
volatile uint16_t result;
static uint16_t work(uint16_t n)
{
    volatile uint16_t i;
    uint16_t acc = 0;
    for (i = 0; i < n; i++)
        acc = (uint16_t) (acc + i);
    return acc;
}
static void tick(void) { ticks++; (void) work(10); }
void (*volatile hook)(void) = tick;
#pragma uc80 interrupt isr
void isr(void) { hook(); }
int main(void)
{
    __asm__("\tei");
    result = work(300);
    __asm__("\tdi");
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_isr_safe_covers_a_function_reached_through_a_pointer(
        flow, tmp_path, libs):
    """work is called by main and, through the pointer hook, by the
    interrupt function: with --isr-safe its locals stay on the stack."""
    results = {}
    for extra in ((), ("--isr-safe",)):
        d = tmp_path / ("safe" if extra else "plain")
        d.mkdir()
        paths, err = build_flow(d, {"t.c": SHARED}, flow, libs, extra=extra)
        m = machine(paths)
        m.add_irq_at(5000)
        m.add_irq_at(13000)
        assert m.run(max_tstates=10_000_000).reason == "halt"
        results[bool(extra)] = m.peek16("_result")
        if not extra:
            assert "'work' is called from an interrupt function" in err
    assert results[True] == sum(range(300)) & 0xFFFF
    assert results[False] != results[True]


# ---- 64-bit and floating-point work areas ----------------------------------
# The embedded runtime lays the work areas out in its own order, not in the
# order of the lib files: the save covers the labels as the program text
# places them.

U64 = r"""
#include <stdint.h>
volatile uint64_t out;
volatile uint64_t iu;
volatile uint16_t isr_n;
%s
void isr(void)
{
    uint64_t q = iu + 0x123456789ULL;
    iu = q * 3 + q / ((isr_n & 7) + 3) + q %% 11;
    isr_n++;
}
int main(void)
{
    uint64_t s = 0;
    __asm__("\tei");
    for (uint16_t i = 1; i < 60; i++) {
        volatile uint64_t x = 0x100000001ULL * i;
        s += x * 5 + x / (i + 3) + x %% 7;
    }
    __asm__("\tdi");
    out = s;
    return 0;
}
"""

FLOAT = r"""
#include <stdint.h>
volatile float out;
volatile float fv = 1.5f;
volatile uint16_t isr_n;
%s
void isr(void)
{
    float v = fv * 1.25f / (float) ((isr_n & 7) + 1) + 0.5f;
    fv = v - (float) isr_n;
    isr_n++;
}
int main(void)
{
    float s = 0.0f;
    __asm__("\tei");
    for (uint16_t i = 1; i < 60; i++) {
        volatile float x = 3.25f * (float) i;
        s += x * 1.5f / (float) (i + 3) + x - 0.25f;
    }
    __asm__("\tdi");
    out = s;
    return 0;
}
"""

FLOAT_SIN = r"""
#include <stdint.h>
#include <math.h>
volatile double out;
volatile double fv = 0.5;
volatile uint16_t isr_n;
%s
void isr(void) { fv = sqrt(fv + 2.0) * sin(fv); isr_n++; }
int main(void)
{
    double s = 0.0;
    __asm__("\tei");
    for (uint16_t i = 1; i < 12; i++)
        s += sqrt((double) i) + sin((double) i);
    __asm__("\tdi");
    out = s;
    return 0;
}
"""


def run_wide(paths, kind, size, every):
    m = machine(paths)
    _p, source, vec = KINDS[kind]
    if source == "nmi":
        m.add_nmi_every(every, start=7000)
        m.run(max_tstates=30_000_000)
    else:
        m.add_irq_every(every, start=7000, vec=vec)
        assert m.run(max_tstates=60_000_000).reason == "halt"
    return m, bytes(m.peek("_out", size))


def baseline(paths, size):
    m = machine(paths)
    assert m.run(max_tstates=60_000_000).reason == "halt"
    return bytes(m.peek("_out", size))


WIDE = {"u64": (U64, 8, 400001), "float": (FLOAT, 4, 40001)}


@pytest.mark.parametrize("what", sorted(WIDE))
@pytest.mark.parametrize("kind", ["int", "nmi"])
@pytest.mark.parametrize("flow", FLOWS)
def test_wide_math_survives_with_the_option(what, kind, flow, tmp_path, libs):
    """A main loop and a handler both do 64-bit or float math: the
    result with the option equals the result without interrupts, and the
    result without the option is wrong."""
    src, size, every = WIDE[what]
    d0 = tmp_path / "base"
    d0.mkdir()
    # the same program with an unused handler (no interrupt arrives)
    pb, _e = build_flow(d0, {"t.c": src % KINDS[kind][0]}, flow, libs)
    want = baseline(pb, size)
    d1 = tmp_path / "opt"
    d1.mkdir()
    pa, err = build_flow(d1, {"t.c": src % KINDS[kind][0]}, flow, libs,
                         extra=("--isr-save-workareas",))
    assert "saves and restores the work areas" in err
    m, got = run_wide(pa, kind, size, every)
    assert m.peek16("_isr_n") > 5
    assert got == want
    assert m.rom_writes == []
    d2 = tmp_path / "plain"
    d2.mkdir()
    pc, _e = build_flow(d2, {"t.c": src % KINDS[kind][0]}, flow, libs,
                        extra=("--no-isr-save-workareas",))
    m, bad = run_wide(pc, kind, size, every)
    assert bad != want


@pytest.mark.parametrize("flow", FLOWS)
def test_the_float_save_names_no_label_of_another_module(
        flow, tmp_path, libs):
    """No second copy of the float data: the program links without a
    message about a duplicate symbol (sdas) and without rt_fdata."""
    paths, err = build_flow(tmp_path, {"t.c": FLOAT % KINDS["int"][0]},
                            flow, libs, extra=("--isr-save-workareas",))
    assert (tmp_path / "t.bin").exists()
    if flow == "um80":
        assert "defined in" not in err


def run_sin(paths, kind, period=450001):
    """Run the FLOAT_SIN program; (machine, the 4 bytes of out)."""
    m = machine(paths)
    _p, source, vec = KINDS[kind]
    if source == "nmi":
        m.add_nmi_every(period, start=7000)
    else:
        m.add_irq_every(period, start=7000, vec=vec)
    m.run(max_tstates=30_000_000)
    return m, bytes(m.peek("_out", 4))


@pytest.mark.parametrize("kind", ["nmi", "int"])
@pytest.mark.parametrize("flow", FLOWS)
def test_the_libc_work_areas_are_saved(kind, flow, tmp_path, libs):
    """sin and sqrt in the main loop and in the handler.  lc_vm (the float
    interpreter), lc_sqrt and the float work areas are saved by the
    prologue through the routines __wasv_<block> of the library, so the
    result equals the result of a run without interrupts.  Without the
    option the result is wrong."""
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    ref_paths, _err = build_flow(ref_dir, {"t.c": FLOAT_SIN % ""}, flow,
                                 libs)
    ref_m = machine(ref_paths)
    ref_m.run(max_tstates=30_000_000)
    want = bytes(ref_m.peek("_out", 4))
    assert want != bytes(4)

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    paths, err = build_flow(bad_dir, {"t.c": FLOAT_SIN % KINDS[kind][0]},
                            flow, libs, extra=("--no-isr-save-workareas",))
    assert "(work area of lc_vm)" in err
    assert "--no-isr-save-workareas" in err
    m, bad = run_sin(paths, kind)
    assert m.peek16("_isr_n") >= 3
    assert bad != want

    paths, err = build_flow(tmp_path, {"t.c": FLOAT_SIN % KINDS[kind][0]},
                            flow, libs, extra=("--isr-save-workareas",))
    assert "warning" not in err
    note = err.split("saves and restores")[1].split("\n")[0]
    for block in ("lc_vm", "lc_sqrt", "rt_fdata", "rt_fmul"):
        assert block in note
    m, got = run_sin(paths, kind)
    assert m.peek16("_isr_n") >= 3
    assert got == want
    assert m.rom_writes == []


def test_every_work_area_module_has_the_save_helpers():
    """gen_wa_helpers.py has run on every module of lib/lc and lib/rt."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, str(REPO / "src" / "uc80" / "lib"
                                            / "gen_wa_helpers.py"),
                        "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_a_handler_without_libm_links_no_helper(tmp_path):
    """The helpers cost nothing for a handler that reaches no library work
    area: the program has no __wasv_ label."""
    r = compile_text(tmp_path, """
volatile unsigned char n;
#pragma uc80 nmi isr
#pragma uc80 isr_save_workareas isr
void isr(void) { n++; }
int main(void) { return 0; }
""")
    assert r.returncode == 0, r.stderr
    assert "__wasv_" not in (tmp_path / "t.mac").read_text()


def test_the_option_is_an_error_without_the_whole_program(tmp_path):
    r = compile_text(tmp_path, """
#include <stdint.h>
volatile uint64_t v;
#pragma uc80 interrupt isr
void isr(void) { v = v * 3 + v / 7; }
""", "--isr-save-workareas", "--no-whole-program")
    assert r.returncode == 1
    assert "needs the whole program" in r.stderr


# ---- what the analysis cannot see, and what it says about it ---------------

def test_a_call_to_another_module_is_warned(tmp_path):
    """A handler that calls a routine of another module: the compiler
    cannot see the work areas of the routine, and says so."""
    r = compile_text(tmp_path, """
extern void helper(void);
#pragma uc80 nmi isr
void isr(void) { helper(); }
""", "--no-whole-program")
    assert r.returncode == 0, r.stderr
    assert "calls '_helper'" in r.stderr
    assert "which this compilation does not define" in r.stderr


def test_a_call_inside_the_program_gives_no_such_warning(tmp_path):
    r = compile_text(tmp_path, """
static void helper(void) { }
#pragma uc80 nmi isr
void isr(void) { helper(); }
int main(void) { return 0; }
""")
    assert "does not define" not in r.stderr


def test_the_pointer_note_through_qsort(tmp_path):
    """qsort calls the comparison through a trampoline in RAM: a call
    through a pointer, and the comparison function is reached."""
    r = compile_text(tmp_path, """
#include <stdlib.h>
volatile int a[4] = {3, 1, 2, 0};
static int cmp(const void *x, const void *y)
{ return *(const int *) x - *(const int *) y; }
#pragma uc80 nmi isr
void isr(void) { qsort((void *) a, 4, sizeof(int), cmp); }
int main(void) { return 0; }
""")
    assert "calls through a function pointer (_isr -> _qsort)" in r.stderr
    assert "cmp" in r.stderr.split("function pointer")[1].split("\n")[0]


def test_the_pointer_note_lists_reached_functions_not_tables(tmp_path):
    """The list of the note has the library functions that the handler
    reaches (sin goes through the math VM), and no data table."""
    r = compile_text(tmp_path, """
#include <math.h>
volatile double v;
static const unsigned char tbl[4] = {1, 2, 3, 4};
volatile unsigned char t;
#pragma uc80 nmi isr
void isr(void) { v = sin(v); t = tbl[t & 3]; }
int main(void) { return 0; }
""")
    note = [l for l in r.stderr.splitlines() if "function pointer" in l][0]
    assert "()" not in note
    assert "tbl" not in note


# ---- the default, the opt-out, libm through a pointer, the split build ------

LIBM_ISR = """
#include <math.h>
volatile double r;
volatile double (*volatile fp)(double) = cos;
#pragma uc80 nmi isr
void isr(void) { r = %s; }
int main(void) { return (int) r; }
"""


def test_a_handler_that_reaches_libm_saves_the_work_areas_by_default(tmp_path):
    r = compile_text(tmp_path, LIBM_ISR % "sin(2.5) + sqrt(7.0)")
    assert r.returncode == 0, r.stderr
    assert "saves and restores the work areas of" in r.stderr
    assert "lc_vm" in r.stderr and "lc_sqrt" in r.stderr
    assert "reaches" not in r.stderr             # no work-area warning


def test_the_opt_out_keeps_the_warning(tmp_path):
    r = compile_text(tmp_path, LIBM_ISR % "sin(2.5) + sqrt(7.0)",
                     "--no-isr-save-workareas")
    assert r.returncode == 0, r.stderr
    assert "saves and restores" not in r.stderr
    assert "work area of lc_vm" in r.stderr
    assert "--no-isr-save-workareas" in r.stderr


def test_a_library_function_called_through_a_pointer_is_saved(tmp_path):
    """The handler calls cos only through the pointer fp, whose value is
    the address of the library routine."""
    r = compile_text(tmp_path, LIBM_ISR % "fp(2.5)")
    assert r.returncode == 0, r.stderr
    assert "calls through a function pointer" in r.stderr
    assert "saves and restores the work areas of" in r.stderr
    assert "lc_vm" in r.stderr


def test_a_split_build_warns_and_the_hint_names_the_whole_program(tmp_path):
    r = compile_text(tmp_path, LIBM_ISR % "sin(2.5)", "--no-whole-program")
    assert r.returncode == 0, r.stderr
    assert "work area of lc_vm" in r.stderr
    assert "needs the whole program" in r.stderr
    assert "--isr-save-workareas makes the handler" not in r.stderr
    r = compile_text(tmp_path, LIBM_ISR % "sin(2.5)", "--no-whole-program",
                     "--isr-save-workareas")
    assert r.returncode == 1
    assert "needs the whole program" in r.stderr
