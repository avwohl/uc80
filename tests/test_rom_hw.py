"""Port intrinsics, __sfr, critical sections, atomic reads, absolute objects
and --isr-safe of the ROM target.  See docs/rom.md ("Hardware access").
"""

import shutil

import pytest

from .test_rom_isr import build, expect_error, image
from .test_rom_target import (_ul80_has_rom_options, compile_rom, link_rom,
                              machine, symbols)

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and _ul80_has_rom_options()),
    reason="needs um80 and a ul80 with ROM options")


def mac(tmp_path):
    return (tmp_path / "t.mac").read_text()


def ports(m, kind=None):
    return [(k, p, v) for (_t, k, p, v) in m.port_log
            if kind is None or k == kind]


def run_ok(m, t=2_000_000):
    res = m.run(max_tstates=t)
    assert res.reason == "halt", res.reason
    return res


class TestPortIntrinsics:
    def test_out_is_never_merged_or_dropped(self, tmp_path):
        build(tmp_path, """
#include <z80.h>
int main(void)
{
    z80_out(0x04, 0x28);
    z80_out(0x04, 0x28);
    z80_out(0x04, 0x28);
    z80_out(0x05, 0x01);
    return 0;
}
""")
        m = machine(tmp_path)
        run_ok(m)
        assert [(p & 0xFF, v) for _k, p, v in ports(m, "OUT")] == [
            (4, 0x28), (4, 0x28), (4, 0x28), (5, 1)]
        text = mac(tmp_path)
        assert text.count("out\t(4),A") == 3

    def test_in_with_constant_port_and_expression_use(self, tmp_path):
        build(tmp_path, """
#include <z80.h>
volatile unsigned int r;
int main(void)
{
    r = z80_in(0x02) + z80_in(0x02);
    r = (z80_in(0x03) & 0x80) ? 7 : 9;
    return 0;
}
""")
        m = machine(tmp_path)
        m.feed.queue(0x02, [10, 20])
        m.feed.queue(0x03, [0x80])
        run_ok(m)
        assert [(p & 0xFF, v) for _k, p, v in ports(m, "IN")] == [
            (2, 10), (2, 20), (3, 0x80)]
        assert m.peek16("_r") == 7
        assert "in\tA,(2)" in mac(tmp_path)

    def test_variable_port_and_value(self, tmp_path):
        build(tmp_path, """
#include <z80.h>
volatile unsigned char sink;
void send(unsigned char port, unsigned char v) { z80_out(port, v); }
unsigned char recv(unsigned char port) { return z80_in(port); }
int main(void)
{
    send(0x10, 1);
    send(0x11, 0xFF);
    sink = recv(0x12);
    send(0x13, sink + 1);
    return 0;
}
""")
        m = machine(tmp_path)
        m.feed.const(0x12, 0x41)
        run_ok(m)
        assert [(p & 0xFF, v) for _k, p, v in ports(m, "OUT")] == [
            (0x10, 1), (0x11, 0xFF), (0x13, 0x42)]

    def test_16_bit_port_is_all_of_bc(self, tmp_path):
        """The Neo Geo bank select: IN with the bank number in B."""
        build(tmp_path, """
#include <z80.h>
volatile unsigned char got;
unsigned char bank_select(unsigned char bank)
{
    return z80_in16(((unsigned int) bank << 8) | 0x0B);
}
int main(void)
{
    bank_select(5);
    bank_select(0x12);
    got = z80_in16(0x3456);
    z80_out16(0x7788, 0x99);
    return 0;
}
""")
        m = machine(tmp_path)
        run_ok(m)
        assert [(p, v) for _k, p, v in ports(m, "IN")][:3] == [
            (0x050B, 0xFF), (0x120B, 0xFF), (0x3456, 0xFF)]
        assert [(p, v) for _k, p, v in ports(m, "OUT")] == [(0x7788, 0x99)]
        assert "in\tA,(C)" in mac(tmp_path)

    def test_bank_select_changes_the_window(self, tmp_path):
        """A read of port 0Bh with the bank in B shows that 16 KB of the
        image at 8000h; the program reads bytes through the window."""
        build(tmp_path, """
#include <z80.h>
volatile unsigned char seen[4];
int main(void)
{
    for (unsigned char b = 2; b < 6; b++) {
        z80_in16(((unsigned int) b << 8) | 0x0B);
        seen[b - 2] = *(volatile unsigned char *) 0x8001;
    }
    return 0;
}
""")
        from romrun import Machine
        img = bytearray(image(tmp_path))
        img += bytes(0x8000 - len(img))
        for n in range(2, 6):                       # bank n = image n * 16K
            blk = bytearray(0x4000)
            blk[1] = 0xA0 + n
            img += blk if len(img) == n * 0x4000 else b""
        m = Machine(ram=(0xF800, 0xFFFF), ram_fill="random", sp="poison")
        m.load_rom(bytes(img))
        m.load_symbols(str(tmp_path / "t.sym"))
        run_ok(m)
        assert m.peek("_seen", 4) == bytes([0xA2, 0xA3, 0xA4, 0xA5])

    def test_busy_poll_loop(self, tmp_path):
        build(tmp_path, """
#include <z80.h>
void ym_write(unsigned char reg, unsigned char v)
{
    while (z80_in(0x04) & 0x80)
        ;
    z80_out(0x04, reg);
    while (z80_in(0x04) & 0x80)
        ;
    z80_out(0x05, v);
}
int main(void) { ym_write(0x27, 0x15); return 0; }
""")
        m = machine(tmp_path)
        m.feed.queue(0x04, [0x80, 0x80, 0x00, 0x80, 0x00])
        run_ok(m)
        assert [(k, p & 0xFF, v) for k, p, v in ports(m)] == [
            ("IN", 4, 0x80), ("IN", 4, 0x80), ("IN", 4, 0), ("OUT", 4, 0x27),
            ("IN", 4, 0x80), ("IN", 4, 0), ("OUT", 5, 0x15)]

    def test_intrinsic_result_is_unsigned_char(self, tmp_path):
        build(tmp_path, """
#include <z80.h>
volatile int r;
int main(void) { r = z80_in(1); return 0; }
""")
        m = machine(tmp_path)
        m.feed.const(1, 0xF0)
        run_ok(m)
        assert m.peek16("_r") == 0xF0               # not sign extended

    def test_wrong_argument_count(self, tmp_path):
        expect_error(tmp_path, """
#include <z80.h>
int main(void) { __builtin_z80_out(1); return 0; }
""", "takes 2 argument")


