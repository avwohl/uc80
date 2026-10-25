"""The MACRO-80 to sdas translator (src/uc80/sdas.py, src/uc80/asmexpr.py).

The unit tests need no assembler.  The round-trip tests need sdasz80 and
sdar (SDCC) and skip without them: every library module, and the .mac
output of every program of the benchmark set (bench/programs.toml), must
translate and assemble with no error.  Two more tests link and run: a
CP/M program linked by sdldz80 must print what the um80 and ul80 build
prints (cpmemu).  See docs/rom_interop.md.
"""

import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import uc80
from uc80 import asmexpr as X
from uc80 import sdas

from .conftest import SharedLock, shared_dir

REPO = Path(__file__).resolve().parent.parent
HAVE_SDAS = all(shutil.which(t) for t in ("sdasz80", "sdldz80", "sdar",
                                          "sdobjcopy"))
needs_sdas = pytest.mark.skipif(not HAVE_SDAS, reason="needs SDCC sdas tools")


def tr(text, **kw):
    return sdas.translate(text, filename="t.mac", **kw)


def body(text, **kw):
    """The translation without the header (the first area lines)."""
    out = tr(text, **kw).splitlines()
    return out[7:]      # the comment, .module, four .area, the first .area


# ---- expressions ----------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("0FFH", 255), ("100", 100), ("1010B", 10), ("17Q", 15),
    ("1+2*3", 7), ("(1+2)*3", 9), ("10-2-3", 5), ("-5+2", -3),
    ("HIGH 1234H", 0x12), ("LOW 1234H", 0x34), ("1 SHL 4", 16),
    ("0FFH AND 0FH", 15), ("7 MOD 3", 1), ("'A'+1", 66),
    ("NOT 0", 0xFFFF), ("2 EQ 2", 0xFFFF), ("3 LT 2", 0),
])
def test_m80_expressions_fold(text, value):
    assert X.fold(X.parse(text, "m80")) == value


def test_symbol_expression_has_no_value():
    assert X.fold(X.parse("_x+1", "m80")) is None
    assert X.fold(X.parse("_x+1", "m80"), {"_x": 4}) == 5


@pytest.mark.parametrize("m80,sdas_text", [
    ("0FFH", "0xFF"), ("0100H", "0x100"), ("12", "12"), ("1010B", "10"),
    ("_x+1", "_x+1"), ("_x+1+2", "_x+1+2"), ("(_x+1)*2", "(_x+1)*2"),
    ("$+5", ".+5"), ("HIGH _x", ">_x"), ("LOW _x", "<_x"),
    ("_a SHL 2", "_a<<2"), ("_a AND 0FH", "_a&0xF"), ("_a MOD 3", "_a%3"),
    ("'A'", "'A'"), ("-_x", "-_x"),
])
def test_m80_to_sdas_text(m80, sdas_text):
    assert X.to_sdas(X.parse(m80, "m80")) == sdas_text


@pytest.mark.parametrize("sdas_text,m80", [
    ("0xFF", "0FFH"), ("0x1F", "1FH"), ("0b101", "5"), ("_x+2", "_x+2"),
    ("<_x", "LOW _x"), (">_x", "HIGH _x"), ("_a&0x0F", "_a AND 0FH"),
    ("_a<<2", "_a SHL 2"), (".+4", "$+4"), ("(_x+1)*2", "(_x+1)*2"),
])
def test_sdas_to_m80_text(sdas_text, m80):
    assert X.to_m80(X.parse(sdas_text, "sdas")) == m80


def test_expression_errors_are_loud():
    with pytest.raises(X.AsmSyntaxError):
        X.parse("1 +", "m80")
    with pytest.raises(X.AsmSyntaxError):
        X.parse("12QZ", "m80")
    with pytest.raises(X.AsmSyntaxError):
        X.to_sdas(X.parse("_x EQ _y", "m80"))     # a relation of symbols


# ---- statements -----------------------------------------------------------

def test_header_declares_the_areas_in_link_order():
    out = tr("\t.Z80\n\tCSEG\n\tEND\n").splitlines()
    areas = [ln.split()[1] for ln in out if ln.startswith("\t.area")]
    assert areas[:4] == ["_CODE", "_INITIALIZER", "_INITIALIZED", "_DATA"]
    assert "\t.module\tt" not in out        # the module is named "uc80"
    assert any(ln.startswith("\t.module\t") for ln in out)


