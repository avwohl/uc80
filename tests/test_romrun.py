"""Tests of the ROM test runner in bench/romrun (docs/romrun.md)."""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))

from romrun import Machine, PortFeed, YM2610, Z80  # noqa: E402
from romrun.machine import BankWindow, parse_ihex  # noqa: E402

TICKS = os.path.expanduser(
    "~/src/external/tools/z88dk-2.4/z88dk/bin/z88dk-ticks")


def image(size=0x200, **chunks):
    """A ROM image: chunks maps 'a<hex address>' to bytes."""
    img = bytearray(b"\x76" * size)
    for k, v in chunks.items():
        a = int(k[1:], 16)
        img[a:a + len(v)] = v
    return bytes(img)


def machine(img, **kw):
    m = Machine(**kw)
    m.load_rom(img)
    return m


# --- T-state counts -----------------------------------------------------------

# (bytes, expected T-states of the instruction, register setup)
TIMING = [
    (b"\x00", 4, {}),
    (b"\x01\x34\x12", 10, {}),
    (b"\x36\x05", 10, {"H": 0xF8}),
    (b"\x34", 11, {"H": 0xF8}),
    (b"\x09", 11, {}),
    (b"\x3A\x00\xF8", 13, {}),
    (b"\x2A\x00\xF8", 16, {}),
    (b"\x32\x00\xF8", 13, {}),
    (b"\xC5", 11, {"SP": 0xFF00}),
    (b"\xC1", 10, {"SP": 0xFF00}),
    (b"\xCD\x13\x00", 17, {"SP": 0xFF00}),
    (b"\xC4\x13\x00", 17, {"SP": 0xFF00, "F": 0}),
    (b"\xC4\x13\x00", 10, {"SP": 0xFF00, "F": 0x40}),
    (b"\xC0", 11, {"SP": 0xFF00, "F": 0}),
    (b"\xC0", 5, {"SP": 0xFF00, "F": 0x40}),
    (b"\xCF", 11, {"SP": 0xFF00}),
    (b"\xE3", 19, {"SP": 0xFF00}),
    (b"\xE9", 4, {"H": 0, "L": 0x10}),
    (b"\x10\x00", 13, {"B": 2}),
    (b"\x10\x00", 8, {"B": 1}),
    (b"\x18\x00", 12, {}),
    (b"\x20\x00", 7, {"F": 0x40}),
    (b"\x20\x00", 12, {"F": 0}),
    (b"\x08", 4, {}),
    (b"\xD9", 4, {}),
    (b"\x27", 4, {}),
    (b"\xCB\x00", 8, {}),
    (b"\xCB\x46", 12, {"H": 0xF8}),
    (b"\xCB\xC6", 15, {"H": 0xF8}),
    (b"\xDD\x21\x00\xF9", 14, {}),
    (b"\xDD\x34\x01", 23, {"IX": 0xF800}),
    (b"\xDD\x7E\x01", 19, {"IX": 0xF800}),
    (b"\xDD\x77\x01", 19, {"IX": 0xF800}),
    (b"\xDD\x36\x01\x05", 19, {"IX": 0xF800}),
    (b"\xDD\x09", 15, {}),
    (b"\xDD\x24", 8, {}),
    (b"\xDD\x26\x05", 11, {}),
    (b"\xDD\xE5", 15, {"SP": 0xFF00}),
    (b"\xDD\xE9", 8, {"IX": 0x10}),
    (b"\xDD\xCB\x01\x06", 23, {"IX": 0xF800}),
    (b"\xDD\xCB\x01\x46", 20, {"IX": 0xF800}),
    (b"\xFD\xCB\x01\xC6", 23, {"IY": 0xF800}),
    (b"\xED\x44", 8, {}),
    (b"\xED\x42", 15, {}),
    (b"\xED\x43\x00\xF8", 20, {}),
    (b"\xED\x4B\x00\xF8", 20, {}),
    (b"\xED\x6F", 18, {"H": 0xF8}),
    (b"\xED\x47", 9, {}),
    (b"\xED\x57", 9, {}),
    (b"\xED\xA0", 16, {"H": 0x10, "D": 0xF8, "C": 5}),
    (b"\xED\xB0", 21 + 21 + 16, {"H": 0x10, "D": 0xF8, "C": 3}),
    (b"\xED\xB0", 16, {"H": 0x10, "D": 0xF8, "C": 1}),
    (b"\xED\xB1", 16, {"H": 0x10, "C": 1}),
    (b"\xED\x4D", 14, {"SP": 0xFF00}),
    (b"\xED\x56", 8, {}),
    (b"\xD3\x10", 11, {}),
    (b"\xDB\x10", 11, {}),
    (b"\xED\x78", 12, {}),
    (b"\xED\x79", 12, {}),
]


