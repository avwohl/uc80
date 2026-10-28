"""Interrupt functions, port intrinsics, critical sections and absolute
objects of the ROM target.  See docs/rom.md ("Hardware access").

The tests compile, assemble and link with uc80, um80 and ul80, and run the
image in the bench/romrun Z80 runner with injected IRQ and NMI.  They skip
when a tool is missing.
"""

import re
import shutil
import subprocess

import pytest

from .test_rom_target import (_ul80_has_rom_options, compile_rom, link_rom,
                              machine, symbols)

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and _ul80_has_rom_options()),
    reason="needs um80 and a ul80 with ROM options")

OP_RETI = b"\xed\x4d"
OP_RETN = b"\xed\x45"


def build(tmp_path, source, *extra, files=()):
    r = compile_rom(tmp_path, source, *extra, files=files)
    assert r.returncode == 0, r.stderr
    link_rom(tmp_path)
    return r.stderr


def image(tmp_path):
    return (tmp_path / "t.bin").read_bytes()


def isr_bytes(tmp_path, name):
    """The bytes of the function ``name`` up to and including RETI or RETN."""
    img = image(tmp_path)
    a = symbols(tmp_path)["_" + name.upper()]
    end = min(i for i in (img.find(OP_RETI, a), img.find(OP_RETN, a))
              if i >= 0)
    return img[a:end + 2]


def expect_error(tmp_path, source, text, *extra):
    r = compile_rom(tmp_path, source, *extra)
    assert r.returncode == 1, r.stderr
    assert "uc80: error:" in r.stderr
    assert text in r.stderr, r.stderr
    assert "Traceback" not in r.stderr


TICK = """
#include <stdint.h>
volatile uint8_t ticks;
%s
int main(void)
{
    __asm__("\\tei");
    while (ticks < 5)
        ;
    __asm__("\\tdi");
    return 0;
}
"""


