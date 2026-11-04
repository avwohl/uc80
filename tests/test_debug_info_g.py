"""``uc80 -g`` (docs/debug_info_g.md): ``;#`` debug markers in the assembler
text.  The rule of the debugger plan (D2) is that the code is the same bytes
with and without ``-g``; most tests here check that rule."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import uc80
from uc80 import asm_dce, looprot, peep_flow, peep_local
from uc80.markers import is_marker, marker_kind

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"


def _um80_has_debug_info():
    if not (shutil.which("um80") and shutil.which("ul80")):
        return False
    r = subprocess.run(["um80", "--help"], capture_output=True, text=True)
    return "--debug-info" in r.stdout + r.stderr


TOOLS_OK = bool(_um80_has_debug_info() and (LIB_DIR / "libc.lib").exists()
                and (LIB_DIR / "runtime.lib").exists())
# A skip that exits 0 hides a missing tool.  With UC80_REQUIRE_UM80 set (CI),
# the missing tool is a failure.
if os.environ.get("UC80_REQUIRE_UM80") and not TOOLS_OK:
    raise RuntimeError(
        "UC80_REQUIRE_UM80 is set, but um80/ul80 with --debug-info or the "
        "built libraries (libc.lib, runtime.lib) are missing")

needs_tools = pytest.mark.skipif(
    not TOOLS_OK,
    reason="um80/ul80 with --debug-info or the built libraries are missing")


def compile_c(source, out, *flags, cwd=None):
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(source),
                        "-o", str(out), *flags], capture_output=True,
                       text=True, cwd=str(cwd or REPO))
    return r


def build(mac, debug):
    """The .com of the .mac text (and the .dbg path when DEBUG)."""
    mac = Path(mac)
    rel, com = mac.with_suffix(".rel"), mac.with_suffix(".com")
    um = ["um80", str(mac), "-o", str(rel)]
    ul = ["ul80", str(rel), str(LIB_DIR / "libc.lib"),
          str(LIB_DIR / "runtime.lib"), "-o", str(com)]
    if debug:
        um.insert(1, "--debug-info")
        ul.insert(1, "--debug-info")
    subprocess.run(um, check=True, capture_output=True, text=True,
                   cwd=str(mac.parent))
    subprocess.run(ul, check=True, capture_output=True, text=True,
                   cwd=str(mac.parent))
    return com, com.with_suffix(".dbg")


SAMPLE = """\
static int sq(int x) { return x * x; }
int a[10];
int g1, g2, g3;
int sumto(int k, int n)
{
    int i, s = 0;
    for (i = 0; i < n; i++)
        s += a[i] + k * 7;
    return s;
}
int main(void)
{
    int r = sq(5);
    int j;
    g1 = 0x1234;
    for (j = 0; j < 10; j++)
        a[j] = sq(j);
    g2 = 0x2345;
    r += sumto(3, 10);
    while (r > 100)
        r -= 7;
    g3 = 0x3456;
    if (r > 50)
        g1 = 1;
    else
        g1 = 2;
    return r;
}
"""


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("g")
    (d / "sample.c").write_text(SAMPLE)
    r = compile_c("sample.c", d / "sample.mac", "-g", cwd=d)
    assert r.returncode == 0, r.stderr
    return d, (d / "sample.mac").read_text()


# ---- the markers in the text --------------------------------------------

def test_the_file_table_has_the_source_file(sample):
    _d, text = sample
    assert re.search(r'^;#file 1 "sample\.c"$', text, re.M)


def test_each_function_has_func_and_endfunc(sample):
    _d, text = sample
    assert re.search(r"^\s*;#func _sumto 1 4 frame=\w+ pops=0$", text, re.M)
    assert re.search(r"^\s*;#func _main 1 11 frame=\w+ pops=0$", text, re.M)
    assert re.search(r"^\s*;#endfunc _sumto$", text, re.M)
    assert re.search(r"^\s*;#endfunc _main$", text, re.M)
    # the function record follows its label, so dead-code removal of the
    # function takes the record along
    assert re.search(r"^_sumto:\n\s*;#func _sumto", text, re.M)


def test_the_three_parts_of_a_for_header_have_a_marker(sample):
    _d, text = sample
    body = text.split("_main:")[1].split(";#endfunc _main")[0]
    # for (j = 0; j < 10; j++): init, condition and update, all line 16
    assert body.count(";#line 1 16") >= 3


def test_an_inlined_body_is_between_inline_and_endinline(sample):
    _d, text = sample
    assert re.search(r"^\s*;#inline 1 13 sq\n\s*;#line 1 1\n", text, re.M)
    assert text.count(";#endinline") >= 2


def test_hoisted_code_is_not_a_step_point(sample):
    _d, text = sample
    body = text.split("_sumto:")[1].split(";#endfunc _sumto")[0]
    assert re.search(r";#line 1 7 nostmt", body)


def test_the_epilogue_has_the_line_of_the_closing_brace(sample):
    _d, text = sample
    assert re.search(r"^\s*;#line 1 10\n@sumto_ret:", text, re.M) or \
        re.search(r"^@sumto_ret:\n\s*;#line 1 10\n", text, re.M)


def test_a_line_directive_and_an_include_name_the_real_file(tmp_path):
    (tmp_path / "inc.h").write_text(
        "static int twice(int x)\n{\n    int y = x + x;\n    return y;\n}\n")
    (tmp_path / "a.c").write_text(
        '#include "inc.h"\nint f(int a)\n{\n    int r = twice(a);\n'
        '#line 500 "gen.y"\n    r = r + 1;\n    return r;\n}\n'
        "int main(void)\n{\n    return f(3);\n}\n")
    r = compile_c("a.c", tmp_path / "a.mac", "-g", "--no-inlining",
                  cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "a.mac").read_text()
    files = dict((m[1], m[0]) for m in
                 re.findall(r'^;#file (\d+) "([^"]+)"$', text, re.M))
    assert {"inc.h", "a.c", "gen.y"} <= set(files)
    no = files["gen.y"]
    assert f";#line {no} 500" in text and f";#line {no} 501" in text
    assert f";#line {files['inc.h']} 3" in text      # int y = x + x;
    assert f";#line {files['a.c']} 4" in text        # int r = twice(a);


def test_without_g_there_is_no_marker(tmp_path):
    (tmp_path / "s.c").write_text(SAMPLE)
    r = compile_c("s.c", tmp_path / "s.mac", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert not any(is_marker(ln) for ln in
                   (tmp_path / "s.mac").read_text().split("\n"))


# ---- every ;#func has its ;#endfunc ------------------------------------------

SPIN = """\
int g;
void spin(void) {
    for (;;)
        g++;
}
int main(void) {
    g = 1;
    spin();
    return 0;
}
"""


def balance_errors(text):
    """The problems of the ;#func / ;#endfunc / ;#inline nesting."""
    errs, funcs, inl = [], [], 0
    for ln in text.split("\n"):
        k = marker_kind(ln)
        if k == "func":
            if funcs:
                errs.append(f"{ln.split()[1]} starts inside {funcs[-1]}")
            funcs.append(ln.split()[1])
        elif k == "endfunc":
            if funcs and ln.split()[1:2] == funcs[-1:]:
                funcs.pop()
            else:
                errs.append(f"stray {ln.strip()}")
        elif k == "inline":
            inl += 1
        elif k == "endinline":
            inl -= 1
            if inl < 0:
                errs.append("stray ;#endinline")
                inl = 0
    errs += [f"{f} has no ;#endfunc" for f in funcs]
    if inl:
        errs.append(f"{inl} ;#inline without ;#endinline")
    return errs