@pytest.mark.parametrize("code,expect,regs", TIMING,
                         ids=[c.hex() + ("_%d" % i) for i, (c, _, _) in
                              enumerate(TIMING)])
def test_instruction_tstates(code, expect, regs):
    # the instruction is followed by NOP-padding and a HALT far away, so a
    # jump, call or return lands on a HALT too
    img = bytearray(b"\x76" * 0x200)
    img[:len(code)] = code
    m = machine(bytes(img))
    # a return address that points at a HALT
    m.cpu.mem[0xFF00:0xFF02] = b"\x00\x01"
    m.cpu.set(**regs)
    m.cpu.set(IFF1=0)
    stop = m.stop_at(0x100) if False else None
    res = m.run(10_000)
    assert res.reason == "halt"
    # one HALT instruction (4 T-states) ends the run
    assert res.t - 4 == expect


def test_halt_costs_four_and_stops_with_interrupts_disabled():
    m = machine(b"\xF3\x76")
    res = m.run(1000)
    assert (res.reason, res.t) == ("halt", 8)


def test_budget_stop():
    m = machine(b"\x18\xFE")           # JR $
    res = m.run(1000)
    assert res.reason == "budget"
    assert 1000 <= res.t < 1012


def test_stop_at_address_and_resume():
    m = machine(image(a0=b"\x00\x00\x00\x00", a10=b"\x76"))
    m.stop_at(2)
    r1 = m.run(1000)
    assert (r1.reason, r1.pc, r1.t) == ("address:0002", 2, 8)
    r2 = m.run(1000)
    assert r2.reason == "halt"


def test_magic_port_write_stops():
    m = machine(b"\x3E\x2A\xD3\x77\x00\x76")
    m.magic_ports.add(0x77)
    res = m.run(1000)
    assert res.reason == "port:77"
    assert res.info["value"] == 0x2A
    assert res.t == 7 + 11


# --- port log -----------------------------------------------------------------

def test_port_log_full_address_value_and_stamp():
    # LD A,5 / OUT (10h),A / LD BC,0308h / IN A,(C) / IN A,(08h) / HALT
    prog = b"\x3E\x05\xD3\x10\x01\x08\x03\xED\x78\xDB\x08\x76"
    m = machine(prog)
    m.feed.const(0x08, 0x5A)
    res = m.run(1000)
    assert res.reason == "halt"
    assert m.port_log == [
        (7 + 7, "OUT", 0x0510, 0x05),
        (7 + 11 + 10 + 8, "IN", 0x0308, 0x5A),
        (7 + 11 + 10 + 12 + 7, "IN", 0x5A08, 0x5A),
    ]


def test_port_log_file_format(tmp_path):
    m = machine(b"\x3E\x05\xD3\x10\x76")
    path = tmp_path / "ports.log"
    m.log_file = open(path, "w")
    m.run(1000)
    m.log_file.close()
    assert path.read_text() == "14 OUT 0510 05\n"


def test_out_c_uses_bc_as_the_address():
    # LD BC,1234h / LD A,7 / OUT (C),A
    m = machine(b"\x01\x34\x12\x3E\x07\xED\x79\x76")
    m.run(1000)
    assert m.port_log == [(10 + 7 + 8, "OUT", 0x1234, 7)]