class TestInterruptFunction:
    def test_pragma_interrupt_vector_and_reti(self, tmp_path):
        build(tmp_path, TICK % """
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
""")
        img = image(tmp_path)
        sy = symbols(tmp_path)
        assert img[0x38] == 0xC3                      # JP tick
        assert int.from_bytes(img[0x39:0x3B], "little") == sy["_TICK"]
        body = isr_bytes(tmp_path, "tick")
        assert body[0] == 0xFB                        # EI first
        assert body[1:6] == b"\xf5\xc5\xd5\xe5\xfd"   # PUSH AF BC DE HL IY
        assert body[-8:] == b"\xfd\xe1\xe1\xd1\xc1\xf1\xed\x4d"[-8:]
        assert body.endswith(OP_RETI)
        m = machine(tmp_path)
        m.add_irq_every(2000, start=2000)
        res = m.run(max_tstates=200_000)
        assert res.reason == "halt"
        assert m.peek8("_ticks") == 5
        assert m.rom_writes == []

    def test_registers_survive_interrupts(self, tmp_path):
        """The main code keeps a checksum and loop state in registers while
        interrupts run an ISR that uses every register."""
        build(tmp_path, """
#include <stdint.h>
volatile uint16_t junk;
volatile uint8_t ticks;
volatile uint16_t sum_out;
uint16_t data[64];

#pragma uc80 interrupt isr
void isr(void)
{
    uint16_t a = junk;
    uint32_t b = 0x12345678UL;
    for (uint8_t i = 0; i < 9; i++)
        a = (uint16_t) (a * 3 + i + (b >> 3));
    junk = a;
    ticks++;
}

int main(void)
{
    uint16_t sum = 0;
    for (uint8_t i = 0; i < 64; i++)
        data[i] = (uint16_t) (i * 257u + 11);
    __asm__("\\tei");
    for (uint8_t round = 0; round < 40; round++)
        for (uint8_t i = 0; i < 64; i++)
            sum = (uint16_t) (sum + (data[i] ^ round) + (sum >> 3));
    __asm__("\\tdi");
    sum_out = sum;
    return 0;
}
""")
        expected = 0
        data = [(i * 257 + 11) & 0xFFFF for i in range(64)]
        for rnd in range(40):
            for i in range(64):
                expected = (expected + (data[i] ^ rnd) + (expected >> 3)) & 0xFFFF
        m = machine(tmp_path)
        m.add_irq_every(6000, start=6000)
        res = m.run(max_tstates=20_000_000)
        assert res.reason == "halt"
        assert m.peek16("_sum_out") == expected
        assert m.peek8("_ticks") > 20             # many interrupts ran

    def test_interrupt_critical_ei_before_reti(self, tmp_path):
        build(tmp_path, TICK % """
#pragma uc80 interrupt tick critical
void tick(void) { ticks++; }
""")
        body = isr_bytes(tmp_path, "tick")
        assert body[0] == 0xF5                         # no EI at entry
        assert body.endswith(b"\xfb" + OP_RETI)        # EI, RETI
        m = machine(tmp_path)
        m.add_irq_every(2000, start=2000)
        assert m.run(max_tstates=200_000).reason == "halt"
        assert m.peek8("_ticks") == 5

    def test_pragma_nmi_vector_and_retn(self, tmp_path):
        build(tmp_path, """
#include <stdint.h>
volatile uint8_t n;
#pragma uc80 nmi on_nmi
void on_nmi(void) { n++; }
int main(void) { while (n < 4) ; return 0; }
""")
        img = image(tmp_path)
        assert img[0x66] == 0xC3
        body = isr_bytes(tmp_path, "on_nmi")
        assert body[0] == 0xF5 and body.endswith(OP_RETN)
        assert 0xFB not in body[:1] and body.count(b"\xfb") == 0
        m = machine(tmp_path)
        m.add_nmi_every(1000, start=1000, count=4)
        res = m.run(max_tstates=100_000)
        assert res.reason == "halt"
        assert m.peek8("_n") == 4
        assert len([a for a in m.accepted if a[0] == "nmi"]) >= 4

    def test_nmi_restores_iff1_with_retn(self, tmp_path):
        build(tmp_path, """
#include <stdint.h>
volatile uint8_t n, seen_iff;
#pragma uc80 nmi on_nmi
void on_nmi(void) { n++; }
int main(void)
{
    __asm__("\\tei");
    while (n < 2) ;
    return 0;
}
""")
        m = machine(tmp_path)
        m.add_nmi_every(3000, start=3000, count=2)
        assert m.run(max_tstates=100_000).reason == "idle"   # HALT, EI
        assert m.peek8("_n") == 2
        assert m.regs()["IFF1"] == 1                  # RETN restored it

    def test_static_isr_and_no_pragma_public(self, tmp_path):
        """A static function can be an interrupt function (the vector
        jump needs its label)."""
        build(tmp_path, TICK % """
#pragma uc80 interrupt tick
static void tick(void) { ticks++; }
""")
        m = machine(tmp_path)
        m.add_irq_every(2000, start=2000)
        assert m.run(max_tstates=200_000).reason == "halt"
        assert m.peek8("_ticks") == 5

    def test_isr_calls_ordinary_functions(self, tmp_path):
        build(tmp_path, TICK % """
static uint8_t bump(uint8_t v) { return (uint8_t) (v + 1); }
#pragma uc80 interrupt tick
void tick(void) { ticks = bump(ticks); }
""")
        m = machine(tmp_path)
        m.add_irq_every(2500, start=2500)
        assert m.run(max_tstates=200_000).reason == "halt"
        assert m.peek8("_ticks") == 5

    def test_locals_early_return_and_frame_pointer(self, tmp_path):
        """An interrupt function with a stack frame and an early return
        leaves IX, SP and the interrupted code's locals alone."""
        build(tmp_path, """
#include <stdint.h>
volatile uint8_t ticks, skipped;
volatile uint16_t out_a, out_b;
#pragma uc80 interrupt isr
void isr(void)
{
    uint8_t buf[12];
    uint8_t i;
    for (i = 0; i < 12; i++)
        buf[i] = (uint8_t) (i * ticks);
    if (ticks & 1) {
        skipped++;
        ticks++;
        return;
    }
    ticks += buf[11] & 1;
    ticks++;
}
int main(void)
{
    uint16_t a = 1, b = 2;
    uint8_t loc[8];
    for (uint8_t i = 0; i < 8; i++)
        loc[i] = (uint8_t) (i + 1);
    __asm__("\\tei");
    for (uint16_t k = 0; k < 3000; k++) {
        a = (uint16_t) (a * 5 + loc[k & 7]);
        b = (uint16_t) (b + a + (loc[(k + 3) & 7] ^ (uint8_t) k));
    }
    __asm__("\\tdi");
    out_a = a;
    out_b = b;
    return 0;
}
""")
        a, b = 1, 2
        loc = list(range(1, 9))
        for k in range(3000):
            a = (a * 5 + loc[k & 7]) & 0xFFFF
            b = (b + a + (loc[(k + 3) & 7] ^ (k & 0xFF))) & 0xFFFF
        m = machine(tmp_path)
        m.add_irq_every(9000, start=9000)     # the handler takes about 4000
        res = m.run(max_tstates=20_000_000)
        assert res.reason == "halt"
        assert (m.peek16("_out_a"), m.peek16("_out_b")) == (a, b)
        assert m.peek8("_skipped") > 0 and m.peek8("_ticks") > 10
        assert m.min_sp is not None and m.rom_writes == []

    def test_isr_cycle_cost(self, tmp_path):
        """T-states from the vector to the end of an empty interrupt
        function: the cost of the register save and RETI."""
        build(tmp_path, TICK % """
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
""")
        sy = symbols(tmp_path)
        end = sy["_TICK"] + len(isr_bytes(tmp_path, "tick")) - 2
        m = machine(tmp_path)
        span = m.measure("isr", "_tick", end)
        m.add_irq_every(2000, start=2000)
        assert m.run(max_tstates=200_000).reason == "halt"
        assert span.count == 5
        # EI 4, PUSH AF/BC/DE/HL 11 each, PUSH IY 15, LD HL 10, INC (HL) 11,
        # POP IY 14, POP HL/DE/BC/AF 10 each; RETI (14) is not in the span.
        assert span.min == span.max == 4 + 44 + 15 + 10 + 11 + 14 + 40


