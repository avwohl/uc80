"""A pointer that holds a constant address is a direct access (stage B,
src/uc80/ptrabs.py).  See docs/rom_hardware.md ("Pointers to hardware").

The programs run in bench/romrun, in the um80 flow and in the sdas flow.
The hardware registers are at ROM addresses (4000h and up), so each write is
in the log of the runner (`rom_writes`, in order, byte by byte).  A read has
no log in the runner: the test puts a breakpoint on each instruction that
reads one of the registers.  The breakpoint records the read and puts the
next value of a script in the register, so each read of a register returns
a value that the test chose.  A register read through a pointer in HL would
not be seen, so a missing breakpoint hit shows a change that did not happen.
"""

import pytest

from .conftest import HAVE_SDAS
from .rom_flows import FLOWS, build_flow, machine, need_flow

@pytest.fixture
def libs(request):
    return request.getfixturevalue("rom_libs") if HAVE_SDAS else None


HW_LO, HW_HI = 0x4000, 0x4400

# opcode bytes of the reads of an absolute address (the operand follows)
READ_OPS = {
    (0x3A,): 1,             # ld a,(nn)
    (0x2A,): 2,             # ld hl,(nn)
    (0xED, 0x4B): 2,        # ld bc,(nn)
    (0xED, 0x5B): 2,        # ld de,(nn)
}


def read_sites(img):
    """[(pc, address, width)] of the reads of 4000h..43FFh in the image."""
    out = []
    for i in range(len(img) - 3):
        for ops, width in READ_OPS.items():
            n = len(ops)
            if tuple(img[i:i + n]) == ops:
                a = img[i + n] | (img[i + n + 1] << 8)
                if HW_LO <= a < HW_HI:
                    out.append((i, a, width))
    return out


def run_traced(paths, script=None, fixed=None, t=3_000_000):
    """Run the image.  `script`: {address: [values]} for the reads of a
    register (the last value repeats); `fixed`: {address: value} put in
    memory before the run.  Returns (machine, events): the reads and the
    writes in order, as ("R", addr, width) and ("W", addr, value)."""
    m = machine(paths)
    for a, v in (fixed or {}).items():
        m.cpu.mem[a] = v
    seq = {a: list(v) for a, v in (script or {}).items()}
    events = []

    def on_read(mm, pc, addr=None, width=1):
        vals = seq.get(addr)
        if vals:
            v = vals.pop(0) if len(vals) > 1 else vals[0]
            mm.cpu.mem[addr] = v & 0xFF
            if width == 2:
                mm.cpu.mem[addr + 1] = (v >> 8) & 0xFF
        events.append((mm.t, 0, "R", addr, width))
        return None

    for pc, a, w in read_sites((paths[0]).read_bytes()):
        m._add_bp(pc, lambda mm, p, a=a, w=w: on_read(mm, p, a, w))
    res = m.run(max_tstates=t)
    assert res.reason == "halt", res.reason
    for (tt, a, v, _pc) in m.rom_writes:
        events.append((tt, 1, "W", a, v))
    events.sort()
    return m, [(k, a, x) for (_t, _o, k, a, x) in events]


REGS = """
#include <stdint.h>
struct chip {
    volatile uint8_t addr;
    volatile uint8_t data;
    volatile uint16_t w;
    volatile uint8_t arr[4];
};
typedef struct { volatile uint8_t status; volatile uint8_t cmd; } dev_t;

static volatile uint8_t *const ym = (volatile uint8_t *)0x4000;
static struct chip *const ch = (struct chip *)0x4100;
static dev_t *const dev = (dev_t *)0x4180;
static volatile uint8_t *pp;
volatile uint8_t out[8];

static void setup(void) { pp = (volatile uint8_t *)0x4200; }

int main(void)
{
    volatile uint16_t *const w = (volatile uint16_t *)0x4300;
    setup();
    ym[0] = 1;
    ym[1] = 2;
    ym[1] = 2;
    out[0] = ym[0];
    out[1] = ch->data;
    ch->w = 0x1234;
    out[2] = (uint8_t)ch->w;
    ch->arr[2] = 7;
    *pp = 9;
    out[3] = *pp;
    *w = 0xBEEF;
    out[4] = (uint8_t)(*w >> 8);
    ch->addr |= 3;
    dev->cmd = 0x55;
    out[5] = dev->status;
    return 0;
}
"""