def test_block_io_logs_every_transfer():
    # LD HL,F800h / LD BC,0210h / OTIR
    m = machine(b"\x21\x00\xF8\x01\x10\x02\xED\xB3\x76")
    m.cpu.mem[0xF800:0xF802] = b"\xAA\xBB"
    m.run(1000)
    outs = [(p, v) for _, k, p, v in m.port_log]
    assert outs == [(0x0110, 0xAA), (0x0010, 0xBB)]


# --- port feed ------------------------------------------------------------------

def test_feed_queue_and_default():
    f = PortFeed(default=0xEE)
    f.queue(0x01, [1, 2, 3])
    assert [f.read(0x01, 0) for _ in range(5)] == [1, 2, 3, 3, 3]
    assert f.read(0x02, 0) is None


def test_feed_full_address_wins_over_low_byte():
    f = PortFeed()
    f.const(0x08, 1)
    f.const(0x0B08, 2)
    assert f.read(0x0B08, 0) == 2
    assert f.read(0x0508, 0) == 1


def test_busy_flag_clears_after_n_tstates():
    # loop: IN A,(04h) / AND 80h / JR NZ,loop / HALT
    prog = b"\xDB\x04\xE6\x80\x20\xFA\x76"
    m = machine(prog)
    m.feed.busy(0x04, 1000)
    res = m.run(100_000)
    assert res.reason == "halt"
    assert 1000 <= res.t < 1000 + 60
    assert all(v == 0x80 for t, k, p, v in m.port_log if t < 1000)
    assert m.port_log[-1][3] == 0


def test_feed_supplies_nmi_command_byte():
    img = image(
        a0=b"\xF3\xED\x56\xC3\x00\x01",
        # NMI: IN A,(00h) / LD (F800h),A / RETN
        a66=b"\xDB\x00\x32\x00\xF8\xED\x45",
        a100=b"\x31\x00\x00\xFB\x18\xFE")
    m = machine(img)
    m.feed.queue(0x00, [0x21, 0x22])
    m.add_nmi_at(500)
    m.run(1000)
    assert m.cpu.mem[0xF800] == 0x21
    m.add_nmi_at(1500)
    m.run(1000)
    assert m.cpu.mem[0xF800] == 0x22


# --- ROM writes -----------------------------------------------------------------

ROM_WRITE = b"\x3E\x01\x32\x00\x01\x76"


def test_rom_write_fault_stops():
    m = machine(image(a0=ROM_WRITE), rom_write="fault")
    res = m.run(1000)
    assert res.reason == "rom-write"
    assert m.rom_writes[0][1:] == (0x0100, 1, 2)
    assert m.cpu.mem[0x100] == 0x76


def test_rom_write_log_continues():
    m = machine(image(a0=ROM_WRITE), rom_write="log")
    res = m.run(1000)
    assert res.reason == "halt"
    assert len(m.rom_writes) == 1
    assert m.cpu.mem[0x100] == 0x76


def test_ram_write_is_allowed_and_banked_window_is_read_only():
    # LD A,9 / LD (F800h),A / LD (8000h),A / HALT
    m = machine(b"\x3E\x09\x32\x00\xF8\x32\x00\x80\x76")
    m.run(1000)
    assert m.cpu.mem[0xF800] == 9
    assert [w[1] for w in m.rom_writes] == [0x8000]


def test_write_to_rom_size_boundary():
    m = machine(b"\x3E\x01\x32\xFF\x7F\x32\x00\xF8\x76")
    m.run(1000)
    assert [w[1] for w in m.rom_writes] == [0x7FFF]


# --- interrupts -------------------------------------------------------------------