class TestSfr:
    SRC = """
__sfr __at(0x08) BANK;
__sfr __at(0x04) YM;
__sfr __banked __at(0x0B00) BANK16;
volatile unsigned char r;
int main(void)
{
    r = BANK;
    BANK |= 0x10;
    BANK = 3;
    BANK++;
    YM = 0x27;
    YM = 0x27;
    r = (unsigned char) (BANK16 + 1);
    BANK16 = 0x55;
    return 0;
}
"""

    def test_reads_and_writes_become_in_and_out(self, tmp_path):
        build(tmp_path, self.SRC)
        m = machine(tmp_path)
        m.feed.queue(0x08, [0x21, 0x21, 0x03])
        m.feed.const(0x0B00, 0x70)
        run_ok(m)
        low = [(k, p & 0xFF, v) for k, p, v in ports(m)]
        assert low[0] == ("IN", 8, 0x21)                   # r = BANK
        assert low[1] == ("IN", 8, 0x21)                   # BANK |= 0x10
        assert low[2] == ("OUT", 8, 0x31)
        assert low[3] == ("OUT", 8, 3)                     # BANK = 3
        assert low[4] == ("IN", 8, 3)                      # BANK++
        assert low[5] == ("OUT", 8, 4)
        assert low[6] == ("OUT", 4, 0x27)                  # YM written twice
        assert low[7] == ("OUT", 4, 0x27)
        assert ports(m)[8] == ("IN", 0x0B00, 0x70)         # 16-bit port
        assert ports(m)[9] == ("OUT", 0x0B00, 0x55)
        assert m.peek8("_r") == 0x71

    def test_or_assign_is_in_or_out_with_no_extra_moves(self, tmp_path):
        build(tmp_path, """
__sfr __at(0x08) P;
int main(void) { P |= 0x10; return 0; }
""")
        text = mac(tmp_path)
        i = text.index("in\tA,(8)")
        assert "or\t16" in text[i:i + 40]
        assert "out\t(8),A" in text[i:i + 60]

    def test_address_of_a_port_is_refused(self, tmp_path):
        expect_error(tmp_path, """
__sfr __at(0x08) P;
int main(void) { unsigned char *p = &P; return *p; }
""", "address of the port")

    def test_port_does_not_fit_8_bits(self, tmp_path):
        expect_error(tmp_path, """
__sfr __at(0x1234) P;
int main(void) { return 0; }
""", "does not fit 8 bits")

    def test_malformed_sfr(self, tmp_path):
        expect_error(tmp_path, """
__sfr P;
int main(void) { return 0; }
""", "expected '__sfr __at(port) NAME;'")


