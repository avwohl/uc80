"""vsnprintf, _Exit, quick_exit, TAB on the console, file modes, file limit.

- vsnprintf shares the printf engine with snprintf: the return value is the
  length that the full output would have, n == 0 writes nothing, and the
  buffer always ends with a NUL when n > 0.
- _Exit returns to CP/M at once.  quick_exit runs only the at_quick_exit
  functions.
- The emulator cpmemu drops the byte 09H, as a terminal that does not move
  the cursor.  A real CP/M BDOS makes spaces of a TAB, so __conout does it.
- fopen has the modes r, w, a, r+, w+ and a+.  A random test compares a
  stream in each mode with a model of the C rules (the model is below).
- Eight files can be open at the same time.
"""

import random
import re
import subprocess
import sys

import pytest

from ._clib import LIB_DIR, REPO, build_and_run, skip_without_tools

pytestmark = skip_without_tools


# ---- vsnprintf and snprintf -------------------------------------------

VS = r"""
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
static int vs(char *b, size_t n, const char *f, ...)
{
    va_list ap; int r;
    va_start(ap, f); r = vsnprintf(b, n, f, ap); va_end(ap);
    return r;
}
int main(void)
{
    char b[20]; int r;
    memset(b, 'Z', 20);
    r = vs(b, 20, "%d-%s-%c", 123, "abc", 'x');
    printf("A %d [%s] %c\n", r, b, b[10]);
    memset(b, 'Z', 20);
    r = vs(b, 5, "%d-%s", 12345, "abcdef");
    printf("B %d [%s] %c\n", r, b, b[5]);
    memset(b, 'Z', 20);
    r = vs(b, 0, "%d", 99);
    printf("C %d [%c%c]\n", r, b[0], b[1]);
    memset(b, 'Z', 20);
    r = vs(b, 1, "%d", 99);
    printf("D %d [%d] %c\n", r, b[0], b[1]);
    memset(b, 'Z', 20);
    r = vs(b, 3, "%d", 99);
    printf("E %d [%s] %d\n", r, b, b[3]);
    memset(b, 'Z', 20);
    r = vs(b, 4, "%d", 99);
    printf("F %d [%s] %c\n", r, b, b[3]);
    r = vs(b, 20, "%5.2f|%-6s|%x|%ld", 3.14159, "ab", 255, 70000L);
    printf("G %d [%s]\n", r, b);
    r = vs(NULL, 0, "hello %d", 7);
    printf("H %d\n", r);
    memset(b, 'Z', 20);
    r = snprintf(b, 6, "%s", "abcdefghij");
    printf("I %d [%s] %c\n", r, b, b[6]);
    memset(b, 'Z', 20);
    r = snprintf(b, 0, "%s", "abc");
    printf("J %d %c\n", r, b[0]);
    r = snprintf(b, 10, "%s", "");
    printf("K %d [%s]\n", r, b);
    memset(b, 'Z', 20);
    r = snprintf(b, 4, "%s", "abc");
    printf("L %d [%s] %c\n", r, b, b[4]);
    r = snprintf(NULL, 0, "%d:%d", 1, 22);
    printf("M %d\n", r);
    return 0;
}
"""


def test_vsnprintf_and_snprintf_truncate_and_count(tmp_path):
    out = build_and_run(tmp_path, VS).split("\n")
    assert out[:13] == [
        "A 9 [123-abc-x] Z",
        "B 12 [1234] Z",
        "C 2 [ZZ]",
        "D 2 [0] Z",
        "E 2 [99] 90",
        "F 2 [99] Z",
        "G 21 [ 3.14|ab    |ff|700]",
        "H 7",
        "I 10 [abcde] Z",
        "J 3 Z",
        "K 0 []",
        "L 3 [abc] Z",
        "M 4",
    ]


# ---- _Exit and quick_exit ---------------------------------------------

def test_exit_functions(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
static void a1(void) { printf("atexit\n"); }
static void q1(void) { printf("q1\n"); }
static void q2(void) { printf("q2\n"); at_quick_exit(q1); }
int main(void)
{
    int i, r = 0;
    atexit(a1);
    for (i = 0; i < 31; i++) at_quick_exit(q1);
    at_quick_exit(q2);                  /* the 32nd */
    r = at_quick_exit(q1);              /* the 33rd */
    printf("33rd %d\n", r);
    quick_exit(0);
    return 0;
}
""")
    lines = out.split("\n")
    assert lines[0] == "33rd 1"
    assert lines[1] == "q2"                   # registered last, runs first
    assert lines[2:2 + 32] == ["q1"] * 32     # its own q1 and the 31 before
    assert "atexit" not in out


def test_exit_unwritten_file_is_not_flushed(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
static void h(void) { printf("handler\n"); }
int main(void)
{
    FILE *f = fopen("ex.txt", "w");
    atexit(h);
    fputs("data", f);
    printf("main\n");
    _Exit(0);
}
""")
    assert out.split("\n")[0] == "main"
    assert "handler" not in out
    assert (tmp_path / "ex.txt").stat().st_size == 0


