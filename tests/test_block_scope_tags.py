"""A struct, union or enum tag declared in a block belongs to that block.

``struct S`` inside a function with the layout of its own used to be the
file-scope ``struct S`` (the back end keys its type table by the bare tag),
so ``sizeof`` and every member offset came from the wrong type.  The
programs here run on the Z80 (int 2 bytes, long 4, pointer 2) and the
expected text is worked out from those sizes.
"""

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

SHADOW = '#include <stdio.h>\nstruct S { int a; int b; int c; };\nstruct S g = {1,2,3};\nint f(void) {\n  struct S { char x; long y; };\n  struct S s; s.x = 7; s.y = 99;\n  return sizeof(struct S) * 100 + s.x + (int)s.y;\n}\nint h(void) {\n  struct S s = {4,5,6};\n  return sizeof(struct S) + s.c;\n}\nstruct T { int q; };\nint k(void) {\n  struct T { int q[10]; } t;\n  { struct T { char z; } u; return sizeof t * 100 + sizeof u; }\n}\nint m(void) {\n  struct U;            /* forward declaration in block scope */\n  struct U { long l[2]; } u;\n  return sizeof(struct U);\n}\nstruct U { char c; };\nint main(void){\n  printf("%d %d %d %d %d\\n", f(), h(), k(), m(), (int)sizeof(struct S));\n  printf("%d %d\\n", g.a+g.b+g.c, (int)sizeof(struct U));\n  return 0;\n}\n'
SCOPES = '#include <stdio.h>\nstruct N { int v; };\nenum E { EA = 1, EB };\nunion W { char c; long l; };\nint sum(void) {\n  struct N { int v; struct N *next; char pad[3]; };   /* self reference */\n  struct N a, b;\n  a.v = 3; a.next = &b; b.v = 4; b.next = 0;\n  return a.next->v + a.v + sizeof(struct N);\n}\nint en(void) {\n  enum E { EX = 40, EY };\n  enum E e = EY;\n  return e + sizeof(enum E) * 1000;\n}\nint un(void) {\n  union W { int i[5]; };\n  union W w; w.i[4] = 9;\n  return sizeof w + w.i[4];\n}\nint nest(void) {\n  struct Out { struct In { int q; char r; } in; int z; } o;   /* In is in the block scope */\n  struct In k;\n  k.q = 5; o.in = k; o.z = 6;\n  return sizeof(struct In) * 10 + sizeof(struct Out) + o.in.q;\n}\nint shadow(void) {\n  struct N { char c; } x;\n  {\n    struct N { long l[3]; } y;\n    return sizeof y * 10 + sizeof x;\n  }\n}\nint fwd(void) {\n  struct Q;                       /* new tag in this block */\n  struct Q *p;\n  struct Q { int a, b, c; } q;\n  p = &q; p->c = 8;\n  return p->c + sizeof(struct Q);\n}\nint main(void){\n  struct N g; \n  printf("%d %d %d %d %d %d\\n", sum(), en(), un(), nest(), shadow(), fwd());\n  printf("%d %d %d\\n", (int)sizeof g, (int)sizeof(enum E), (int)sizeof(union W));\n  return 0;\n}\n'


def test_a_block_tag_hides_the_file_scope_tag_of_the_same_name(tmp_path):
    # f: struct {char; long} = 5 bytes; h: file-scope S = 6 bytes;
    # k: 20-byte and 1-byte U's in nested blocks; m: forward declared block tag
    assert build_and_run(tmp_path, SHADOW) == "606 12 2001 8 6\n6 1\n"


def test_block_tags_of_every_kind_and_nesting(tmp_path):
    # self-referential struct, enum, union, a struct declared inside a
    # struct body (its tag belongs to the block), shadowing in two nested
    # blocks, and a tag used before its definition in the same block.
    assert build_and_run(tmp_path, SCOPES) == "14 2041 19 40 121 14\n2 2 4\n"


def test_block_tags_with_32_bit_int(tmp_path):
    # int is 4 bytes, so every size that holds an int grows
    assert build_and_run(tmp_path, SHADOW, ("--int", "32")) == \
        "606 18 4001 8 12\n6 1\n"
    assert build_and_run(tmp_path, SCOPES, ("--int", "32")) == \
        "16 4041 29 64 121 20\n4 4 4\n"
