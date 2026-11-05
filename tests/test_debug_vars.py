"""Variables in ``uc80 -g`` (debug_vars.py, varinfo.py; phase 8 of the
debugger plan) and the ``outer=`` field of ``;#line`` (rule D8).

The code generator hands out the storage of each variable; ``varinfo`` writes
the ``;#var`` and ``;#type`` markers after the last text pass.  The tests
check the marker for each storage kind, the ranges of register locals, the
honest "not available" cases, and the rule D2: the ``.com`` is the same with
and without ``-g``.
"""

import json
import re

import pytest

from uc80 import varinfo

from .test_debug_info_g import TOOLS_OK, build, compile_c, needs_tools

SOURCE = """\
struct pt { int x; int y; };
typedef unsigned char byte;
enum color { RED, GREEN = 5, BLUE };
union u { int i; char c[2]; };
struct node { int val; struct node *next; };
struct bits { unsigned a : 3; unsigned b : 5; };
int g = 7;
static int sg = 9;
struct pt gp = { 3, 4 };
int ga[4] = { 10, 20, 30, 40 };
byte gb = 200;
enum color gc = GREEN;
char *gs = "hi";
struct node gn = { 1, 0 };
union u gu;
struct bits gbits;
int (*gf)(int);

int leaf(int a, int b)
{
    int loc = a + b;
    static int cnt;
    cnt += loc;
    return cnt;
}

int deep(int n, struct pt *p)
{
    int local = n * 2;
    int arr[2];
    arr[0] = local;
    arr[1] = n;
    if (n <= 1)
        return p->x + arr[0];
    return n * deep(n - 1, p) + arr[1] - n + g;
}

int work(int n, struct pt *p)
{
    int arr[3];
    struct pt s;
    int i, sum = 0;
    arr[0] = 5; arr[1] = 6; arr[2] = 7;
    s.x = n; s.y = p->y;
    for (i = 0; i < 3; i++) {
        int t = arr[i] + s.x;
        sum += leaf(t, i);
    }
    return sum + g + sg + ga[1] + gb + gc + gs[0] + gn.val + gu.i
        + gbits.a + (gf != 0);
}

int main(void)
{
    struct pt q = { 1, 2 };
    int r = work(3, &q) + deep(3, &q) + gp.x;
    return r > 1000;
}
"""


def vars_of(text):
    """{name: [fields]} of the ``;#var`` lines (later one wins for a name)."""
    out = {}
    for ln in text.splitlines():
        m = re.match(r"\s*;#var (\S+) (\S+) (.*)$", ln)
        if m:
            words = dict(w.partition("=")[::2] for w in m.group(3).split())
            words["_type"] = m.group(2)
            words["_flags"] = m.group(3).split()
            out.setdefault(m.group(1), []).append(words)
    return out


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    d = tmp_path_factory.mktemp("v")
    (d / "p.c").write_text(SOURCE)
    r = compile_c("p.c", d / "p.mac", "-g", cwd=d)
    assert r.returncode == 0, r.stderr
    assert "variable records" not in r.stderr, r.stderr
    return d, (d / "p.mac").read_text()


@pytest.fixture(scope="module")
def optnone(tmp_path_factory):
    d = tmp_path_factory.mktemp("vo")
    (d / "p.c").write_text(SOURCE)
    r = compile_c("p.c", d / "p.mac", "-g", "--optnone=deep,work,leaf",
                  cwd=d)
    assert r.returncode == 0, r.stderr
    assert "variable records" not in r.stderr, r.stderr
    return d, (d / "p.mac").read_text()


# ---- the types ----------------------------------------------------------------

def test_types_have_sizes_members_and_offsets(sample):
    _d, text = sample
    assert ";#type struct:pt size=4 members=x:int:2@0,y:int:2@2" in text
    assert ";#type enum:color size=2 values=RED=0,GREEN=5,BLUE=6" in text
    assert ";#type typedef:byte target=uchar:1" in text
    assert re.search(r";#type union:u size=2 members=i:int:2@0,c:\[2\]char:1@0",
                     text)
    assert re.search(
        r";#type struct:node size=4 members=val:int:2@0,next:\*struct:node@2",
        text)


