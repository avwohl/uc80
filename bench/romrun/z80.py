"""A Z80 core with exact T-state counts, written for the ROM test runner.

The core is generated source text.  `_build_source()` writes one factory
function; `Z80()` runs that function once per CPU.  Every register is a
closure cell of the factory, so an instruction handler reads and writes a
register without an attribute lookup.  This is the fastest layout that
CPython has, and it is the reason the generator exists.

Public surface (class Z80):

  mem, wok       64 KiB bytearray of memory, and a 64 KiB bytearray of
                 write flags (1 = writable, 0 = a write calls `wbad`)
  bp             64 KiB bytearray of breakpoint flags
  get() / set()  all registers as a dict (set takes a partial dict)
  run()          run until a hook or a limit asks to stop; returns the
                 reason (a value other than None)
  hooks          see Z80.set_hooks()

Behaviour that matters to a ROM test:

  - T-states follow the Zilog manual.  No wait states.  The IN and OUT
    I/O machine cycle is stamped at the T-state where it starts.
  - HALT idles in steps of 4 T-states.  The runner jumps to the next event.
  - IFF1/IFF2, the one-instruction EI delay, IM 0/1/2, NMI to 0066h and
    RETN/RETI restoring IFF1 from IFF2 are modelled.
  - X and Y flag bits are modelled.  The MEMPTR (WZ) register is not, so
    the X and Y bits of BIT n,(HL) come from H and not from WZ.
"""

from __future__ import annotations

# --- tables -----------------------------------------------------------------

def _parity(v: int) -> int:
    return 4 if bin(v).count("1") % 2 == 0 else 0


SZ = [(v & 0xA8) | (0x40 if v == 0 else 0) for v in range(256)]
SZP = [SZ[v] | _parity(v) for v in range(256)]

# State cells of the factory.  Each name is a closure variable.
STATE_REGS = ["A", "F", "B", "C", "D", "E", "H", "L", "SP", "PC", "IX", "IY",
              "A_", "F_", "B_", "C_", "D_", "E_", "H_", "L_", "I", "R", "R7",
              "IFF1", "IFF2", "IM", "HALTED", "T"]
OTHER_CELLS = ["EV", "EIDELAY", "NMI_P", "IRQ_P", "IRQ_L", "BUDGET", "STOP",
               "IVEC", "INH", "OUTH", "WBAD", "FIRE", "WAKE", "BPHIT", "AD", "ONACC", "IPC"]
ALL_CELLS = STATE_REGS + OTHER_CELLS

R8 = ["B", "C", "D", "E", "H", "L", None, "A"]
CC = ["not F&64", "F&64", "not F&1", "F&1", "not F&4", "F&4", "not F&128",
      "F&128"]


# --- snippet helpers --------------------------------------------------------

def W(ad: str, v: str) -> list[str]:
    """Write byte v (a simple name) at address ad (any expression)."""
    return [f"w_=({ad})", f"if wok[w_]: mem[w_]={v}", f"else: WBAD(w_,{v})"]


def push16(hi: str, lo: str) -> list[str]:
    return (["SP=(SP-1)&65535"] + W("SP", hi) + ["SP=(SP-1)&65535"]
            + W("SP", lo))


FETCH_N = ["n=mem[PC]; PC=(PC+1)&65535"]
FETCH_NN = ["nn=mem[PC]|(mem[(PC+1)&65535]<<8); PC=(PC+2)&65535"]


def alu(y: int, v: str) -> list[str]:
    """Accumulator operation y (add adc sub sbc and xor or cp) with v."""
    if y in (0, 1):
        cin = "" if y == 0 else "+(F&1)"
        return [f"r=A+{v}{cin}",
                f"F=SZ[r&255]|(r>>8)|((A^{v}^r)&16)"
                f"|((((A^{v}^128)&(A^r))>>5)&4)",
                "A=r&255"]
    if y in (2, 3):
        cin = "" if y == 2 else "-(F&1)"
        return [f"r=A-{v}{cin}",
                f"F=SZ[r&255]|((r>>8)&1)|2|((A^{v}^r)&16)"
                f"|((((A^{v})&(A^r))>>5)&4)",
                "A=r&255"]
    if y == 7:
        return [f"r=A-{v}",
                f"F=(SZ[r&255]&208)|({v}&40)|((r>>8)&1)|2|((A^{v}^r)&16)"
                f"|((((A^{v})&(A^r))>>5)&4)"]
    if y == 4:
        return [f"A=A&{v}", "F=SZP[A]|16"]
    if y == 5:
        return [f"A=A^{v}", "F=SZP[A]"]
    return [f"A=A|{v}", "F=SZP[A]"]


