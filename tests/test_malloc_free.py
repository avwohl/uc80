"""malloc/calloc/realloc/free (lc_malloc.mac): first-fit free list with coalescing.

free() used to be a no-op on a bump allocator, so a program that allocated
and freed in a loop (the binary-trees benchmark) ran out of memory.

The allocator is deterministic: a free list sorted by address, first fit,
the request carved from the end of the chosen block, merging on free.  The
test drives a random mix of malloc, free and realloc on the Z80 and writes a
checksum of the returned addresses for each block of operations.  A Python
model of the same algorithm makes the same checksums.  The program also fills
every block with a pattern and checks it before the block is freed or
resized, which catches overlapping blocks.
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

pytestmark = pytest.mark.skipif(
    not (shutil.which("um80") and shutil.which("ul80") and CPMEMU.exists()
         and (LIB_DIR / "libc.lib").exists()
         and (LIB_DIR / "runtime.lib").exists()),
    reason="um80/ul80/cpmemu or the built libraries are missing")

SLOTS = 24
BLOCK = 100

C_SOURCE = r"""
#include <stdio.h>
#include <stdlib.h>
static unsigned long s;
static unsigned long rnd(void) { s = s * 1664525UL + 1013904223UL; return s; }
static unsigned char *ptr[SLOTS];
static unsigned size[SLOTS];
static unsigned char tag[SLOTS];
static unsigned char *base;
static int bad;

static void fill(int i) {
    unsigned k;
    for (k = 0; k < size[i]; k++) ptr[i][k] = (unsigned char)(tag[i] + k);
}
static void verify(int i, unsigned n) {
    unsigned k;
    for (k = 0; k < n; k++)
        if (ptr[i][k] != (unsigned char)(tag[i] + k)) { bad++; return; }
}
static unsigned pick(void) {
    unsigned r = (unsigned)(rnd() >> 16);
    unsigned k = r & 15;
    if (k == 15) return 600 + (r >> 4) % 900;
    if (k == 14) return 65000U;          /* never fits */
    if (k >= 9) return 40 + (r >> 4) % 160;
    return 1 + (r >> 4) % 40;
}
int main(void) {
    int b, i, n;
    unsigned long sum;
    unsigned char *p;
    s = SEED;
    base = (unsigned char *)malloc(2);   /* first block: header sits just below */
    free(base);
    base -= 2;
    for (b = 0; b < BLOCKS; b++) {
        sum = 0;
        for (n = 0; n < BLOCK; n++) {
            unsigned long r = rnd();
            int slot = (int)((r >> 16) % SLOTS);
            int act = (int)((r >> 8) & 7);
            unsigned sz;
            if (ptr[slot] == 0) {
                sz = pick();
                p = (unsigned char *)malloc(sz);
                if (p) {
                    ptr[slot] = p; size[slot] = sz; tag[slot] = (unsigned char)r;
                    fill(slot);
                    sum = sum * 31 + (unsigned)(p - base);
                } else {
                    sum = sum * 31 + 1;
                }
            } else if (act < 5) {
                verify(slot, size[slot]);
                free(ptr[slot]);
                ptr[slot] = 0;
                sum = sum * 31 + 2;
            } else {
                sz = pick();
                if (sz == 65000U) sz = 300;
                verify(slot, size[slot]);
                p = (unsigned char *)realloc(ptr[slot], sz);
                if (p) {
                    unsigned keep = size[slot] < sz ? size[slot] : sz;
                    ptr[slot] = p;
                    verify(slot, keep);
                    size[slot] = sz;
                    fill(slot);
                    sum = sum * 31 + (unsigned)(p - base);
                } else {
                    sum = sum * 31 + 3;
                }
            }
        }
        printf("%08lx %d\n", sum, bad);
    }
    return 0;
}
"""


class Model:
    """The allocator's algorithm, on a heap that starts at address 0."""

    def __init__(self):
        self.top = 0
        self.free = []      # [addr, size] sorted by addr
        self.size = {}      # payload address -> payload size

    def malloc(self, n):
        n = max(n, 2)
        for i, (a, sz) in enumerate(self.free):
            if sz < n:
                continue
            if sz - n >= 4:
                left = sz - n - 2
                self.free[i][1] = left
                hdr = a + 2 + left
                self.size[hdr + 2] = n
                return hdr + 2
            del self.free[i]
            self.size[a + 2] = sz
            return a + 2
        if self.top + 2 + n > 0xFFFF - 6000:
            return 0
        hdr = self.top
        self.top += 2 + n
        self.size[hdr + 2] = n
        return hdr + 2

    def free_(self, p):
        if p == 0:
            return
        a = p - 2
        sz = self.size.pop(p)
        i = 0
        while i < len(self.free) and self.free[i][0] < a:
            i += 1
        self.free.insert(i, [a, sz])
        if i + 1 < len(self.free) and a + 2 + self.free[i][1] == self.free[i + 1][0]:
            self.free[i][1] += 2 + self.free[i + 1][1]
            del self.free[i + 1]
        if i > 0 and self.free[i - 1][0] + 2 + self.free[i - 1][1] == self.free[i][0]:
            self.free[i - 1][1] += 2 + self.free[i][1]
            del self.free[i]

    def realloc(self, p, n):
        if self.size[p] >= n:
            return p
        q = self.malloc(n)
        if q == 0:
            return 0
        self.free_(p)
        return q


