"""Register locals and the names under an ``&`` (docs/register_locals.md).

``&a[i]``, ``&p->m`` and ``&*p`` only read ``i`` and ``p``: the two names
can live in registers.  ``&x`` takes the address of ``x``: ``x`` stays in
memory.  ``UC80_RL_TAINT_ALL=1`` restores the old rule (every name under
an ``&``), so the test can count the memory accesses of both rules.
"""

import os
import re
import subprocess
import sys

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_struct_return_abi import REPO


def build_run(tmp_path, source, *flags):
    _, mac = compile_mac(tmp_path, source, *flags)
    return run_mac(tmp_path, mac)


TAINT = r"""
#include <stdio.h>
int tab[8];
int sink;
int *gp;
struct N { int v; int w; struct N *next; };
struct N n3 = {30, 3, 0}, n2 = {20, 2, &n3}, n1 = {10, 1, &n2};
static int f(int n)
{
    int i, s = 0;
    for (i = 0; i < n; i++) { gp = &tab[i]; s += *gp + i; }
    return s;
}
static int g(struct N *p)
{
    int t = 0;
    while (p) { gp = &p->w; t += *gp + p->v; p = p->next; }
    return t;
}
static int h(void)
{
    int x = 5, *q = &x;
    *q += 2;
    gp = &x;
    *gp += 1;
    return x + sink;
}
int main(void)
{
    int k;
    for (k = 0; k < 8; k++) tab[k] = k * 3;
    printf("%d\n", f(8));
    printf("%d\n", g(&n1));
    printf("%d\n", h());
    return 0;
}
"""


def _mem_accesses(mac, name):
    s = mac.index(f"\n_{name}:")
    body = mac[s:mac.index(f"@{name}_ret:", s)]
    return len(re.findall(r"\(\?\?AUTO\+\d+\)|\(IX[+-]\d+\)", body))


@needs_run
@pytest.mark.parametrize("flags", [(), ("--no-register-locals",),
                                   ("--no-shared-storage",)])
def test_names_under_address_of_index(tmp_path, flags):
    # &tab[i] reads i, &p->v reads p; &x takes the address of x
    assert build_run(tmp_path, TAINT, *flags).split() == [
        "112", "66", "8"]


def test_index_and_arrow_keep_their_names_in_registers(tmp_path):
    on, _ = compile_mac(tmp_path, TAINT, "--no-inlining")
    env = dict(os.environ, UC80_RL_TAINT_ALL="1")
    c_file = tmp_path / "t.c"
    r = subprocess.run(
        [sys.executable, "-m", "uc80.main", str(c_file), "--no-inlining",
         "-o", str(tmp_path / "old.mac")],
        capture_output=True, text=True, cwd=str(REPO), env=env)
    assert r.returncode == 0, r.stderr
    old = (tmp_path / "old.mac").read_text()
    for name in ("f", "g"):
        assert _mem_accesses(on, name) < _mem_accesses(old, name), name
    # h takes &x: x stays in memory with either rule
    assert _mem_accesses(on, "h") == _mem_accesses(old, "h")