def test_a_function_that_ends_in_an_endless_loop_keeps_its_endfunc(tmp_path):
    (tmp_path / "spin.c").write_text(SPIN)
    r = compile_c("spin.c", tmp_path / "spin.mac", "-g", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "spin.mac").read_text()
    assert balance_errors(text) == []
    assert re.search(r"jr @FOR1\n\s*;#endfunc _spin\n_main:", text)


@needs_tools
def test_um80_has_no_warning_for_a_function_that_ends_in_a_loop(tmp_path):
    (tmp_path / "spin.c").write_text(SPIN)
    assert compile_c("spin.c", tmp_path / "spin.mac", "-g",
                     cwd=tmp_path).returncode == 0
    r = subprocess.run(["um80", "--debug-info", "spin.mac", "-o", "spin.rel"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "no #endfunc" not in r.stdout + r.stderr, r.stdout + r.stderr
    assert "inside" not in r.stdout + r.stderr, r.stdout + r.stderr


@pytest.mark.parametrize("src", sorted((REPO / "examples").glob("*.c")),
                         ids=lambda p: p.stem)
def test_every_func_of_each_example_has_its_endfunc(src, tmp_path):
    r = compile_c(src, tmp_path / "p.mac", "-g")
    if r.returncode != 0:
        pytest.skip("the program does not compile")
    assert balance_errors((tmp_path / "p.mac").read_text()) == []


# ---- D2: the code is the same ---------------------------------------------

def _programs():
    out = []
    for pat in ("examples/*.c", "tests/c-testsuite-z80/*.c", "tests/gen3/*.c"):
        out += sorted(REPO.glob(pat))
    return out


def _flt(text):
    return [ln for ln in text.split("\n") if not is_marker(ln)]


@needs_tools
@pytest.mark.parametrize("src", _programs(), ids=lambda p: p.stem)
def test_g_gives_the_same_com_as_no_g(src, tmp_path):
    if "__TIME__" in src.read_text(errors="replace"):
        pytest.skip("the program holds the time of the build")
    n, g = tmp_path / "n", tmp_path / "g"
    n.mkdir()
    g.mkdir()
    rn = compile_c(src, n / "p.mac")
    if rn.returncode != 0:
        pytest.skip("the program does not compile without -g")
    rg = compile_c(src, g / "p.mac", "-g")
    assert rg.returncode == 0, rg.stderr
    com_n, _ = build(n / "p.mac", debug=False)
    com_g, dbg = build(g / "p.mac", debug=True)
    assert com_g.read_bytes() == com_n.read_bytes()
    assert dbg.exists()


@needs_tools
@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-register-locals",),
                                   ("--no-inlining",), ("--no-licm",)],
                         ids=lambda f: "".join(f) or "default")
