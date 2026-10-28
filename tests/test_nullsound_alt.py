"""The warning for code that uses the alternate registers next to
nullsound (--with-nullsound).  See docs/rom_interop.md.

nullsound's NMI handler keeps its state in AF', BC', DE' and HL'.  A C
function (inline assembly or an assembly file) or a runtime helper (the
float division) that executes EXX or EX AF,AF' can corrupt that state.  The
programs also run in bench/romrun, in the um80 flow and in the sdas flow.
"""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import FLOWS, build_flow, machine

pytestmark = pytest.mark.skipif(
    not (HAVE_SDAS or __import__("shutil").which("um80")),
    reason="needs um80 and ul80, or sdas")


@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


FLOAT_DIV = """
volatile float a = 1.5f, b = 2.25f, r;
volatile int out;
void cmd(void) { r = a / b; out = (int) (r * 9.0f); }
int main(void) { cmd(); return 0; }
"""

EXX_C = """
volatile int out;
static void burn(void) { __asm__("\\texx\\n\\tld bc,1234h\\n\\texx"); }
void helper(void) { burn(); out = 7; }
void plain(void) { out = 3; }
int main(void) { helper(); plain(); return 0; }
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_float_division_is_reported_with_the_chain(flow, tmp_path, libs):
    paths, err = build_flow(tmp_path, {"t.c": FLOAT_DIV}, flow, libs,
                            extra=("--with-nullsound",))
    assert "runtime helper '__fdiv_man' uses EXX or EX AF,AF'" in err
    assert "_cmd -> __fdiv" in err and "nullsound" in err
    m = machine(paths)
    assert m.run(max_tstates=20_000_000).reason == "halt"
    assert m.peek16("_out") == 6


@pytest.mark.parametrize("flow", FLOWS)
def test_no_warning_without_the_option(flow, tmp_path, libs):
    _paths, err = build_flow(tmp_path, {"t.c": FLOAT_DIV}, flow, libs)
    assert "nullsound" not in err


@pytest.mark.parametrize("flow", FLOWS)
def test_a_c_function_with_exx_is_reported_by_name(flow, tmp_path, libs):
    paths, err = build_flow(tmp_path, {"t.c": EXX_C}, flow, libs,
                            extra=("--with-nullsound",))
    assert "function '_burn' uses EXX or EX AF,AF'" in err
    assert "plain" not in err                    # the clean function
    m = machine(paths)
    assert m.run(max_tstates=2_000_000).reason == "halt"
    assert m.peek16("_out") == 3


@pytest.mark.parametrize("flow", FLOWS)
def test_every_user_is_reported_once(flow, tmp_path, libs):
    src = """
volatile int out;
void e1(void) { __asm__("\\texx\\n\\texx"); out = 1; }
void e2(void) { __asm__("\\texx\\n\\texx"); out = 2; }
int main(void) { e1(); e2(); e1(); return 0; }
"""
    _paths, err = build_flow(tmp_path, {"t.c": src}, flow, libs,
                             extra=("--with-nullsound",))
    assert err.count("function '_e1' uses EXX") == 1
    assert err.count("function '_e2' uses EXX") == 1


@pytest.mark.parametrize("flow", FLOWS)
def test_an_interrupt_function_that_divides_floats_is_reported(
        flow, tmp_path, libs):
    src = """
volatile float a = 1.5f, b = 2.25f, r;
volatile int n;
#pragma uc80 interrupt isr
void isr(void) { r = a / b; n++; }
int main(void) { return 0; }
"""
    _paths, err = build_flow(tmp_path, {"t.c": src}, flow, libs,
                             extra=("--with-nullsound",))
    assert "reached from interrupt function 'isr'" in err