class TestCriticalSections:
    def test_di_ei(self, tmp_path):
        build(tmp_path, """
#include <z80.h>
int main(void) { z80_ei(); z80_di(); z80_out(0xFF, 0); return 0; }
""")
        img = image(tmp_path)
        assert b"\xfb\xf3" in img                    # EI, DI

    SECTION = """
#include <z80.h>
#include <stdint.h>
volatile uint8_t ticks;
volatile uint8_t a, b;
#pragma uc80 interrupt tick
void tick(void) { ticks++; }
static void spin(void) { for (volatile uint8_t i = 0; i < 100; i++) ; }
int main(void)
{
    z80_ei();
    for (uint8_t round = 0; round < 8; round++) {
        %s
    }
    z80_out(0xFF, 0);
    return 0;
}
"""

    def run_section(self, tmp_path, body):
        build(tmp_path, self.SECTION % body)
        m = machine(tmp_path)
        m.magic_ports.add(0xFF)
        m.add_irq_every(400, start=400)
        res = m.run(max_tstates=3_000_000)
        assert res.reason.startswith("port:"), res.reason
        return m

    def test_control_without_a_critical_section(self, tmp_path):
        """Without the section, the interrupt runs inside the window."""
        m = self.run_section(tmp_path, """
        a = ticks; spin(); b = ticks;
        if (a != b) z80_out(0x20, 1);""")
        assert any(p & 0xFF == 0x20 for _k, p, _v in ports(m, "OUT"))

    def test_critical_block_keeps_interrupts_out(self, tmp_path):
        m = self.run_section(tmp_path, """
        __critical { a = ticks; spin(); b = ticks; }
        if (a != b) z80_out(0x20, 1);""")
        assert not any(p & 0xFF == 0x20 for _k, p, _v in ports(m, "OUT"))
        assert m.peek8("_ticks") > 5                  # the IRQs got in later
        assert m.regs()["IFF1"] == 1

    def test_irq_save_restore_pair(self, tmp_path):
        m = self.run_section(tmp_path, """
        uint8_t s = z80_irq_save();
        a = ticks; spin(); b = ticks;
        z80_irq_restore(s);
        if (a != b) z80_out(0x20, 1);""")
        assert not any(p & 0xFF == 0x20 for _k, p, _v in ports(m, "OUT"))
        assert m.regs()["IFF1"] == 1

    def test_macro_pair(self, tmp_path):
        m = self.run_section(tmp_path, """
        z80_critical_enter();
        a = ticks; spin(); b = ticks;
        z80_critical_leave();
        if (a != b) z80_out(0x20, 1);""")
        assert not any(p & 0xFF == 0x20 for _k, p, _v in ports(m, "OUT"))
        assert m.regs()["IFF1"] == 1

    @pytest.mark.parametrize("body", [
        "__critical { a = ticks; }",
        "uint8_t s = z80_irq_save(); a = ticks; z80_irq_restore(s);",
        "z80_critical_enter(); a = ticks; z80_critical_leave();",
    ])
    def test_state_is_restored_not_forced(self, tmp_path, body):
        """Interrupts that were off stay off after the section."""
        build(tmp_path, f"""
#include <z80.h>
#include <stdint.h>
volatile uint8_t ticks, a;
int main(void)
{{
    z80_di();
    {body}
    z80_out(0xFF, 0);
    return 0;
}}
""")
        m = machine(tmp_path)
        m.magic_ports.add(0xFF)
        m.run(max_tstates=100_000)
        assert m.regs()["IFF1"] == 0

    def test_nested_sections(self, tmp_path):
        build(tmp_path, """
#include <z80.h>
#include <stdint.h>
volatile uint8_t a;
int main(void)
{
    z80_ei();
    __critical {
        a = 1;
        __critical { a = 2; }
        z80_out(0xFE, a);
    }
    z80_out(0xFF, 0);
    return 0;
}
""")
        m = machine(tmp_path)
        m.magic_ports.add(0xFF)
        m.run(max_tstates=100_000)
        assert m.regs()["IFF1"] == 1
        assert [v for _k, p, v in ports(m, "OUT") if p & 0xFF == 0xFE] == [2]

    @pytest.mark.parametrize("word", ["return", "goto out", "break",
                                      "continue"])
    def test_jump_out_of_a_critical_block_is_refused(self, tmp_path, word):
        expect_error(tmp_path, f"""
int f(int x)
{{
    __critical {{
        if (x) {word};
    }}
    out: return 0;
}}
int main(void) {{ return f(1); }}
""", "inside '__critical { }'")

    def test_break_inside_a_loop_inside_the_block_is_fine(self, tmp_path):
        build(tmp_path, """
#include <stdint.h>
volatile uint8_t a;
int main(void)
{
    __critical {
        for (uint8_t i = 0; i < 9; i++) { if (i == 3) break; a = i; }
    }
    return 0;
}
""")
        m = machine(tmp_path)
        run_ok(m)
        assert m.peek8("_a") == 2