# ---- TAB and the other control characters -----------------------------

def test_tab_makes_spaces_to_the_next_tab_stop(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    putchar('\t'); puts("a");
    printf("ab\tc\n");
    fputs("1234567\td\n", stdout);
    fputs("12345678\te\n", stdout);
    putchar('x'); putchar('\b'); putchar('\t'); putchar('f'); putchar('\n');
    printf("q\r\tg\n");
    printf("%d\t%d\n", 1, 2);
    fputc('\t', stdout); fputc('h', stdout); fputc('\n', stdout);
    return 0;
}
""", raw=True)
    assert out.split("\r\n")[:8] == [
        "        a",
        "ab      c",
        "1234567 d",
        "12345678        e",
        "x" + "\b" + " " * 8 + "f",
        "q\r        g",
        "1       2",
        "        h",
    ]


def test_every_console_route_sends_the_same_bytes(tmp_path):
    # The characters 1 to 31 and 127 go through putchar, puts, printf and
    # fputs.  Each route must send the same bytes: all go through __conout.
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    int c;
    char s[3]; char fmt[4];
    for (c = 1; c < 32; c++) {
        if (c == 10 || c == 27 || c == 26) continue;   /* LF, ESC, ^Z */
        s[0] = (char)c; s[1] = 0;
        fmt[0] = (char)c; fmt[1] = 0;
        putchar('<'); putchar(c); putchar('>'); putchar('|');
        printf("<%c>|", c);
        printf("<"); printf(s); printf(">|");
        fputs("<", stdout); fputs(s, stdout); fputs(">|", stdout);
        fputc('<', stdout); fputc(c, stdout); fputc('>', stdout);
        putchar('\n');
    }
    putchar(127); putchar('\n');
    printf("%c\n", 127);
    fputs("\x7f\n", stdout);
    return 0;
}
""", raw=True)
    lines = out.split("\r\n")
    tab_line = lines[8]                      # the character 9
    for line in lines[:28]:
        if line is tab_line:
            continue
        # five routes, each ends with '|' except the last
        routes = line.split("|")
        assert len(routes) == 5, repr(line)
        assert len(set(routes)) == 1, repr(line)
    # TAB is the only one of them that is not sent as it is.  It becomes
    # spaces up to the next tab stop, so the width depends on the column.
    assert tab_line == ("<" + " " * 7 + ">|" + ("<" + " " * 5 + ">|") * 3
                        + "<" + " " * 5 + ">")
    assert lines[28:31] == ["\x7f", "\x7f", "\x7f"]


# ---- file modes -------------------------------------------------------

