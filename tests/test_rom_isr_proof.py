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