class TestAtomicRead:
    SRC = """
#include <z80.h>
#include <stdint.h>
volatile uint16_t cnt;
volatile uint16_t seen_bad;
volatile uint16_t reads;
volatile uint16_t *volatile ptr = &cnt;
uint16_t word_value(void) { return %s; }
#pragma uc80 interrupt tick critical
void tick(void)
{
    /* The counter changes between 00FFh and 0100h on each interrupt, so a
     * read of one byte before and one after the change is 0000h or 01FFh. */
    cnt = (cnt == 0x00FF) ? 0x0100 : 0x00FF;
}
int main(void)
{
    cnt = 0x00FF;
    z80_ei();
    for (uint16_t i = 0; i < 4000; i++) {
        uint16_t v = word_value();
        if (v != 0x00FF && v != 0x0100)
            seen_bad++;
        reads++;
    }
    z80_di();
    return 0;
}
"""

    def run_reads(self, tmp_path, expr):
        build(tmp_path, self.SRC % expr)
        m = machine(tmp_path)
        m.add_irq_every(331, start=331)
        res = m.run(max_tstates=60_000_000)
        assert res.reason == "halt"
        assert m.peek16("_reads") == 4000
        return m.peek16("_seen_bad")

    def test_plain_read_through_a_pointer_can_tear(self, tmp_path):
        assert self.run_reads(tmp_path, "*ptr") > 0

    def test_load16_of_a_known_address_is_one_instruction(self, tmp_path):
        assert self.run_reads(tmp_path, "z80_load16(&cnt)") == 0
        text = mac(tmp_path)
        assert "ld\tHL,(_cnt)" in text

    def test_load16_through_a_pointer_turns_interrupts_off(self, tmp_path):
        assert self.run_reads(tmp_path, "z80_load16(ptr)") == 0
        text = mac(tmp_path)
        assert "ld\tA,I" in text and "jp\tPO," in text

    def test_load16_keeps_interrupts_on_when_they_were_on(self, tmp_path):
        self.run_reads(tmp_path, "z80_load16(ptr)")
        m = machine(tmp_path)
        m.add_irq_every(331, start=331)
        m.run(max_tstates=3_000_000)
        assert m.accepted                              # interrupts ran


class TestAbsoluteObjects:
    def test_at_makes_a_symbol_without_storage(self, tmp_path):
        build(tmp_path, """
volatile unsigned char reg __at(0xF900);
unsigned int word __at(0xF902);
int main(void) { reg = 5; word = 0x1234; reg |= 2; return reg; }
""")
        sy = symbols(tmp_path)
        assert sy["_REG"] == 0xF900 and sy["_WORD"] == 0xF902
        assert sy["__BSS_END"] == sy["__BSS_START"]       # no storage
        m = machine(tmp_path)
        run_ok(m)
        assert m.peek8(0xF900) == 7 and m.peek16(0xF902) == 0x1234

    def test_leading_form_and_attribute_form_and_array(self, tmp_path):
        build(tmp_path, """
__at(0xF900) unsigned char a;
[[uc80::at(0xF901)]] unsigned char b;
unsigned char c [[uc80::at(0xF902)]];
unsigned char tab[4] __at(0xF910);
int main(void) { a = 1; b = 2; c = 3; tab[0] = 4; tab[3] = 7; return 0; }
""")
        m = machine(tmp_path)
        run_ok(m)
        assert m.peek(0xF900, 3) == bytes([1, 2, 3])
        assert m.peek8(0xF910) == 4 and m.peek8(0xF913) == 7

    def test_every_access_happens(self, tmp_path):
        build(tmp_path, """
unsigned char reg __at(0xF900);
int main(void) { reg = 1; reg = 1; reg = 1; return 0; }
""")
        assert mac(tmp_path).count("ld\t(_reg),A") == 3

    def test_initial_value_is_refused(self, tmp_path):
        expect_error(tmp_path, """
unsigned char reg __at(0xF900) = 3;
int main(void) { return 0; }
""", "no storage and no initial value")

    def test_address_must_be_a_constant(self, tmp_path):
        expect_error(tmp_path, """
unsigned char reg __at(base);
int main(void) { return 0; }
""", "address must be an integer constant")

    def test_address_too_large(self, tmp_path):
        expect_error(tmp_path, """
unsigned char reg __at(0x12345);
int main(void) { return 0; }
""", "16-bit address")

    def test_at_on_a_local_is_refused(self, tmp_path):
        expect_error(tmp_path, """
int main(void) { unsigned char r __at(0xF900); return r; }
""", "file-scope")

    def test_constant_volatile_pointer_loads_directly(self, tmp_path):
        build(tmp_path, """
#define REG8  (*(volatile unsigned char *) 0xF900)
#define REG16 (*(volatile unsigned int *) 0xF902)
#define TAB   ((volatile unsigned char *) 0xF910)
volatile unsigned int sink;
int main(void)
{
    REG8 = 5;
    REG8 |= 8;
    REG16 = 0x1234;
    sink = REG16 + REG8;
    TAB[2] = 9;
    TAB[2] = 9;
    return 0;
}
""")
        text = mac(tmp_path)
        assert "ld\tHL,63744" not in text                # no pointer in HL
        assert "ld\tA,(___absF900uc)" in text
        assert "ld\t(___absF900uc),A" in text
        assert "ld\tHL,(___absF902ui)" in text
        assert text.count("ld\t(___absF912uc),A") == 2     # both stores
        m = machine(tmp_path)
        run_ok(m)
        assert m.peek8(0xF900) == 13 and m.peek16(0xF902) == 0x1234
        assert m.peek8(0xF912) == 9
        assert m.peek16("_sink") == 0x1234 + 13

    def test_non_volatile_deref_is_unchanged(self, tmp_path):
        build(tmp_path, """
int main(void) { *(unsigned char *) 0xF900 = 5; return 0; }
""")
        assert "___abs" not in mac(tmp_path)