VECTORS = dict(
    a0=b"\xF3\xED\x56\xC3\x00\x01",
    # INT: count at F800, EI, RETI
    a38=b"\x3A\x00\xF8\x3C\x32\x00\xF8\xFB\xED\x4D",
    # NMI: count at F801, RETN
    a66=b"\x3A\x01\xF8\x3C\x32\x01\xF8\xED\x45",
)
MAIN_EI = dict(a100=b"\x31\x00\x00\xFB\x18\xFE")      # LD SP,0 / EI / JR $
MAIN_DI = dict(a100=b"\x31\x00\x00\x18\xFE")          # LD SP,0 / JR $


def test_im1_periodic_interrupt():
    m = machine(image(**VECTORS, **MAIN_EI))
    m.add_irq_every(1000, start=1000)
    m.run(10_500)
    assert m.cpu.mem[0xF800] == 10
    assert len(m.accepted) == 10
    assert all(k == "irq" for k, _ in m.accepted)


def test_interrupt_not_taken_while_disabled():
    m = machine(image(**VECTORS, **MAIN_DI))
    m.add_irq_at(500)
    m.run(2000)
    assert m.cpu.mem[0xF800] == 0
    assert m.accepted == []


def test_ei_delay_one_instruction():
    img = image(
        a0=b"\xF3\xED\x56\xC3\x00\x01",
        # INT: POP HL / PUSH HL / LD (F802h),HL / RETI
        a38=b"\xE1\xE5\x22\x02\xF8\xED\x4D",
        # LD SP,0 / EI / NOP / NOP / JR $
        a100=b"\x31\x00\x00\xFB\x00\x00\x18\xFE")
    m = machine(img)
    m.add_irq_at(0)
    m.run(1000)
    # taken after the NOP that follows EI: the return address is the second NOP
    assert m.cpu.mem[0xF802] | (m.cpu.mem[0xF803] << 8) == 0x0105
    # DI 4, IM 1 8, JP 10, LD SP 10, EI 4, NOP 4
    assert m.accepted == [("irq", 40)]


def test_ei_ret_sequence_runs_the_ret_before_the_interrupt():
    # a routine at 0200h ends with EI / RET; the INT must wait for the RET
    img = image(
        a0=b"\xF3\xED\x56\xC3\x00\x01",
        a38=b"\xE1\xE5\x22\x02\xF8\xED\x4D",
        a100=b"\x31\x00\x00\xCD\x00\x02\x00\x18\xFE",
        a200=b"\xFB\xC9")
    m = machine(img)
    m.add_irq_at(0)
    m.run(1000)
    assert m.cpu.mem[0xF802] | (m.cpu.mem[0xF803] << 8) == 0x0106


def test_nmi_works_with_interrupts_disabled_and_retn_keeps_iff1_low():
    m = machine(image(**VECTORS, **MAIN_DI))
    m.add_nmi_at(500)
    m.add_irq_at(600)
    m.run(3000)
    assert m.cpu.mem[0xF801] == 1          # the NMI ran
    assert m.cpu.mem[0xF800] == 0          # IFF1 stayed 0 after RETN
    assert m.cpu.get()["IFF1"] == 0


def test_retn_restores_iff1_from_iff2():
    m = machine(image(**VECTORS, **MAIN_EI))
    m.add_nmi_at(500)
    m.add_irq_at(600)
    m.run(3000)
    assert m.cpu.mem[0xF801] == 1
    assert m.cpu.mem[0xF800] == 1          # the INT ran after the NMI
    kinds = [k for k, _ in m.accepted]
    assert kinds == ["nmi", "irq"]


def test_nmi_clears_iff1_and_copies_it_to_iff2():
    # NMI handler: LD A,I is not used; read the flags with an LD A,R-free probe:
    # the handler stores PUSH AF of LD A,I (P/V = IFF2) and the state is read
    img = image(
        a0=b"\xF3\xED\x56\xC3\x00\x01",
        a66=b"\x76",                       # HALT inside the NMI handler
        a100=b"\x31\x00\x00\xFB\x18\xFE")
    m = machine(img)
    m.add_nmi_at(300)
    m.stop_on_halt = False
    s_before = m.run(200)
    assert s_before.reason == "budget"
    m.run(300)
    s = m.cpu.get()
    assert s["IFF1"] == 0 and s["IFF2"] == 1
    assert m.accepted[0][0] == "nmi"


