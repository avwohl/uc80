"""The floating-point conversions of scanf are linked only when needed.

They add about 5 KB (the reader and strtod).  The compiler puts a reference
to the module in the program when a scanf call has a float conversion in
its format, when the format is not a string literal, or when a scanf
function is used without a call (scan_hook.py).
"""

import subprocess
import sys

import pytest

from ._clib import CPMEMU as CPMEMU_PATH
from ._clib import LIB_DIR, REPO, build_and_run, skip_without_tools

pytestmark = skip_without_tools


def link_symbols(tmp_path, source):
    c_file = tmp_path / "p.c"
    c_file.write_text(source)
    mac, rel, com, sym = (tmp_path / ("p" + e)
                          for e in (".mac", ".rel", ".com", ".sym"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file), "-o",
                        str(mac)], capture_output=True, text=True,
                       cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True,
                   capture_output=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com), "-S",
                    str(sym)], check=True, capture_output=True)
    return sym.read_text().upper()


@pytest.mark.parametrize("body, with_float", [
    ('sscanf("5", "%d", &i);', False),
    ('sscanf("5 x", "%d %s %c %[a-z] %n", &i, s, s, s, &i);', False),
    ('sscanf("5", "%f", &f);', True),
    ('sscanf("5", "%lf", &d);', True),
    ('sscanf("5", "%*f");', True),
    ('sscanf("5", "%5e", &f);', True),
    ('sscanf("5", fmt, &f);', True),
    ('fscanf(stdin, "%g", &f);', True),
    ('scanf("%d", &i);', False),
    ('scanf("%d %a", &i, &f);', True),
    ('vsscanf("5", "%d", (char *)&i);', False),
    ('{ int (*p)(const char *, const char *, ...) = sscanf; '
     'p("5", "%d", &i); }', True),
])
def test_float_conversions_are_linked_when_needed(tmp_path, body, with_float):
    src = r"""
#include <stdio.h>
int main(int argc, char **argv)
{
    int i; float f; double d; char s[10];
    const char *fmt = argc > 3 ? argv[1] : "%%d";
    %s
    return 0;
}
""" % body
    syms = link_symbols(tmp_path, src)
    assert ("_STRTOD" in syms) == with_float
    assert ("__SC_FLOAT" in syms)           # one of the two modules
    assert "__SCAN_RUN" in syms


def test_float_scanf_still_works_when_linked(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    float f; double d; int i, n;
    n = sscanf("12 2.5 -0.125e1", "%d %f %lf", &i, &f, &d);
    printf("%d %d %d %d\n", n, i, (int)(f * 100), (int)(d * 100));
    return 0;
}
""")
    assert out.split("\n") == ["3 12 250 -125", ""]


A_C = """#include <stdio.h>
int fa(const char *s)
{
    int i = -1;
    sscanf(s, "%d", &i);
    return i;
}
"""
B_C = """#include <stdio.h>
int fa(const char *);
int main(void)
{
    float f = -1;
    int r = sscanf("2.5", "%f", &f);
    printf("%d %d %d\\n", r, (int)(f * 10), fa("77"));
    return 0;
}
"""


@pytest.mark.parametrize("order", ["ab", "ba"])
def test_float_scanf_in_one_of_two_units(tmp_path, order):
    """The unit with the float format gets the reference, the other has none,
    and the link works in either order (separate compilation)."""
    rels = {}
    for name, src in (("a", A_C), ("b", B_C)):
        (tmp_path / (name + ".c")).write_text(src)
        r = subprocess.run([sys.executable, "-m", "uc80.main", "--no-whole-program",
                            str(tmp_path / (name + ".c")), "-o",
                            str(tmp_path / (name + ".mac"))],
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(tmp_path / (name + ".mac")), "-o",
                        str(tmp_path / (name + ".rel"))], check=True,
                       capture_output=True)
        rels[name] = str(tmp_path / (name + ".rel"))
    mac_a = (tmp_path / "a.mac").read_text()
    mac_b = (tmp_path / "b.mac").read_text()
    assert "__sc_float_on" not in mac_a
    assert "__sc_float_on" in mac_b
    com = tmp_path / "p.com"
    subprocess.run(["ul80", str(LIB_DIR / "crt0.rel"), *[rels[c] for c in order],
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", str(com)], check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU_PATH), str(com)], capture_output=True,
                         timeout=60)
    assert "1 25 77" in run.stdout.decode("latin-1")