# the writes in order, byte by byte; and the reads in order
EXPECT = [
    ("W", 0x4000, 1),
    ("W", 0x4001, 2),
    ("W", 0x4001, 2),
    ("R", 0x4000, 1),
    ("R", 0x4101, 1),
    ("W", 0x4102, 0x34), ("W", 0x4103, 0x12),
    ("R", 0x4102, 2),
    ("W", 0x4106, 7),
    ("W", 0x4200, 9),
    ("R", 0x4200, 1),
    ("W", 0x4300, 0xEF), ("W", 0x4301, 0xBE),
    ("R", 0x4300, 2),
    ("R", 0x4100, 1), ("W", 0x4100, 0x0B),
    ("W", 0x4181, 0x55),
    ("R", 0x4180, 1),
]
SCRIPT = {0x4000: [0x11], 0x4101: [0x22], 0x4102: [0x3344], 0x4200: [0x55],
          0x4300: [0xA1B2], 0x4100: [0x08], 0x4180: [0x66]}


def _events_ignoring_ram(events):
    return [e for e in events if HW_LO <= e[1] < HW_HI]


@pytest.mark.parametrize("flow", FLOWS)
def test_every_access_happens_in_order_through_constant_pointers(
        flow, tmp_path, libs):
    paths, _err = build_flow(tmp_path, {"t.c": REGS}, flow, libs)
    m, events = run_traced(paths, SCRIPT)
    assert _events_ignoring_ram(events) == EXPECT
    out = m.peek("_out", 6)
    assert list(out) == [0x11, 0x22, 0x44, 0x55, 0xA1, 0x66]


def test_the_assembly_has_no_pointer_in_hl(tmp_path):
    need_flow("um80")
    build_flow(tmp_path, {"t.c": REGS}, "um80")
    text = (tmp_path / "t.mac").read_text()
    for name in ("_ym", "_ch", "_dev", "_pp"):
        assert "(%s)" % name not in text, name
        assert "\n%s:" % name not in text, name         # no storage either
    assert "ld\tA,(___abs4000uc)" in text
    assert "ld\t(___abs4001uc),A" in text
    assert "ld\tHL,(___abs4102us)" in text
    assert "ld\t(___abs4106uc),A" in text
    assert "ld\tHL,(___abs4300us)" in text


@pytest.mark.parametrize("flow", FLOWS)
def test_same_result_as_the_access_through_the_pointer(
        flow, tmp_path, libs):
    """The program with --no-direct-pointers does the same writes and
    leaves the same values (the registers hold one value each)."""
    fixed = {0x4000: 0x11, 0x4101: 0x22, 0x4102: 0x44, 0x4103: 0x33,
             0x4200: 0x55, 0x4300: 0xB2, 0x4301: 0xA1, 0x4100: 0x08,
             0x4180: 0x66}
    res = {}
    for tag, extra in (("new", ()), ("old", ("--no-direct-pointers",))):
        d = tmp_path / tag
        d.mkdir()
        paths, _err = build_flow(d, {"t.c": REGS}, flow, libs,
                                 extra=extra)
        m = machine(paths)
        for a, v in fixed.items():
            m.cpu.mem[a] = v
        assert m.run(max_tstates=3_000_000).reason == "halt"
        res[tag] = (list(m.peek("_out", 6)),
                    [(a, v) for (_t, a, v, _pc) in m.rom_writes
                     if HW_LO <= a < HW_HI],
                    len(paths[0].read_bytes()))
    assert res["new"][:2] == res["old"][:2]
    assert res["new"][2] <= res["old"][2]


REPEAT = """
#include <stdint.h>
static volatile uint8_t *const reg = (volatile uint8_t *)0x4010;
volatile uint8_t out[4];
int main(void)
{
    uint8_t a, b;
    (void)*reg;
    (void)*reg;
    a = *reg;
    b = *reg;
    a = (uint8_t)(a + b);
    *reg = a;
    *reg = a;
    *reg = 0;
    out[0] = a;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_repeated_volatile_accesses_all_happen(flow, tmp_path, libs):
    paths, _err = build_flow(tmp_path, {"t.c": REPEAT}, flow, libs)
    m, events = run_traced(paths, {0x4010: [1, 2, 3, 4, 5, 6]})
    ev = _events_ignoring_ram(events)
    assert ev == [("R", 0x4010, 1)] * 4 + [("W", 0x4010, 7)] * 2 + [
        ("W", 0x4010, 0)]
    assert m.peek8("_out") == 7              # reads 3 and 4: 3 + 4


NEGATIVE = """
#include <stdint.h>
volatile uint8_t out[8];
/* two different constants: stays a pointer */
static volatile uint8_t *two;
/* its address is taken */
static volatile uint8_t *taken;
static volatile uint8_t **tp = &taken;
/* it moves */
static volatile uint8_t *walk;
/* not volatile: stays */
static uint8_t *const plain = (uint8_t *)0x4040;
/* not constant */
volatile uint8_t * volatile moving = (volatile uint8_t *)0x4050;
static volatile uint8_t *const shadow = (volatile uint8_t *)0x4060;
static volatile uint8_t *const passed = (volatile uint8_t *)0x4070;