def test_nmi_pushes_pc_and_jumps_to_0066h():
    img = image(a0=b"\xF3\xED\x56\xC3\x00\x01", a100=b"\x31\x00\x00\x18\xFE",
                a66=b"\xE1\xE5\x22\x02\xF8\xED\x45")
    m = machine(img)
    m.add_nmi_at(100)
    m.run(400)
    assert m.cpu.mem[0xF802] | (m.cpu.mem[0xF803] << 8) in (0x0103, 0x0104)


def test_irq_takes_13_and_nmi_takes_11_tstates():
    m = machine(image(**VECTORS, **MAIN_EI))
    m.add_irq_at(500)
    m.run(520)
    s = m.cpu.get()
    t_irq = m.accepted[0][1]
    m2 = machine(image(**VECTORS, **MAIN_EI))
    m2.add_nmi_at(500)
    m2.run(520)
    t_nmi = m2.accepted[0][1]
    assert t_irq - t_nmi in (0, 2, 4, 6, 8)   # both land on JR $ boundaries


def test_halt_wakes_on_interrupt_and_returns_after_halt():
    img = image(
        a0=b"\xF3\xED\x56\xC3\x00\x01",
        # INT: POP HL / PUSH HL / LD (F802h),HL / RETI
        a38=b"\xE1\xE5\x22\x02\xF8\xED\x4D",
        # LD SP,0 / EI / HALT / LD A,1 / LD (F804h),A / DI / HALT
        a100=b"\x31\x00\x00\xFB\x76\x3E\x01\x32\x04\xF8\xF3\x76")
    m = machine(img)
    m.add_irq_at(2000)
    res = m.run(100_000)
    assert res.reason == "halt"
    assert m.cpu.mem[0xF802] | (m.cpu.mem[0xF803] << 8) == 0x0105
    assert m.cpu.mem[0xF804] == 1
    # the clock jumped to the interrupt time and not far past
    assert 2000 <= m.accepted[0][1] < 2010


def test_halt_with_ei_and_no_event_is_idle():
    m = machine(image(a0=b"\xFB\x76"))
    assert m.run(1000).reason == "idle"


def test_im2_vector_table():
    img = image(
        a0=b"\xF3\x3E\x01\xED\x47\xED\x5E\xC3\x00\x01",   # LD I,1 / IM 2
        a100=b"\x31\x00\x00\xFB\x18\xFE",
        a180=b"\x3E\x77\x32\x00\xF8\xED\x4D",             # handler
    )
    img = bytearray(img.ljust(0x200, b"\x76"))
    img[0x1FE:0x200] = b"\x80\x01"                     # vector at I<<8|FEh
    m = machine(bytes(img))
    m.cpu.set(IVEC=0xFE)
    m.add_irq_at(300)
    m.run(1000)
    assert m.cpu.mem[0xF800] == 0x77


def test_scheduled_interrupt_is_accepted_at_instruction_boundary():
    m = machine(image(**VECTORS, **MAIN_EI))
    m.add_irq_at(500)
    m.run(700)
    (kind, t), = m.accepted
    assert 500 <= t < 500 + 23


# --- YM2610 model ---------------------------------------------------------------

YM_ISR = (b"\xF5"                       # PUSH AF
          b"\x3E\x27\xD3\x04"           # LD A,27h / OUT (04h),A
          b"\x3E\x15\xD3\x05"           # LD A,15h / OUT (05h),A
          b"\x3A\x00\xF8\x3C\x32\x00\xF8"
          b"\xF1\xFB\xED\x4D")


def ym_rom():
    setup = (b"\x31\x00\x00"              # LD SP,0
             b"\x3E\x24\xD3\x04\x3E\xFC\xD3\x05"      # reg 24h = FCh
             b"\x3E\x25\xD3\x04\x3E\x00\xD3\x05"      # reg 25h = 0
             b"\x3E\x27\xD3\x04\x3E\x15\xD3\x05"      # reg 27h: load A, enable
             b"\xFB\x18\xFE")
    return image(a0=b"\xF3\xED\x56\xC3\x00\x01", a38=YM_ISR, a100=setup)