def test_module_name_is_made_safe():
    out = tr("\tCSEG\n", module="snd-cmdq 1")
    assert "\t.module\tsnd_cmdq_1" in out


def test_ngdevkit_style_names():
    out = tr("\tCSEG\n_a:\tret\n\tCOMMON //\n_b:\tds 2\n", style="ngdevkit")
    assert "\t.area\tCODE" in out and "\t.area\tDATA" in out
    assert "_CODE" not in out


def test_area_override():
    a = sdas.area_names("sdcc", "bss=BSS,code=HOME")
    assert (a.bss, a.code, a.rom_init) == ("BSS", "HOME", "_INITIALIZER")
    with pytest.raises(ValueError):
        sdas.area_names("sdcc", "nosuch=X")
    with pytest.raises(ValueError):
        sdas.area_names("nosuch")


def test_instruction_operands():
    b = body("""\tCSEG
\tld a,5
\tld hl,_x
\tld hl,(_x)
\tld (_x),a
\tld a,(ix+4)
\tld (iy-2),7
\tld a,(hl)
\tadd a,48
\tcp 37
\tsub 1
\tand 0FH
\tout (0FEH),a
\tin a,(c)
\tjp (hl)
\tjp _f
\tjr nz,_f
\tcall _f
\tbit 7,a
\tim 1
\trst 38H
\tex af,af'
\tpush ix
\tret z
""")
    want = ["\tld\ta,#5", "\tld\thl,#_x", "\tld\thl,(_x)", "\tld\t(_x),a",
            "\tld\ta,(ix+4)", "\tld\t(iy-2),#7", "\tld\ta,(hl)",
            "\tadd\ta,#48", "\tcp\t#37", "\tsub\t#1", "\tand\t#0xF",
            "\tout\t(0xFE),a", "\tin\ta,(c)", "\tjp\t(hl)", "\tjp\t_f",
            "\tjr\tnz,_f", "\tcall\t_f", "\tbit\t7,a", "\tim\t1",
            "\trst\t0x38", "\tex\taf,af'", "\tpush\tix", "\tret\tz"]
    assert b == want


def test_set_is_an_instruction_or_a_directive():
    b = body("\tCSEG\n\tset 7,a\nfoo\tset 5\n\tld a,foo\n")
    assert "\tset\t7,a" in b
    assert "\t.equ\tfoo, 5" in b


def test_labels_and_local_names():
    b = body("\tCSEG\n@DO1:\n\tjr @DO1\n_f::\n\tld hl,??AUTO+2\n"
             "_g:\tret\n")
    assert b[0] == "L$DO1:" and "\tjr\tL$DO1" in b
    assert "_f::" in b and "_g:" in b
    assert "\tld\thl,#Q$Q$AUTO+2" in b


def test_symbol_case_follows_the_first_spelling():
    b = body("\tCSEG\n_Foo:\tret\n\tcall _FOO\n")
    assert "\tcall\t_Foo" in b


def test_label_without_colon_before_a_directive():
    b = body("\tCSEG\ntab\tdw 1,2\nmsg\tdb 'hi',0\n")
    assert "tab:" in b and "msg:" in b
    assert "\t.dw\t1, 2" in b
    assert '\t.ascii\t"hi"' in b


def test_strings_and_bytes():
    b = body("\tCSEG\n\tdb 'a',0AH,'it''s',0\n\tdb 'say \"x\"'\n\tdb 7FH,'A'+1\n")
    joined = "\n".join(b)
    assert '.ascii\t"it\'s"' in joined
    assert "0x61, 0xA" in joined
    # a double quote and a backslash cannot be in an .ascii string
    assert ".ascii\t\"say" not in joined


def test_equ_and_ds_use_constants():
    b = body("N\tEQU 4\n\tCSEG\n\tds N*2\n")
    assert "\t.equ\tN, 4" in b and "\t.ds\t8" in b


