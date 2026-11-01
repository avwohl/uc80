"""A public label (``name::``) must not move the line numbers of the passes
that delete lines after the peephole pass (reglocals.prune,
reglocals.drop_dead_copies and iyptr.prune_iy).

``intrinsic_label(TIMER_START)`` writes ``TIMER_START::``.  The lenient
parser read the second colon as an instruction, so each pass saw one node
more than the text has lines.  The pass then deleted the lines after the
ones it meant, and ``heap_adjust`` of embench-huffbench lost
``ld L,E / ld H,D``: its result was wrong and the program ended early
without a message.
"""

import pytest

from .test_famj_kernels import ids, same_as_host
from .test_struct_return_abi import _toolchain_available

pytestmark = pytest.mark.skipif(
    not _toolchain_available(),
    reason="um80/ul80/cpmemu or the built libraries are not available")

SRC = r"""
#include <stdio.h>
#include <stddef.h>

#ifdef __UC80__
#define intrinsic_label(name) asm(#name "::")
#else
#define intrinsic_label(name)
#endif

static size_t freq[16] = {9, 4, 12, 1, 7, 3, 15, 2, 8, 6, 11, 5, 14, 10, 13, 0};
static size_t heap[17];

static void heap_adjust(size_t *f, size_t *h, int n, int k);

int main(void)
{
    int i, n = 15;
    intrinsic_label(TIMER_START);
    for (i = 0; i < n; i++)
        heap[i] = (size_t)i;
    for (i = n; i > 0; --i)
        heap_adjust(freq, heap, n, i);
    intrinsic_label(TIMER_STOP);
    for (i = 0; i < n; i++)
        printf("%u ", (unsigned)heap[i]);
    printf("\n");
    return 0;
}

/* the shape of heap_adjust in embench-huffbench */
static void heap_adjust(size_t *f, size_t *h, int n, int k)
{
    int j;
    int v;

    --h;
    v = (int)h[k];
    while (k <= n / 2) {
        j = k + k;
        if (j < n && f[h[j]] > f[h[j + 1]])
            ++j;
        if (f[v] < f[h[j]])
            break;
        h[k] = h[j];
        k = j;
    }
    h[k] = (size_t)v;
}
"""


@pytest.mark.parametrize("flags", [
    (), ("--no-byte-regs",), ("--no-param-reg",), ("-O0",),
    ("--no-register-locals",), ("--no-byte-regs", "--no-param-reg"),
], ids=ids)
def test_public_label_keeps_the_line_numbers(tmp_path, flags):
    same_as_host(tmp_path, SRC, *flags)


def test_lenient_parser_gives_one_node_for_a_public_label():
    from uc80 import reglocals
    lines = ["TIMER_START::", "\tld\tA,B", "name:", "\tret"]
    nodes = reglocals.parse_lenient(lines, reglocals.Env())
    assert len(nodes) == len(lines)
    assert [n.kind for n in nodes] == ["label", "ins", "label", "ins"]
    assert nodes[0].label == "TIMER_START"


def test_the_passes_leave_a_text_alone_when_nodes_and_lines_differ():
    from uc80 import reglocals
    # a label and an instruction on one line: two nodes for one line
    code = "\n".join(["; Function f", "_f:", "@a: ld B,H", "\tld\tC,L",
                      "@f_ret:", "\tret"])
    assert reglocals.drop_dead_copies(code) == code