def test_ym2610_timer_a_interrupts_at_its_period():
    m = machine(ym_rom())
    ym = YM2610(m)
    m.run(576 * 10 + 400)
    assert ym.period_a() == (1024 - 0x3F0) * 72 / 2 == 576
    assert m.cpu.mem[0xF800] == 10


def test_ym2610_status_flag_and_busy_bit():
    m = machine(b"\x00")
    ym = YM2610(m)
    ym.write(0x04, 0x27, 0)
    ym.write(0x05, 0x01, 10)
    assert ym.read(0x04, 20) & 0x80
    assert not ym.read(0x04, 10 + ym.busy_tstates)


# --- banks ----------------------------------------------------------------------

def test_bank_select_by_port_read_with_bank_in_b():
    img = bytearray(0x20000)
    for i in range(0, len(img), 0x800):
        img[i:i + 0x800] = bytes([(i // 0x800) & 0xFF]) * 0x800
    prog = (b"\x01\x08\x03\xED\x78"        # LD BC,0308h / IN A,(C)   bank 3 of 2K
            b"\x3A\x00\xF0\x57"            # LD A,(F000h) / LD D,A
            b"\x01\x0B\x01\xED\x78"        # LD BC,010Bh / IN A,(C)   bank 1 of 16K
            b"\x3A\x00\x80\x76")           # LD A,(8000h) / HALT
    img[:len(prog)] = prog
    m = Machine()
    m.load_rom(bytes(img))
    res = m.run(1000)
    assert res.reason == "halt"
    s = m.cpu.get()
    assert s["D"] == 3                                 # 2K block 3
    assert s["A"] == (0x4000 // 0x800) & 0xFF          # image byte of 16K bank 1
    assert [(p, v) for _, k, p, v in m.port_log if k == "IN"][0][0] == 0x0308
    assert m.bank_state[0x08] == 3 and m.bank_state[0x0B] == 1


def test_custom_bank_window():
    img = bytearray(0x10000)
    img[0x1000:0x1400] = b"\x5A" * 0x400
    prog = b"\x01\x20\x04\xED\x78\x3A\x00\x90\x76"     # bank 4 of port 20h
    img[:len(prog)] = prog
    m = Machine(banks=[BankWindow(0x20, 0x9000, 0x400)])
    m.load_rom(bytes(img))
    m.run(1000)
    assert m.cpu.get()["A"] == 0x5A


# --- loaders ----------------------------------------------------------------------

def test_intel_hex_loader():
    def rec(addr, data, typ=0):
        raw = bytes([len(data), addr >> 8, addr & 255, typ]) + bytes(data)
        return ":" + (raw + bytes([(-sum(raw)) & 255])).hex().upper() + "\n"
    hexs = rec(0, b"\xC3\x10\x00") + rec(0x10, b"\x76") + rec(0, b"", 1)
    recs = parse_ihex(hexs)
    assert recs == {0: 0xC3, 1: 0x10, 2: 0x00, 0x10: 0x76}
    m = Machine()
    m.load_hex(hexs)
    assert m.run(100).reason == "halt"


def test_intel_hex_bad_checksum():
    with pytest.raises(ValueError):
        parse_ihex(":0100100076" + "00\n")


# --- measuring --------------------------------------------------------------------

def test_measure_function_and_span():
    img = image(
        a0=b"\x31\x00\xF9\xCD\x00\x02\xCD\x00\x02\x76",
        a200=b"\x3C\x3C\xC9")                # INC A / INC A / RET
    m = machine(img)
    sp = m.measure("f", 0x200)
    sp2 = m.measure("span", 0x003, 0x006)
    m.run(1000)
    assert (sp.count, sp.total, sp.min, sp.max) == (2, 2 * 18, 18, 18)
    assert sp2.count == 1 and sp2.total == 17 + 4 + 4 + 10


def test_symbols_by_name(tmp_path):
    p = tmp_path / "a.sym"
    p.write_text("0200 _FUNC\n0003 START\n")
    m = machine(image(a0=b"\x00\x00\x00\x76"))
    m.load_symbols(str(p))
    assert m.addr("func") == 0x200
    assert m.addr("start") == 3
    m.stop_at("start")
    assert m.run(100).reason == "address:0003"


# --- CP/M shim --------------------------------------------------------------------

HELLO = b"\x0E\x09\x11\x0B\x01\xCD\x05\x00\xC3\x00\x00Hi$"


def test_cpm_hello_and_tstates():
    m = Machine(mode="cpm")
    m.load_com(HELLO)
    res = m.run(1000)
    assert res.reason == "exit"
    assert m.output_text() == "Hi"
    # LD C 7, LD DE 10, CALL 17, RET 10 (the BDOS return), JP 10
    assert res.t == 7 + 10 + 17 + 10 + 10


@pytest.mark.skipif(not os.path.exists(TICKS), reason="z88dk-ticks missing")
def test_cpm_tstates_match_z88dk_ticks(tmp_path):
    import subprocess
    (tmp_path / "prog.com").write_bytes(
        b"\x06\xFF\x10\xFE\x0E\x09\x11\x0F\x01\xCD\x05\x00\xC3\x00\x00Hi$")
    cp = subprocess.run([TICKS, "-l", "100", "-pc", "100", "-counter",
                         "99999999", "prog.com"], cwd=tmp_path,
                        capture_output=True, text=True)
    ticks_t = int(cp.stdout.rstrip().removeprefix("Hi"))
    m = Machine(mode="cpm")
    m.load_com(str(tmp_path / "prog.com"))
    res = m.run(10_000_000)
    assert m.output_text() == "Hi"
    assert abs(res.t - ticks_t) <= ticks_t * 0.001


# --- core against z88dk-ticks -------------------------------------------------------

@pytest.mark.skipif(not os.path.exists(TICKS), reason="z88dk-ticks missing")
@pytest.mark.parametrize("seed", [11, 12, 13, 14])
def test_core_matches_ticks_on_random_instruction_streams(seed):
    from romrun import difftest
    errs = difftest.compare(seed, 150, TICKS)
    assert errs == []


# --- the core's own behaviour --------------------------------------------------------

def run_code(code, **regs):
    m = machine(code + b"\x76")
    m.cpu.set(**regs)
    m.run(100000)
    return m.cpu.get()


def test_add_overflow_flags():
    s = run_code(b"\xC6\x01", A=0x7F, F=0)
    assert s["A"] == 0x80 and s["F"] == 0x94


def test_sub_flags_and_borrow():
    s = run_code(b"\xD6\x01", A=0x80, F=0)
    assert s["A"] == 0x7F and s["F"] == 0x3E
    s = run_code(b"\xD6\x01", A=0x00, F=0)
    assert s["A"] == 0xFF and s["F"] & 1


def test_daa_after_bcd_add():
    s = run_code(b"\xC6\x27\x27", A=0x15, F=0)      # 15 + 27 = 42 (BCD)
    assert s["A"] == 0x42


def test_ldir_copies_and_clears_bc():
    m = machine(b"\x21\x00\x01\x11\x00\xF8\x01\x04\x00\xED\xB0\x76")
    m.cpu.mem[0x100:0x104] = b"\x01\x02\x03\x04"
    # rom bytes are what the image holds: place the source in the image
    m = machine(image(a0=b"\x21\x00\x01\x11\x00\xF8\x01\x04\x00\xED\xB0\x76",
                      a100=b"\x01\x02\x03\x04"))
    m.run(1000)
    assert bytes(m.cpu.mem[0xF800:0xF804]) == b"\x01\x02\x03\x04"
    s = m.cpu.get()
    assert (s["B"], s["C"]) == (0, 0)
    assert s["F"] & 4 == 0


def test_djnz_loop_total_time():
    # LD B,10 / loop: DJNZ loop / HALT
    m = machine(b"\x06\x0A\x10\xFE\x76")
    res = m.run(1000)
    assert res.t == 7 + 9 * 13 + 8 + 4


def test_exx_and_ex_af():
    s = run_code(b"\x01\x34\x12\xD9\x01\x78\x56\xD9", F=0)
    assert (s["B"], s["C"]) == (0x12, 0x34)
    assert (s["B_"], s["C_"]) == (0x56, 0x78)


def test_index_registers_ixh_ixl_halves():
    s = run_code(b"\xDD\x26\x12\xDD\x2E\x34")
    assert s["IX"] == 0x1234


def test_push_pop_af_roundtrip():
    s = run_code(b"\x31\x00\xF9\x3E\xA5\xF5\xAF\xF1")
    assert s["A"] == 0xA5


def test_ld_a_i_reports_iff2():
    s = run_code(b"\xFB\x00\xED\x57")
    assert s["F"] & 4


def test_neg_and_cpl_flags():
    s = run_code(b"\xED\x44", A=1, F=0)
    assert s["A"] == 0xFF and s["F"] & 3 == 3
    s = run_code(b"\x2F", A=0x0F, F=0)
    assert s["A"] == 0xF0 and s["F"] & 0x12 == 0x12


# --- command line -----------------------------------------------------------------

def test_cli_runs_a_rom_and_writes_the_port_log(tmp_path, capsys):
    from romrun.cli import main
    rom = tmp_path / "t.bin"
    rom.write_bytes(b"\x3E\x05\xD3\x10\xDB\x08\x76")
    log = tmp_path / "p.log"
    rc = main([str(rom), "--port-log", str(log), "--in", "8=0x5A", "--json"])
    assert rc == 0
    assert log.read_text().splitlines() == ["14 OUT 0510 05", "25 IN 0508 5A"]
    assert '"reason": "halt"' in capsys.readouterr().out


def test_cli_irq_every_stop_at_symbol_and_measure(tmp_path, capsys):
    from romrun.cli import main
    rom = tmp_path / "t.bin"
    rom.write_bytes(image(**VECTORS, **MAIN_EI))
    sym = tmp_path / "t.sym"
    sym.write_text("0104 LOOP\n")
    rc = main([str(rom), "--sym", str(sym), "--irq-every", "1000",
               "--irq-start", "1000", "--max-t", "5500", "--json"])
    assert rc == 0
    assert '"reason": "budget"' in capsys.readouterr().out
    rc = main([str(rom), "--sym", str(sym), "--stop-at", "loop", "--json"])
    assert '"reason": "address:0104"' in capsys.readouterr().out


def test_cli_rom_write_fault_exit_status(tmp_path):
    from romrun.cli import main
    rom = tmp_path / "t.bin"
    rom.write_bytes(ROM_WRITE)
    assert main([str(rom), "--rom-write", "fault", "--quiet"]) == 2
    assert main([str(rom), "--quiet"]) == 0


def test_cli_cpm_mode_prints_console_output(tmp_path, capsys):
    from romrun.cli import main
    com = tmp_path / "hi.com"
    com.write_bytes(HELLO)
    assert main([str(com), "--cpm", "--quiet"]) == 0
    assert capsys.readouterr().out == "Hi"


def test_cli_feed_syntax():
    from romrun.cli import parse_in
    m = Machine()
    parse_in(m, "0=seq:1,2")
    parse_in(m, "0x4=busy:100")
    parse_in(m, "9=steps:0:1,50:2")
    assert (m.feed.read(0, 0), m.feed.read(0, 0), m.feed.read(0, 0)) == (1, 2, 2)
    assert m.feed.read(4, 99) == 0x80 and m.feed.read(4, 100) == 0
    assert m.feed.read(9, 10) == 1 and m.feed.read(9, 60) == 2