def inc8(x: str) -> list[str]:
    return [f"r=({x}+1)&255",
            f"F=(F&1)|SZ[r]|(16 if (r&15)==0 else 0)|(4 if {x}==127 else 0)"]


def dec8(x: str) -> list[str]:
    return [f"r=({x}-1)&255",
            f"F=(F&1)|SZ[r]|2|(16 if (r&15)==15 else 0)|(4 if {x}==128 else 0)"]


ROT = [
    "c_=v>>7; r=((v<<1)|c_)&255",
    "c_=v&1; r=(v>>1)|(c_<<7)",
    "c_=v>>7; r=((v<<1)|(F&1))&255",
    "c_=v&1; r=(v>>1)|((F&1)<<7)",
    "c_=v>>7; r=(v<<1)&255",
    "c_=v&1; r=(v>>1)|(v&128)",
    "c_=v>>7; r=((v<<1)|1)&255",
    "c_=v&1; r=v>>1",
]


def cb_core(op: int, xy: str) -> list[str]:
    """Lines for CB opcode op on the byte v; the result is r (not for BIT)."""
    x, y = op >> 6, (op >> 3) & 7
    if x == 0:
        return [ROT[y], "F=SZP[r]|c_"]
    mask = 1 << y
    if x == 1:
        hi = "|(v&128)" if y == 7 else ""
        return [f"F=(F&1)|16|({xy}&40)|(0 if v&{mask} else 68){hi}"]
    if x == 2:
        return [f"r=v&{(~mask) & 255}"]
    return [f"r=v|{mask}"]


# --- opcode generator -------------------------------------------------------

