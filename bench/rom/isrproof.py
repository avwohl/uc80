#!/usr/bin/env python3
"""Proof of the interrupt and port features: three sound-driver ROMs that
run in bench/romrun with real interrupts, compared with expected port logs.

Part of the uc80 benchmark harness.  SPDX-License-Identifier: GPL-3.0-or-later
See docs/rom.md ("Proof: interrupts and ports").

    python3 bench/rom/isrproof.py            # all three
    python3 bench/rom/isrproof.py nmi seq bank
    python3 bench/rom/isrproof.py --sdas     # uc80 --asm-syntax=sdas, sdldz80

nmi   The snd-cmdq kernel with a real NMI.  The NMI handler (#pragma uc80
      nmi) reads the command byte from port 0 and queues it; the main
      loop dispatches.  The expected port log comes from a Python model of
      the dispatch switch.
seq   The snd-seq kernel driven by a YM2610 timer A interrupt (IM 1, the
      handler at 38h acknowledges the timer and runs one sequencer tick).
      The YM write sequence must equal the write sequence of the plain
      loop version of the same kernel.
bank  Bank selection with z80_in16: IN with the bank number in B, then
      reads through the banked window at 8000h.  The port log must be the
      expected sequence of 16-bit port addresses and the bytes read must
      be the bytes of the image.
"""

import random
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(HERE))

from romrun import Machine, YM2610  # noqa: E402
from sndrom import (KERNELS, LAYOUTS, run, tools_missing,  # noqa: E402
                    sdas_compile_link, sdas_libs, sdas_tools_missing)

NEOGEO = LAYOUTS["neogeo"]
SDAS_LIBS = None        # a directory: build with sdasz80 and sdldz80


def build_source(work: Path, c_name: str, text: str, layout="neogeo",
                 min_stack=0x100):
    """Compile text with the ROM snd.h and z80io.mac, assemble and link.
    Returns (ok, log, bin path, sym path, compiler messages)."""
    work.mkdir(parents=True, exist_ok=True)
    (work / c_name).write_text('#include "bench.h"\n' + text)
    (work / "z80io.mac").write_text((HERE / "z80io.mac").read_text())
    if SDAS_LIBS:
        return sdas_compile_link(work, c_name, layout, SDAS_LIBS)
    lay = LAYOUTS[layout]
    lib = Path(run(["uc80", "--print-lib-dir"], work)[1].strip())
    steps = [
        ["uc80", "--target=rom", "-I", str(HERE), "-I", str(BENCH),
         c_name, "z80io.mac", "-o", "k.mac"],
        ["um80", "k.mac", "-o", "k.rel"],
        ["ul80", "--bin", "-p", "0", "-d", "%X" % lay["data"], "--rom-data",
         "--rom-size", "%X" % lay["rom_size"],
         "--ram-size", "%X" % (0x10000 - lay["data"]),
         "--min-stack", "%X" % min_stack, "--verbose",
         "-o", "k.bin", "-S", "k.sym", "k.rel",
         str(lib / "libc_rom.lib"), str(lib / "runtime.lib")],
    ]
    log = []
    for cmd in steps:
        rc, out = run(cmd, work)
        log.append(out)
        if rc != 0:
            return dict(ok=False, log="\n".join(log))
    return dict(ok=True, log="\n".join(log), bin=work / "k.bin",
                sym=work / "k.sym", compiler=log[0])


def ports_of(m, lo=0, hi=0xFF, kind="OUT"):
    return [(p & 0xFF, v) for (_t, k, p, v) in m.port_log
            if k == kind and lo <= (p & 0xFF) <= hi]


def isr_spans(m, info, name):
    """Measure the T-states from the first instruction of the interrupt
    function to the final RETI or RETN (14 T-states are added for it)."""
    img = Path(info["bin"]).read_bytes()
    start = m.addr(name)
    end = min(i for i in (img.find(b"\xed\x4d", start),
                          img.find(b"\xed\x45", start)) if i >= 0)
    return m.measure("isr_" + name, start, end), 14