def test_ds_needs_a_constant():
    with pytest.raises(sdas.SdasError):
        tr("\tCSEG\n\tds _x\n")


def test_public_and_extrn_become_globl():
    b = body("\tPUBLIC _a,_b\n\tEXTRN _c\n\tCSEG\n")
    assert b[:3] == ["\t.globl\t_a", "\t.globl\t_b", "\t.globl\t_c"]


def test_dseg_splits_image_and_space():
    out = tr("\tCSEG\n\tDSEG\n_v:\tdw 5\n_w:\tdb 1,2,3\n\tCSEG\n_c:\tret\n")
    lines = out.splitlines()
    i = lines.index("\t.area\t_INITIALIZER", 6)
    j = lines.index("\t.area\t_INITIALIZED", i)
    assert lines[i + 1:j] == ["\t.dw\t5", "\t.db\t1, 2, 3"]
    assert lines[j + 1:j + 5] == ["_v:", "\t.ds\t2", "_w:", "\t.ds\t3"]


def test_dseg_is_flat_for_cpm():
    lines = tr("\tDSEG\n_v:\tdw 5\n", flat_data=True).splitlines()
    i = max(k for k, ln in enumerate(lines) if ln == "\t.area\t_INITIALIZED")
    assert lines[i + 1:i + 3] == ["_v:", "\t.dw\t5"]


def test_common_is_bss_and_refuses_data():
    b = body("\tcommon //\n_b:\tds 4\n")
    assert b[0] == "\t.area\t_DATA" and "\t.ds\t4" in b
    with pytest.raises(sdas.SdasError) as e:
        tr("\tcommon //\n_b:\tdb 4\n")
    assert "COMMON" in str(e.value) and "line 2" in str(e.value)


def test_org_makes_an_absolute_area_until_reloc():
    b = body("\tCSEG\n\tORG 0\n\tjp _s\n\tORG 66H\n\tretn\n;@RELOC 68H\n_s:\tret\n")
    assert b[0] == "\t.area\t_ABS (ABS)"
    assert "\t.org\t0x66" in b
    k = b.index("\t.area\t_CODE", 3)
    assert "_s:" in b[k:]
    assert tr("\tCSEG\n\tORG 0\n;@RELOC 69H\n").startswith(
        "; uc80-sdas: code-origin 0x69")
    assert sdas.code_origin_of(tr("\tCSEG\n\tORG 0\n;@RELOC 69H\n")) == 0x69


def test_unsupported_statements_are_loud_with_the_line():
    for text, word in (("\tCSEG\n\tIF 1\n\tENDIF\n", "IF"),
                       ("\tCSEG\n\tfrob a\n", "frob"),
                       ("\tCSEG\n\tdb 'a\n", "quote"),
                       ("\tCSEG\n\tld a,\n", "")):
        with pytest.raises(sdas.SdasError) as e:
            tr(text)
        assert word in str(e.value) and "line 2" in str(e.value)


# ---- instruction sizes and the JR range check -----------------------------

def test_jr_out_of_range_becomes_jp_like_um80():
    pad = "\tnop\n" * 200
    out = tr(f"\tCSEG\n\tjr nz,_far\n\tdjnz _far\n\tjr _far\n{pad}_far:\tret\n")
    assert "\tjp\tnz,_far" in out
    assert "\tdec\tb\n\tjp\tnz,_far" in out
    assert "\tjp\t_far" in out
    assert "\tjr\t" not in out


def test_jr_in_range_stays():
    out = tr("\tCSEG\n_a:\tjr nz,_a\n\tdjnz _a\n")
    assert "\tjr\tnz,_a" in out and "\tdjnz\t_a" in out