class TestAlternateRegisters:
    ALT_ISR = """
#include <stdint.h>
volatile uint8_t ticks;
volatile uint16_t alt_bc, alt_de, alt_hl, alt_af;
extern void alt_user(void);
#pragma uc80 interrupt tick
void tick(void) { alt_user(); ticks++; }
int main(void)
{
    __asm__("\\texx\\n\\tld bc,1234h\\n\\tld de,5678h\\n\\tld hl,9abch\\n\\texx");
    __asm__("\\tex af,af'\\n\\tld a,0a5h\\n\\tex af,af'");
    __asm__("\\tei");
    while (ticks < 5) ;
    __asm__("\\tdi");
    __asm__("\\texx\\n\\tld (_alt_bc),bc\\n\\tld (_alt_de),de\\n\\tld (_alt_hl),hl\\n\\texx");
    __asm__("\\tex af,af'\\n\\tpush af\\n\\tpop hl\\n\\tld (_alt_af),hl\\n\\tex af,af'");
    return 0;
}
"""
    # A routine that uses the alternate registers, as the float48 runtime does.
    ALT_ASM = ("\t.Z80\n\tCSEG\n\tPUBLIC\t_alt_user\n_alt_user:\n"
               "\texx\n\tld\tbc,0ffffh\n\tld\tde,0eeeeh\n\tld\thl,0dddch\n\texx\n"
               "\tex\taf,af'\n\tld\ta,0\n\tex\taf,af'\n\tret\n\tend\n")

    def test_isr_that_reaches_exx_saves_alternates(self, tmp_path):
        (tmp_path / "alt.mac").write_text(self.ALT_ASM)
        err = build(tmp_path, self.ALT_ISR, files=[str(tmp_path / "alt.mac")])
        assert "alternate registers" in err
        assert "tick" in err and "_alt_user" in err and "->" in err
        body = isr_bytes(tmp_path, "tick")
        assert b"\xd9" in body and b"\x08" in body    # EXX, EX AF,AF'
        m = machine(tmp_path)
        m.add_irq_every(6000, start=6000)
        res = m.run(max_tstates=2_000_000)
        assert res.reason == "halt"
        assert m.peek8("_ticks") == 5
        assert m.peek16("_alt_bc") == 0x1234
        assert m.peek16("_alt_de") == 0x5678
        assert m.peek16("_alt_hl") == 0x9ABC
        assert m.peek16("_alt_af") >> 8 == 0xA5

    def test_a_pointer_to_an_exx_user_saves_the_alternates(self, tmp_path):
        """The ISR reaches the EXX routine through a pointer.  The check
        treats every function whose address is taken as reachable, so the
        alternates are saved and a note tells why."""
        (tmp_path / "alt.mac").write_text(self.ALT_ASM)
        src = self.ALT_ISR.replace(
            "void tick(void) { alt_user(); ticks++; }",
            "void (*volatile hide)(void) = alt_user;\n"
            "void tick(void) { hide(); ticks++; }")
        err = build(tmp_path, src, files=[str(tmp_path / "alt.mac")])
        assert "calls through a function pointer" in err
        assert "uc80: note:" in err and "alt_user" in err
        assert "alternate registers" in err
        m = machine(tmp_path)
        m.add_irq_every(6000, start=6000)
        assert m.run(max_tstates=2_000_000).reason == "halt"
        assert m.peek16("_alt_bc") == 0x1234
        assert m.peek16("_alt_de") == 0x5678

    def test_plain_isr_does_not_save_alternates(self, tmp_path):
        err = build(tmp_path, TICK % """
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
""")
        assert "alternate" not in err
        body = isr_bytes(tmp_path, "tick")
        assert b"\xd9" not in body and b"\x08" not in body


