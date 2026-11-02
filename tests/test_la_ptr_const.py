"""L1: a static pointer that holds one object address becomes the address
(docs/family_l.md).  Each test runs the program with uc80 and with the host
compiler and compares the output; the .mac text shows if the pointer is
gone."""

from ._faml import needs_toolchain, run_both

pytestmark = needs_toolchain

STATE = r"""
#include <stdio.h>
#include <string.h>
typedef struct State {
    int count;
    unsigned char flags;
    char name[6];
    int table[4];
    int (*op)(int);
    struct State *self;
} State;
static int twice(int x) { return 2 * x; }
"""


def check(text, ptr="g"):
    # no load of the pointer variable is left
    return f"(_{ptr})" not in text and f"_{ptr}," not in text


def test_store_in_main_with_function_static(tmp_path):
    src = STATE + r"""
static State *g = NULL;
static void bump(int n) { g->count += n; g->table[n & 3] += n; }
static int total(void)
{
    int i, t = g->count;
    for (i = 0; i < 4; i++) t += g->table[i];
    return t + g->op(3) + (g->self == g);
}
int main(int argc, char **argv)
{
    static State state;
    const char *tag = argc > 5 ? argv[1] : "abc";
    g = &state;
    g->op = twice;
    g->self = g;
    g->flags = 0x81;
    strcpy(g->name, tag);
    bump(1); bump(2); bump(7);
    printf("%d %s %d %d\n", total(), g->name, g->flags, sizeof(*g) == sizeof(State));
    printf("%d\n", g != NULL);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert check(text)
    assert "__ls_main_state" in text


def test_store_in_main_with_global_object(tmp_path):
    src = STATE + r"""
static State obj;
static State *g;
static int get(void) { return g->count + g->table[2]; }
int main(void)
{
    g = &obj;
    g->count = 5;
    g->table[2] = 9;
    printf("%d %d\n", get(), obj.count);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "14 5"
    assert check(text)


def test_initializer_form(tmp_path):
    src = STATE + r"""
static State obj = { 3, 4, "xy", { 1, 2, 3, 4 }, twice, 0 };
static State *const g = &obj;
static const State *h = &obj;
int main(void)
{
    printf("%d %d %s %d %d\n", g->count, h->table[3], h->name, g->op(4),
           (int) (g->flags + h->flags));
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert check(text, "h")


def test_pointer_read_before_the_store_is_kept(tmp_path):
    src = STATE + r"""
static State obj;
static State *g = NULL;
static void show(void) { printf("%d\n", g == NULL); }
int main(void)
{
    show();
    g = &obj;
    show();
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "1\n0"
    assert not check(text)


def test_two_assignments_keep_the_pointer(tmp_path):
    src = STATE + r"""
static State a, b;
static State *g;
int main(void)
{
    g = &a;
    g->count = 1;
    g = &b;
    g->count = 2;
    printf("%d %d\n", a.count, b.count);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "1 2"
    assert not check(text)


def test_address_taken_pointer_is_kept(tmp_path):
    src = STATE + r"""
static State a, b;
static State *g;
static void redirect(State **pp) { *pp = &b; }
int main(void)
{
    g = &a;
    redirect(&g);
    g->count = 7;
    printf("%d %d\n", a.count, b.count);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "0 7"
    assert not check(text)


def test_pointer_arithmetic_is_kept(tmp_path):
    src = r"""
#include <stdio.h>
static int arr[4];
static int *p;
int main(void)
{
    p = &arr[0];
    *p = 5;
    p++;
    *p = 6;
    printf("%d %d\n", arr[0], arr[1]);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "5 6"


def test_call_before_the_store_keeps_the_pointer(tmp_path):
    src = STATE + r"""
static State obj;
static State *g;
static int peek(void) { return g == NULL; }
int main(void)
{
    int early = peek();
    g = &obj;
    printf("%d %d\n", early, peek());
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "1 0"


def test_other_pointer_type_is_kept(tmp_path):
    src = r"""
#include <stdio.h>
static long obj = 0x01020304L;
static unsigned char *g;
int main(void)
{
    g = (unsigned char *) &obj;
    printf("%d\n", g[0]);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want


def test_volatile_pointer_is_kept(tmp_path):
    src = STATE + r"""
static State obj;
static State *volatile g;
int main(void)
{
    g = &obj;
    g->count = 4;
    printf("%d\n", obj.count);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "4"
    assert not check(text)


def test_local_with_the_name_of_the_pointer(tmp_path):
    src = STATE + r"""
static State obj;
static State *g;
static int other(State *g) { return g->count; }
int main(void)
{
    State mine;
    g = &obj;
    g->count = 3;
    mine.count = 8;
    printf("%d %d\n", other(&mine), other(g));
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "8 3"


def test_option_keeps_the_pointer(tmp_path):
    src = STATE + r"""
static State obj;
static State *g;
int main(void)
{
    g = &obj;
    g->count = 3;
    printf("%d\n", g->count);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src, "--no-ptr-const")
    assert got == want == "3"
    assert not check(text)
    want, got, text = run_both(tmp_path, src)
    assert got == want
    assert check(text)


def test_function_static_with_initializer_moves_to_file_scope(tmp_path):
    src = r"""
#include <stdio.h>
typedef struct W { int *ref; int n; } W;
static int slot = 7;
static W *g;
int main(void)
{
    static W w = { &slot, 3 };
    g = &w;
    *g->ref += g->n;
    printf("%d %d\n", slot, w.n);
    return 0;
}
"""
    want, got, text = run_both(tmp_path, src)
    assert got == want == "10 3"
    assert check(text)