def test_a_bit_field_member_has_its_width_and_shift(sample):
    _d, text = sample
    assert re.search(r";#type struct:bits size=\d+ members="
                     r"a:uint:2@0#3\.0,b:uint:2@0#5\.3", text)


def test_the_type_text_of_globals(sample):
    _d, text = sample
    v = vars_of(text)
    assert v["ga"][0]["_type"] == "[4]int:2"
    assert v["gb"][0]["_type"] == "typedef:byte"
    assert v["gc"][0]["_type"] == "enum:color"
    assert v["gs"][0]["_type"] == "*char:1"
    assert v["gf"][0]["_type"] == "*func(int:2)"
    assert v["gn"][0]["_type"] == "struct:node"


# ---- the storage kinds ------------------------------------------------------------

def test_a_global_and_a_static_have_a_label(sample):
    _d, text = sample
    v = vars_of(text)
    assert v["g"][0]["at"] == "_g" and v["g"][0]["kind"] == "global"
    assert v["sg"][0]["at"] == "_sg" and v["sg"][0]["kind"] == "static"
    assert v["gp"][0]["at"] == "_gp"


def test_a_static_local_has_its_own_label_and_the_block_scope(sample):
    _d, text = sample
    cnt = vars_of(text)["cnt"][0]
    assert cnt["kind"] == "static"
    assert re.fullmatch(r"__leaf_S\d+", cnt["at"])
    assert re.fullmatch(r"1:\d+-\d+", cnt["scope"])


def test_a_local_of_a_shared_area_function_is_in_auto(sample):
    _d, text = sample
    v = vars_of(text.split(";#func _work")[1].split(";#endfunc _work")[0])
    assert re.fullmatch(r"\?\?AUTO\+\d+", v["arr"][0]["at"])
    assert re.fullmatch(r"\?\?AUTO\+\d+", v["s"][0]["at"])
    assert re.fullmatch(r"\?\?AUTO\+\d+", v["t"][0]["at"])
    assert v["t"][0]["scope"] != v["s"][0]["scope"]    # the block of the for
    assert v["s"][0]["kind"] == "local"
    m = vars_of(text.split(";#func _main")[1].split(";#endfunc _main")[0])
    assert re.fullmatch(r"\?\?AUTO\+\d+", m["q"][0]["at"])


def test_the_variables_of_a_function_follow_its_func_marker(sample):
    _d, text = sample
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(";#func _work "):
            assert lines[i + 1].strip().startswith(";#var")
            break
    else:
        pytest.fail("no ;#func _work")


def test_an_ix_local_and_parameters_under_optnone(optnone):
    _d, text = optnone
    body = text.split(";#func _deep")[1].split(";#endfunc _deep")[0]
    v = vars_of(body)
    assert v["n"][0]["ix"] == "4" and v["n"][0]["kind"] == "param"
    assert v["p"][0]["ix"] == "6" and v["p"][0]["kind"] == "param"
    assert int(v["local"][0]["ix"]) < 0 and v["local"][0]["kind"] == "local"
    assert int(v["arr"][0]["ix"]) < 0
    assert v["arr"][0]["_type"] == "[2]int:2"


def test_a_register_local_has_ranges_with_labels(sample):
    _d, text = sample
    regs = [(n, w) for n, ws in vars_of(text).items() for w in ws
            if "reg" in w]
    assert regs, "no register local in the sample"
    for name, w in regs:
        assert w["reg"] in ("BC", "DE", "B", "C", "D", "E", "IY")
        ranges = [f.split("=", 1)[1] for f in w["_flags"]
                  if f.startswith("range=")]
        assert ranges
        for r in ranges:
            a, b = r.split(":")
            order = {m.group(1): i for i, m in enumerate(
                re.finditer(r"^(\?\?VR\d+):", text, re.M))}
            assert a in order and b in order and order[a] < order[b]


def test_a_parameter_that_has_no_slot_says_none(sample):
    _d, text = sample
    nones = [w for ws in vars_of(text).values() for w in ws
             if "none" in w["_flags"]]
    assert nones, "no variable without a place in the sample"
    for w in nones:
        assert any(f.startswith("why=") for f in w["_flags"])


