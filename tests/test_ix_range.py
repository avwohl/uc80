"""The IX range of a function (ixrange.py, phase 6 of the debugger plan).

``uc80 -g`` writes ``ixbegin=`` and ``ixend=`` on the ``;#func`` marker of a
function that keeps its frame in IX, and the two labels, after the last text
pass.  A label stops the peephole patterns, so the labels must not change the
code (D2): the tests compare the ``.com`` of a program with and without
``-g``, and check the addresses in ``prog.dbg`` against the bytes."""

import json
import re

import pytest

from uc80.ixrange import add_ix_ranges

from .test_debug_info_g import (TOOLS_OK, build, compile_c, needs_tools,
                                _programs)

SOURCE = """\
int g1;
int sum(int n, int *buf)
{
    int t[3];
    int r;
    t[0] = n;
    if (n <= 0)
        return 0;
    r = sum(n - 1, buf);
    buf[0] = r;
    return r + t[0];
}
static int leaf(int a) { return a + 1; }
int fill(int n, int d)
{
    int x = leaf(n);
    if (d > 0)
        return fill(x, d - 1) + 1;
    return x;
}
int main(void)
{
    int b[2];
    g1 = sum(4, b);
    return fill(1, 3) == 5 ? 0 : 1;
}
"""


def mac(tmp_path, text=SOURCE, *flags):
    (tmp_path / "p.c").write_text(text)
    r = compile_c("p.c", tmp_path / "p.mac", "-g", *flags, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    return (tmp_path / "p.mac").read_text()


def func_records(text):
    """{name: (frame, ixbegin or None, ixend or None)} of the ;#func lines."""
    out = {}
    for m in re.finditer(r"^\s*;#func (\S+) .*?frame=(\w+)(.*)$", text, re.M):
        b = re.search(r"ixbegin=(\S+)", m.group(3))
        e = re.search(r"ixend=(\S+)", m.group(3))
        out[m.group(1)] = (m.group(2), b and b.group(1), e and e.group(1))
    return out


def body_of(text, name):
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if re.match(rf"\s*;#func {name} ", ln))
    j = next(k for k in range(i, len(lines)) if lines[k].strip().startswith(";#endfunc"))
    return lines[i + 1:j]


# ---- the text ------------------------------------------------------------

def test_an_ix_function_has_the_range_labels(tmp_path):
    text = mac(tmp_path)
    rec = func_records(text)
    assert rec["_sum"][0] == "ix" and rec["_fill"][0] == "ix"
    for name in ("_sum", "_fill"):
        _kind, b, e = rec[name]
        assert b and e and b != e
        body = [ln.strip() for ln in body_of(text, name)
                if ln.strip() and not ln.strip().startswith(";")]
        # ixbegin is the line after `add IX,SP`; ixend is in front of the
        # last `pop IX`
        k = body.index(b + ":")
        assert body[k - 1].lower().split() == ["add", "ix,sp"]
        k = body.index(e + ":")
        assert body[k + 1].lower().split() == ["pop", "ix"]
        assert body[k + 2].lower() == "ret"


def test_a_function_with_no_frame_has_no_range(tmp_path):
    text = mac(tmp_path)
    rec = func_records(text)
    assert rec["_main"][0] in ("none", "static")
    assert rec["_main"][1:] == (None, None)
    assert "??IXB" not in "\n".join(body_of(text, "_main"))


def test_the_frame_kind_comes_from_the_final_text(tmp_path):
    # The frame of `leaf` is dropped by the passes: the record says none
    # (or static), not ix.  `main` keeps no IX.
    text = mac(tmp_path)
    kinds = {n: r[0] for n, r in func_records(text).items()}
    for name in ("_leaf", "_main"):
        if name in kinds:
            assert kinds[name] in ("none", "static"), kinds
    for name, body in ((n, "\n".join(body_of(text, n))) for n in kinds):
        has_ix = re.search(r"^\s*push\s+IX\s*$", body, re.M | re.I) is not None
        assert (kinds[name] == "ix") == has_ix, (name, kinds)