def test_g_gives_the_same_com_with_other_options(flags, tmp_path):
    (tmp_path / "s.c").write_text(SAMPLE)
    n, g = tmp_path / "n", tmp_path / "g"
    n.mkdir()
    g.mkdir()
    (n / "s.c").write_text(SAMPLE)
    (g / "s.c").write_text(SAMPLE)
    assert compile_c("s.c", n / "s.mac", *flags, cwd=n).returncode == 0
    assert compile_c("s.c", g / "s.mac", "-g", *flags, cwd=g).returncode == 0
    assert build(n / "s.mac", False)[0].read_bytes() == \
        build(g / "s.mac", True)[0].read_bytes()


@pytest.mark.parametrize("name", ["test_arith", "test_unsigned",
                                  "neogeo_driver", "test_ctype"])
def test_text_passes_make_the_same_code_with_markers(name, tmp_path):
    """The probe of the plan: a marker after each instruction and label in
    the input of each text pass changes no instruction."""
    probe = Path(__file__).with_name("_marker_probe.py")
    src = REPO / "examples" / f"{name}.c"
    for every in ("1", "3"):
        r = subprocess.run(
            [sys.executable, str(probe), every, str(src),
             "-o", str(tmp_path / "x.mac")],
            capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        res = json.loads(r.stderr.strip().split("RES ")[-1])
        assert res
        for pass_name, (calls, other, lost) in res.items():
            assert other == 0, (pass_name, every, res)
            assert lost == 0, (pass_name, every, res)


# ---- the passes, one at a time ---------------------------------------------

def test_peep_flow_ins_after_skips_markers():
    rs = peep_flow._parse(["\tld\tA,1", "\t;#line 1 5", ";#line 1 6",
                           "\tld\tB,2", "", "\tld\tC,3"])
    assert peep_flow._ins_after(rs, 0, 2) == [3, 5]


ROT = """\
@FOR1:
%s\tld\tHL,(_i)
%s\tld\tDE,(_n)
%s\tor\tA
%s\tsbc\tHL,DE
%s\tjp\tNC,@ENDFOR2
\tld\tHL,(_s)
\tinc\tHL
\tld\t(_s),HL
@FORUPD3:
\tld\tHL,(_i)
\tinc\tHL
\tld\t(_i),HL
\tjp\t@FOR1
@ENDFOR2:
\tret
"""


def test_looprot_copies_the_markers_of_the_test():
    plain = ROT % ("", "", "", "", "")
    marked = ROT % tuple(f"\t;#line 1 {n}\n" for n in range(10, 15))
    out_plain = looprot.rotate(plain)
    out_marked = looprot.rotate(marked)
    assert "@RB0" in out_plain                      # the loop is rotated
    assert [ln for ln in out_marked.split("\n") if not is_marker(ln)] == \
        out_plain.split("\n")
    # the copy of the test, at the end of the loop, has the markers again
    for n in range(10, 15):
        assert out_marked.count(f";#line 1 {n}") == 2


def test_looprot_window_does_not_count_markers():
    body = "".join(f"\tld\tA,{k}\n\t;#line 1 {k}\n" for k in range(30))
    text = ("@WHILE1:\n\tld\tA,(_x)\n\tor\tA\n\tjp\tZ,@ENDWHILE2\n"
            + body + "\tjp\t@WHILE1\n@ENDWHILE2:\n\tret\n")
    plain = "\n".join(ln for ln in text.split("\n") if not is_marker(ln))
    assert [ln for ln in looprot.rotate(text).split("\n")
            if not is_marker(ln)] == looprot.rotate(plain).split("\n")


def test_asm_dce_keeps_the_markers_of_a_live_label_after_a_dead_block():
    text = ("\t.z80\n\tPUBLIC\t_main\n_main:\n\tjp\t@L1\n"
            "_dead:\n\tld\tA,1\n\t;#line 1 9\n@L1:\n\tret\n\tend\n")
    out = asm_dce.eliminate_dead_code(text)
    assert "_dead" not in out
    # the marker was in front of @L1, in the block of _dead
    assert re.search(r"@L1:\n\s*;#line 1 9\n\s*ret", out)


def test_peep_local_branch_merge_keeps_the_markers_it_cuts():
    text = ("\tld\tA,(_x)\n\tor\tA\n\tjr\tz,@ELSE1\n"
            "\t;#line 1 2\n\tcall\t_p\n\t;#line 1 3\n\tpop\tDE\n\tjr\t@END2\n"
            "\t;#line 1 4\n@ELSE1:\n"
            "\t;#line 1 5\n\tcall\t_q\n\t;#line 1 6\n\tpop\tDE\n"
            "@END2:\n\tret\n")
    plain = "\n".join(ln for ln in text.split("\n") if not is_marker(ln))
    out = peep_local.optimize(text)
    assert [ln for ln in out.split("\n") if not is_marker(ln)] == \
        peep_local.optimize(plain).split("\n")
    for n in range(2, 7):
        assert f";#line 1 {n}" in out


def test_marker_kind():
    assert marker_kind("\t;#line 1 5") == "line"
    assert marker_kind(";  #Func x 1 2") == "func"
    assert marker_kind("; Function x") == ""
    assert not is_marker("\tld A,1 ;#line 1 2")


# ---- through um80 and ul80 --------------------------------------------------

@needs_tools
def test_the_lines_of_prog_dbg_map_statements_to_their_code(sample):
    d, _text = sample
    com, dbg = build(d / "sample.mac", debug=True)
    info = json.loads(dbg.read_text())
    image = info["image"]
    data = com.read_bytes()
    assert image["kind"] == "com" and image["load"] == 0x100

    def code_at(addr, n=3):
        off = image["offset"] + addr - image["load"]
        return data[off:off + n]

    rows = {}                  # line of sample.c -> [(start, nostmt)]
    files = info["files"]
    sample_no = next(i for i, f in enumerate(files) if f.endswith("sample.c"))
    for start, _end, groups, *_ in info["lines"]:
        for f, line, nostmt in [g[:3] for g in groups]:
            if f == sample_no:
                rows.setdefault(line, []).append((start, nostmt))
    # g1 = 0x1234;  g2 = 0x2345;  g3 = 0x3456;  load HL with the constant
    for line, k in ((15, 0x1234), (18, 0x2345), (22, 0x3456)):
        starts = [a for a, ns in rows[line] if not ns]
        assert starts, line
        assert any(code_at(a) == bytes([0x21, k & 255, k >> 8])
                   for a in starts), (line, [hex(a) for a in starts])
    # the function records are where the labels are
    labels = {n.lower(): a for n, a, *_ in info["labels"]}   # M80: upper case
    funcs = {f["name"]: f for f in info["funcs"]}
    assert funcs["_sumto"]["start"] == labels["_sumto"]
    assert funcs["_main"]["start"] == labels["_main"]
    assert funcs["_sumto"]["end"] <= funcs["_main"]["start"]
    assert funcs["_sumto"]["line"] == 4 and funcs["_main"]["line"] == 11
    # each line of a function that has code has a row
    for line in (6, 7, 8, 9, 13, 16, 17, 19, 20, 21, 23, 24, 26, 27):
        assert line in rows, line


@needs_tools
def test_prog_dbg_has_the_inlined_function(sample):
    d, _text = sample
    _com, dbg = build(d / "sample.mac", debug=True)
    info = json.loads(dbg.read_text())
    names = [r[3][0][0] for r in info["lines"] if len(r) > 3]
    assert "sq" in names
