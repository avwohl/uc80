"""Three miscompiles found while checking speed families D and E.

They did not come from the new byte paths, but a wrong value in the
caller changes what a byte path sees.  Each program ran with a wrong
result before the fix; the expected text is what a host compiler prints.

  * the inliner let a callee modify the caller's variable through a
    by-value parameter (``v++``, ``*p++``);
  * the inliner skipped the narrowing cast when the parameter type, or
    the return type, was a typedef name such as ``u8``;
  * copy propagation ignored ``++`` inside a declaration initialiser, so
    ``int a = v; int b = a++; return a;`` returned ``v``.
"""

from tests.test_const_muldiv import build_and_run, needs_toolchain


@needs_toolchain
def test_inlined_callee_does_not_change_caller_variable(tmp_path):
    src = """
    typedef unsigned char u8;
    int g(u8 v) { return v++ == 5; }
    int f3(u8 *p, u8 v) { return v < *p++; }
    int h(int v) { return (v += 3) * 2; }
    int main(void) {
        volatile int z = 0;
        u8 v = z, b[4] = {0, 1, 0, 0};
        int w = z + 4;
        int r = g(v);
        printf("%d %d\\n", r, v);
        printf("%d\\n", f3(b, 0));
        printf("%d %d\\n", h(w), w);
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "inl_param_write")
    assert out.split() == ["0", "0", "0", "14", "4"]


@needs_toolchain
def test_inlined_typedef_parameter_and_return_narrow(tmp_path):
    src = """
    typedef unsigned char u8;
    typedef signed char s8;
    int g16(int x) { return x; }
    int p1(s8 c) { return g16(c); }
    int p2(u8 c) { return g16(c) + 1; }
    int p27(int x) { return p2(x) + p1(x); }
    u8 r8(int x) { return x; }
    s8 s8f(int x) { return x; }
    int main(void) {
        volatile int z = 212;
        int i = z;
        printf("%d\\n", p27(i));
        printf("%d %d\\n", r8(i + 100) + 0, s8f(i) + 0);
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "inl_typedef_narrow")
    assert out.split() == ["169", "56", "-44"]


@needs_toolchain
def test_copy_propagation_sees_increment_in_initialiser(tmp_path):
    src = """
    typedef unsigned char u8;
    int p1(u8 v) { u8 a = v; u8 b = a++; return b + a * 7; }
    int p2(u8 v) { u8 a = v; a++; return a * 7; }
    int p3(u8 v) { u8 a = v; u8 b = a++; return a * 7; }
    int p4(u8 v) { u8 a = v; u8 b = a++; return a; }
    int p5(u8 v) { u8 a = v; u8 b = a++; return a * 7 + b; }
    int p6(int v) { int a = v; int b = a++; return a; }
    int g;
    int bump(void) { return ++g; }
    int p7(int v) { int a = g; int b = bump(); return a + b * 10; }
    int main(void) {
        volatile u8 z = 0;
        printf("%d %d %d %d %d %d\\n", p1(z), p2(z), p3(z), p4(z), p5(z), p6(z));
        printf("%d\\n", p7(0));
        return 0;
    }
    """
    out = build_and_run(tmp_path, src, "copyprop_init")
    assert out.split() == ["7", "7", "7", "1", "7", "1", "10"]
