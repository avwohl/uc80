"""optnone (docs/optnone.md): no optimization for one function.

The rules come from D6 of the debugger plan in cpmemu
(docs/debugger_plan.md).  A function under optnone is not inlined and gets
no inlined call, keeps its IX frame and its calls, is written between
``;#noopt begin`` and ``;#noopt end``, and no text pass changes it.  A
program without optnone is the same bytes as before (D2)."""

import json
import re
import subprocess
import sys

import pytest

from uc80 import asm_dce, optnone

from ._clib import REPO, build_and_run, skip_without_tools
from .test_debug_info_g import TOOLS_OK, build, compile_c, needs_tools

SOURCE = """\
int g1;
static int sq(int x) { return x * x; }
static int add(int a, int b) { return a + b; }
int sink(int v);
int work(int n)
{
    int s = 0;
    int i;
    for (i = 0; i < n; i++)
        s = add(s, sq(i));
    g1 = s;
    return sink(s);
}
int sink(int v) { g1 = v; return v + 1; }
int main(void)
{
    return work(4) == 15 ? 0 : 1;
}
"""


def mac(tmp_path, text, *flags, name="p"):
    (tmp_path / f"{name}.c").write_text(text)
    r = compile_c(f"{name}.c", tmp_path / f"{name}.mac", *flags, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return (tmp_path / f"{name}.mac").read_text(), r.stderr


def regions(text):
    """{label: [lines]} of the ``;#noopt`` regions of the text."""
    lines = text.split("\n")
    out = {}
    i = 0
    while i < len(lines):
        if optnone.is_begin(lines[i]):
            m = re.match(r"\s*; optnone (\S+)", lines[i + 1])
            j = i + 1
            while not optnone.is_end(lines[j]):
                j += 1
            out[m.group(1)] = lines[i + 2:j]
            i = j
        i += 1
    return out


def function_text(text, label):
    """The lines of one function: from its label to the next ``; Function``."""
    lines = text.split("\n")
    start = lines.index(f"{label}:")
    end = next((k for k in range(start + 1, len(lines))
                if lines[k].startswith("; Function")), len(lines))
    return lines[start:end]


# ---- the ways to mark a function -----------------------------------------

def test_pragma_marks_the_next_function(tmp_path):
    src = SOURCE.replace("int work(int n)",
                         "#pragma uc80 optnone\nint work2(int n)\n"
                         "{ return n; }\nint work(int n)")
    src = src.replace("? 0 : 1", "? work2(0) : 1")
    text, _ = mac(tmp_path, src)
    assert list(regions(text)) == ["_work2"]


def test_pragma_with_names(tmp_path):
    text, _ = mac(tmp_path, "#pragma uc80 optnone work sink\n" + SOURCE)
    assert sorted(regions(text)) == ["_sink", "_work"]


@pytest.mark.parametrize("mark", [
    "[[uc80::optnone]]",
    "[[gnu::optimize(\"O0\")]]",
    "__attribute__((optimize(\"O0\")))",
    "__attribute__((noinline, optimize(\"-O0\")))",
    "__attribute__((__optnone__))",
    "[[clang::optnone]]",
])
def test_attribute_forms(tmp_path, mark):
    text, _ = mac(tmp_path, SOURCE.replace("int work(int n)",
                                           f"{mark} int work(int n)"))
    assert list(regions(text)) == ["_work"]


def test_attribute_on_a_prototype_marks_the_definition(tmp_path):
    text, _ = mac(tmp_path, SOURCE.replace(
        "int sink(int v);", "[[uc80::optnone]] int sink(int v);"))
    assert list(regions(text)) == ["_sink"]


def test_option_names(tmp_path):
    text, _ = mac(tmp_path, SOURCE, "--optnone=work,sink")
    assert sorted(regions(text)) == ["_sink", "_work"]
    text, _ = mac(tmp_path, SOURCE, "--optnone=work", "--optnone=main")
    assert sorted(regions(text)) == ["_main", "_work"]


def test_option_all(tmp_path):
    text, _ = mac(tmp_path, SOURCE, "--optnone=all")
    assert sorted(regions(text)) == ["_add", "_main", "_sink", "_sq", "_work"]


def test_option_unknown_name_warns(tmp_path):
    _text, err = mac(tmp_path, SOURCE, "--optnone=nosuch")
    assert "--optnone: 'nosuch' is not a function" in err


def test_mark_that_is_not_before_a_function_warns(tmp_path):
    _text, err = mac(tmp_path, "#pragma uc80 optnone\n" + SOURCE)
    assert "#pragma uc80 optnone is not before a function" in err
    _text, err = mac(tmp_path, SOURCE.replace(
        "int g1;", "[[uc80::optnone]] int g1;"))
    assert "optnone attribute is not before a function" in err


def test_pragma_names_that_are_not_functions_warn(tmp_path):
    _text, err = mac(tmp_path, "#pragma uc80 optnone nosuch\n" + SOURCE)
    assert "'nosuch' is not a function with a body" in err


def test_static_function_that_the_merge_renames(tmp_path):
    (tmp_path / "a.c").write_text(
        "static int h(int x) { return x + 1; }\n"
        "int fa(int x) { return h(x); }\n")
    (tmp_path / "b.c").write_text(
        "#pragma uc80 optnone\n"
        "static int h(int x) { return x + 2; }\n"
        "int fb(int x) { return h(x); }\n"
        "int fa(int x);\n"
        "int main(void) { return fa(1) + fb(1); }\n")
    r = subprocess.run([sys.executable, "-m", "uc80.main", "a.c", "b.c",
                        "-o", "ab.mac"], capture_output=True, text=True,
                       cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    got = sorted(regions((tmp_path / "ab.mac").read_text()))
    assert len(got) == 1 and got[0].startswith("_h")


# ---- what a function under optnone keeps ---------------------------------

def test_not_inlined_and_gets_no_inlined_call(tmp_path):
    base, _ = mac(tmp_path, SOURCE, "-g")
    assert ";#inline" in "\n".join(function_text(base, "_work"))
    text, _ = mac(tmp_path, SOURCE, "-g", "--optnone=work")
    body = "\n".join(function_text(text, "_work"))
    assert ";#inline" not in body
    assert "call\t_add" in body and "call\t_sq" in body
    # sq is not inlined into work, but main's other callers are unchanged
    text, _ = mac(tmp_path, SOURCE, "-g", "--optnone=sq")
    assert "call\t_sq" in text


def test_keeps_the_ix_frame(tmp_path):
    base, _ = mac(tmp_path, SOURCE, "-g")
    assert "frame=none" in "\n".join(function_text(base, "_sink"))
    text, _ = mac(tmp_path, SOURCE, "-g", "--optnone=sink")
    body = "\n".join(function_text(text, "_sink"))
    assert "frame=ix" in body
    assert "\tpush\tIX\n\tld\tIX,0\n\tadd\tIX,SP" in body
    assert "\tpop\tIX\n\tret" in body


def test_keeps_call_and_ret(tmp_path):
    src = ("int g;\nint h(int x) { g = x; return x; }\n"
           "int f(int x) { return h(x); }\n"
           "int main(void) { return f(1) - 1; }\n")
    base, _ = mac(tmp_path, src, "--no-inlining")
    assert "jp\t_h" in "\n".join(function_text(base, "_f"))
    text, _ = mac(tmp_path, src, "--no-inlining", "--optnone=f")
    body = "\n".join(function_text(text, "_f"))
    assert "call\t_h" in body and "jp\t_h" not in body
    # the call is followed by the epilogue of the function
    assert re.search(r"call\t_h\n\s*jp\t@f_ret\n@f_ret:", body)


def test_no_loop_unroll_no_dead_store(tmp_path):
    src = ("int g, v[3];\n"
           "int f(void)\n{\n    int i;\n    for (i = 0; i < 3; i++)\n"
           "        v[i] = i;\n    g = 1;\n    g = 2;\n    return g;\n}\n"
           "int main(void) { return f() - 2; }\n")
    base, _ = mac(tmp_path, src, "--no-inlining")
    body = "\n".join(function_text(base, "_f"))
    assert "@FOR" not in body                      # unrolled
    assert body.count("(_g)") < 2                   # dead store removed
    text, _ = mac(tmp_path, src, "--no-inlining", "--optnone=f")
    body = "\n".join(function_text(text, "_f"))
    assert "@FOR" in body
    assert body.count("(_g)") >= 2                  # both stores


def test_no_register_locals_and_no_shared_storage(tmp_path):
    src = ("int f(int n)\n{\n    int a = 0;\n    int i;\n"
           "    for (i = 0; i < n; i++)\n        a += i;\n    return a;\n}\n"
           "int main(void) { return f(3) - 3; }\n")
    base, _ = mac(tmp_path, src, "--no-inlining")
    assert "??AUTO" in base or "\tBC" in base or ",BC" in base
    text, _ = mac(tmp_path, src, "--no-inlining", "--optnone=f")
    body = "\n".join(function_text(text, "_f"))
    assert "??AUTO" not in body
    assert "(IX-" in body and "(IX+4)" in body
    assert not re.search(r"\bBC\b", body)


def test_text_of_the_region_is_the_text_the_generator_wrote(tmp_path):
    """The text passes do not change the region.  The region is the same
    with the passes that can be turned off, off."""
    text, _ = mac(tmp_path, SOURCE, "--optnone=work")
    plain, _ = mac(tmp_path, SOURCE, "--optnone=work", "--no-optimize",
                   "--no-loop-rotate", "--no-flow-opt", "--no-asm-dce")
    a, b = regions(text)["_work"], regions(plain)["_work"]
    norm = lambda ls: [re.sub(r"@(\w+?)\d+", r"@\1", x) for x in ls]
    assert norm(a) == norm(b)


def test_other_functions_are_still_optimized(tmp_path):
    base, _ = mac(tmp_path, SOURCE)
    text, _ = mac(tmp_path, SOURCE, "--optnone=main")
    assert function_text(base, "_work") == function_text(text, "_work")
    assert function_text(base, "_sink") == function_text(text, "_sink")


# ---- D2: a program without optnone is the same ---------------------------

def test_no_optnone_no_marker(tmp_path):
    text, _ = mac(tmp_path, SOURCE)
    assert "#noopt" not in text


@needs_tools
def test_g_does_not_change_the_code_under_optnone(tmp_path):
    for flags in (["--optnone=work"], ["--optnone=all"],
                  ["--optnone=main,sink"]):
        out = {}
        for g in (False, True):
            d = tmp_path / ("g" if g else "n") / "_".join(flags).replace(
                "=", "_").replace(",", "_")
            d.mkdir(parents=True)
            (d / "p.c").write_text(SOURCE)
            r = compile_c("p.c", d / "p.mac", *(["-g"] if g else []), *flags,
                          cwd=d)
            assert r.returncode == 0, r.stderr
            com, _dbg = build(d / "p.mac", g)
            out[g] = com.read_bytes()
        assert out[True] == out[False], flags


# ---- step points: prog.dbg ------------------------------------------------

STEPS = """\
int g1;
static int add(int a, int b) { return a + b; }
#pragma uc80 optnone
int work(int n)
{
    int s = 0;
    int i;
    for (i = 0; i < n; i++)
        s = add(s, i);
    g1 = s;
    s = s + 0;
    return add(s, 1);
}
int plain(int n)
{
    int s = 0;
    s = add(s, n);
    return add(s, 2);
}
int main(void)
{
    return work(5) + plain(5) - 22;
}
"""


@needs_tools
def test_prog_dbg_has_a_step_row_for_each_statement(tmp_path):
    (tmp_path / "s.c").write_text(STEPS)
    r = compile_c("s.c", tmp_path / "s.mac", "-g", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    _com, dbg = build(tmp_path / "s.mac", True)
    d = json.loads(dbg.read_text())
    f = next(x for x in d["funcs"] if x["name"] == "_work")
    assert f["frame"] == "ix"
    assert d["noopt"] == [[f["start"], f["end"]]]
    rows = [(r[0], r[1], r[2]) for r in d["lines"]
            if f["start"] <= r[0] < f["end"]]
    steps = {g[1] for _lo, _hi, groups in rows for g in groups if not g[2]}
    # the statements of work: the declaration line, then lines 6, 8 (for),
    # 9, 10, 11, 12, and the closing brace
    assert {4, 6, 8, 9, 10, 11, 12, 13} <= steps
    # every statement has code of its own
    for line in (6, 8, 9, 10, 11, 12):
        assert any(g[1] == line for _lo, _hi, groups in rows
                   for g in groups), line
    # the plain function has no region, and keeps no frame
    p = next(x for x in d["funcs"] if x["name"] == "_plain")
    assert p["frame"] == "none"
    assert not (f["start"] <= p["start"] < f["end"])


@needs_tools
def test_each_statement_of_the_unoptimized_function_has_its_own_bytes(tmp_path):
    """s = s + 0; is a statement with code under optnone."""
    (tmp_path / "s.c").write_text(STEPS)
    r = compile_c("s.c", tmp_path / "s.mac", "-g", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    _com, dbg = build(tmp_path / "s.mac", True)
    d = json.loads(dbg.read_text())
    f = next(x for x in d["funcs"] if x["name"] == "_work")
    sizes = {}
    for lo, hi, groups, *_ in d["lines"]:
        if f["start"] <= lo < f["end"] and len(groups) == 1:
            sizes[groups[0][1]] = sizes.get(groups[0][1], 0) + (hi - lo)
    assert sizes.get(11, 0) > 0


# ---- run ------------------------------------------------------------------

RUN = """\
#include <stdio.h>
int g;
static int sq(int x) { return x * x; }
static int tw(int x) { return x + x; }
int rec(int n) { return n <= 1 ? 1 : n * rec(n - 1); }
long big(long a, long b) { return a * b + 7; }
int sw(int k)
{
    switch (k) {
    case 1: return 10;
    case 2: return 20;
    default: return tw(k);
    }
}
int loopy(int n)
{
    int s = 0, i, j;
    for (i = 0; i < n; i++)
        for (j = 0; j < 3; j++)
            s += sq(i) + j;
    return s;
}
int main(void)
{
    char buf[16];
    sprintf(buf, "%d", loopy(5));
    printf("%s %d %ld %d %d %d\\n", buf, rec(5), big(300L, 200L), sw(1),
           sw(2), sw(7));
    return 0;
}
"""


@skip_without_tools
@pytest.mark.parametrize("flags", [
    ["--optnone=all"], ["--optnone=loopy,rec"], ["--optnone=main"],
    ["--optnone=all", "-g"], ["--optnone=all", "--target=cpm"],
])
def test_program_gives_the_same_output(tmp_path, flags):
    want = build_and_run(tmp_path, RUN)
    assert want.strip() == "105 120 60007 10 20 14"
    got = build_and_run(tmp_path, RUN, args=flags)
    assert got == want


@skip_without_tools
def test_pragma_program_runs(tmp_path):
    src = RUN.replace("int loopy", "#pragma uc80 optnone\nint loopy")
    assert build_and_run(tmp_path, src).strip() == "105 120 60007 10 20 14"


# ---- the passes ------------------------------------------------------------

def test_guard_puts_the_region_back():
    code = ("a\n\t;#noopt begin\n\t; optnone _f\n\tx1\n\tx2\n\t;#noopt end\nb")

    def wreck(text):
        return text.replace("x1", "y1").replace("a", "A").replace("b", "B")
    assert optnone.guard(wreck, code).split("\n")[3] == "\tx1"
    assert optnone.guard(wreck, code).split("\n")[0] == "A"


def test_guard_drops_a_pass_that_loses_a_marker():
    code = "a\n\t;#noopt begin\n\t; optnone _f\n\tx1\n\t;#noopt end\nb"
    assert optnone.guard(lambda t: t.replace("\t;#noopt end\n", ""), code) \
        == code


def test_guard_without_region_is_the_pass():
    assert optnone.guard(lambda t: t.upper(), "abc") == "ABC"


def test_guard_lets_a_pass_remove_a_whole_region():
    code = ("a\n\t;#noopt begin\n\t; optnone _f\n\tx1\n\t;#noopt end\n"
            "\t;#noopt begin\n\t; optnone _g\n\tx2\n\t;#noopt end\nb")

    def drop_f(text):
        return text.replace(
            "\t;#noopt begin\n\t; optnone _f\n\tx1\n\t;#noopt end\n", "")
    out = optnone.guard(drop_f, code)
    assert "_f" not in out and "x2" in out


ASM = """\
\t.z80
\tPUBLIC\t_main
\tCSEG
_main:
\tcall\t_f
\tret
_f:
\t;#noopt begin
\t; optnone _f
\tjp\tL1
\tcall\t_helper
@dead:
\tcall\t_gone
L1:
\tret
\t;#noopt end
_helper:
\tret
_gone:
\tret
_unused:
\tret
\tEND
"""


def test_asm_dce_keeps_the_whole_region():
    out = asm_dce.eliminate_dead_code(ASM)
    # the blocks of f that nothing jumps to stay, and what they call
    assert "@dead:" in out and "_gone:" in out and "_helper:" in out
    assert "_unused:" not in out
    assert "optnone _f" in out and "\t;#noopt end" in out


def test_asm_dce_removes_a_dead_function_with_its_region():
    out = asm_dce.eliminate_dead_code(ASM.replace("\tcall\t_f\n", ""))
    assert "#noopt" not in out and "_f:" not in out


def test_attribute_on_a_local_variable_marks_no_function(tmp_path):
    text, err = mac(tmp_path, SOURCE.replace(
        "int s = 0;", "int s __attribute__((optimize(\"O0\"))) = 0;"))
    assert regions(text) == {}
    assert "optnone attribute is not before a function" in err


def test_attribute_on_a_local_variable_in_a_one_line_function(tmp_path):
    text, err = mac(tmp_path, "int f(int a){ int x __attribute__((optnone)) "
                    "= a; return x; }\nint g(int a){ return a + 1; }\n"
                    "int main(void){ return f(1) + g(1) - 3; }\n")
    assert regions(text) == {}
    assert "optnone attribute is not before a function" in err


def test_guard_warns_when_a_pass_loses_a_marker(capsys):
    code = "\n".join(["_f:", "\t;#noopt begin", "\t; optnone _f", "\tnop",
                      "\t;#noopt end", "\tret"])
    out = optnone.guard(lambda c: c.replace("\t;#noopt end\n", ""), code)
    assert out == code
    assert "pass is dropped" in capsys.readouterr().err
    bad = code.replace("\t;#noopt end\n", "")
    optnone.guard(lambda c: c, bad)
    assert "do not pair" in capsys.readouterr().err