# ---- nmi: the snd-cmdq kernel with a real NMI ---------------------------

NMI_TAIL = r"""
#include <z80.h>

#define NCMD 300

static volatile uint16_t nmi_count;

#pragma uc80 nmi nmi_isr
void nmi_isr(void)
{
    nmi_cmd(z80_in(0x00));          /* the command byte from the 68k */
    nmi_count++;
}

int main(void)
{
    uint16_t done = 0;
    int c;

    while (done < NCMD) {
        c = cmd_pop();
        if (c < 0)
            continue;
        dispatch((uint8_t) c);
        done++;
    }
    out(PORT_END, 0);
    return 0;
}
"""


class Dispatch:
    """The dispatch switch of cmdq.c, as the ports see it."""

    def __init__(self):
        self.adpcm = [0] * 6
        self.master = 0x3F
        self.music = 0

    def __call__(self, cmd):
        out = []
        w = lambda p, v: out.append((p, v & 0xFF))      # noqa: E731
        n = cmd & 7
        hi = cmd >> 4
        if hi in (1, 2, 3) and n > 5:
            n -= 6
        if hi == 1:
            w(6, 0x08 + n)
            w(7, 0xC0 | self.adpcm[n])
            w(6, 0x00)
            w(7, 1 << n)
        elif hi == 2:
            w(6, 0x00)
            w(7, 0x80 | (1 << n))
        elif hi == 3:
            self.adpcm[n] = ((cmd & 0x0F) << 1) & 0xFF
        elif hi == 4:
            if self.master > 3:
                self.master -= 4
            w(6, 1)
            w(7, self.master)
        elif hi == 5:
            if self.master < 0x3C:
                self.master += 4
            w(6, 1)
            w(7, self.master)
        elif hi == 6:
            self.music = 1
            w(4, 0x27)
            w(5, 0x3F)
        elif hi == 7:
            self.music = 0
            for k in range(4):
                w(4, 0x28)
                w(5, k + 1 if k < 2 else k + 3)
        elif hi in (8, 9):
            tempo = (60 + ((cmd & 0x1F) << 2)) & 0xFF
            w(4, 0x26)
            w(5, 256 - tempo)
        elif hi in (0xA, 0xB):
            k = cmd % 3
            w(4, k << 1)
            w(5, cmd)
            w(4, 8 + k)
            w(5, 0x0F)
        elif hi == 0xC:
            w(6, 0xA6)
            w(7, 0x20 | n)
            w(6, 0xA2)
            w(7, cmd)
            w(4, 0x28)
            w(5, 0xF6)
        elif hi in (0xE, 0xF):
            w(0x0C, self.music | (self.master << 1))
        return out


def commands(count=300, seed=2610):
    rng = random.Random(seed)
    cmds = [rng.randrange(256) for _ in range(count)]
    for i, hi in enumerate(range(16)):          # every case at least once
        cmds[i * 5] = (hi << 4) | rng.randrange(16)
    return cmds


def prove_nmi(work: Path, period=6000):
    text = (BENCH / "programs" / "snd-cmdq" / KERNELS["snd-cmdq"]).read_text()
    head = text[:text.index("int main(void)")]
    info = build_source(work / "nmi", "nmicmdq.c", head + NMI_TAIL)
    if not info["ok"]:
        return dict(ok=False, why="build failed\n" + info["log"])
    cmds = commands()
    m = Machine(ram=NEOGEO["ram"], banks=None, ram_fill="random", sp="poison",
                track=True)
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    m.magic_ports.add(0xFF)
    m.feed.queue(0x00, cmds)
    m.add_nmi_every(period, start=period, count=len(cmds))
    span, extra = isr_spans(m, info, "_nmi_isr")
    res = m.run(max_tstates=len(cmds) * period + 2_000_000)
    model = Dispatch()
    expected = []
    for c in cmds:
        expected.append((0x0C, c))              # the reply of the NMI side
        expected += model(c)
    got = [(p, v) for (p, v) in ports_of(m, 0, 0xFD)]
    ok = (res.reason.startswith("port:") and got == expected
          and len([a for a in m.accepted if a[0] == "nmi"]) == len(cmds)
          and m.rom_writes == [])
    return dict(ok=ok, got=len(got), expected=len(expected),
                reason=res.reason, nmis=len(cmds),
                isr_min=span.min + extra, isr_max=span.max + extra,
                isr_mean=span.total / span.count + extra,
                stack=m.stack_depth(top=0x10000),
                first_diff=next((i for i, (a, b) in enumerate(
                    zip(got, expected)) if a != b), None))