def model_sums(seed, blocks):
    s = seed
    m = Model()
    first = m.malloc(2)     # the program's probe: malloc(2), free
    m.free_(first)
    base = first - 2

    def rnd():
        nonlocal s
        s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
        return s

    def pick():
        r = rnd() >> 16
        k = r & 15
        if k == 15:
            return 600 + (r >> 4) % 900
        if k == 14:
            return 65000
        if k >= 9:
            return 40 + (r >> 4) % 160
        return 1 + (r >> 4) % 40

    ptr = [0] * SLOTS
    out = []
    for _ in range(blocks):
        total = 0
        for _ in range(BLOCK):
            r = rnd()
            slot = (r >> 16) % SLOTS
            act = (r >> 8) & 7
            if ptr[slot] == 0:
                sz = pick()
                p = m.malloc(sz)
                if p:
                    ptr[slot] = p
                    total = (total * 31 + (p - base)) & 0xFFFFFFFF
                else:
                    total = (total * 31 + 1) & 0xFFFFFFFF
            elif act < 5:
                m.free_(ptr[slot])
                ptr[slot] = 0
                total = (total * 31 + 2) & 0xFFFFFFFF
            else:
                sz = pick()
                if sz == 65000:
                    sz = 300
                p = m.realloc(ptr[slot], sz)
                if p:
                    ptr[slot] = p
                    total = (total * 31 + (p - base)) & 0xFFFFFFFF
                else:
                    total = (total * 31 + 3) & 0xFFFFFFFF
        out.append("%08x 0" % total)
    return out


def build_and_run(tmp_path, source, timeout=300):
    c_file = tmp_path / "m.c"
    c_file.write_text(source)
    mac, rel, com = (tmp_path / ("m" + e) for e in (".mac", ".rel", ".com"))
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(c_file), "-o", str(mac)],
                       capture_output=True, text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(rel)], check=True, capture_output=True)
    subprocess.run(["ul80", str(rel), str(LIB_DIR / "libc.lib"),
                    str(LIB_DIR / "runtime.lib"), "-o", str(com)],
                   check=True, capture_output=True)
    run = subprocess.run([str(CPMEMU), str(com)], capture_output=True, text=True,
                         timeout=timeout, env=dict(os.environ, PYTHONHASHSEED="0"))
    return run.stdout.replace("\r", "")


@pytest.mark.parametrize("seed,blocks", [(1, 20), (777, 20), (424242, 40)])
def test_random_operations_match_the_model(tmp_path, seed, blocks):
    src = ("#define SEED %dUL\n#define BLOCKS %d\n#define BLOCK %d\n#define SLOTS %d\n"
           % (seed, blocks, BLOCK, SLOTS)) + C_SOURCE
    got = build_and_run(tmp_path, src).splitlines()
    want = model_sums(seed, blocks)
    assert [g.split()[1] for g in got] == ["0"] * blocks, "a block lost its contents"
    bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w]
    assert len(got) == len(want)
    assert not bad, "blocks of %d operations that differ: %s" % (BLOCK, bad)


def test_freed_memory_is_reused(tmp_path):
    """Allocate and free far more than the heap holds, with a live block."""
    src = r"""
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    int i;
    char *keep = malloc(100);
    char *first = 0, *p;
    for (i = 0; i < 2000; i++) {
        p = malloc(1000);
        if (!p) { printf("FAIL at %d\n", i); return 1; }
        if (!first) first = p;
        if (p != first) { printf("moved at %d\n", i); return 1; }
        free(p);
    }
    free(keep);
    printf("ok\n");
    return 0;
}
"""
    assert build_and_run(tmp_path, src).strip() == "ok"


def test_neighbours_merge(tmp_path):
    """Three adjacent blocks freed in any order merge back into one."""
    src = r"""
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    char *a = malloc(100), *b = malloc(100), *c = malloc(100), *d = malloc(100);
    char *big;
    free(b); free(c); free(a);          /* a b c merge: 3*100 + 2*2 bytes */
    big = malloc(304);
    printf("%d\n", big == a);
    free(big); free(d);
    return 0;
}
"""
    # The merged block starts at a's header and holds 100+2+100+2+100 bytes,
    # so malloc(304) takes it whole and returns a.
    assert build_and_run(tmp_path, src).strip() == "1"


def test_calloc_zeroes_reused_memory(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void) {
    char *a = malloc(64);
    char *z;
    int i, nz = 0;
    memset(a, 0xAA, 64);
    free(a);
    z = calloc(8, 8);
    for (i = 0; i < 64; i++) nz += z[i] != 0;
    printf("%d\n", nz);
    return 0;
}
"""
    assert build_and_run(tmp_path, src).strip() == "0"


def test_realloc_keeps_contents_and_handles_null(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    int i, ok = 1;
    char *p = realloc(0, 10);
    for (i = 0; i < 10; i++) p[i] = (char)(i + 1);
    p = realloc(p, 500);
    for (i = 0; i < 10; i++) if (p[i] != (char)(i + 1)) ok = 0;
    p = realloc(p, 5);
    for (i = 0; i < 5; i++) if (p[i] != (char)(i + 1)) ok = 0;
    free(p);
    free(0);
    printf("%d\n", ok);
    return 0;
}
"""
    assert build_and_run(tmp_path, src).strip() == "1"


def test_out_of_memory_returns_null(tmp_path):
    src = r"""
#include <stdio.h>
#include <stdlib.h>
int main(void) {
    char *p = malloc(65000U);
    char *q = malloc(10);
    printf("%d %d\n", p == 0, q != 0);
    return 0;
}
"""
    assert build_and_run(tmp_path, src).strip() == "1 1"