def test_instruction_size_table():
    c = {"reg": lambda r: ("reg", r), "ind": lambda r: ("ind", r),
         "idx": lambda r: ("idx", r, None), "mem": ("mem", None),
         "imm": ("imm", None)}
    cases = [("nop", [], 1), ("ldir", [], 2), ("ld", [("reg", "a"),
             ("imm", 0)], 2), ("ld", [("reg", "hl"), ("mem", 0)], 3),
             ("ld", [("reg", "bc"), ("mem", 0)], 4),
             ("ld", [("reg", "ix"), ("imm", 0)], 4),
             ("ld", [("idx", "ix", 1), ("imm", 0)], 4),
             ("ld", [("mem", 0), ("reg", "sp")], 4),
             ("bit", [("imm", 0), ("idx", "iy", 1)], 4),
             ("add", [("reg", "ix"), ("reg", "bc")], 2),
             ("sbc", [("reg", "hl"), ("reg", "de")], 2),
             ("jp", [("ind", "hl")], 1), ("call", [("imm", 0)], 3),
             ("push", [("reg", "iy")], 2), ("inc", [("idx", "ix", 1)], 3)]
    for op, ops, size in cases:
        assert sdas.insn_size(op, ops) == size, (op, ops)


# ---- round trip with the assembler ----------------------------------------

def _rel_area_sizes(rel: Path):
    return {m.group(1): int(m.group(2), 16) for m in re.finditer(
        r"^A (\S+) size ([0-9A-F]+)", rel.read_text(), re.M)}


@needs_sdas
def test_every_library_module_translates_and_assembles(rom_libs):
    n = sum(1 for _ in (rom_libs / "rt").glob("*.rel")) + sum(
        1 for _ in (rom_libs / "lc").glob("*.rel"))
    srcs = sum(1 for _ in (uc80.lib_dir() / "rt").glob("*.mac")) + sum(
        1 for _ in (uc80.lib_dir() / "lc").glob("*.mac"))
    assert n == srcs and n > 300
    assert (rom_libs / "uc80_rt.lib").exists()
    assert (rom_libs / "uc80_lc.lib").exists()


@needs_sdas
def test_instruction_sizes_agree_with_sdasz80(rom_libs):
    """The area sizes that the translator computes, including the JR to JP
    changes, equal the sizes in the .rel files of sdasz80."""
    bad = []
    for kind in ("rt", "lc"):
        for mac in sorted((uc80.lib_dir() / kind).glob("*.mac")):
            rel = rom_libs / kind / (mac.stem + ".rel")
            mine = sdas.area_sizes(mac.read_text(errors="replace"))
            theirs = _rel_area_sizes(rel)
            for area, size in theirs.items():
                if not area.startswith("_ABS") and mine.get(area, 0) != size:
                    bad.append((mac.name, area, size, mine.get(area)))
    assert bad == []


def _bench_modules():
    sys.path.insert(0, str(REPO / "bench"))
    import run as R
    man = R.load_manifest()
    repos = man.get("repo", {})
    progs = []
    for p in man["program"]:
        if p.get("kind") == "size-only":
            continue
        ok = all((R.CACHE / f["repo"] / repos[f["repo"]]["commit"]
                  / f["path"]).exists()
                 for f in p["files"] if "repo" in f)
        if ok:
            progs.append(p)
    return R, repos, progs


@pytest.fixture(scope="session")
def bench_macs(tmp_path_factory):
    """The .mac output of uc80 for each program of the benchmark set whose
    sources are in bench/_cache (the CP/M target, as bench/run.py builds
    the program).  The compile runs in several threads."""
    if not shutil.which("uc80"):
        pytest.skip("needs uc80 on PATH")
    R, repos, progs = _bench_modules()
    root = shared_dir(tmp_path_factory, "benchmacs")
    env = dict(os.environ)

    def one(p):
        d = root / p["name"].replace("+", "_")
        if (d / "prog.mac").exists():
            return p["name"], d / "prog.mac"
        try:
            srcs = R.prepare_sources(p, repos, d)
        except SystemExit:
            return None
        defs = R.build_defines(p, "check")
        cmd = ["uc80", *map(str, srcs), f"-I{REPO / 'bench'}", f"-I{d}",
               *[f"-D{x}" for x in defs], "-o", str(d / "prog.mac")]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           timeout=300)
        return (p["name"], d / "prog.mac") if r.returncode == 0 else None
    with SharedLock(root), ThreadPoolExecutor(8) as pool:
        out = [x for x in pool.map(one, progs) if x]
    if len(out) < 10:
        pytest.skip("the benchmark sources are not in bench/_cache")
    return out