# ---- seq: the snd-seq kernel driven by the YM2610 timer ------------------

SEQ_IRQ = r"""
#include <z80.h>

/* Timer A: NA = 0, the longest period: 1024 * 72 chip clocks, which are
 * 36 864 T-states at the Neo Geo clocks. */
static volatile uint16_t irq_frames;

static void ym_timer_start(void)
{
    z80_out(0x04, 0x24); z80_out(0x05, 0x00);       /* NA high 8 bits */
    z80_out(0x04, 0x25); z80_out(0x05, 0x00);       /* NA low 2 bits */
    z80_out(0x04, 0x27); z80_out(0x05, 0x15);       /* load, enable, reset */
}

/* The timer interrupt: acknowledge it, run one sequencer tick.  No EI at
 * entry (the timer line stays high until the flag is reset); EI before RETI. */
#pragma uc80 interrupt ym_irq critical
void ym_irq(void)
{
    if (z80_in(0x04) & 0x01) {                      /* timer A flag */
        z80_out(0x04, 0x27); z80_out(0x05, 0x15);   /* reset the flag */
        tick();
        irq_frames++;
    }
}
"""

SEQ_LOOP = """    ym_timer_start();
    intrinsic_label(TIMER_START);
    z80_ei();
    while (z80_load16(&irq_frames) < ticks) {
        __asm__("\\thalt");
    }
    z80_di();
    intrinsic_label(TIMER_STOP);
"""


def run_plain_seq(work: Path):
    """The loop version of snd-seq: the reference write sequence."""
    text = (BENCH / "programs" / "snd-seq" / KERNELS["snd-seq"]).read_text()
    info = build_source(work / "seq-loop", "seq.c", text)
    if not info["ok"]:
        return None, info["log"]
    m = Machine(ram=NEOGEO["ram"], banks=None, ram_fill="random", sp="poison")
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    m.magic_ports.add(0xFF)
    res = m.run(max_tstates=200_000_000)
    return (ports_of(m, 0, 0xFD), res), None


def prove_seq(work: Path):
    ref, why = run_plain_seq(work)
    if ref is None:
        return dict(ok=False, why="reference build failed\n" + why)
    ref_writes, _ = ref
    text = (BENCH / "programs" / "snd-seq" / KERNELS["snd-seq"]).read_text()
    a = text.index("int main(void)")
    loop_a = text.index("    intrinsic_label(TIMER_START);")
    loop_b = text.index("    intrinsic_label(TIMER_STOP);\n") + len(
        "    intrinsic_label(TIMER_STOP);\n")
    src = text[:a] + SEQ_IRQ + "\n" + text[a:loop_a] + SEQ_LOOP + text[loop_b:]
    info = build_source(work / "seq-irq", "seqirq.c", src)
    if not info["ok"]:
        return dict(ok=False, why="build failed\n" + info["log"])
    m = Machine(ram=NEOGEO["ram"], banks=None, ram_fill="random", sp="poison",
                track=True)
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    m.magic_ports.add(0xFF)
    ym = YM2610(m)
    span, extra = isr_spans(m, info, "_ym_irq")
    res = m.run(max_tstates=200_000_000)
    # Drop the set-up writes before the first interrupt and the two
    # acknowledge writes at the start of each interrupt.
    accepts = [t for (k, t) in m.accepted if k == "irq"]
    got = []
    skip = 0
    ai = 0
    for (t, kind, port, value) in m.port_log:
        if kind != "OUT" or (port & 0xFF) > 0xFD:
            continue
        while ai < len(accepts) and t >= accepts[ai]:
            ai += 1
            skip = 2
        if not accepts or t < accepts[0]:
            continue
        if skip:
            skip -= 1
            continue
        got.append((port & 0xFF, value))
    text_out = m.output_text() if False else None
    ok = (res.reason.startswith("port:") and got == ref_writes
          and len(accepts) == 500 and m.rom_writes == [])
    return dict(ok=ok, got=len(got), expected=len(ref_writes),
                reason=res.reason, irqs=len(accepts),
                isr_min=span.min + extra, isr_max=span.max + extra,
                isr_mean=span.total / span.count + extra,
                stack=m.stack_depth(top=0x10000),
                compiler=info["compiler"].strip(),
                first_diff=next((i for i, (x, y) in enumerate(
                    zip(got, ref_writes)) if x != y), None))