static uint8_t use(volatile uint8_t *p) { return *p; }

int main(void)
{
    volatile uint8_t *shadow2;
    two = (volatile uint8_t *)0x4000;
    *two = 1;
    two = (volatile uint8_t *)0x4001;
    *two = 2;
    taken = (volatile uint8_t *)0x4002;
    **tp = 3;
    walk = (volatile uint8_t *)0x4010;
    *walk = 4;
    walk++;
    *walk = 5;
    *plain = 6;
    *moving = 7;
    {
        volatile uint8_t *const shadow = (volatile uint8_t *)0x4062;
        *shadow = 8;
    }
    *shadow = 9;
    shadow2 = shadow;
    *shadow2 = 10;
    out[0] = use(passed);
    *passed = 11;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_pointers_that_do_not_qualify_keep_working(flow, tmp_path, libs):
    fixed = {0x4070: 0x42}
    res = {}
    for tag, extra in (("new", ()), ("old", ("--no-direct-pointers",))):
        d = tmp_path / tag
        d.mkdir()
        paths, _err = build_flow(d, {"t.c": NEGATIVE}, flow, libs,
                                 extra=extra)
        m = machine(paths)
        for a, v in fixed.items():
            m.cpu.mem[a] = v
        assert m.run(max_tstates=3_000_000).reason == "halt"
        res[tag] = ([(a, v) for (_t, a, v, _pc) in m.rom_writes
                     if HW_LO <= a < HW_HI], m.peek8("_out"))
    want = [(0x4000, 1), (0x4001, 2), (0x4002, 3), (0x4010, 4), (0x4011, 5),
            (0x4040, 6), (0x4050, 7), (0x4062, 8), (0x4060, 9), (0x4060, 10),
            (0x4070, 11)]
    assert res["new"][0] == want
    assert res["old"][0] == want
    assert res["new"][1] == res["old"][1] == 0x42


def test_pointers_that_do_not_qualify_stay_in_the_assembly(tmp_path):
    need_flow("um80")
    build_flow(tmp_path, {"t.c": NEGATIVE}, "um80")
    text = (tmp_path / "t.mac").read_text()
    for name in ("_two", "_walk", "_plain", "_moving", "_taken"):
        assert "\n%s:" % name in text, name
    # the const pointers of the shadow test: the block-scope one and the
    # file-scope one are different objects
    assert "___abs4062uc" in text
    assert "___abs4060uc" in text


SHADOW = """
#include <stdint.h>
volatile uint8_t out[4];
static volatile uint8_t *const reg = (volatile uint8_t *)0x4000;
static void f(volatile uint8_t *reg) { *reg = 5; }       /* a parameter */
int main(void)
{
    volatile uint8_t buf[2] = {0, 0};
    *reg = 1;
    f(&buf[0]);
    out[0] = buf[0];
    for (int i = 0; i < 2; i++) {
        volatile uint8_t *reg = &buf[1];                 /* a local */
        *reg = (uint8_t)(i + 20);
    }
    out[1] = buf[1];
    *reg = 2;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_a_parameter_and_a_local_of_the_same_name_are_not_the_constant(
        flow, tmp_path, libs):
    paths, _err = build_flow(tmp_path, {"t.c": SHADOW}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    assert [(a, v) for (_t, a, v, _pc) in m.rom_writes
            if HW_LO <= a < HW_HI] == [(0x4000, 1), (0x4000, 2)]
    assert list(m.peek("_out", 2)) == [5, 21]


TWO_FILES = ("""
#include <stdint.h>
extern volatile uint8_t out[2];
volatile uint8_t * const exported = (volatile uint8_t *)0x4020;
extern void other(void);
int main(void) { *exported = 3; other(); return 0; }
""", """
#include <stdint.h>
extern volatile uint8_t * const exported;
void other(void) { *exported = 4; }
""")


@pytest.mark.parametrize("flow", FLOWS)
def test_a_const_pointer_with_external_linkage_keeps_its_definition(
        flow, tmp_path, libs):
    paths, _err = build_flow(
        tmp_path, {"a.c": TWO_FILES[0].replace(
            "extern volatile uint8_t out[2];", "volatile uint8_t out[2];"),
            "b.c": TWO_FILES[1]}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=3_000_000).reason == "halt"
    assert [(a, v) for (_t, a, v, _pc) in m.rom_writes
            if HW_LO <= a < HW_HI] == [(0x4020, 3), (0x4020, 4)]


LOCAL_PTR = """
#include <stdint.h>
volatile uint8_t out[2];
int main(void)
{
    volatile uint8_t *p;
    volatile uint8_t * const q = (volatile uint8_t *)0x4030;
    p = (volatile uint8_t *)0x4031;
    *p = 1;
    *q = 2;
    p[0] = 3;
    out[0] = *q;
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_local_pointers_are_direct_accesses(flow, tmp_path, libs):
    paths, _err = build_flow(tmp_path, {"t.c": LOCAL_PTR}, flow, libs)
    m, events = run_traced(paths, {0x4030: [0x77]})
    assert _events_ignoring_ram(events) == [
        ("W", 0x4031, 1), ("W", 0x4030, 2), ("W", 0x4031, 3),
        ("R", 0x4030, 1)]
    assert m.peek8("_out") == 0x77


def test_cpm_program_runs(tmp_path):
    """The same pass in a CP/M program: RAM, so the values stay."""
    import subprocess
    import sys
    from tests.test_struct_return_abi import CPMEMU, LIB_DIR, REPO
    if not (CPMEMU.exists() and (LIB_DIR / "libc.lib").exists()):
        pytest.skip("needs cpmemu and the libraries")
    (tmp_path / "t.c").write_text("""
#include <stdio.h>
#include <stdint.h>
struct r { volatile uint8_t a; volatile uint16_t b; };
static struct r *const rp = (struct r *)0xC000;
static volatile uint8_t *q;
int main(void)
{
    q = (volatile uint8_t *)0xC010;
    rp->a = 7; rp->b = 0x1234; *q = 9; rp->a += 1;
    printf("%d %x %d\\n", rp->a, rp->b, *q);
    return 0;
}
""")
    r = subprocess.run([sys.executable, "-m", "uc80.main",
                        str(tmp_path / "t.c"), "-o", str(tmp_path / "t.mac")],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    assert "___abs" in (tmp_path / "t.mac").read_text()
    subprocess.run(["um80", "t.mac", "-o", "t.rel"], cwd=tmp_path,
                   check=True, capture_output=True)
    subprocess.run(["ul80", str(LIB_DIR / "crt0.rel"), "t.rel",
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", "t.com"], cwd=tmp_path, check=True,
                   capture_output=True)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "t.com")],
                         capture_output=True, timeout=60)
    assert run.stdout.decode("latin-1").replace("\r", "") == "8 1234 9\n"


ASM_NAMED = """
#include <stdint.h>
volatile uint8_t out[2];
static volatile uint8_t *const reg = (volatile uint8_t *)0x4000;
static volatile uint8_t *const other = (volatile uint8_t *)0x4001;
int main(void)
{
    *reg = 1;
    *other = 2;
    __asm__("\\tld\\thl,(_reg)\\n\\tld\\ta,l\\n\\tld\\t(_out),a");
    return 0;
}
"""


def test_a_pointer_that_asm_text_names_keeps_its_storage(tmp_path):
    need_flow("um80")
    build_flow(tmp_path, {"t.c": ASM_NAMED}, "um80")
    text = (tmp_path / "t.mac").read_text()
    assert "\n_reg:" in text                  # named in the asm text: stays
    assert "\n_other:" not in text            # not named: direct
    paths = (tmp_path / "t.bin", tmp_path / "t.sym")
    m = machine(paths)
    assert m.run(max_tstates=200_000).reason == "halt"
    assert m.peek8("_out") == 0x00            # low byte of 4000h
    assert [(a, v) for (_t, a, v, _pc) in m.rom_writes
            if HW_LO <= a < HW_HI] == [(0x4000, 1), (0x4001, 2)]


VARIADIC = """
#include <stdarg.h>
#include <stdint.h>
volatile uint8_t out[2];
static volatile uint8_t *const reg = (volatile uint8_t *)0x4000;
static int sum(int n, ...)
{
    va_list ap;
    int s = 0;
    va_start(ap, n);
    while (n--) s += va_arg(ap, int);
    va_end(ap);
    return s;
}
int main(void)
{
    *reg = (uint8_t)sum(3, 1, 2, 3);
    return 0;
}
"""


@pytest.mark.parametrize("flow", FLOWS)
def test_a_function_with_a_variable_argument_list(flow, tmp_path, libs):
    paths, _err = build_flow(tmp_path, {"t.c": VARIADIC}, flow, libs)
    m = machine(paths)
    assert m.run(max_tstates=500_000).reason == "halt"
    assert [(a, v) for (_t, a, v, _pc) in m.rom_writes
            if HW_LO <= a < HW_HI] == [(0x4000, 6)]