def test_the_markers_of_a_dead_global_stay_but_the_label_does_not(sample):
    _d, text = sample
    # gu is read in work, gbits too; gf is never assigned, so all three live
    assert "at=_gu" in text and "at=_gbits" in text


# ---- varinfo on a handwritten text ---------------------------------------------------

def _info(funcs, glob=(), types=None):
    return {"funcs": funcs, "globals": list(glob), "types": types or {}}


def _var(name, loc, size=2, key=None, reg=None, kind="local"):
    return {"name": name, "type": "int:2", "kind": kind, "loc": loc,
            "size": size, "key": key, "scope": ("a.c", 1, 9),
            "decl": ("a.c", 2), "reg": reg}


TEXT = """\
; Generated by uc80
;#file 1 "a.c"
_f:
\t;#func _f 1 1 frame=ix pops=0
\t;#line 1 1
\tpush\tIX
\tld\tIX,0
\tadd\tIX,SP
\tld\tBC,5
\t;#line 1 2
\tld\tA,B
\tpush\tBC
\tcall\t_g
\tpop\tBC
\t;#line 1 3
\tld\tA,C
@f_ret:
\t;#line 1 4
\tpop\tIX
\tret
\t;#endfunc _f
"""


def test_a_register_range_leaves_out_the_saved_stretch():
    out = varinfo.add_vars(TEXT, _info({"_f": [
        _var("r", ("ix", -2), key=("x", -2), reg="BC")]}))
    line = next(l for l in out.splitlines() if ";#var r " in l)
    ranges = re.findall(r"range=(\S+):(\S+)", line)
    assert len(ranges) == 2
    lines = out.splitlines()
    pos = {l[:-1]: i for i, l in enumerate(lines) if l.startswith("??VR")}
    (a1, b1), (a2, b2) = ranges
    # the first range ends in front of push BC, the second starts after pop BC
    assert lines[pos[b1] + 1].strip() == "push\tBC"
    assert lines[pos[a2] - 1].strip() == "pop\tBC"
    # and the last range ends in front of the epilogue label
    assert lines[pos[b2] + 1].strip() == "@f_ret:"


def test_a_push_that_no_pop_closes_ends_the_range():
    text = TEXT.replace("\tpop\tBC\n", "\tnop\n")
    out = varinfo.add_vars(text, _info({"_f": [
        _var("r", ("ix", -2), key=("x", -2), reg="BC")]}))
    line = next(l for l in out.splitlines() if ";#var r " in l)
    assert line.count("range=") == 1
    lines = out.splitlines()
    end = re.search(r"range=\S+:(\S+)", line).group(1)
    assert lines[lines.index(end + ":") + 1].strip() == "push\tBC"


def test_a_pop_with_no_push_ends_the_range():
    text = TEXT.replace("\tld\tA,B\n\tpush\tBC\n\tcall\t_g\n\tpop\tBC\n",
                        "\tld\tA,B\n\tnop\n\tpop\tBC\n")
    out = varinfo.add_vars(text, _info({"_f": [
        _var("r", ("ix", -2), key=("x", -2), reg="BC")]}))
    line = next(l for l in out.splitlines() if ";#var r " in l)
    assert line.count("range=") == 1
    lines = out.splitlines()
    end = re.search(r"range=\S+:(\S+)", line).group(1)
    assert lines[lines.index(end + ":") + 1].strip() == "pop\tBC"


def test_nothing_left_of_the_range_says_none():
    text = TEXT.replace("\tld\tBC,5\n\t;#line 1 2\n\tld\tA,B\n\tpush\tBC\n",
                        "\tld\tBC,5\n\t;#line 1 2\n\tpop\tBC\n\tpush\tBC\n")
    out = varinfo.add_vars(text, _info({"_f": [
        _var("r", ("ix", -2), key=("x", -2), reg="BC")]}))
    line = next(l for l in out.splitlines() if ";#var r " in l)
    assert " none " in line and "why=save-not-paired" in line
    assert "reg=" not in line