def test_without_g_there_is_no_label(tmp_path):
    (tmp_path / "p.c").write_text(SOURCE)
    r = compile_c("p.c", tmp_path / "p.mac", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert "??IX" not in (tmp_path / "p.mac").read_text()


# ---- the function on its own -----------------------------------------------

FRAME = """\
\t;#func _f 1 1 frame=ix pops=0
\tpush\tIX
\tld\tIX,0
\tadd\tIX,SP
\tpush\tHL
%s
\tld\tSP,IX
\tpop\tIX
\tret

\t;#endfunc _f
"""


def test_unit_the_range_goes_around_the_frame():
    out = add_ix_ranges(FRAME % "\tnop")
    assert "ixbegin=??IXB1 ixend=??IXE1" in out
    lines = out.split("\n")
    assert lines[lines.index("??IXB1:") - 1] == "\tadd\tIX,SP"
    assert lines[lines.index("??IXE1:") + 1] == "\tpop\tIX"


def test_unit_a_pointer_use_of_ix_is_not_the_epilogue():
    # IX lent to the code: `push IX ... pop IX` with other code after the
    # pop.  The range ends at the pop IX that has a ret after it.
    body = "\tpush\tIX\n\tpush\tBC\n\tld\tDE,-8\n\tadd\tIX,DE\n\tpop\tDE\n\tld\tL,(IX+0)\n\tpop\tIX\n\tpush\tDE"
    out = add_ix_ranges(FRAME % body)
    lines = out.split("\n")
    assert lines[lines.index("??IXE1:") + 2] == "\tret"


def test_unit_an_early_return_is_in_the_range():
    body = "\tld\tSP,IX\n\tpop\tIX\n\tret\n\tnop"
    out = add_ix_ranges(FRAME % body)
    assert "ixbegin=" in out
    # the label is in front of the last pop IX
    lines = out.split("\n")
    k = lines.index("??IXE1:")
    assert lines[k + 1] == "\tpop\tIX" and lines[k + 2] == "\tret"
    assert lines[k - 1] == "\tld\tSP,IX"


def test_unit_a_function_that_saves_registers_first_has_no_range():
    # an interrupt handler: the saved registers are in front of the saved IX,
    # so the return address is not at IX+2
    text = FRAME.replace("\tpush\tIX\n", "\tpush\tAF\n\tpush\tIX\n", 1) % "\tnop"
    out = add_ix_ranges(text)
    assert "ixbegin" not in out and "??IX" not in out
    assert "frame=ix" in out


def test_unit_a_deleted_frame_changes_the_kind():
    text = "\t;#func _g 1 1 frame=ix pops=0\n\tld\tHL,1\n\tret\n\t;#endfunc _g\n"
    assert "frame=none" in add_ix_ranges(text)
    text = text.replace("ld\tHL,1", "ld\tHL,(??AUTO+2)")
    assert "frame=static" in add_ix_ranges(text)


def test_unit_the_text_without_markers_is_the_same():
    text = "\tpush\tIX\n\tld\tIX,0\n\tadd\tIX,SP\n\tpop\tIX\n\tret\n"
    assert add_ix_ranges(text) == text


def test_unit_only_marker_and_label_lines_change():
    text = FRAME % "\tnop"
    out = add_ix_ranges(text).split("\n")
    kept = [ln for ln in out if not ln.startswith("??IX")]
    assert kept[0].startswith("\t;#func _f 1 1 frame=ix pops=0 ixbegin=")
    assert kept[1:] == text.split("\n")[1:]


# ---- the bytes -------------------------------------------------------------

@needs_tools
def test_prog_dbg_range_is_at_the_frame_bytes(tmp_path):
    mac(tmp_path)
    com, dbg = build(tmp_path / "p.mac", debug=True)
    data = json.loads(dbg.read_text())
    code = com.read_bytes()
    load = data["image"]["load"]

    def byte(a):
        return code[a - load]

    ix_funcs = [f for f in data["funcs"] if f["frame"] == "ix"]
    assert {f["name"] for f in ix_funcs} >= {"_sum", "_fill"}
    for f in ix_funcs:
        b, e = f["ix"]
        # `add ix,sp` is DD 39 in front of ixbegin; `pop ix` (DD E1, then
        # RET) is at ixend
        assert (byte(b - 2), byte(b - 1)) == (0xDD, 0x39), f
        assert (byte(e), byte(e + 1), byte(e + 2)) == (0xDD, 0xE1, 0xC9), f
        assert f["start"] < b < e < f["end"]
        assert f["pops"] == 0


# ---- D2: the labels do not change the code ----------------------------------

@needs_tools
@pytest.mark.parametrize("flags", [(), ("-O0",), ("--no-register-locals",),
                                   ("--no-inlining",), ("--no-licm",)],
                         ids=lambda f: "".join(f) or "default")
def test_the_labels_do_not_change_the_code(flags, tmp_path):
    n, g = tmp_path / "n", tmp_path / "g"
    n.mkdir()
    g.mkdir()
    for d in (n, g):
        (d / "s.c").write_text(SOURCE)
    assert compile_c("s.c", n / "s.mac", *flags, cwd=n).returncode == 0
    assert compile_c("s.c", g / "s.mac", "-g", *flags, cwd=g).returncode == 0
    assert "??IXB" in (g / "s.mac").read_text()
    assert build(n / "s.mac", False)[0].read_bytes() == \
        build(g / "s.mac", True)[0].read_bytes()


def _text_without_debug(text):
    from uc80.markers import is_marker
    return [ln for ln in text.split("\n")
            if not is_marker(ln) and not ln.startswith(("??IX", "??VR"))
            and not re.match(r"\s*;\s*#(var|type)\b", ln) and ln.strip()]


@pytest.mark.parametrize("src", _programs()[:40], ids=lambda p: p.stem)
def test_the_text_is_the_same_but_for_markers_and_range_labels(src, tmp_path):
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
    a = _text_without_debug((n / "p.mac").read_text())
    b = _text_without_debug((g / "p.mac").read_text())
    if a != b:
        # the two known differences of docs/debug_info_g.md: a label name
        # made from a line number, and the time of the build
        a = [re.sub(r"@SG\d+_\d+", "@SG", x) for x in a]
        b = [re.sub(r"@SG\d+_\d+", "@SG", x) for x in b]
    assert a == b