# ---- bank: z80_in16 selects banks ----------------------------------------

BANK_SRC = r"""
#include <z80.h>

static const uint16_t offsets[4] = {0, 1, 0x1234, 0x3FFF};

static uint8_t read_bank(uint8_t bank, uint16_t offset)
{
    z80_in16(((uint16_t) bank << 8) | 0x0B);        /* select, B = bank */
    return *(const volatile uint8_t *) (0x8000 + offset);
}

int main(void)
{
    uint8_t bank, k;

    for (bank = 2; bank < 8; bank++)
        for (k = 0; k < 4; k++)
            z80_out(0x0C, read_bank(bank, offsets[k]));
    return 0;
}
"""


def prove_bank(work: Path):
    info = build_source(work / "bank", "bank.c", BANK_SRC)
    if not info["ok"]:
        return dict(ok=False, why="build failed\n" + info["log"])
    code = Path(info["bin"]).read_bytes()
    img = bytearray(code) + bytes(0x8000 - len(code))
    rnd = random.Random(0xB0B)
    img += bytes(rnd.randrange(256) for _ in range(0x4000 * 6 + 0x4000))
    m = Machine(ram_fill="random", sp="poison")           # the Neo Geo map
    m.load_rom(bytes(img))
    m.load_symbols(str(info["sym"]))
    res = m.run(max_tstates=1_000_000)
    offsets = [0, 1, 0x1234, 0x3FFF]
    exp_in = []
    exp_out = []
    for bank in range(2, 8):
        for off in offsets:
            exp_in.append(((bank << 8) | 0x0B, 0xFF))
            exp_out.append(img[bank * 0x4000 + off])
    got_in = [(p, v) for (_t, k, p, v) in m.port_log
              if k == "IN"]
    got_out = [v for (_t, k, p, v) in m.port_log
               if k == "OUT" and (p & 0xFF) == 0x0C]
    ok = (res.reason in ("halt", "idle") and got_in == exp_in
          and got_out == exp_out)
    return dict(ok=ok, reads=len(got_in), reason=res.reason,
                bank_ports=sorted({p for p, _v in got_in}))


PROOFS = {"nmi": prove_nmi, "seq": prove_seq, "bank": prove_bank}


def main(argv=None):
    global SDAS_LIBS
    args = list(argv if argv is not None else sys.argv[1:])
    sdas = "--sdas" in args
    args = [a for a in args if a != "--sdas"]
    names = args or list(PROOFS)
    missing = sdas_tools_missing() if sdas else tools_missing()
    if missing:
        print("missing tools: " + " ".join(missing), file=sys.stderr)
        return 2
    bad = 0
    work = Path(tempfile.mkdtemp(prefix="isrproof-"))
    SDAS_LIBS = sdas_libs(work / "sdaslib") if sdas else None
    for name in names:
        r = PROOFS[name](work)
        flag = "OK" if r["ok"] else "FAILED"
        detail = " ".join(f"{k}={v}" for k, v in r.items()
                          if k not in ("ok", "compiler", "why"))
        print(f"{name}: {flag}  {detail}")
        if not r["ok"]:
            bad += 1
            if r.get("why"):
                print(r["why"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