def test_modes_append_and_update(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
static void dump(const char *name)
{
    FILE *f = fopen(name, "r"); int c; long n = 0;
    if (!f) { printf("cannot open %s\n", name); return; }
    printf("%s:", name);
    while ((c = fgetc(f)) != EOF) { putchar(c == '\n' ? '|' : c); n++; }
    printf(" (%ld)\n", n);
    fclose(f);
}
int main(void)
{
    FILE *f; int i, c;
    f = fopen("ap.txt", "w"); fputs("one\n", f); fclose(f);
    f = fopen("ap.txt", "a"); fputs("two\n", f); fclose(f);
    dump("ap.txt");
    f = fopen("ap.txt", "a"); fseek(f, 0L, SEEK_SET);
    fputs("three\n", f); fclose(f);
    dump("ap.txt");
    remove("new.txt");
    f = fopen("new.txt", "a"); fputs("x", f); fclose(f);
    dump("new.txt");
    f = fopen("rp.txt", "w"); fputs("hello world\n", f); fclose(f);
    f = fopen("rp.txt", "r+"); c = fgetc(f); fseek(f, 0L, SEEK_SET);
    fputs("J", f); fflush(f); printf("%c%c\n", c, fgetc(f)); fclose(f);
    dump("rp.txt");
    f = fopen("wp.txt", "w+"); fputs("abc\ndef\n", f); rewind(f);
    while ((c = fgetc(f)) != EOF) putchar(c == '\n' ? '|' : c);
    fputs("ghi\n", f); fclose(f); printf("\n");
    dump("wp.txt");
    f = fopen("wp.txt", "a+"); fputs("jkl\n", f); rewind(f);
    for (i = 0; i < 3; i++) putchar(fgetc(f));
    fputs("mno\n", f); fclose(f); printf("\n");
    dump("wp.txt");
    f = fopen("wp.txt", "r"); printf("fputc on r: %d\n", fputc('x', f));
    fclose(f);
    f = fopen("wp.txt", "w"); printf("fgetc on w: %d\n", fgetc(f));
    fclose(f);
    f = fopen("nothere.txt", "r+"); printf("r+ missing: %d\n", f == 0);
    f = tmpfile(); fputs("tmp", f); rewind(f);
    printf("tmpfile: %c\n", fgetc(f));
    return 0;
}
""").split("\n")
    assert out[:13] == [
        "ap.txt:one|two| (8)",
        "ap.txt:one|two|three| (14)",
        "new.txt:x (1)",
        "he",
        "rp.txt:Jello world| (12)",
        "abc|def|",
        "wp.txt:abc|def|ghi| (12)",
        "abc",
        "wp.txt:abc|def|ghi|jkl|mno| (20)",
        "fputc on r: -1",
        "fgetc on w: -1",
        "r+ missing: 1",
        "tmpfile: t",
    ]


def test_update_keeps_the_rest_of_the_file(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f; int i, c, bad = 0;
    f = fopen("long.txt", "w");
    for (i = 0; i < 300; i++) fputc('a' + i % 26, f);
    fclose(f);
    f = fopen("long.txt", "r+");
    fputs("XY", f);                       /* the start of record 0 */
    fseek(f, 126L, SEEK_SET); fputs("12345", f);   /* across a record end */
    fclose(f);
    f = fopen("long.txt", "w+");          /* w+ starts empty */
    printf("w+ empty: %d\n", fgetc(f));
    fclose(f);
    f = fopen("long.txt", "w");
    for (i = 0; i < 300; i++) fputc('a' + i % 26, f);
    fseek(f, 120L, SEEK_SET); fputs("0123456789", f);  /* "w" after a seek */
    fclose(f);
    f = fopen("long.txt", "r");
    for (i = 0; i < 300; i++) {
        c = fgetc(f);
        if (i >= 120 && i < 130) c = (c == '0' + i - 120) ? 'a' + i % 26 : '!';
        if (c != 'a' + i % 26) bad++;
    }
    fclose(f);
    printf("bad %d\n", bad);
    return 0;
}
""").split("\n")
    assert out[:2] == ["w+ empty: -1", "bad 0"]


# ---- random comparison with a model of the C rules --------------------

def gen_program(mode, seed, nops=60):
    """C source for a random series of operations on one stream, and the
    lines that the program must print.  A file is a list of bytes; a read
    stops at the end; a write in "a" mode goes to the end.  The characters
    are letters, so the file has no line ends and no Ctrl-Z."""
    rnd = random.Random(seed)
    can_r = mode[0] == "r" or "+" in mode
    can_w = mode[0] in "wa" or "+" in mode
    app = mode[0] == "a"
    init = b""
    if mode[0] in "ra":
        size = rnd.choice([0, 1, 50, 127, 128, 129, 256, 300, 400])
        init = bytes(97 + (i * 7 + seed) % 26 for i in range(size))
    data = bytearray(init)
    pos = 0                 # "a" starts at the start; a write goes to the end
    lines = ['FILE *f; int c; long t;',
             'f = fopen("z.dat", "%s");' % mode,
             'if (!f) { printf("open failed\\n"); return 1; }']
    exp = []
    count = 0
    for _ in range(nops):
        ops = ["tell", "seek"]
        if can_r:
            ops += ["read"] * 3
        if can_w:
            ops += ["write"] * 3
        if can_w and can_r:
            ops += ["flush", "rewind"]
        op = rnd.choice(ops)
        if op == "tell":
            lines.append('t = ftell(f); printf("t%ld\\n", t);')
            exp.append("t%d" % pos)
        elif op == "seek":
            how = rnd.choice(["set", "cur", "end"])
            npos = rnd.randint(0, len(data))
            off = {"set": npos, "cur": npos - pos, "end": npos - len(data)}[how]
            lines.append('c = fseek(f, %dL, %d); printf("s%%d\\n", c);'
                         % (off, ["set", "cur", "end"].index(how)))
            pos = npos
            exp.append("s0")
        elif op == "read":
            n = rnd.choice([1, 2, 5, 60, 130, 200])
            lines.append('{ int i; printf("r"); for (i = 0; i < %d; i++) '
                         '{ c = fgetc(f); if (c < 0) { printf("$"); break; } '
                         'putchar(c); } printf("\\n"); }' % n)
            got = bytes(data[pos:pos + n])
            pos += len(got)
            exp.append("r" + got.decode() + ("$" if len(got) < n else ""))
        elif op == "write":
            n = rnd.choice([1, 2, 5, 60, 130, 200])
            chars = bytes(65 + (count + i) % 26 for i in range(n))
            lines.append('{ int i; for (i = 0; i < %d; i++) '
                         'fputc(65 + (%d + i) %% 26, f); }' % (n, count))
            count += n
            if app:
                data += chars
                pos = len(data)
            else:
                data[pos:pos + n] = chars
                pos += n
        elif op == "flush":
            lines.append('c = fflush(f); printf("f%d\\n", c);')
            exp.append("f0")
        else:
            lines.append('rewind(f);')
            pos = 0
    lines.append('fclose(f);')
    lines.append('f = fopen("z.dat", "r"); printf("F"); '
                 'while ((c = fgetc(f)) != EOF) putchar(c); '
                 'printf("\\n"); fclose(f);')
    exp.append("F" + data.decode())
    src = ('#include <stdio.h>\nint main(void)\n{\n' + "\n".join(lines)
           + '\nreturn 0;\n}\n')
    return init, src, exp