class Gen:
    """Builds the handler source of one opcode, for HL or for IX/IY."""

    def __init__(self, ix: str | None):
        self.ix = ix            # None, "IX" or "IY"

    # operands
    def hl(self) -> str:
        return self.ix or "((H<<8)|L)"

    def set_hl(self, w: str) -> list[str]:
        if self.ix:
            return [f"{self.ix}={w}"]
        return [f"H={w}>>8", f"L={w}&255"]

    def prelude(self) -> list[str]:
        """Compute `ad` for the (HL) / (IX+d) operand."""
        if not self.ix:
            return ["ad=(H<<8)|L"]
        return ["d=mem[PC]; PC=(PC+1)&65535",
                f"ad=({self.ix}+(d^128)-128)&65535"]

    def rd(self, i: int, plain: bool = False) -> str:
        if i == 6:
            return "mem[ad]"
        if self.ix and not plain and i == 4:
            return f"({self.ix}>>8)"
        if self.ix and not plain and i == 5:
            return f"({self.ix}&255)"
        return R8[i]

    def wr(self, i: int, v: str, plain: bool = False) -> list[str]:
        if i == 6:
            return W("ad", v)
        if self.ix and not plain and i == 4:
            return [f"{self.ix}=({self.ix}&255)|({v}<<8)"]
        if self.ix and not plain and i == 5:
            return [f"{self.ix}=({self.ix}&65280)|{v}"]
        return [f"{R8[i]}={v}"]

    def rp(self, p: int) -> str:
        return ["((B<<8)|C)", "((D<<8)|E)", self.hl(), "SP"][p]

    def set_rp(self, p: int, w: str) -> list[str]:
        if p == 0:
            return [f"B={w}>>8", f"C={w}&255"]
        if p == 1:
            return [f"D={w}>>8", f"E={w}&255"]
        if p == 2:
            return self.set_hl(w)
        return [f"SP={w}"]

    def rp2(self, p: int) -> str:
        return ["((B<<8)|C)", "((D<<8)|E)", self.hl(), "((A<<8)|F)"][p]

    def set_rp2(self, p: int, w: str) -> list[str]:
        if p == 3:
            return [f"A={w}>>8", f"F={w}&255"]
        return self.set_rp(p, w)

    # the opcode
    def gen(self, op: int):
        """Return (lines, cycles) or None.  cycles is an int or a string."""
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        ix = self.ix
        L: list[str] = []
        if ix and not self.uses_hl(op):
            return None
        pre = 4 if ix else 0

        if x == 0:
            if z == 0:
                if y == 0:
                    return [], 4
                if y == 1:
                    return ["A,A_=A_,A", "F,F_=F_,F"], 4
                if y == 2:
                    return (FETCH_N + ["B=(B-1)&255", "if B:",
                            "    PC=(PC+(n^128)-128)&65535", "    return 13"],
                            8)
                if y == 3:
                    return FETCH_N + ["PC=(PC+(n^128)-128)&65535"], 12
                return (FETCH_N + [f"if {CC[y-4]}:",
                        "    PC=(PC+(n^128)-128)&65535", "    return 12"], 7)
            if z == 1:
                if q == 0:
                    return (FETCH_NN + self.set_rp(p, "nn"), 10 + pre)
                L = [f"hl={self.hl()}", f"v={self.rp(p)}", "r=hl+v",
                     "F=(F&196)|((r>>8)&40)|(r>>16)|(((hl^v^r)>>8)&16)",
                     "r=r&65535"] + self.set_hl("r")
                return L, 11 + pre
            if z == 2:
                if p == 0 or p == 1:
                    rr = "((B<<8)|C)" if p == 0 else "((D<<8)|E)"
                    if q == 0:
                        return W(rr, "A"), 7
                    return [f"A=mem[{rr}]"], 7
                if p == 2:
                    if q == 0:
                        return (FETCH_NN + [f"hl={self.hl()}", "lo=hl&255",
                                "hi=hl>>8"] + W("nn", "lo")
                                + W("(nn+1)&65535", "hi"), 16 + pre)
                    return (FETCH_NN + ["lo=mem[nn]", "hi=mem[(nn+1)&65535]",
                            "w=(hi<<8)|lo"] + self.set_hl("w"), 16 + pre)
                if q == 0:
                    return FETCH_NN + W("nn", "A"), 13
                return FETCH_NN + ["A=mem[nn]"], 13
            if z == 3:
                op_ = "+" if q == 0 else "-"
                return ([f"w=({self.rp(p)}{op_}1)&65535"]
                        + self.set_rp(p, "w"), 6 + pre)
            if z in (4, 5):
                fn = inc8 if z == 4 else dec8
                if y == 6:
                    L = self.prelude() + ["v=mem[ad]"] + fn("v") + W("ad", "r")
                    return L, (11 if not ix else 23)
                return fn(self.rd(y)) + self.wr(y, "r"), 4 + pre
            if z == 6:
                if y == 6:
                    return (self.prelude() + FETCH_N + W("ad", "n"),
                            10 if not ix else 19)
                return FETCH_N + self.wr(y, "n"), 7 + pre
            # z == 7
            if y == 0:
                return ["c_=A>>7", "A=((A<<1)|c_)&255",
                        "F=(F&196)|(A&40)|c_"], 4
            if y == 1:
                return ["c_=A&1", "A=(A>>1)|(c_<<7)",
                        "F=(F&196)|(A&40)|c_"], 4
            if y == 2:
                return ["c_=A>>7", "A=((A<<1)|(F&1))&255",
                        "F=(F&196)|(A&40)|c_"], 4
            if y == 3:
                return ["c_=A&1", "A=(A>>1)|((F&1)<<7)",
                        "F=(F&196)|(A&40)|c_"], 4
            if y == 4:
                return ["a=A; nf=F&2; cf=F&1; hf=F&16", "co=0",
                        "if hf or (a&15)>9: co=6",
                        "nc=0",
                        "if cf or a>153: co|=96; nc=1",
                        "if nf: A=(a-co)&255; h=16 if (hf and (a&15)<6) else 0",
                        "else: A=(a+co)&255; h=16 if (a&15)>9 else 0",
                        "F=SZP[A]|nf|nc|h"], 4
            if y == 5:
                return ["A=A^255", "F=(F&197)|(A&40)|18"], 4
            if y == 6:
                return ["F=(F&196)|(A&40)|1"], 4
            return ["c_=F&1", "F=(F&196)|(c_<<4)|(A&40)|(c_^1)"], 4

        if x == 1:
            if op == 0x76:
                return ["HALTED=True", "EV=0"], 4
            if y == 6 or z == 6:
                L = self.prelude()
                if y == 6:
                    L += W("ad", self.rd(z, plain=True))
                else:
                    L += self.wr(y, "mem[ad]", plain=True)
                return L, (7 if not ix else 19)
            return self.wr(y, self.rd(z)), 4 + pre

        if x == 2:
            if z == 6:
                return self.prelude() + ["v=mem[ad]"] + alu(y, "v"), (
                    7 if not ix else 19)
            return alu(y, self.rd(z)), 4 + pre

        # x == 3
        if z == 0:
            return ([f"if {CC[y]}:", "    lo=mem[SP]; hi=mem[(SP+1)&65535]",
                     "    SP=(SP+2)&65535", "    PC=(hi<<8)|lo",
                     "    return 11"], 5)
        if z == 1:
            if q == 0:
                return (["lo=mem[SP]; hi=mem[(SP+1)&65535]",
                         "SP=(SP+2)&65535", "w=(hi<<8)|lo"]
                        + self.set_rp2(p, "w"), 10 + pre)
            if p == 0:
                return ["lo=mem[SP]; hi=mem[(SP+1)&65535]", "SP=(SP+2)&65535",
                        "PC=(hi<<8)|lo"], 10
            if p == 1:
                return ["B,B_=B_,B", "C,C_=C_,C", "D,D_=D_,D", "E,E_=E_,E",
                        "H,H_=H_,H", "L,L_=L_,L"], 4
            if p == 2:
                return [f"PC={self.hl()}"], 4 + pre
            return [f"SP={self.hl()}"], 6 + pre
        if z == 2:
            return FETCH_NN + [f"if {CC[y]}: PC=nn"], 10
        if z == 3:
            if y == 0:
                return FETCH_NN + ["PC=nn"], 10
            if y == 1:
                return None   # CB prefix, built apart
            if y == 2:
                return (FETCH_N + ["OUTH((A<<8)|n,A,T+7)", "EV=0"], 11)
            if y == 3:
                return FETCH_N + ["A=INH((A<<8)|n,T+7)"], 11
            if y == 4:
                hl = self.hl()
                return ([f"hl={hl}", "lo=mem[SP]; hi=mem[(SP+1)&65535]",
                         "vl=hl&255; vh=hl>>8"] + W("SP", "vl")
                        + W("(SP+1)&65535", "vh")
                        + ["w=(hi<<8)|lo"] + self.set_hl("w"), 19 + pre)
            if y == 5:
                return ["H,D=D,H", "L,E=E,L"], 4
            if y == 6:
                return ["IFF1=0; IFF2=0"], 4
            return ["IFF1=1; IFF2=1", "EIDELAY=True", "EV=0"], 4
        if z == 4:
            return (FETCH_NN + [f"if {CC[y]}:", "    pc_=PC",
                    "    ph=pc_>>8; pl=pc_&255"]
                    + ["    "+s for s in push16("ph", "pl")]
                    + ["    PC=nn", "    return 17"], 10)
        if z == 5:
            if q == 0:
                return ([f"w={self.rp2(p)}", "wh=w>>8; wl=w&255"]
                        + push16("wh", "wl"), 11 + pre)
            if p == 0:
                return (FETCH_NN + ["pc_=PC", "ph=pc_>>8; pl=pc_&255"]
                        + push16("ph", "pl") + ["PC=nn"], 17)
            return None   # DD, ED, FD prefixes, built apart
        if z == 6:
            return FETCH_N + alu(y, "n"), 7
        return (["pc_=PC", "ph=pc_>>8; pl=pc_&255"] + push16("ph", "pl")
                + [f"PC={y*8}"], 11)

    @staticmethod
    def uses_hl(op: int) -> bool:
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        if x == 0:
            if z == 1:
                return p == 2 or q == 1
            if z in (2, 3):
                return p == 2 if z == 2 else p == 2
            if z in (4, 5, 6):
                return y in (4, 5, 6)
            return False
        if x == 1:
            return op != 0x76 and (y in (4, 5, 6) or z in (4, 5, 6))
        if x == 2:
            return z in (4, 5, 6)
        if z == 1:
            return (q == 0 and p == 2) or (q == 1 and p in (2, 3))
        if z == 3:
            return y == 4
        if z == 5:
            return q == 0 and p == 2
        return False