@needs_sdas
def test_every_benchmark_program_translates_and_assembles(bench_macs, tmp_path):
    def one(item):
        name, mac = item
        try:
            text = sdas.translate(mac.read_text(errors="replace"),
                                  module="prog", filename=name,
                                  flat_data=True)
        except sdas.SdasError as e:
            return name, f"translate: {e}"
        s = mac.with_suffix(".s")
        s.write_text(text)
        ok, msg = sdas.assemble(s, flags=())       # no -g: all .globl there
        return name, None if ok else msg[:300]
    with ThreadPoolExecutor(8) as pool:
        bad = [r for r in pool.map(one, bench_macs) if r[1]]
    assert bad == []


CPMEMU = Path.home() / "src" / "cpmemu" / "src" / "cpmemu"


@needs_sdas
@pytest.mark.skipif(not (CPMEMU.exists() and shutil.which("um80")
                         and shutil.which("ul80")),
                    reason="needs cpmemu, um80 and ul80")
@pytest.mark.parametrize("name", ["tak", "sieve", "fannkuch",
                                  "sort-ran-20", "dhrystone21"])
def test_cpm_program_linked_by_sdldz80_prints_the_same(
        name, bench_macs, cpm_libs, tmp_path):
    by_name = dict(bench_macs)
    if name not in by_name:
        pytest.skip("source not in the cache")
    mac = by_name[name]
    lib = uc80.lib_dir()
    ref = tmp_path / "ref"
    ref.mkdir()
    for cmd in (["um80", str(mac), "-o", "ref.rel"],
                ["ul80", "ref.rel", str(lib / "libc.lib"),
                 str(lib / "runtime.lib"), "-o", "ref.com"]):
        r = subprocess.run(cmd, cwd=ref, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    s = tmp_path / "t.s"
    s.write_text(sdas.translate(mac.read_text(errors="replace"),
                                module="t", flat_data=True))
    ok, msg = sdas.assemble(s)
    assert ok, msg
    flags = sdas.link_flags("sdcc", 0x100, target="cpm")
    r = subprocess.run(["sdldz80", *flags, "-i", "t.ihx", "t.rel",
                        "-l", str(cpm_libs / "uc80_lc.lib"),
                        "-l", str(cpm_libs / "uc80_rt.lib")],
                       cwd=tmp_path, capture_output=True, text=True)
    assert r.returncode == 0 and "Undefined" not in r.stdout + r.stderr, \
        r.stdout + r.stderr
    subprocess.run(["sdobjcopy", "-I", "ihex", "-O", "binary", "t.ihx",
                    "t.com"], cwd=tmp_path, check=True)
    want = subprocess.run([str(CPMEMU), "ref.com"], cwd=ref,
                          capture_output=True, timeout=120,
                          stdin=subprocess.DEVNULL).stdout
    got = subprocess.run([str(CPMEMU), "t.com"], cwd=tmp_path,
                         capture_output=True, timeout=120,
                         stdin=subprocess.DEVNULL).stdout
    assert want and got == want


def _all_forms_source():
    """A MACRO-80 module with every Z80 instruction form, operand shapes
    and expressions that uc80 and the hand-written modules use, and JR and
    DJNZ that um80 must change to JP."""
    R8="a b c d e h l".split(); R16="bc de hl sp".split(); CC="nz z nc c po pe p m".split()
    L=[]
    add=L.append
    for d in R8:
        for s in R8: add(f"ld {d},{s}")
        add(f"ld {d},12H"); add(f"ld {d},(hl)"); add(f"ld (hl),{d}")
        add(f"ld {d},(ix+5)"); add(f"ld {d},(iy-3)"); add(f"ld (ix+5),{d}"); add(f"ld (iy-3),{d}")
    add("ld (hl),34H"); add("ld (ix+7),99H"); add("ld (iy+127),0FFH"); add("ld (iy-128),1")
    add("ld a,(bc)"); add("ld a,(de)"); add("ld (bc),a"); add("ld (de),a")
    add("ld a,(1234H)"); add("ld (1234H),a"); add("ld a,i"); add("ld a,r"); add("ld i,a"); add("ld r,a")
    for r in R16+["ix","iy"]:
        add(f"ld {r},1234H"); add(f"ld {r},_sym+3")
    add("ld hl,(1234H)"); add("ld (1234H),hl")
    for r in ["bc","de","sp","ix","iy"]:
        add(f"ld {r},(1234H)"); add(f"ld (1234H),{r}")
    add("ld sp,hl"); add("ld sp,ix"); add("ld sp,iy")
    for r in ["af","bc","de","hl","ix","iy"]: add(f"push {r}"); add(f"pop {r}")
    for x in ["de,hl","af,af'","(sp),hl","(sp),ix","(sp),iy"]: add(f"ex {x}")
    for x in "exx ldi ldir ldd lddr cpi cpir cpd cpdr daa cpl neg ccf scf nop halt di ei rlca rla rrca rra rld rrd reti retn ini inir ind indr outi otir outd otdr ret".split(): add(x)
    for op in ["add","adc","sub","sbc","and","xor","or","cp"]:
        pre = "a," if op in("add","adc","sbc") else ""
        for s in R8: add(f"{op} {pre}{s}")
        add(f"{op} {pre}56H"); add(f"{op} {pre}(hl)"); add(f"{op} {pre}(ix+2)"); add(f"{op} {pre}(iy-2)")
        if pre==""   : add(f"{op} a,5") if op in ("sub","and","xor","or","cp") else None
    for r in R16: add(f"add hl,{r}"); add(f"adc hl,{r}"); add(f"sbc hl,{r}")
    for r in ["bc","de","ix","sp"]: add(f"add ix,{r}")
    for r in ["bc","de","iy","sp"]: add(f"add iy,{r}")
    for r in R8+R16+["ix","iy"]: add(f"inc {r}"); add(f"dec {r}")
    add("inc (hl)"); add("dec (hl)"); add("inc (ix+1)"); add("dec (iy-1)")
    for n in (0,1,2): add(f"im {n}")
    for op in "rlc rl rrc rr sla sra srl".split():
        for s in R8: add(f"{op} {s}")
        add(f"{op} (hl)"); add(f"{op} (ix+3)"); add(f"{op} (iy-3)")
    for op in ["bit","res","set"]:
        for b in range(8):
            add(f"{op} {b},a"); add(f"{op} {b},(hl)"); add(f"{op} {b},(ix+4)")
    add("jp 1234H"); add("jp _lab"); add("jp (hl)"); add("jp (ix)"); add("jp (iy)")
    for c in CC: add(f"jp {c},_lab"); add(f"call {c},_lab"); add(f"ret {c}")
    for c in ["nz","z","nc","c"]: add(f"jr {c},_lab")
    add("jr _lab"); add("djnz _lab"); add("call _lab"); add("call 4321H")
    for n in range(0,0x40,8): add(f"rst {n:02X}H")
    add("in a,(23H)"); add("out (23H),a")
    for r in R8: add(f"in {r},(c)"); add(f"out (c),{r}")
    # expressions
    add("ld a,1+2*3"); add("ld hl,(_sym+1)*2" ); add("ld a,LOW _sym"); add("ld a,HIGH _sym"); add("ld hl,_sym-1"); add("ld a,'A'"); add("ld a,'A'+1"); add("ld hl,_sym SHR 1") 
    add("ld a,0FFH AND 0FH"); add("ld hl,100H OR 5"); add("ld a,-1"); add("ld a,NOT 0 AND 0FFH")
    add("db 1,2,3"); add("db 'abc',0"); add("db 'it''s',0DH,0AH"); add("dw 1234H,_lab,_lab+2"); add("ds 3")
    add("dw $+4"); add("jr $"); add("jr $+4")
    far = ["\tjr nz,_far", "\tdjnz _far", "\tjr _far"] + ["\tnop"] * 200
    src = (["\t.z80", "\tcseg", "_sym\tequ 0123H", "_lab:"]
           + ["\t" + x for x in L] + far + ["_far:\tret", "\tend"])
    return "\n".join(src) + "\n"


@needs_sdas
@pytest.mark.skipif(not (shutil.which("um80") and shutil.which("ul80")),
                    reason="needs um80 and ul80")
def test_every_instruction_form_assembles_to_the_bytes_of_um80(tmp_path):
    """The oracle test: um80 and ul80 on the MACRO-80 text, and the
    translator, sdasz80 and sdldz80 on the same text, give the same
    bytes."""
    (tmp_path / "t.mac").write_text(_all_forms_source())
    for cmd in (["um80", "t.mac", "-o", "u.rel"],
                ["ul80", "--bin", "-p", "0", "-o", "u.bin", "u.rel"]):
        r = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    (tmp_path / "t.s").write_text(sdas.translate(
        (tmp_path / "t.mac").read_text(), module="t"))
    ok, msg = sdas.assemble(tmp_path / "t.s", flags=())
    assert ok, msg
    for cmd in (["sdldz80", "-n", "-i", "t.ihx", "t.rel"],
                ["sdobjcopy", "-I", "ihex", "-O", "binary", "t.ihx", "t.bin"]):
        r = subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
    want = (tmp_path / "u.bin").read_bytes()
    got = (tmp_path / "t.bin").read_bytes()
    assert len(got) > 1000
    assert want.startswith(got) and set(want[len(got):]) <= {0xFF}


@pytest.mark.skipif(not (shutil.which("um80") and shutil.which("ul80")),
                    reason="needs um80 and ul80")
def test_sdas_block_translation_round_trips_every_instruction_form(tmp_path):
    """MACRO-80 -> sdas (sdas.py) -> MACRO-80 (the __asm block translator)
    -> um80 gives the bytes of um80 on the first text."""
    from uc80 import sdas_inline
    mac = _all_forms_source()
    text = sdas.translate(mac, module="t")
    body = [ln for ln in text.splitlines()
            if not re.match(r"\s*\.(area|module|globl)", ln)
            and not ln.startswith(";")]
    back = "\n".join(sdas_inline.sdas_block_to_m80("\n".join(body)))
    (tmp_path / "a.mac").write_text(mac)
    (tmp_path / "b.mac").write_text("\t.z80\n\tcseg\n" + back + "\n\tend\n")
    images = []
    for stem in "ab":
        for cmd in (["um80", f"{stem}.mac", "-o", f"{stem}.rel"],
                    ["ul80", "--bin", "-p", "0", "-o", f"{stem}.bin",
                     f"{stem}.rel"]):
            r = subprocess.run(cmd, cwd=tmp_path, capture_output=True,
                               text=True)
            assert r.returncode == 0, r.stdout + r.stderr
        images.append((tmp_path / f"{stem}.bin").read_bytes())
    assert images[0] == images[1] and len(images[0]) > 1000


# ---- the compiler option --------------------------------------------------

def run_uc80(args, cwd):
    return subprocess.run([sys.executable, "-m", "uc80.main", *args],
                          cwd=cwd, capture_output=True, text=True)


def test_option_writes_a_s_file_and_m80_output_is_unchanged(tmp_path):
    (tmp_path / "a.c").write_text(
        "int g = 3;\nint f(int x) { return x + g; }\n"
        "int main(void) { return f(2); }\n")
    r = run_uc80(["a.c", "-o", "a.mac"], tmp_path)
    assert r.returncode == 0, r.stderr
    r = run_uc80(["a.c", "--asm-syntax=sdas", "-o", "a.s"], tmp_path)
    assert r.returncode == 0, r.stderr
    mac = (tmp_path / "a.mac").read_text()
    s = (tmp_path / "a.s").read_text()
    assert "DSEG" in mac and ".area" not in mac
    assert ".area" in s and "_main:" in s and "DSEG" not in s
    # the default output name
    r = run_uc80(["a.c", "--asm-syntax=sdas"], tmp_path)
    assert r.returncode == 0 and (tmp_path / "a.s").exists()


def test_bad_style_is_an_error(tmp_path):
    (tmp_path / "a.c").write_text("int main(void) { return 0; }\n")
    r = run_uc80(["a.c", "--asm-syntax=sdas", "--sdas-areas=bogus=X"], tmp_path)
    assert r.returncode != 0 and "bogus" in r.stderr


@needs_sdas
def test_build_sdas_libs_option(tmp_path):
    r = run_uc80(["--target=rom", "--build-sdas-libs", str(tmp_path / "L")],
                 tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "L" / "uc80_lc.lib").exists()