class TestRefusals:
    def test_isr_with_parameter(self, tmp_path):
        expect_error(tmp_path, """
#pragma uc80 interrupt f
void f(int x) { (void) x; }
int main(void) { return 0; }
""", "must take no parameters")

    def test_isr_with_return_value(self, tmp_path):
        expect_error(tmp_path, """
#pragma uc80 interrupt f
int f(void) { return 1; }
int main(void) { return 0; }
""", "must return void")

    def test_direct_call(self, tmp_path):
        expect_error(tmp_path, """
#pragma uc80 interrupt f
void f(void) { }
int main(void) { f(); return 0; }
""", "calls the interrupt function 'f'")

    def test_missing_function(self, tmp_path):
        expect_error(tmp_path, """
#pragma uc80 interrupt nothere
int main(void) { return 0; }
""", "nothere")

    def test_bad_pragma_form(self, tmp_path):
        expect_error(tmp_path, """
#pragma uc80 interrupt f extra words
void f(void) { }
int main(void) { return 0; }
""", "expected '#pragma uc80 interrupt NAME'")

    def test_two_functions_on_one_vector(self, tmp_path):
        expect_error(tmp_path, """
#pragma uc80 interrupt a
#pragma uc80 interrupt b
void a(void) { }
void b(void) { }
int main(void) { return 0; }
""", "vector is already set")

    @pytest.mark.parametrize("spelling", [
        "void f(void) [[gnu::interrupt]] { }",
        "__attribute__((interrupt)) void f(void) { }",
        "void __naked f(void) { }",
        "void f(void) __critical { }",
        "void f(void) __banked { }",
        "void f(void) __interrupt __reentrant { }",
    ])
    def test_other_spellings_stay_errors(self, tmp_path, spelling):
        expect_error(tmp_path, spelling + "\nint main(void) { return 0; }\n",
                     "")


class TestSdccSpellings:
    @pytest.mark.parametrize("spelling,ends,ei_first", [
        ("__interrupt", OP_RETI, True),
        ("__interrupt(1)", OP_RETI, True),
        ("__critical __interrupt", OP_RETN, False),
        ("__critical __interrupt(2)", b"\xfb" + OP_RETI, False),
        ("__interrupt __critical", OP_RETN, False),
    ])
    def test_interrupt_spellings(self, tmp_path, spelling, ends, ei_first):
        build(tmp_path, TICK % f"void tick(void) {spelling} {{ ticks++; }}")
        body = isr_bytes(tmp_path, "tick")
        assert body.endswith(ends)
        assert (body[0] == 0xFB) == ei_first
        img = image(tmp_path)
        vec = 0x66 if ends == OP_RETN else 0x38
        assert img[vec] == 0xC3
        assert (int.from_bytes(img[vec + 1:vec + 3], "little")
                == symbols(tmp_path)["_TICK"])

    def test_sdcc_form_equals_pragma_form(self, tmp_path):
        build(tmp_path, TICK % "void tick(void) __interrupt { ticks++; }")
        a = image(tmp_path)
        build(tmp_path, TICK % """
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
""")
        assert image(tmp_path) == a

    def test_sdcc_interrupt_runs(self, tmp_path):
        build(tmp_path, TICK % "void tick(void) __interrupt(1) { ticks++; }")
        m = machine(tmp_path)
        m.add_irq_every(2000, start=2000)
        assert m.run(max_tstates=200_000).reason == "halt"
        assert m.peek8("_ticks") == 5

    def test_interrupt_in_a_name_is_not_a_marker(self, tmp_path):
        build(tmp_path, """
int interrupt_count;
struct interrupt { int a; };
int main(void) { struct interrupt s; s.a = 1; interrupt_count = s.a; return 0; }
""")


class TestDeadCode:
    def test_asm_dce_reti_ends_a_block(self):
        from uc80.asm_dce import eliminate_dead_code
        code = """\t.z80
\tCSEG
\tPUBLIC\t_isr
\tPUBLIC\t_main
_isr:
\tpush\tAF
\tpop\tAF
\treti
_dead:
\tld\tA,1
\tret
_main:
\tret
\tend
"""
        out = eliminate_dead_code(code, entry_points={"_isr", "_main"})
        assert "_dead" not in out
        out = eliminate_dead_code(code.replace("reti", "retn"),
                                  entry_points={"_isr", "_main"})
        assert "_dead" not in out

    def test_peephole_leaves_call_before_reti(self):
        from uc80 import peep_local
        text = "\tcall\t_f\n\tpop\tAF\n\treti\n"
        assert "jp" not in peep_local.optimize(text)
        text = "\tcall\t_f\n\treti\n"
        assert "jp" not in peep_local.optimize(text)