def _emit(name: str, body: list[str], out: list[str]) -> None:
    out.append(f"    def {name}():")
    out.append("        nonlocal " + ", ".join(
        n for n in ALL_CELLS if n not in ("SZ", "SZP")))
    for line in body:
        out.append("        " + line)
    out.append("")


def _op_function(name, lines, cycles, out):
    """One handler.  A line may return early; the last line returns the
    T-states of the common path."""
    _emit(name, list(lines) + [f"return {cycles}"], out)


def _gen_cb(out: list[str]) -> None:
    for op in range(256):
        z = op & 7
        x = op >> 6
        L: list[str] = []
        if z == 6:
            L = ["ad=(H<<8)|L", "v=mem[ad]"] + cb_core(op, "(ad>>8)")
            if x != 1:
                L += W("ad", "r")
            cyc = 12 if x == 1 else 15
        else:
            L = [f"v={R8[z]}"] + cb_core(op, "v")
            if x != 1:
                L += [f"{R8[z]}=r"]
            cyc = 8
        L.append(f"return {cyc}")
        _emit(f"cb_{op:02X}", L, out)


def _gen_ddcb(ix: str, out: list[str]) -> None:
    """DD CB d op: one function per opcode, d already fetched into `ad`."""
    for op in range(256):
        z = op & 7
        x = op >> 6
        L = ["ad=AD", "v=mem[ad]"] + cb_core(op, "(ad>>8)")
        if x != 1:
            L += W("ad", "r")
            if z != 6:
                L += [f"{R8[z]}=r"]
        L.append(f"return {20 if x == 1 else 23}")
        _emit(f"{ix.lower()}cb_{op:02X}", L, out)


