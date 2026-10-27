"""A pointer local in IY (docs/register_locals.md, "IY as a pointer").

A function that reads and writes fields through one pointer keeps the
pointer in IY.  The tests check:

  - the shape: field accesses become (IY+d), ``p++`` becomes ``inc IY``,
    and the switch UC80_NO_IY turns the pass off;
  - the calls: a call that may change IY gets push IY / pop IY while the
    pointer is live; a call to code that never touches IY gets none, and
    no ``; RYn`` tag stays in the output;
  - the answers: the same output with the pass on and off, in both
    storage modes (the answers come from a host build).

bench/rom/isrproof.py (proof "iy") covers the interrupt function.
"""

import re

import pytest

from .test_register_cc import compile_mac, needs_run, run_mac
from .test_register_locals import body_of, build_run

WALK = """
struct N { signed char c; unsigned char u; int v; struct N *next; };
int g;
int walk(struct N *p) {
    int t = 0;
    while (p) {
        t += p->c + p->u;
        p->v = t;
        g = p->v;
        p = p->next;
    }
    return t;
}
int main(void) { struct N a = {1, 2, 3, 0}; return walk(&a); }
"""

STR = """
int strsum(const char *s) { int n = 0; while (*s) { n += *s++; } return n; }
int main(void) { return strsum("abc"); }
"""

FRAME = """
struct N { int a; int b; int c; int d; struct N *next; };
int f(struct N *p, int n) { int t = 0; while (n--) { t += p->a + p->b; p->c = t; p->d = p->a ^ p->b; t += p->c; p->a++; } return t; }
int main(void) { struct N a = {1, 2, 3, 0}; return f(&a, 3); }
"""

# the callee has an IY pointer of its own, so the caller must save IY
CALLER = """
struct N { int a; int b; int c; struct N *next; };
struct N pool[4];
int sink;
void touch(void) {
    struct N *q = pool;
    while (q) { q->a++; q->b += q->a; q->c = q->a ^ q->b; sink += q->c + q->b; q->a += q->c; q = q->next; }
}
int caller(struct N *p, int n) {
    int t = 0;
    while (n--) { t += p->a + p->b + p->c; p->c = t; touch(); t += p->a; p = p->next; }
    return t;
}
int main(void) { return caller(pool, 2); }
"""

# the callee touches nothing: no wrap
CLEAN = """
struct N { int a; int b; int c; struct N *next; };
int clean(int x) { return x + 1; }
int caller(struct N *p, int n) {
    int t = 0;
    while (n--) { t += p->a + p->b + p->c; p->c = t; t = clean(t); t += p->a; p = p->next; }
    return t;
}
int main(void) { struct N a = {1, 2, 3, 0}; return caller(&a, 1); }
"""


def has_iy(txt):
    return "(IY+" in txt


def comp(tmp_path, src, *flags, env=None, monkeypatch=None):
    if monkeypatch is not None and env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    return compile_mac(tmp_path, src, "--no-inlining", *flags)[0]


class TestShape:
    def test_fields_use_iy(self, tmp_path):
        txt = body_of(comp(tmp_path, WALK), "walk")
        assert has_iy(txt)
        assert "LD\tHL,(??AUTO" not in txt.upper()

    def test_switch_turns_the_pass_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UC80_NO_IY", "1")
        assert not has_iy(body_of(comp(tmp_path, WALK), "walk"))

    def test_no_register_locals_means_no_iy(self, tmp_path):
        mac = comp(tmp_path, WALK, "--no-register-locals")
        assert not has_iy(body_of(mac, "walk"))

    def test_post_increment_is_inc_iy(self, tmp_path):
        txt = body_of(comp(tmp_path, STR), "strsum")
        assert "inc\tIY" in txt and has_iy(txt)

    def test_frame_mode_moves_the_parameter_at_entry(self, tmp_path):
        txt = body_of(comp(tmp_path, FRAME, "--no-shared-storage"), "f")
        assert has_iy(txt)
        entry = txt.split("(IY+")[0]
        assert re.search(r"push\tHL\s+pop\tIY", entry)


    def test_word_in_memory_increments_through_iy(self, tmp_path):
        txt = body_of(comp(tmp_path, INC16), "inc")
        assert "inc\t(IY+4)" in txt and "inc\t(IY+5)" in txt
        assert "inc\tHL" not in txt

    def test_word_load_into_hl_has_no_swap(self, tmp_path):
        txt = body_of(comp(tmp_path, INC16), "inc")
        assert re.search(r"ld\tL,\(IY\+0\)\s+ld\tH,\(IY\+1\)", txt)


class TestCalls:
    def test_call_that_changes_iy_is_wrapped(self, tmp_path):
        mac = comp(tmp_path, CALLER)
        assert has_iy(body_of(mac, "touch"))
        txt = body_of(mac, "caller")
        assert has_iy(txt)
        assert re.search(r"push\tIY(?:.|\n)*call\t_touch(?:.|\n)*pop\tIY", txt)

    def test_call_to_clean_code_is_not_wrapped(self, tmp_path):
        txt = body_of(comp(tmp_path, CLEAN), "caller")
        assert has_iy(txt)
        assert "push\tIY" not in txt

    @pytest.mark.parametrize("src", [CALLER, CLEAN, WALK, STR])
    def test_no_tag_stays(self, tmp_path, src):
        assert "; RY" not in comp(tmp_path, src)


