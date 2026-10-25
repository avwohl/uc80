"""The proof ROMs of bench/rom/isrproof.py: a real NMI command queue, a
YM2610 timer interrupt driving the sequencer, and bank selection with
z80_in16.  Each ROM runs in bench/romrun and its port log is compared with
the expected sequence.  See docs/rom.md ("Proof: interrupts and ports").
"""

import shutil
import sys
from pathlib import Path

import pytest

from .test_rom_target import _ul80_has_rom_options

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "bench" / "rom"))

import isrproof  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and _ul80_has_rom_options()),
    reason="needs um80 and a ul80 with ROM options")


def test_nmi_command_queue(tmp_path):
    r = isrproof.prove_nmi(tmp_path)
    assert r["ok"], r
    assert r["nmis"] == 300
    # 667 T-states from the NMI handler's first instruction to its RETN
    # (the NMI acknowledge, 11 T-states, is not in the figure)
    assert r["isr_min"] == r["isr_max"] == 667


def test_ym_timer_irq_drives_the_sequencer(tmp_path):
    r = isrproof.prove_seq(tmp_path)
    assert r["ok"], r
    assert r["irqs"] == 500
    assert r["got"] == r["expected"] == 7934      # the CP/M golden count


def test_bank_select_with_in16(tmp_path):
    r = isrproof.prove_bank(tmp_path)
    assert r["ok"], r
    assert r["bank_ports"] == [(b << 8) | 0x0B for b in range(2, 8)]


def test_neogeo_driver_example(tmp_path):
    """examples/neogeo_driver.c (docs/neogeo_driver.md): NMI command,
    reply on port 0Ch, bank select, YM writes, timer IRQ."""
    import os
    from sndrom import LAYOUTS, run
    from romrun import Machine, YM2610
    src = REPO / "examples" / "neogeo_driver.c"
    (tmp_path / "d.c").write_text(src.read_text())
    lay = LAYOUTS["neogeo"]
    lib = Path(run(["uc80", "--print-lib-dir"], tmp_path)[1].strip())
    steps = [
        ["uc80", "--target=rom", "d.c", "-o", "d.mac"],
        ["um80", "d.mac", "-o", "d.rel"],
        ["ul80", "--bin", "-p", "0", "-d", "%X" % lay["data"], "--rom-data",
         "--rom-size", "%X" % lay["rom_size"],
         "--ram-size", "%X" % (0x10000 - lay["data"]), "--min-stack", "100",
         "-o", "d.bin", "-S", "d.sym", "d.rel",
         str(lib / "libc_rom.lib"), str(lib / "runtime.lib")],
    ]
    for cmd in steps:
        rc, out = run(cmd, tmp_path)
        assert rc == 0, out
    m = Machine(nmi_gate=True)
    m.load_rom(str(tmp_path / "d.bin"))
    m.load_symbols(str(tmp_path / "d.sym"))
    YM2610(m)
    m.feed.queue(0x00, [0x02])
    m.add_nmi_at(400000)
    m.run(max_tstates=1_500_000)
    outs = [(p & 0xFF, v) for (_t, k, p, v) in m.port_log if k == "OUT"]
    ins = [p for (_t, k, p, v) in m.port_log if k == "IN"]
    assert outs.index((0x08, 0)) > outs.index((0x18, 0))   # gate: close, open
    assert (0x0C, 0x02) in outs                            # reply
    assert 0x020B in ins                                   # bank select, B=2
    assert (0x04, 0x10) in outs                            # YM reg 10h
    assert m.peek8("_ticks") >= 1                          # timer IRQ ran