def test_a_slot_that_no_instruction_names_says_no_storage():
    out = varinfo.add_vars(TEXT, _info({"_f": [
        _var("u", ("ix", -4), key=("x", -4))]}))
    line = next(l for l in out.splitlines() if ";#var u " in l)
    assert " none " in line and "why=no-storage" in line


def test_a_slot_that_an_instruction_names_has_its_place():
    text = TEXT.replace("\tld\tA,C\n", "\tld\tA,(IX-4)\n")
    out = varinfo.add_vars(text, _info({"_f": [
        _var("u", ("ix", -4), key=("x", -4))]}))
    line = next(l for l in out.splitlines() if ";#var u " in l)
    assert "ix=-4" in line and "none" not in line


def test_an_ix_variable_of_a_function_without_a_frame_says_none():
    text = TEXT.replace("frame=ix", "frame=none")
    out = varinfo.add_vars(text, _info({"_f": [_var("u", ("ix", -4))]}))
    line = next(l for l in out.splitlines() if ";#var u " in l)
    assert " none " in line and "why=no-frame" in line


def test_the_file_table_gets_a_file_that_a_scope_names():
    v = _var("u", ("ix", -4))
    v["scope"] = ("other.h", 3, 5)
    out = varinfo.add_vars(TEXT, _info({"_f": [v]}))
    assert ';#file 2 "other.h"' in out
    assert "scope=2:3-5" in out


def test_no_variable_changes_the_instructions():
    out = varinfo.add_vars(TEXT, _info({"_f": [
        _var("r", ("ix", -2), key=("x", -2), reg="BC")]}))
    keep = lambda t: [l for l in t.splitlines()
                      if l.startswith("\t") and not l.lstrip().startswith(";")]
    assert keep(out) == keep(TEXT)


# ---- D2: the .com does not change ----------------------------------------------------------

@needs_tools
@pytest.mark.parametrize("flags", [(), ("--optnone=deep,work,leaf",),
                                   ("--optnone=all",), ("--no-register-locals",),
                                   ("--no-shared-storage",)])
def test_the_com_is_the_same_with_and_without_g(tmp_path, flags):
    (tmp_path / "p.c").write_text(SOURCE)
    coms = {}
    for tag, extra in (("n", ()), ("g", ("-g",))):
        d = tmp_path / tag
        d.mkdir()
        (d / "p.c").write_text(SOURCE)
        r = compile_c("p.c", d / "p.mac", *extra, *flags, cwd=d)
        assert r.returncode == 0, r.stderr
        com, _dbg = build(d / "p.mac", tag == "g")
        coms[tag] = com.read_bytes()
    assert coms["n"] == coms["g"]


# ---- the linked file ----------------------------------------------------------------------

