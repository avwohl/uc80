"""An NMI during the push fill of the BSS clear (integrate12 repair).

The push fill of crt0.mac puts SP in the BSS, so an NMI pushes its return
address and its registers into the BSS.  The first version cleared the low
bytes before the push loop, and an NMI near the end of the loop left its
pushed bytes in the cleared part.  Now the loop stops 32 bytes or more above
the start of the BSS, and a byte loop on the real stack clears the low part
last.  The test starts an NMI every 401 T-states at many phases and checks
that the whole BSS is zero, on a CP/M machine with random RAM at start.
"""

import subprocess
import sys

import pytest

from .rom_flows import REPO
from .test_struct_return_abi import LIB_DIR, _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>
unsigned char head[8] = {1, 2, 3, 4, 5, 6, 7, 8};
unsigned char a1[SIZE];
unsigned char a2[5];
unsigned i, bad;
int main(void)
{
    for (i = 0; i < sizeof a1; i++) if (a1[i]) bad++;
    for (i = 0; i < sizeof a2; i++) if (a2[i]) bad++;
    printf("head=%u%u%u%u%u%u%u%u bad=%u\n", head[0], head[1], head[2],
           head[3], head[4], head[5], head[6], head[7], bad);
    return 0;
}
"""


def build(tmp_path, size, *flags):
    (tmp_path / "t.c").write_text(SRC)
    cmd = [sys.executable, "-m", "uc80.main", str(tmp_path / "t.c"),
           f"-DSIZE={size}", *flags, "-o", str(tmp_path / "t.mac")]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(tmp_path / "t.mac"), "-o",
                    str(tmp_path / "t.rel")], check=True, capture_output=True)
    subprocess.run(["ul80", str(tmp_path / "t.rel"), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o",
                    str(tmp_path / "t.com")], check=True, capture_output=True)
    return tmp_path / "t.com"


def run_with_nmi(com, start, period=401):
    sys.path.insert(0, str(REPO / "bench"))
    from romrun import Machine
    m = Machine(mode="cpm", ram_fill="random", im=1)
    m.load_com(str(com))
    mem = m.cpu.mem
    mem[0xFC:0x100] = bytes([0xED, 0x56, 0xFB, 0x00])        # IM 1; EI; NOP
    m.cpu.set(PC=0xFC)
    # NMI handler at 66h: push af; ld a,(0FD01h); inc a; ld (0FD01h),a;
    # pop af; retn
    mem[0x66:0x66 + 11] = bytes([0xF5, 0x3A, 0x01, 0xFD, 0x3C, 0x32, 0x01,
                                 0xFD, 0xF1, 0xED, 0x45])
    mem[0xFD01] = 0
    m.add_nmi_every(period, start=start)
    res = m.run(max_tstates=3_000_000)
    out = bytes(m.console).replace(b"\r\n", b"\n").decode("latin-1")
    return res.reason, out, mem[0xFD01]


@pytest.mark.parametrize("size", [6000, 6001, 6013])
def test_an_nmi_at_any_phase_of_the_push_fill_leaves_the_bss_zero(
        tmp_path, size):
    com = build(tmp_path, size)
    assert "__bss_big" in (tmp_path / "t.mac").read_text()
    seen_nmi = False
    for start in range(0, 401, 5):
        reason, out, n = run_with_nmi(com, start)
        seen_nmi = seen_nmi or n > 0
        assert out.strip() == "head=12345678 bad=0", (start, reason, out)
    assert seen_nmi