class TestIsrSafe:
    SRC = """
#include <z80.h>
#include <stdint.h>
volatile uint8_t ticks;
volatile uint16_t result;
static uint16_t work(uint16_t n)
{
    volatile uint16_t i;
    uint16_t acc = 0;
    for (i = 0; i < n; i++)
        acc = (uint16_t) (acc + i);
    return acc;
}
#pragma uc80 interrupt isr
void isr(void) { ticks++; (void) work(10); }
int main(void)
{
    z80_ei();
    result = work(300);
    z80_di();
    return 0;
}
"""

    def run(self, tmp_path, *extra):
        r = compile_rom(tmp_path, self.SRC, *extra)
        assert r.returncode == 0, r.stderr
        link_rom(tmp_path)
        m = machine(tmp_path)
        m.add_irq_at(5000)             # two interrupts inside work (300)
        m.add_irq_at(13000)
        assert m.run(max_tstates=10_000_000).reason == "halt"
        return m, r.stderr

    def test_shared_function_is_corrupted_and_warned_without_the_flag(
            self, tmp_path):
        m, err = self.run(tmp_path)
        assert "'work' is called from an interrupt function" in err
        assert "--isr-safe" in err
        assert m.peek16("_result") != sum(range(300)) & 0xFFFF
        assert "??AUTO" in mac(tmp_path)

    def test_isr_safe_keeps_locals_on_the_stack(self, tmp_path):
        m, err = self.run(tmp_path, "--isr-safe")
        assert "is called from an interrupt function" not in err
        assert m.peek16("_result") == sum(range(300)) & 0xFFFF
        assert m.peek8("_ticks") == 2
        body = mac(tmp_path)
        work = body[body.index("_work:"):]
        work = work[:work.index("@work_ret")]
        assert "??AUTO" not in work


class TestFixedMemoryDiagnostic:
    def test_long_division_in_an_isr_warns_with_the_chain(self, tmp_path):
        r = compile_rom(tmp_path, """
volatile unsigned long a = 100000, q;
static void divide(void) { q = a / 7; }
#pragma uc80 interrupt isr
void isr(void) { divide(); }
int main(void) { return 0; }
""", "--no-isr-save-workareas")
        assert r.returncode == 0, r.stderr
        assert "interrupt function 'isr' reaches" in r.stderr
        assert "__div32_dvd" in r.stderr
        assert "isr -> " in r.stderr and "__div32" in r.stderr

    def test_plain_isr_gets_no_warning(self, tmp_path):
        r = compile_rom(tmp_path, """
volatile unsigned char t;
#pragma uc80 interrupt isr
void isr(void) { t++; }
int main(void) { return 0; }
""")
        assert r.returncode == 0 and r.stderr == ""