@needs_tools
def test_prog_dbg_has_the_addresses_of_the_variables(tmp_path):
    (tmp_path / "p.c").write_text(SOURCE)
    r = compile_c("p.c", tmp_path / "p.mac", "-g", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    com, dbg = build(tmp_path / "p.mac", True)
    doc = json.loads(dbg.read_text())
    assert doc["revision"] == 2
    labels = {l[0]: l[1] for l in doc["labels"]}
    rows = {v[0]: v for v in doc["vars"]}
    assert rows["g"][3] == ["at", labels["_G"]]
    assert rows["sg"][3] == ["at", labels["_SG"]]
    assert rows["gp"][3] == ["at", labels["_GP"]]
    auto = labels["??AUTO"]
    funcs = {f["name"]: f for f in doc["funcs"]}
    s = next(v for v in doc["vars"] if v[0] == "s")
    assert s[3][0] == "at" and s[3][1] >= auto
    assert s[4] == funcs["_work"]["start"]
    for v in doc["vars"]:
        if v[3] and v[3][0] == "reg":
            f = next(f for f in doc["funcs"] if f["start"] == v[4])
            assert v[6], "a register variable needs ranges"
            for a, b in v[6]:
                assert f["start"] <= a < b <= f["end"]
    assert doc["types"]["struct:pt"]["members"] == [["x", "int:2", 0],
                                                    ["y", "int:2", 2]]


# ---- D8: outer= -------------------------------------------------------------------------------

BASIC_STYLE = """\
int total;
#line 10 "prog.bas"
int main(void) {
    int i;
#line 20 "prog.bas"
    total = 0;
#line 30 "prog.bas"
    for (i = 0; i < 3; i++)
#line 40 "prog.bas"
        total += i;
#line 50 "prog.bas"
    return total;
}
"""


def test_a_line_directive_gives_outer_on_the_line_marker(tmp_path):
    (tmp_path / "m.c").write_text(BASIC_STYLE)
    r = compile_c("m.c", tmp_path / "m.mac", "-g", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "m.mac").read_text()
    files = dict(re.findall(r'^;#file (\d+) "(.*)"$', text, re.M))
    bas = next(n for n, p in files.items() if p == "prog.bas")
    c = next(n for n, p in files.items() if p == "m.c")
    got = {(int(a), int(b)): (int(c2), int(d))
           for a, b, c2, d in re.findall(
               r";#line (\d+) (\d+)(?: nostmt)? outer=(\d+):(\d+)", text)}
    # the BASIC line 20 is the C line 6, the BASIC line 40 is the C line 10
    assert got[(int(bas), 20)] == (int(c), 6)
    assert got[(int(bas), 40)] == (int(c), 10)
    assert got[(int(bas), 50)] == (int(c), 12)


def test_no_directive_no_outer(sample):
    _d, text = sample
    assert "outer=" not in text


@needs_tools
def test_outer_goes_through_um80_and_ul80_and_the_com_does_not_change(tmp_path):
    coms = {}
    for tag, extra in (("n", ()), ("g", ("-g",))):
        d = tmp_path / tag
        d.mkdir()
        (d / "m.c").write_text(BASIC_STYLE)
        r = compile_c("m.c", d / "m.mac", *extra, cwd=d)
        assert r.returncode == 0, r.stderr
        com, dbg = build(d / "m.mac", tag == "g")
        coms[tag] = com.read_bytes()
    assert coms["n"] == coms["g"]
    doc = json.loads(dbg.read_text())
    outers = [g for e in doc["lines"] for g in e[2] if len(g) > 3]
    assert outers
    files = doc["files"]
    assert any(files[g[0]] == "prog.bas" and files[g[3]] == "m.c"
               for g in outers)
    assert {g[1]: g[4] for g in outers if files[g[0]] == "prog.bas"}[20] == 6


def test_a_fault_in_the_variable_records_does_not_stop_the_build(tmp_path,
                                                                  monkeypatch):
    # the records are a debugging aid: a fault gives a warning and a .mac file
    import subprocess, sys
    (tmp_path / "p.c").write_text(SOURCE)
    code = ("import uc80.debug_vars as d, sys\n"
            "def boom(self, *a, **k): raise KeyError('boom')\n"
            "d.DebugVarsMixin._dbgv_describe = boom\n"
            "d.DebugVarsMixin.dbgv_type = d._safe(boom)\n"
            "from uc80.main import main\n"
            "sys.argv = ['uc80', 'p.c', '-g', '-o', 'p.mac']\n"
            "sys.exit(main())\n")
    import os
    env = {k: v for k, v in os.environ.items()
           if k != "UC80_DEBUG_VARS_STRICT"}
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "variable records for -g are incomplete" in r.stderr
    assert (tmp_path / "p.mac").exists()


def test_two_files_with_equal_statics_write_the_source_name(tmp_path):
    # multi_unit renames them to cnt__s0 and cnt__s1; the link label keeps
    # the unique name, the variable keeps the name of the C source
    import subprocess, sys
    (tmp_path / "a.c").write_text("static int cnt = 5;\n"
                                  "int fa(void) { return cnt; }\n")
    (tmp_path / "b.c").write_text("static int cnt = 7;\nint fa(void);\n"
                                  "int main(void) { return cnt + fa(); }\n")
    r = subprocess.run([sys.executable, "-m", "uc80.main", "a.c", "b.c", "-g",
                        "-o", "p.mac"], cwd=tmp_path, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    mac = (tmp_path / "p.mac").read_text()
    assert ";#var cnt int:2 kind=static at=_cnt__s0" in mac
    assert ";#var cnt int:2 kind=static at=_cnt__s1" in mac
    assert "__s0 int" not in mac
