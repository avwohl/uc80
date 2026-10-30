#!/usr/bin/env python3
"""Proof of the interrupt and port features: three sound-driver ROMs that
run in bench/romrun with real interrupts, compared with expected port logs.

Part of the uc80 benchmark harness.  SPDX-License-Identifier: GPL-3.0-or-later
See docs/rom.md ("Proof: interrupts and ports").

    python3 bench/rom/isrproof.py            # all three
    python3 bench/rom/isrproof.py nmi seq bank iy
    python3 bench/rom/isrproof.py --sdas     # uc80 --asm-syntax=sdas, sdldz80
    python3 bench/rom/isrproof.py --save-workareas   # uc80 --isr-save-workareas

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
iy    Pointer locals in IY and registers in BC and DE under a real NMI.
      walk_main () keeps its list pointer in IY and calls mark (), which
      uses IY too (the caller must save IY).  The NMI function runs
      walk_isr () on another list (the interrupt function must save IY).
      The three sums must equal a Python model, and each of the three
      functions must use (IY+d).
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
UC80_FLAGS = []         # more uc80 options (--save-workareas)


def build_source(work: Path, c_name: str, text: str, layout="neogeo",
                 min_stack=0x100):
    """Compile text with the ROM snd.h and z80io.mac, assemble and link.
    Returns (ok, log, bin path, sym path, compiler messages)."""
    work.mkdir(parents=True, exist_ok=True)
    (work / c_name).write_text('#include "bench.h"\n' + text)
    (work / "z80io.mac").write_text((HERE / "z80io.mac").read_text())
    if SDAS_LIBS:
        return sdas_compile_link(work, c_name, layout, SDAS_LIBS,
                                 extra=UC80_FLAGS)
    lay = LAYOUTS[layout]
    lib = Path(run(["uc80", "--print-lib-dir"], work)[1].strip())
    steps = [
        ["uc80", "--target=rom", "-I", str(HERE), "-I", str(BENCH),
         *UC80_FLAGS, c_name, "z80io.mac", "-o", "k.mac"],
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


# ---- iy: a pointer in IY and register locals survive an NMI --------------

IY_SRC = r"""
#include "snd.h"
#include <z80.h>

struct rec { uint8_t a; uint8_t b; uint16_t c; uint8_t f; struct rec *next; };
static struct rec recs[6];
static struct rec irecs[4];
static struct rec mrecs[3];
static volatile uint16_t isr_total, isr_n;
static uint16_t side;

/* Each function keeps its list pointer p in IY.  mark () runs inside the
 * loop of walk_main (): walk_main () must save IY around the call.  The NMI
 * function runs walk_isr (): the interrupt function must save IY.  Each
 * function has its own copy, so no function shares locals with an interrupt. */
static void mark(void)
{
    struct rec *q = mrecs;
    while (q) {
        q->b++;
        q->f = (uint8_t) (q->f + q->b);
        side = (uint16_t) (side + q->a + q->c + q->f);
        q->c = (uint16_t) (q->c + 1);
        q = q->next;
    }
}

static uint16_t walk_main(void)
{
    struct rec *p = recs;
    uint16_t t = 0;
    while (p) {
        t = (uint16_t) (t + p->a + p->c);
        p->b++;
        p->f = (uint8_t) (p->a ^ p->b);
        if (p->f & 1)
            t++;
        mark();
        t = (uint16_t) (t + p->c);
        p = p->next;
    }
    return t;
}

static uint16_t walk_isr(void)
{
    struct rec *p = irecs;
    uint16_t t = 0;
    while (p) {
        t = (uint16_t) (t + p->a + p->c);
        p->b++;
        p->f = (uint8_t) (p->a ^ p->b);
        if (p->f & 1)
            t++;
        t = (uint16_t) (t + p->c);
        p = p->next;
    }
    return t;
}

#pragma uc80 nmi nmi_isr
void nmi_isr(void)
{
    isr_total = (uint16_t) (isr_total + walk_isr());
    isr_n++;
}

