"""Strict mode: uc80 refuses interrupt and fastcall markers.

The uc_core frontend erases these markers in its tolerant mode (the DOS
tolerance of uc386).  On a Z80 the markers mean something (RETI, register
saves, register arguments), so uc80 asks for strict mode and a program that
uses one gets an error instead of a silent miscompile.  See
docs/rom_target.md, Phase 0.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def compile_c(tmp_path, text):
    src = tmp_path / "t.c"
    src.write_text(text)
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", str(src), "-o",
         str(tmp_path / "t.mac")],
        capture_output=True, text=True, cwd=str(REPO))


REFUSED = [
    ("__interrupt", "__interrupt void isr(void) { }\nint main(void) { return 0; }\n"),
    ("interrupt", "interrupt void isr(void) { }\nint main(void) { return 0; }\n"),
    ("__interrupt(1)", "void isr(void) __interrupt(1) { }\nint main(void) { return 0; }\n"),
    ("[[gnu::interrupt]]", "[[gnu::interrupt]] void isr(void) { }\nint main(void) { return 0; }\n"),
    ("__attribute__((interrupt))",
     "void isr(void) __attribute__((interrupt));\n"
     "void isr(void) { }\nint main(void) { return 0; }\n"),
    ("__interrupt", "__critical __interrupt void isr(void) { }\nint main(void) { return 0; }\n"),
    ("__fastcall", "int __fastcall f(int a) { return a; }\nint main(void) { return f(1); }\n"),
]


@pytest.mark.parametrize("name,text", REFUSED,
                         ids=[t[0] + "-" + str(i) for i, t in enumerate(REFUSED)])
def test_marker_is_refused(tmp_path, name, text):
    r = compile_c(tmp_path, text)
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stderr.startswith("uc80: error: "), r.stderr
    assert "t.c:" in r.stderr
    assert "uc80 does not support" in r.stderr
    assert "docs/rom_target.md" in r.stderr
    assert "internal error" not in r.stderr
    assert not (tmp_path / "t.mac").exists()


def test_error_names_file_and_line(tmp_path):
    r = compile_c(tmp_path, "int x;\nint y;\n__interrupt void isr(void) { }\n"
                            "int main(void) { return 0; }\n")
    assert r.returncode == 1
    assert "t.c:3: uc80 does not support '__interrupt'" in r.stderr


def test_identifiers_that_contain_the_words_compile(tmp_path):
    r = compile_c(tmp_path, """
struct interrupt { int level; };
int interrupt_count;
int fastcall_total;
int my_interrupt(struct interrupt *p) { return p->level + interrupt_count; }
int main(void)
{
    struct interrupt i;
    i.level = 2;
    fastcall_total = my_interrupt(&i);
    return fastcall_total;
}
""")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "t.mac").exists()


def test_marker_in_string_is_not_a_marker(tmp_path):
    r = compile_c(tmp_path, 'const char *s = "__interrupt void";\n'
                            'int main(void) { return s[0]; }\n')
    assert r.returncode == 0, r.stderr
