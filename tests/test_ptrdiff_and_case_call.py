"""Two codegen bugs found by the curses games.

1. ``(p - q) + n``: a pointer difference is an int, so the added integer
   is not scaled by the pointee size.
2. A function and a global whose names differ only in case: the
   assembler is case-insensitive, so one label gets a suffix; the call
   site must use the same label as the definition.
"""

import subprocess
import sys


def compile_source(tmp_path, source):
    c_file = tmp_path / "prog.c"
    c_file.write_text(source)
    mac = tmp_path / "prog.mac"
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return mac.read_text()


def test_ptrdiff_plus_int_is_not_scaled(tmp_path):
    text = compile_source(tmp_path, '''
struct S { int a[55]; };
struct S arr[2];
struct S *pp = arr;
int main(void) { return (pp - arr) + 21; }
''')
    main = text.split("_main:")[1]
    # The 21 is added as it is; it is not multiplied by sizeof(struct S).
    assert "ld\tDE,21\n\tadd\tHL,DE" in main
    assert "2310" not in main


def test_call_uses_case_collision_label(tmp_path):
    text = compile_source(tmp_path, '''
int Score;
void score(int x) { Score = x; }
int main(void) { score(1); return Score; }
''')
    assert "call\t_score_1" in text or "call _score_1" in text
    assert "call\t_score\n" not in text