@pytest.mark.parametrize("mode", ["w", "r", "a", "r+", "w+", "a+"])
@pytest.mark.parametrize("seed", [1, 2])
def test_random_operations_match_the_model(tmp_path, mode, seed):
    init, src, exp = gen_program(mode, seed)
    (tmp_path / "z.dat").write_bytes(init)
    out = build_and_run(tmp_path, src).split("\n")
    assert out[:len(exp)] == exp


def test_eight_files_can_be_open(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f[12]; int i, n = 0, ok = 0, c; char name[16];
    for (i = 0; i < 12; i++) {
        sprintf(name, "mf%d.txt", i);
        f[i] = fopen(name, "w");
        if (!f[i]) break;
        n++;
    }
    printf("opened %d\n", n);
    for (i = 0; i < n; i++) fputc('a' + i, f[i]);
    fclose(f[3]);
    f[3] = fopen("mf3.txt", "w");          /* a closed slot is free again */
    printf("reopen %d\n", f[3] != 0);
    fputc('d', f[3]);
    for (i = 0; i < n; i++) fclose(f[i]);
    for (i = 0; i < n; i++) {
        sprintf(name, "mf%d.txt", i);
        f[i] = fopen(name, "r");
        c = fgetc(f[i]);
        if (c == 'a' + i) ok++;
        fclose(f[i]);
    }
    printf("contents %d\n", ok);
    return 0;
}
""").split("\n")
    assert out[:3] == ["opened 8", "reopen 1", "contents 8"]


def test_file_limit_can_be_set_when_the_library_is_built(monkeypatch):
    from uc80.lib import build_libs
    path = build_libs.LC_DIR + "/lc_fslots.mac"
    monkeypatch.delenv("UC80_MAX_FILES", raising=False)
    assert "MAX_FILES\tEQU\t8" in build_libs.module_text(path)
    monkeypatch.setenv("UC80_MAX_FILES", "3")
    text = build_libs.module_text(path)
    assert "MAX_FILES\tEQU\t3" in text and "EQU\t8" not in text
    monkeypatch.setenv("UC80_MAX_FILES", "0")
    with pytest.raises(SystemExit):
        build_libs.module_text(path)


# ---- the short file switch (file_hook.py) -----------------------------

def link_symbols(tmp_path, source, args=()):
    """Compile and link SOURCE; return {symbol name: address}."""
    c_file = tmp_path / "prog.c"
    c_file.write_text(source)
    mac, rel, com, sym = (tmp_path / ("prog" + e)
                          for e in (".mac", ".rel", ".com", ".sym"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file),
                        "-o", str(mac), *args], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True,
                   capture_output=True, text=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com),
                    "-S", str(sym)], check=True, capture_output=True,
                   text=True)
    return {name: int(addr, 16) for addr, name in
            re.findall(r"^([0-9A-F]{4}) (\S+)", sym.read_text(), re.M)}


def prog(body):
    return "#include <stdio.h>\nint main(int argc, char **argv)\n{\n" + body + "\nreturn 0;\n}\n"


@pytest.mark.parametrize("body", [
    'FILE *f = fopen("a", "r"); fgetc(f); fclose(f);',
    'FILE *f = fopen("a", "wb"); fputc(1, f); fclose(f);',
    'FILE *f = fopen("a", "r"); FILE *g = fopen("b", "w"); fputc(fgetc(f), g);',
    'fputs("x", stdout); fprintf(stderr, "%d", argc);',
])
def test_plain_modes_link_the_short_switch(tmp_path, body):
    sym = link_symbols(tmp_path, prog(body))
    assert "__FIO_LITE" in sym


@pytest.mark.parametrize("body", [
    'FILE *f = fopen("a", "r+"); fgetc(f); fputc(1, f);',
    'FILE *f = fopen("a", "w+"); fputc(1, f);',
    'FILE *f = fopen("a", "a"); fputc(1, f);',
    'FILE *f = fopen("a", "ab"); fputc(1, f);',
    'FILE *f = fopen("a", argv[1]); fputc(1, f);',
    'FILE *f = tmpfile(); fputc(1, f);',
    'FILE *f = freopen("a", "a+", stdout); fputc(1, f);',
    'FILE *(*p)(const char *, const char *) = fopen; FILE *f = p("a", "r");'
    ' fputc(1, f);',
])
def test_update_modes_link_the_full_switch(tmp_path, body):
    sym = link_symbols(tmp_path, prog(body))
    assert "__FIO_LITE" not in sym


def test_separate_compilation_links_the_full_switch(tmp_path):
    sym = link_symbols(
        tmp_path, prog('FILE *f = fopen("a", "r"); fgetc(f);'),
        args=("--no-whole-program",))
    assert "__FIO_LITE" not in sym


def test_short_switch_is_smaller(tmp_path):
    lite = link_symbols(tmp_path, prog('FILE *f = fopen("a", "w"); fputc(1, f);'))
    full = link_symbols(tmp_path, prog('FILE *f = fopen("a", "w+"); fputc(1, f);'))
    assert full["__BSS_START"] - lite["__BSS_START"] > 150


def test_short_switch_keeps_the_rules_of_r_and_w(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f = fopen("lite.txt", "w");
    printf("fgetc on w: %d\n", fgetc(f));
    fputs("abc", f); fclose(f);
    printf("fputc on closed: %d\n", fputc('x', f));
    f = fopen("lite.txt", "r");
    printf("fputc on r: %d\n", fputc('x', f));
    printf("fgetc on r: %c\n", fgetc(f));
    fclose(f);
    return 0;
}
""").split("\n")
    assert out[:4] == ["fgetc on w: -1", "fputc on closed: -1",
                       "fputc on r: -1", "fgetc on r: a"]


