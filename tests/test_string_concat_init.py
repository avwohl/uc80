"""Adjacent string literals initialise an object with their bytes.

C 6.4.5p5 concatenates adjacent string literals in translation phase 6,
so `"ab" "cd"` is `"abcd"` before code generation sees it.  The auto-AST
keeps the pieces in a Python list, and the initialiser emitters matched
only a single ast.StringLiteral or a one-element list of them, so a
multi-piece group fell through every branch:

    char s[] = "ab" "cd";   ->   _s: ds 5

Correctly sized and entirely zero, which is what made it look deliberate
rather than dropped -- the program printed an empty string with a clean
exit.  Inside a struct initialiser the pieces were worse than dropped:
they reached _emit_array_init_flat and were spread across the following
members as though each piece were a separate initialiser, so a `char *`
member after a char array got a string's bytes where its pointer
belonged.

The single-literal spelling always worked, which is why this survived.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LIB_DIR = REPO / "src" / "uc80" / "lib"
CPMEMU = REPO.parent / "cpmemu" / "src" / "cpmemu"


def compile_source(tmp_path, source, name="prog"):
    c_file = tmp_path / (name + ".c")
    c_file.write_text(source)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return mac.read_text()


def storage_after(mac_text, label):
    out, seen = [], False
    for line in mac_text.split("\n"):
        if line.startswith(label + ":"):
            seen = True
            continue
        if not seen:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        if not line[0].isspace():
            break
        if stripped.split("\t")[0].lower() not in ("ds", "dw", "db"):
            break
        out.append(stripped)
    return out


class TestEmittedBytes:
    def test_global_char_array(self, tmp_path):
        mac = compile_source(tmp_path, 'char s[] = "ab" "cd";\n'
                                       'int main(void){ return s[0]; }\n')
        assert storage_after(mac, "_s") == ["db\t'abcd',0"]

    def test_single_literal_is_unchanged(self, tmp_path):
        mac = compile_source(tmp_path, 'char s[] = "abcd";\n'
                                       'int main(void){ return s[0]; }\n')
        assert storage_after(mac, "_s") == ["db\t'abcd',0"]

    def test_three_pieces(self, tmp_path):
        mac = compile_source(tmp_path, 'char s[] = "a" "b" "c";\n'
                                       'int main(void){ return s[0]; }\n')
        assert storage_after(mac, "_s") == ["db\t'abc',0"]

    def test_padded_to_a_declared_size(self, tmp_path):
        mac = compile_source(tmp_path, 'char s[10] = "ab" "cd";\n'
                                       'int main(void){ return s[0]; }\n')
        assert storage_after(mac, "_s") == ["db\t'abcd',0", "ds\t5"]

    def test_composited_with_an_extern(self, tmp_path):
        mac = compile_source(tmp_path, 'extern char s[10];\n'
                                       'char s[] = "xy" "z";\n'
                                       'int main(void){ return s[0]; }\n')
        assert storage_after(mac, "_s") == ["db\t'xyz',0", "ds\t6"]

    def test_escapes_survive_the_join(self, tmp_path):
        mac = compile_source(tmp_path, 'char s[] = "a\\tb" "\\n";\n'
                                       'int main(void){ return s[0]; }\n')
        # The join must happen on the decoded bytes, not the source text.
        assert storage_after(mac, "_s") == ["db\t'a',009H,'b',00AH,'',0"]

    def test_struct_pointer_member_after_a_char_array(self, tmp_path):
        """The pieces used to be spread across the following members."""
        mac = compile_source(tmp_path,
                             'struct { char f[8]; char *g; } st = '
                             '{ "aa" "bb", "cc" "dd" };\n'
                             'int main(void){ return st.f[0]; }\n')
        storage = storage_after(mac, "_st")
        assert storage[0] == "db\t'aabb',0"
        assert storage[1] == "ds\t3"
        assert storage[2].startswith("dw\t@STR")


def _toolchain_available():
    return (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
            and (LIB_DIR / "libc.lib").exists()
            and (LIB_DIR / "runtime.lib").exists())


@pytest.mark.skipif(not _toolchain_available(),
                    reason="um80/ul80/cpmemu or built libraries not available")
class TestOnEmulator:
    def build_and_run(self, tmp_path, source):
        c_file = tmp_path / "prog.c"
        c_file.write_text(source)
        mac, rel, com = (tmp_path / ("prog" + e)
                         for e in (".mac", ".rel", ".com"))
        r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                            "-o", str(mac)], capture_output=True, text=True,
                           cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(mac), "-o", str(rel)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                       check=True, capture_output=True, text=True)
        env = dict(os.environ, PYTHONHASHSEED="0")
        run = subprocess.run([str(CPMEMU), str(com)], capture_output=True,
                             timeout=60, env=env)
        return run.stdout.decode("latin-1").replace("\r", "")

    def test_every_shape_carries_its_bytes(self, tmp_path):
        out = self.build_and_run(tmp_path, """
#include <stdio.h>
char s[] = "ab" "cd";
extern char u[10];
char u[] = "xy" "z";
char *p = "pq" "rs";
struct { char f[8]; char *g; } st = { "aa" "bb", "cc" "dd" };
int main(void){
  char loc[] = "lo" "cal";
  printf("s=[%s]%u u=[%s]%u p=[%s]\\n",
         s, (unsigned)sizeof(s), u, (unsigned)sizeof(u), p);
  printf("st=[%s][%s] loc=[%s]%u\\n",
         st.f, st.g, loc, (unsigned)sizeof(loc));
  return 0;
}
""")
        assert out == ("s=[abcd]5 u=[xyz]10 p=[pqrs]\n"
                       "st=[aabb][ccdd] loc=[local]6\n")