def _gen_ed(out: list[str]) -> None:
    g = Gen(None)
    for op in range(0x40, 0x80):
        y, z = (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        L: list[str] = []
        cyc = 8
        if z == 0:
            L = ["port=(B<<8)|C", "v=INH(port,T+8)", "F=(F&1)|SZP[v]"]
            if y != 6:
                L += [f"{R8[y]}=v"]
            cyc = 12
        elif z == 1:
            val = "0" if y == 6 else R8[y]
            L = [f"OUTH((B<<8)|C,{val},T+8)", "EV=0"]
            cyc = 12
        elif z == 2:
            rr = g.rp(p)
            L = [f"hl=((H<<8)|L)", f"v={rr}", "c_=F&1"]
            if q == 0:
                L += ["r=hl-v-c_",
                      "F=((r>>8)&128)|(0 if r&65535 else 64)|((r>>8)&40)"
                      "|(((hl^v^r)>>8)&16)|((((hl^v)&(hl^r))>>13)&4)|2"
                      "|((r>>16)&1)"]
            else:
                L += ["r=hl+v+c_",
                      "F=((r>>8)&128)|(0 if r&65535 else 64)|((r>>8)&40)"
                      "|(((hl^v^r)>>8)&16)|((((hl^v^32768)&(hl^r))>>13)&4)"
                      "|(r>>16)"]
            L += ["r=r&65535", "H=r>>8", "L=r&255"]
            cyc = 15
        elif z == 3:
            if q == 0:
                L = (FETCH_NN + [f"v={g.rp(p)}", "lo=v&255", "hi=v>>8"]
                     + W("nn", "lo") + W("(nn+1)&65535", "hi"))
            else:
                L = (FETCH_NN + ["lo=mem[nn]", "hi=mem[(nn+1)&65535]",
                     "w=(hi<<8)|lo"] + g.set_rp(p, "w"))
            cyc = 20
        elif z == 4:
            L = ["v=A", "r=-v", "F=SZ[r&255]|((r>>8)&1)|2|((v^r)&16)"
                 "|(4 if v==128 else 0)", "A=r&255"]
        elif z == 5:
            L = ["lo=mem[SP]; hi=mem[(SP+1)&65535]", "SP=(SP+2)&65535",
                 "PC=(hi<<8)|lo", "IFF1=IFF2", "EV=0"]
            cyc = 14
        elif z == 6:
            mode = [0, 0, 1, 2, 0, 0, 1, 2][y]
            L = [f"IM={mode}"]
        else:
            L = {
                0: ["I=A"], 1: ["R=A&127; R7=A&128"],
                2: ["A=I", "F=(F&1)|SZ[A]|(4 if IFF2 else 0)"],
                3: ["A=(R&127)|R7", "F=(F&1)|SZ[A]|(4 if IFF2 else 0)"],
                4: ["v=mem[(H<<8)|L]", "a=A", "A=(a&240)|(v&15)",
                    "nv=((a&15)<<4)|(v>>4)", "hl_=(H<<8)|L"]
                   + W("hl_", "nv") + ["F=(F&1)|SZP[A]"],
                5: ["v=mem[(H<<8)|L]", "a=A", "A=(a&240)|(v>>4)",
                    "nv=((v<<4)|(a&15))&255", "hl_=(H<<8)|L"]
                   + W("hl_", "nv") + ["F=(F&1)|SZP[A]"],
                6: [], 7: []}[y]
            cyc = 9 if y < 4 else (18 if y < 6 else 8)
        if z == 4 or (z == 5) or z == 6:
            cyc = {4: 8, 5: 14, 6: 8}[z]
        L.append(f"return {cyc}")
        _emit(f"ed_{op:02X}", L, out)
    # block instructions
    for op in (0xA0, 0xA1, 0xA2, 0xA3, 0xA8, 0xA9, 0xAA, 0xAB,
               0xB0, 0xB1, 0xB2, 0xB3, 0xB8, 0xB9, 0xBA, 0xBB):
        kind = op & 3
        dec = bool(op & 8)
        rep = bool(op & 0x10)
        step = "-" if dec else "+"
        L = ["hl=(H<<8)|L"]
        cond = None
        if kind == 0:   # LDI / LDD
            L += ["de=(D<<8)|E", "bc=(((B<<8)|C)-1)&65535", "n=mem[hl]"]
            L += W("de", "n")
            L += [f"hl=(hl{step}1)&65535", f"de=(de{step}1)&65535",
                  "t_=(A+n)&255",
                  "F=(F&193)|(4 if bc else 0)|(t_&8)|((t_&2)<<4)",
                  "H=hl>>8; L=hl&255; D=de>>8; E=de&255; B=bc>>8; C=bc&255"]
            cond = "bc"
        elif kind == 1:  # CPI / CPD
            L += ["bc=(((B<<8)|C)-1)&65535", "n=mem[hl]", "r=A-n",
                  "res=r&255", "h=(A^n^r)&16", f"hl=(hl{step}1)&65535",
                  "t_=(res-(1 if h else 0))&255",
                  "F=(F&1)|2|(res&128)|(64 if res==0 else 0)|h"
                  "|(4 if bc else 0)|(t_&8)|((t_&2)<<4)",
                  "H=hl>>8; L=hl&255; B=bc>>8; C=bc&255"]
            cond = "(bc and res)"
        elif kind == 2:  # INI / IND
            L += ["v=INH((B<<8)|C,T+9)"]
            L += W("hl", "v")
            L += [f"hl=(hl{step}1)&65535", "B=(B-1)&255",
                  f"k=v+((C{'-' if dec else '+'}1)&255)",
                  "F=SZ[B]|((v>>6)&2)|(17 if k>255 else 0)"
                  "|SZP[(k&7)^B]&4",
                  "H=hl>>8; L=hl&255", "EV=0"]
            cond = "B"
        else:            # OUTI / OUTD
            L += ["v=mem[hl]", "B=(B-1)&255", "OUTH((B<<8)|C,v,T+9)",
                  f"hl=(hl{step}1)&65535", "k=v+(hl&255)",
                  "F=SZ[B]|((v>>6)&2)|(17 if k>255 else 0)"
                  "|SZP[(k&7)^B]&4",
                  "H=hl>>8; L=hl&255", "EV=0"]
            cond = "B"
        if rep:
            L += [f"if {cond}:", "    PC=(PC-2)&65535", "    return 21"]
        L.append("return 16")
        _emit(f"ed_{op:02X}", L, out)


def _build_source() -> str:
    out: list[str] = []
    out.append("def _factory(mem, wok, bp, SZ, SZP):")
    out.append("    " + ", ".join(ALL_CELLS) + " = " +
               ", ".join(["0"] * len(ALL_CELLS)))
    out.append("    pass")
    # base opcodes
    g = Gen(None)
    for op in range(256):
        r = g.gen(op)
        if r is None:
            continue
        lines, cyc = r
        _op_function(f"op_{op:02X}", lines, cyc, out)
    # DD and FD tables
    for ixn in ("IX", "IY"):
        gi = Gen(ixn)
        pf = ixn.lower()
        for op in range(256):
            r = gi.gen(op)
            if r is None:
                continue
            lines, cyc = r
            _op_function(f"{pf}_{op:02X}", lines, cyc, out)
        _gen_ddcb(ixn, out)
    _gen_cb(out)
    _gen_ed(out)
    _tail(out)
    return "\n".join(out)


def _tail(out: list[str]) -> None:
    """Tables, prefix handlers, the event service, run(), state access."""
    nl = ", ".join(ALL_CELLS)
    out.append(f"""
    OPS = [None] * 256
    CBOPS = [None] * 256
    EDOPS = [None] * 256
    DDOPS = [None] * 256
    FDOPS = [None] * 256
    DDCB = [None] * 256
    FDCB = [None] * 256
    g = locals()
    for i in range(256):
        OPS[i] = g.get('op_%02X' % i)
        CBOPS[i] = g['cb_%02X' % i]
        EDOPS[i] = g.get('ed_%02X' % i)
        DDOPS[i] = g.get('ix_%02X' % i)
        FDOPS[i] = g.get('iy_%02X' % i)
        DDCB[i] = g['ixcb_%02X' % i]
        FDCB[i] = g['iycb_%02X' % i]

    def p_cb():
        nonlocal R, PC
        R += 1
        op = mem[PC]
        PC = (PC + 1) & 65535
        return CBOPS[op]()

    def p_ed():
        nonlocal R, PC
        R += 1
        op = mem[PC]
        PC = (PC + 1) & 65535
        h = EDOPS[op]
        if h is None:
            return 8
        return h()

    def _pdx(tab, cbtab, ixn):
        def pfx():
            nonlocal R, PC, AD
            op = mem[PC]
            if op == 203:
                d = mem[PC]
                d = mem[(PC + 1) & 65535]
                o2 = mem[(PC + 2) & 65535]
                PC = (PC + 3) & 65535
                R += 2
                base = IX if ixn == 0 else IY
                AD = (base + (d ^ 128) - 128) & 65535
                return cbtab[o2]()
            h = tab[op]
            if h is None:
                return 4
            PC = (PC + 1) & 65535
            R += 1
            return h()
        return pfx

    OPS[0xCB] = p_cb
    OPS[0xED] = p_ed
    OPS[0xDD] = _pdx(DDOPS, DDCB, 0)
    OPS[0xFD] = _pdx(FDOPS, FDCB, 1)
    BIG = 1 << 62
    EV = 0
    BUDGET = BIG
    STOP = None
    IM = 0

    def service():
        nonlocal T, EV, PC, SP, HALTED, IFF1, IFF2, EIDELAY, NMI_P, IRQ_P
        nonlocal STOP, R
        while True:
            nxt = FIRE(T)
            if STOP is not None:
                s = STOP
                STOP = None
                return s
            if T >= BUDGET:
                return 'budget'
            if NMI_P:
                NMI_P = False
                HALTED = False
                IFF2 = IFF1
                IFF1 = 0
                pc_ = PC
                SP = (SP - 1) & 65535
                w_ = SP
                if wok[w_]: mem[w_] = pc_ >> 8
                else: WBAD(w_, pc_ >> 8)
                SP = (SP - 1) & 65535
                w_ = SP
                if wok[w_]: mem[w_] = pc_ & 255
                else: WBAD(w_, pc_ & 255)
                PC = 102
                ONACC('nmi', T)
                T += 11
                R += 1
                continue
            if (IRQ_P or IRQ_L) and IFF1 and not EIDELAY:
                IRQ_P = False
                HALTED = False
                IFF1 = 0
                IFF2 = 0
                pc_ = PC
                SP = (SP - 1) & 65535
                w_ = SP
                if wok[w_]: mem[w_] = pc_ >> 8
                else: WBAD(w_, pc_ >> 8)
                SP = (SP - 1) & 65535
                w_ = SP
                if wok[w_]: mem[w_] = pc_ & 255
                else: WBAD(w_, pc_ & 255)
                ONACC('irq', T)
                if IM == 2:
                    vec = (I << 8) | (IVEC & 254)
                    PC = mem[vec] | (mem[(vec + 1) & 65535] << 8)
                    T += 19
                elif IM == 1:
                    PC = 56
                    T += 13
                else:
                    PC = IVEC & 56
                    T += 13
                R += 1
                continue
            if EIDELAY:
                EIDELAY = False
                EV = 0
                return None
            if HALTED:
                if not WAKE(IFF1):
                    return 'halt' if not IFF1 else 'idle'
                tgt = BUDGET if nxt is None or BUDGET < nxt else nxt
                k = (tgt - T + 3) // 4
                T += 4 * (k if k > 0 else 1)
                continue
            EV = BUDGET if nxt is None or BUDGET < nxt else nxt
            return None

    def run(skip_bp):
        nonlocal T, PC, R, IPC
        dpc = PC if skip_bp else -1     # a breakpoint handled at (dpc, dt)
        dt = T
        while True:
            pc = PC
            if bp[pc] and (pc != dpc or T != dt):
                dpc = pc
                dt = T
                s = BPHIT(pc)
                if s is not None:
                    return s
                if PC != pc or T != dt:     # the hook changed the state
                    continue
            if T >= EV:
                s = service()
                if s is not None:
                    return s
                if PC != pc:                # an interrupt moved PC
                    continue
            PC = (pc + 1) & 65535
            IPC = pc
            R += 1
            T += OPS[mem[pc]]()

    def getstate():
        return dict(""")
    for n in STATE_REGS + ["EIDELAY", "NMI_P", "IRQ_P", "IRQ_L", "IVEC",
                           "BUDGET", "EV", "IPC"]:
        out.append(f'            {n}={n},')
    out.append("        )")
    out.append("")
    out.append("    def setstate(d):")
    out.append("        nonlocal " + ", ".join(
        STATE_REGS + ["EIDELAY", "NMI_P", "IRQ_P", "IRQ_L", "IVEC", "BUDGET",
                      "EV"]))
    for n in STATE_REGS + ["EIDELAY", "NMI_P", "IRQ_P", "IRQ_L", "IVEC",
                           "BUDGET", "EV"]:
        out.append(f"        if '{n}' in d: {n} = d['{n}']")
    out.append("")
    out.append("    def sethooks(inh, outh, wbad, fire, wake, bphit, onacc):")
    out.append("        nonlocal INH, OUTH, WBAD, FIRE, WAKE, BPHIT, ONACC")
    out.append("        INH = inh; OUTH = outh; WBAD = wbad; FIRE = fire")
    out.append("        WAKE = wake; BPHIT = bphit; ONACC = onacc")
    out.append("")
    out.append("    def poke(**kw):")
    out.append("        nonlocal EV, STOP, NMI_P, IRQ_P, IRQ_L, BUDGET")
    out.append("        if 'stop' in kw: STOP = kw['stop']")
    out.append("        if 'nmi' in kw: NMI_P = kw['nmi']")
    out.append("        if 'irq' in kw: IRQ_P = kw['irq']")
    out.append("        if 'irq_level' in kw: IRQ_L = kw['irq_level']")
    out.append("        if 'budget' in kw: BUDGET = kw['budget']")
    out.append("        EV = 0")
    out.append("")
    out.append("    return run, getstate, setstate, sethooks, poke")


_CODE = None


def _code():
    global _CODE
    if _CODE is None:
        _CODE = compile(_build_source(), "<z80core>", "exec")
    return _CODE


class Z80:
    """One Z80 CPU with its own memory.  See the module docstring."""

    def __init__(self):
        self.mem = bytearray(65536)
        self.wok = bytearray(b"\x01") * 65536
        self.bp = bytearray(65536)
        ns: dict = {}
        exec(_code(), ns)
        (self._run, self._get, self._set, self._sethooks,
         self._poke) = ns["_factory"](self.mem, self.wok, self.bp, SZ, SZP)
        self._set(dict(A=255, F=255, SP=65535, IVEC=255,
                       BUDGET=1 << 62, EV=0))
        self._skip = False
        self.set_hooks()

    def set_hooks(self, inh=None, outh=None, wbad=None, fire=None, wake=None,
                  bphit=None, onacc=None):
        """Install the callbacks.  Defaults do nothing.

        inh(port16, t) -> byte      a port read; t is the T-state stamp
        outh(port16, value, t)      a port write
        wbad(addr, value)           a write to a non-writable address
        fire(t) -> next event or None   run due events; returns next time
        wake(iff1) -> bool          can a future event end a HALT
        bphit(pc) -> reason|None    a breakpoint flag was set at pc
        onacc(kind, t)              the CPU took an 'irq' or 'nmi' at T-state t
        """
        self._sethooks(inh or (lambda p, t: 255),
                       outh or (lambda p, v, t: None),
                       wbad or (lambda a, v: None),
                       fire or (lambda t: None),
                       wake or (lambda iff1: False),
                       bphit or (lambda pc: None),
                       onacc or (lambda kind, t: None))

    def get(self) -> dict:
        return self._get()

    def set(self, **kw) -> None:
        self._set(kw)

    def poke(self, **kw) -> None:
        """Set a stop reason, an interrupt request or the budget, and make
        the run loop look at the events again at the next instruction."""
        self._poke(**kw)

    def run(self, skip_bp: bool = False):
        return self._run(skip_bp)

    @property
    def t(self) -> int:
        return self._get()["T"]