static void fill(struct rec *r, int n, uint8_t seed)
{
    for (int i = 0; i < n; i++) {
        r[i].a = (uint8_t) (seed + 7 * i);
        r[i].b = 0;
        r[i].c = (uint16_t) (seed * 100 + 13 * i);
        r[i].next = i + 1 < n ? &r[i + 1] : 0;
    }
}

int main(void)
{
    uint16_t total = 0;

    isr_total = 0;
    isr_n = 0;
    fill(recs, 6, 11);
    fill(irecs, 4, 90);
    fill(mrecs, 3, 40);
    for (int r = 0; r < 120; r++)
        total = (uint16_t) (total + walk_main());
    out(0x10, (uint8_t) total);
    out(0x11, (uint8_t) (total >> 8));
    out(0x12, (uint8_t) isr_total);
    out(0x13, (uint8_t) (isr_total >> 8));
    out(0x14, (uint8_t) isr_n);
    out(0x15, (uint8_t) (isr_n >> 8));
    out(0x16, (uint8_t) side);
    out(0x17, (uint8_t) (side >> 8));
    out(PORT_END, 0);
    return 0;
}
"""


def _model_walk(n, seed, rounds, marks=0):
    """The sum that walk_main () or walk_isr () returns over the rounds
    (b counts the visits, f = a ^ b)."""
    recs = [dict(a=(seed + 7 * i) & 255, b=0, c=(seed * 100 + 13 * i) & 0xFFFF)
            for i in range(n)]
    tot = []
    for _ in range(rounds):
        t = 0
        for r in recs:
            t += r["a"] + r["c"]
            r["b"] = (r["b"] + 1) & 255
            if (r["a"] ^ r["b"]) & 1:
                t += 1
            t += r["c"]
        tot.append(t & 0xFFFF)
    return tot


def prove_iy(work: Path, period=3000, rounds=120, nmis=100, start=40000):
    info = build_source(work / "iy", "iyptr.c", IY_SRC)
    if not info["ok"]:
        return dict(ok=False, why="build failed\n" + info["log"])
    # the text of the compiler: k.mac (um80 flow) or k.s (sdas flow)
    text = ""
    for nm in ("k.mac", "k.s"):
        if (work / "iy" / nm).exists():
            text = (work / "iy" / nm).read_text()
    uses = {}
    for fn in ("walk_main", "walk_isr", "mark"):
        body = ""
        if f"\n_{fn}:" in text:
            body = text.split(f"\n_{fn}:")[1].split("\n; Function ")[0]
        uses[fn] = "(iy+" in body.lower()
    m = Machine(ram=NEOGEO["ram"], banks=None, ram_fill="random", sp="poison",
                track=True)
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    m.magic_ports.add(0xFF)
    m.add_nmi_every(period, start=start, count=nmis)
    res = m.run(max_tstates=200_000_000)
    got = {p & 0xFF: v for (_t, k, p, v) in m.port_log if k == "OUT"}
    word = lambda lo: got.get(lo, 0) | (got.get(lo + 1, 0) << 8)   # noqa: E731
    n = word(0x14)
    exp_main = sum(_model_walk(6, 11, rounds)) & 0xFFFF
    # the NMI function visits irecs n times: b counts across the calls
    exp_isr = sum(_model_walk(4, 90, n)) & 0xFFFF if n else 0
    # mark () runs 6 times per walk_main (): it changes b, f and c of mrecs
    mr = [dict(a=(40 + 7 * i) & 255, b=0, c=(4000 + 13 * i) & 0xFFFF, f=0)
          for i in range(3)]
    side = 0
    for _ in range(6 * rounds):
        for r in mr:
            r["b"] = (r["b"] + 1) & 255
            r["f"] = (r["f"] + r["b"]) & 255
            side += r["a"] + r["c"] + r["f"]
            r["c"] = (r["c"] + 1) & 0xFFFF
            side &= 0xFFFF
    exp_side = side
    ok = (res.reason.startswith("port:") and n == nmis
          and word(0x10) == exp_main and word(0x12) == exp_isr
          and word(0x16) == exp_side and all(uses.values())
          and m.rom_writes == [])
    return dict(ok=ok, nmis=n, main_total=word(0x10), exp_main=exp_main,
                isr_total=word(0x12), exp_isr=exp_isr, side=word(0x16),
                exp_side=exp_side, reason=res.reason, uses_iy=uses,
                stack=m.stack_depth(top=0x10000))


MEMISR_SRC = r"""
#include "snd.h"
#include <z80.h>
#include <string.h>
typedef unsigned short U; typedef unsigned char B;
struct r3 { B a,b,c; }; struct r7 { B v[7]; };
static struct r3 ia[4], ib[4];
static struct r7 ja[3], jb[3];
static const struct r7 jc[3] = {{{1,2,3,4,5,6,7}},{{9,8,7,6,5,4,3}},{{0}}};
static B ibuf[64], jbuf[64];
static const B kbuf[24] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24};
static volatile U isr_total, isr_n, nested;
static volatile B ready, busy;
static U sumb(const B *p, int n) { U s = 0; int i; for (i = 0; i < n; i++) s = (U)(s*31 + p[i] + 1); return s; }
static U sumi(const B *p, int n) { U s = 0; int i; for (i = 0; i < n; i++) s = (U)(s*31 + p[i] + 1); return s; }
#pragma uc80 nmi nmi_isr
void nmi_isr(void)
{
    int k;
    if (!ready || isr_n >= 60) return;
    if (busy) nested++;
    busy = 1;
    k = isr_n & 3;
    memset(ibuf, (B)isr_n, 5);
    memcpy(ibuf+8, kbuf, 20);
    memset(ibuf+30, 3, 4);
    memcpy(ibuf+40, ibuf, 3);
    ia[k] = ib[(k+1)&3]; ib[k].a = (B)isr_n; ja[k&1] = jc[1]; ja[2].v[k] = (B)k;
    isr_total = (U)(isr_total + sumi(ibuf,64) + sumi((B*)ia,12) + sumi((B*)ja,21));
    isr_n++;
    busy = 0;
}
static U mainwork(void)
{
    U a = 1, b = 2, c = 3, t = 0; int i, j; B *p = jbuf;
    for (i = 0; i < 40; i++) {
        memset(p, (B)(i+a), 4);
        memcpy(p+8, jbuf+32, 4);
        memset(p+16, 0x5A, 9);
        memcpy(p+32, p+48, 7);
        for (j = 0; j < 3; j++) { a = (U)(a*3 + p[j] + j); b ^= a; c = (U)(c + b); }
        jb[i%3] = jb[(i+1)%3]; jb[i%3].v[0] = (B)i;
        t = (U)(t + a + b + c + sumb(jbuf,64) + sumb((B*)jb,21));
    }
    return t;
}
int main(void)
{
    U total = 0; int r;
    for (r = 0; r < 64; r++) { jbuf[r] = (B)(r*5+1); ibuf[r] = (B)r; }
    for (r = 0; r < 7; r++) jb[r%3].v[r] = (B)(r+9);
    ready = 1;
    for (r = 0; r < 10; r++) total = (U)(total + mainwork());
    while (isr_n < 60) ;
    out(0x10,(B)total); out(0x11,(B)(total>>8));
    out(0x12,(B)isr_total); out(0x13,(B)(isr_total>>8));
    out(0x14,(B)isr_n); out(0x15,(B)(isr_n>>8));
    out(0x16,(B)nested);
    out(PORT_END,0); return 0;
}
"""


def _sum31(buf):
    s = 0
    for x in buf:
        s = (s * 31 + x + 1) & 0xFFFF
    return s


def _model_memisr(nmis=60, rounds=10):
    """Python model of MEMISR_SRC: (main total, isr total)."""
    kbuf = list(range(1, 25)) + [0] * 40
    jc1 = [9, 8, 7, 6, 5, 4, 3]
    ibuf = list(range(64))
    ia = [[0, 0, 0] for _ in range(4)]
    ib = [[0, 0, 0] for _ in range(4)]
    ja = [[0] * 7 for _ in range(3)]
    tot = 0
    for n in range(nmis):
        k = n & 3
        ibuf[0:5] = [n & 255] * 5
        ibuf[8:28] = kbuf[0:20]
        ibuf[30:34] = [3] * 4
        ibuf[40:43] = ibuf[0:3]
        ia[k] = list(ib[(k + 1) & 3])
        ib[k][0] = n & 255
        ja[k & 1] = list(jc1)
        ja[2][k] = k
        flat = lambda rows: [x for r in rows for x in r]   # noqa: E731
        tot = (tot + _sum31(ibuf) + _sum31(flat(ia)) + _sum31(flat(ja))) & 0xFFFF
    jbuf = [(r * 5 + 1) & 255 for r in range(64)]
    jb = [[0] * 7 for _ in range(3)]
    for r in range(7):
        jb[r % 3][r] = r + 9
    total = 0
    for _ in range(rounds):
        a, b, c, t = 1, 2, 3, 0
        for i in range(40):
            jbuf[0:4] = [(i + a) & 255] * 4
            jbuf[8:12] = jbuf[32:36]
            jbuf[16:25] = [0x5A] * 9
            jbuf[32:39] = jbuf[48:55]
            for j in range(3):
                a = (a * 3 + jbuf[j] + j) & 0xFFFF
                b ^= a
                c = (c + b) & 0xFFFF
            jb[i % 3] = list(jb[(i + 1) % 3])
            jb[i % 3][0] = i & 255
            t = (t + a + b + c + _sum31(jbuf) + _sum31([x for r in jb for x in r])) & 0xFFFF
        total = (total + t) & 0xFFFF
    return total, tot


def prove_memisr(work: Path, period=120011):
    """memset, memcpy, struct copies and a helper call in an NMI function
    while main does the same on other buffers.  The handler ignores the
    NMIs that arrive before main has initialised the buffers (ready) and
    counts a nested entry (a period shorter than the handler)."""
    info = build_source(work / "memisr", "memisr.c", MEMISR_SRC)
    if not info["ok"]:
        return dict(ok=False, why="build failed\n" + info["log"])
    m = Machine(ram=NEOGEO["ram"], banks=None, ram_fill="random", sp="poison",
                track=True)
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    m.magic_ports.add(0xFF)
    m.add_nmi_every(period, start=20000, count=2000)
    res = m.run(max_tstates=400_000_000)
    got = {p & 0xFF: v for (_t, k, p, v) in m.port_log if k == "OUT"}
    word = lambda lo: got.get(lo, 0) | (got.get(lo + 1, 0) << 8)   # noqa: E731
    exp_main, exp_isr = _model_memisr()
    ok = (res.reason.startswith("port:") and word(0x14) == 60
          and got.get(0x16) == 0
          and word(0x10) == exp_main and word(0x12) == exp_isr
          and m.rom_writes == [])
    return dict(ok=ok, nmis=word(0x14), nested=got.get(0x16),
                main_total=word(0x10), exp_main=exp_main,
                isr_total=word(0x12), exp_isr=exp_isr, reason=res.reason)


LIBM_SRC = r"""
#include "snd.h"
#include <z80.h>
#include <math.h>
typedef unsigned char B;
typedef unsigned short U;
static volatile B ready;
static volatile U isr_n;
static volatile long isr_val;
#ifndef REF
SAVE_PRAGMA
#pragma uc80 nmi nmi_isr
void nmi_isr(void)
{
    if (!ready || isr_n >= 12)
        return;
    isr_val = (long) (sin(2.5) * 100000.0 + sqrt(7.0) * 1000.0);
    isr_n++;
}
#endif
static void put32(B port, long v)
{
    out(port, (B) v); out(port, (B) (v >> 8));
    out(port, (B) (v >> 16)); out(port, (B) (v >> 24));
}
int main(void)
{
    int i;
    double x = 0.1, y = 3.0;
    long v;

    ready = 1;
    for (i = 0; i < 40; i++) {
        v = (long) (sin(x) * 100000.0 + sqrt(y) * 1000.0 + cos(x + 1.0) * 10.0);
        put32(0x10, v);
        x += 0.037;
        y += 1.7;
    }
#ifdef REF
    isr_val = (long) (sin(2.5) * 100000.0 + sqrt(7.0) * 1000.0);
    isr_n = 12;
#else
    while (isr_n < 12)
        ;
#endif
    put32(0x20, isr_val);
    out(0x24, (B) isr_n);
    out(PORT_END, 0);
    return 0;
}
"""


def _run_libm(work: Path, tag: str, defs: str, save: bool, nmis: bool):
    src = defs + LIBM_SRC.replace(
        "SAVE_PRAGMA", "#pragma uc80 isr_save_workareas nmi_isr"
        if save else "")
    info = build_source(work / tag, tag + ".c", src)
    if not info["ok"]:
        return None, info
    m = Machine(ram=NEOGEO["ram"], banks=None, ram_fill="random", sp="poison",
                track=True)
    m.load_rom(str(info["bin"]))
    m.load_symbols(str(info["sym"]))
    m.magic_ports.add(0xFF)
    if nmis:
        # the first NMI comes after main has set ready (the handler also
        # ignores an NMI before that)
        m.add_nmi_every(900011, start=30000, count=400)
    res = m.run(max_tstates=120_000_000)
    outs = [(p & 0xFF, v) for (_t, k, p, v) in m.port_log if k == "OUT"]
    return (res, outs, m), info


def prove_libm(work: Path):
    """main computes sin, sqrt and cos in a loop while an NMI function also
    calls sin and sqrt (the float interpreter lc_vm and lc_sqrt keep their
    work areas at fixed addresses).  The handler saves the work areas
    (#pragma uc80 isr_save_workareas).  The output of main must equal the
    output of a run without interrupts.  The same program without the pragma
    must differ: the proof has teeth."""
    ref, info = _run_libm(work, "libmref", "#define REF 1\n", False, False)
    if ref is None:
        return dict(ok=False, why="build failed\n" + info["log"])
    _res, ref_outs, _m = ref
    got, info = _run_libm(work, "libmsave", "", True, True)
    if got is None:
        return dict(ok=False, why="build failed\n" + info["log"])
    res, outs, m = got
    bad, info = _run_libm(work, "libmbad", "", False, True)
    if bad is None:
        return dict(ok=False, why="build failed\n" + info["log"])
    bad_outs = bad[1]
    main_ref = [x for x in ref_outs if x[0] == 0x10]
    main_got = [x for x in outs if x[0] == 0x10]
    main_bad = [x for x in bad_outs if x[0] == 0x10]
    accepted = len([a for a in m.accepted if a[0] == "nmi"])
    ok = (res.reason.startswith("port:") and len(main_ref) == 160
          and main_got == main_ref and outs[-5:] == ref_outs[-5:]
          and accepted >= 12 and main_bad != main_ref
          and m.rom_writes == [])
    return dict(ok=ok, nmis=accepted, values=len(main_got) // 4,
                reason=res.reason, same_as_no_interrupts=main_got == main_ref,
                without_save_differs=main_bad != main_ref,
                stack=m.stack_depth(top=0x10000))


PROOFS = {"nmi": prove_nmi, "seq": prove_seq, "bank": prove_bank,
          "iy": prove_iy, "memisr": prove_memisr,
          "libm": prove_libm}


def main(argv=None):
    global SDAS_LIBS
    args = list(argv if argv is not None else sys.argv[1:])
    sdas = "--sdas" in args
    if "--save-workareas" in args:
        UC80_FLAGS.append("--isr-save-workareas")
    while "--uc80-flag" in args:
        i = args.index("--uc80-flag")
        UC80_FLAGS.append(args[i + 1])
        del args[i:i + 2]
    args = [a for a in args if a not in ("--sdas", "--save-workareas")]
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