# a word in memory: inc (HL) / jr nz / inc HL / inc (HL) becomes two inc (IY+d)
INC16 = """
struct T { short a; short b; unsigned short cnt; char c; };
short g;
void inc(struct T *t, int n) {
    while (n--) {
        t->cnt++;
        t->a = (short)(t->a + t->b);
        t->b = (short)(t->a ^ t->cnt);
        g = (short)(g + t->a);
    }
}
int main(void) { struct T t = {1, 2, 0xFFFE, 3}; inc(&t, 5); return t.cnt; }
"""


PROGRAMS = {
    "words": ('#include <stdio.h>\nstruct T { short a; short b; unsigned short cnt; char c; };\nshort g;\nvoid inc(struct T *t, int n) {\n    while (n--) {\n        t->cnt++;\n        t->a = (short)(t->a + t->b);\n        t->b = (short)(t->a ^ t->cnt);\n        g = (short)(g + t->a);\n    }\n}\nint walkstr(const char *s, char *d) { int n = 0; while (*s) { *d++ = *s++; n++; } *d = 0; return n; }\nint main(void) {\n    struct T t = {1, 2, 0xFFFE, 3};\n    char buf[16];\n    inc(&t, 5);\n    printf("%d %d %u %d\\n", (int)t.a, (int)t.b, (unsigned)t.cnt, (int)g);\n    printf("%d %s\\n", walkstr("hello", buf), buf);\n    return 0;\n}\n', "-4 -1 3 -7\n5 hello\n"),
    "mixed": ('#include <stdio.h>\nstruct N { signed char c; unsigned char u; int v; struct N *next; struct N *l, *r; unsigned long big; };\nstruct N pool[8];\nint cnt;\nvoid touch(void) { cnt++; }\nint pure(int x) { return x * 3; }\n\nint walk(struct N *p) {\n    int t = 0;\n    while (p) {\n        t += p->c + p->u;\n        p->v = t;\n        touch();\n        t += p->v & 7;\n        p = p->next;\n    }\n    return t;\n}\nint tree(struct N *p, int d) {\n    if (!p) return 0;\n    p->u++;\n    int s = p->v + tree(p->l, d + 1) + p->c;\n    s += tree(p->r, d + 1);\n    p->c += 1;\n    return s + p->u + d;\n}\nlong bigsum(struct N *p, int n) {\n    long s = 0;\n    for (int i = 0; i < n; i++) { s += p->big; p->big += i; p->c ^= 3; touch(); s += p->c; }\n    return s;\n}\nvoid tail(struct N *p) { p->v += 5; touch(); if (p->v < 100) tail(p); }\nint strl(const char *s) { int n = 0; while (*s) { n += *s; s++; } return n; }\nint pure_loop(struct N *p, int k) {\n    int a = 0;\n    for (int i = 0; i < k; i++) { a += p->v + pure(i); p->v--; p->c--; }\n    return a;\n}\nint swapnext(struct N *p, struct N *q) {\n    struct N *t = p->next; p->next = q->next; q->next = t;\n    p->u |= 0x80; q->u &= 0x7f;\n    return p->v - q->v;\n}\nint main(void) {\n    for (int i = 0; i < 8; i++) {\n        pool[i].c = i - 3; pool[i].u = 200 + i; pool[i].v = i * 11; pool[i].big = 100000L * i;\n        pool[i].next = i < 7 ? &pool[i + 1] : 0;\n        pool[i].l = 2 * i + 1 < 8 ? &pool[2 * i + 1] : 0;\n        pool[i].r = 2 * i + 2 < 8 ? &pool[2 * i + 2] : 0;\n    }\n    printf("%d\\n", walk(pool));\n    printf("%d\\n", tree(pool, 0));\n    printf("%ld\\n", bigsum(&pool[2], 5));\n    tail(&pool[3]);\n    printf("%d %d\\n", pool[3].v, cnt);\n    printf("%d\\n", strl("hello world"));\n    printf("%d\\n", pure_loop(&pool[4], 6));\n    printf("%d\\n", swapnext(&pool[0], &pool[1]));\n    printf("%d %d %d\\n", pool[0].u, pool[1].u, (int)(pool[0].next - pool));\n    return 0;\n}\n', '1658\n9007\n1000019\n814 14\n1116\n6120\n-204\n201 74 2\n'),
}


@needs_run
@pytest.mark.parametrize("flags", [(), ("--no-shared-storage",), ("--no-inlining",)],
                         ids=["default", "frame", "noinline"])
@pytest.mark.parametrize("name", ["mixed", "words"])
def test_same_answer(tmp_path, flags, name):
    src, expected = PROGRAMS[name]
    assert build_run(tmp_path, src, *flags) == expected


@needs_run
@pytest.mark.parametrize("name", ["mixed", "words"])
def test_answer_without_the_pass(tmp_path, monkeypatch, name):
    monkeypatch.setenv("UC80_NO_IY", "1")
    src, expected = PROGRAMS[name]
    assert build_run(tmp_path, src, "--no-inlining") == expected
