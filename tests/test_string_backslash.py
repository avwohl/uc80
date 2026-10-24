"""A backslash in a string literal reaches the assembly once.

The string decoder turns "\\\\" into one backslash.  The assembly string
writer used to read that backslash as another escape and dropped it or
the character after it, so "a\\\\b" came out as "ab".
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


def test_backslash_kept_in_data(tmp_path):
    text = compile_source(tmp_path, r'''
char g[] = "p\\q";
const char *h = "r\\\\s\\n";
int main(void) { return g[1] + h[1]; }
''')
    assert "'p\\q'" in text
    assert "'r\\\\s\\n'" in text