# ---- freopen and a gap made by fseek ------------------------------------

def test_freopen_reuses_the_stream(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f = fopen("s.txt", "w"), *g; int c;
    fputs("one", f);
    g = freopen("s.txt", "a", f);
    printf("same %d\n", g == f);
    fputs("two", g);
    g = freopen("s.txt", "r", f);
    printf("same %d\n", g == f);
    while ((c = fgetc(g)) != EOF) putchar(c);
    putchar('\n');
    g = freopen("s.txt", "w", f);
    fputs("new", g);
    fclose(g);
    f = fopen("nothere.txt", "r");
    g = fopen("s.txt", "r");
    g = freopen("nothere.txt", "r", g);
    printf("missing %d\n", g == NULL);
    g = freopen("s.txt", "w", stdout);
    printf("stdout %d\n", g == NULL);
    return 0;
}
""").split("\n")
    assert [x for x in out if x] == [
        "same 1", "same 1", "onetwo", "missing 1", "stdout 1"]
    assert (tmp_path / "s.txt").read_bytes().rstrip(b"\x1a") == b"new"


def test_fseek_past_the_end_leaves_zeros(tmp_path):
    build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f;
    f = fopen("a.txt", "w"); fputs("12345", f);
    fseek(f, 10L, SEEK_SET); fputc('z', f); fclose(f);
    f = fopen("b.txt", "w+"); fputs("12345", f);
    fseek(f, 200L, SEEK_SET); fputc('y', f); fclose(f);
    f = fopen("c.txt", "w"); fputs("abc", f); fclose(f);
    f = fopen("c.txt", "r+"); fseek(f, 7L, SEEK_SET); fputc('x', f);
    fclose(f);
    return 0;
}
""")
    a = (tmp_path / "a.txt").read_bytes()
    assert a[:11] == b"12345" + b"\0" * 5 + b"z" and set(a[11:]) <= {0x1A}
    b = (tmp_path / "b.txt").read_bytes()
    assert b[:5] == b"12345" and set(b[5:200]) == {0}
    assert b[200:201] == b"y" and set(b[201:]) <= {0x1A}
    c = (tmp_path / "c.txt").read_bytes()
    assert c[:8] == b"abc" + b"\0" * 4 + b"x" and set(c[8:]) <= {0x1A}
