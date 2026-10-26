"""atexit handlers, exit() and the return from main.

Returning from main used to jump to the warm boot without calling the
atexit handlers and without closing the open files, so the output of a
handler was missing and a file that the program wrote but did not close
was empty.  crt0 now jumps through the word __exit_hook, which atexit()
and fopen() set to the exit code (lc_exit.mac).  atexit takes 32 handlers.
"""

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools

HANDLERS = r"""
#include <stdio.h>
#include <stdlib.h>
static void h1(void) { printf("h1\n"); }
static void h2(void) { printf("h2\n"); atexit(h1); }
static void h3(void) { printf("h3\n"); }
"""


def test_return_from_main_runs_the_handlers_last_first(tmp_path):
    out = build_and_run(tmp_path, HANDLERS + r"""
int main(void)
{
    atexit(h3);
    atexit(h2);
    printf("main\n");
    return 3;
}
""")
    assert out.split("\n")[:4] == ["main", "h2", "h1", "h3"]


def test_exit_runs_the_handlers_the_same_way(tmp_path):
    out = build_and_run(tmp_path, HANDLERS + r"""
static void f(void) { printf("f\n"); exit(2); printf("not reached\n"); }
int main(void)
{
    atexit(h3);
    atexit(h2);
    f();
    return 0;
}
""")
    assert out.split("\n")[:4] == ["f", "h2", "h1", "h3"]


def test_32_handlers_are_accepted_and_the_33rd_is_not(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
static int count;
static void h(void) { count++; }
static void report(void) { printf("ran %d\n", count); }
int main(void)
{
    int i, fail = -1;
    atexit(report);                 /* the first handler runs last */
    for (i = 1; i < 40; i++)
        if (atexit(h) != 0) { fail = i; break; }
    printf("first failure at %d\n", fail);
    return 0;
}
""")
    lines = out.split("\n")
    assert lines[0] == "first failure at 32"
    assert lines[1] == "ran 31"


def test_a_program_without_atexit_still_returns_from_main(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void) { printf("plain\n"); return 0; }
""")
    assert out.split("\n")[0] == "plain"


def test_open_files_are_flushed_at_exit(tmp_path):
    for end in ("return 0;", "exit(0);"):
        build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdlib.h>
int main(void)
{
    FILE *f = fopen("out.txt", "w");
    fputs("written, not closed\n", f);
    %s
}
""" % end)
        data = (tmp_path / "out.txt").read_bytes()
        assert data.startswith(b"written, not closed\n"), (end, data[:40])
        (tmp_path / "out.txt").unlink()


def test_handlers_work_with_separate_compilation(tmp_path):
    import subprocess
    import sys
    from ._clib import CPMEMU, LIB_DIR, REPO
    src = tmp_path / "p.c"
    src.write_text(HANDLERS + 'int main(void){ atexit(h3); atexit(h2);'
                   ' printf("main\\n"); return 0; }\n')
    subprocess.run([sys.executable, "-m", "uc80.main", str(src),
                    "--no-whole-program", "--printf", "int", "-o",
                    str(tmp_path / "p.mac")], check=True,
                   capture_output=True, text=True, cwd=str(REPO))
    subprocess.run(["um80", str(tmp_path / "p.mac"), "-o",
                    str(tmp_path / "p.rel")], check=True, capture_output=True)
    subprocess.run(["ul80", str(LIB_DIR / "crt0.rel"), str(tmp_path / "p.rel"),
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", str(tmp_path / "p.com")], check=True,
                   capture_output=True)
    run = subprocess.run([str(CPMEMU), str(tmp_path / "p.com")],
                         capture_output=True, text=True, timeout=60)
    assert run.stdout.replace("\r", "").split("\n")[:4] == [
        "main", "h2", "h1", "h3"]
