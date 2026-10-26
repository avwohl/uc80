"""printf audit: length modifiers, %p, %n, %a, the printf family and the field
width limit.  The expected text is what the host
printf prints, except for %p, which C leaves to the implementation: uc80
prints 0x and the hex digits, also for a null pointer (0x0).
"""

import re

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools


def test_hh_narrows_the_argument(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    printf("[%hhd][%hhu][%hhx][%hhX][%hho][%hhi]\n", 300, 300, 511, 255, 257, -129);
    printf("[%hd][%hu][%d]\n", 40000u, (unsigned)70000UL, -5);
    printf("[%5hhd][%-5hhu][%.3hhd]\n", 130, 258, 200);
    return 0;
}
""")
    assert out.split("\n")[:3] == [
        "[44][44][ff][FF][1][127]",
        "[-25536][4464][-5]",
        "[ -126][2    ][-056]"]


def test_t_z_and_l_before_a_float_conversion(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stddef.h>
int main(void)
{
    ptrdiff_t d = -7; size_t z = 65535u;
    double x = 2.5;
    printf("[%td][%5ti][%zu][%zx]\n", d, d, z, z);
    printf("[%lf][%Lf][%le][%lg][%5.1lf]\n", x, x, x, x, x);
    return 0;
}
""")
    assert out.split("\n")[:2] == [
        "[-7][   -7][65535][ffff]",
        "[2.500000][2.500000][2.500000e+00][2.5][  2.5]"]


def test_p_prints_0x_and_hex_also_for_null(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    printf("[%p][%p][%8p][%-8p][%p]\n", (void *)0, (void *)0x1234,
           (void *)0xabc, (void *)0xabc, (void *)0xffff);
    return 0;
}
""")
    assert out.split("\n")[0] == "[0x0][0x1234][   0xabc][0xabc   ][0xffff]"


def test_n_stores_the_count(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    int a = -1, b = -1;
    char buf[40];
    printf("abc%nde%n\n", &a, &b);
    printf("%d %d\n", a, b);
    sprintf(buf, "%d%n%s", 12345, &a, "xyz");
    printf("%d %s\n", a, buf);
    return 0;
}
""")
    assert out.split("\n")[:3] == ["abcde", "3 5", "5 12345xyz"]


def test_ll_upper_case_hex(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    unsigned long long v = 0xdeadbeefcafebabeULL;
    printf("[%llx][%llX][%#llX][%20llX]\n", v, v, v, v);
    return 0;
}
""")
    assert out.split("\n")[0] == ("[deadbeefcafebabe][DEADBEEFCAFEBABE]"
                                  "[0XDEADBEEFCAFEBABE][    DEADBEEFCAFEBABE]")


def test_the_printf_family_agrees(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
#include <stdarg.h>
static int vp(const char *f, ...)
{ va_list ap; int r; va_start(ap, f); r = vprintf(f, ap); va_end(ap); return r; }
int main(void)
{
    char buf[64];
    float x = 3.14159f;
    int n = snprintf(buf, 12, "%8.3f|%-6d|%s", x, 42, "abcdef");
    printf("[%s] %d\n", buf, n);
    n = sprintf(buf, "%+08.2f|%5.1e", -x, x);
    printf("[%s] %d\n", buf, n);
    n = fprintf(stdout, "[%10.4f]", x);
    printf(" %d\n", n);
    n = vp("[%-10.2g]", x);
    printf(" %d\n", n);
    return 0;
}
""")
    assert out.split("\n")[:4] == [
        "[   3.142|42] 22", "[-0003.14|3.1e+00] 16",
        "[    3.1416] 12", "[3.1       ] 12"]


def test_field_width_and_precision_saturate_at_255(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    int n = printf("[%255d]", 1);
    printf(" %d\n", n);
    n = printf("[%1000d]", 1);
    printf(" %d\n", n);
    return 0;
}
""")
    first, second = out.split("\n")[:2]
    assert re.fullmatch(r"\[ {254}1\] 257", first)
    assert re.fullmatch(r"\[ {254}1\] 257", second)    # 1000 is limited to 255


def hex_float(x, digits=None, upper=False):
    """The text of %a (digits=None) or %.<digits>a for a float x: the
    digits of the float, rounded to nearest, a tie to even (glibc)."""
    import struct
    if x == 0:
        body = "0x0" + ("." + "0" * digits if digits else "") + "p+0"
        return body.upper() if upper else body
    bits = struct.unpack("<I", struct.pack("<f", abs(x)))[0]
    e = (bits >> 23) & 255
    frac = bits & 0x7FFFFF
    if e == 0:                              # subnormal: normalize
        n = frac.bit_length()               # the leading 1 is bit n - 1
        exp = n - 1 - 149
        frac = (frac << (24 - n)) & 0x7FFFFF
        lead = 1
    else:
        lead = 1
        exp = e - 127
    total = (lead << 24) | (frac << 1)
    if digits is None:
        text = "%06x" % (total & 0xFFFFFF)
        text = text.rstrip("0")
        first = total >> 24
    elif digits >= 6:
        text = "%06x" % (total & 0xFFFFFF) + "0" * (digits - 6)
        first = total >> 24
    else:
        drop = 4 * (6 - digits)
        q, r = divmod(total, 1 << drop)
        half = 1 << (drop - 1)
        if r > half or (r == half and (q & 1)):
            q += 1
        first = q >> (4 * digits)
        text = ("%0*x" % (digits, q & ((1 << (4 * digits)) - 1))) if digits else ""
    out = "%s0x%d%s%sp%+d" % ("-" if x < 0 else "", first,
                              "." if text else "", text, exp)
    return out.upper() if upper else out


def test_hex_float_conversion(tmp_path):
    import struct
    values = [1.0, 2.5, -3.0, 0.1, 255.0, 1.0 / 3, 1e10, 1e-10, 0.0,
              3.14159, 12345.678, -0.375]
    # subnormal floats come from their bits (a constant would be flushed)
    bits = [0x00000001, 0x000ccdef, 0x00400000, 0x007fffff, 0x80012345]
    lines = []
    for v in values:
        lines.append(f'    printf("[%a][%A]\\n", (float){v!r}, (float){v!r});')
    for b in bits:
        lines.append(f'    printf("[%a][%A]\\n", fbits(0x{b:08x}UL), fbits(0x{b:08x}UL));')
    for v in (3.14159, 255.0, 0.1):
        for p in (0, 1, 2, 3, 5, 6, 8):
            lines.append(f'    printf("[%.{p}a]\\n", (float){v!r});')
    lines.append('    printf("[%12a][%-12a][%012a][%+a][% a][%#.0a]\\n",'
                 ' 1.5f, 1.5f, 1.5f, 1.5f, 1.5f, 1.0f);')
    out = build_and_run(tmp_path, "#include <stdio.h>\n#include <stdint.h>\n"
                        "static float fbits(uint32_t u)\n"
                        "{ union { uint32_t u; float f; } x; x.u = u; return x.f; }\n"
                        "int main(void)\n{\n"
                        + "\n".join(lines) + "\n    return 0;\n}\n")
    got = out.split("\n")
    k = 0
    f32 = lambda v: struct.unpack("<f", struct.pack("<f", v))[0]
    for v in values:
        x = f32(v)
        assert got[k] == f"[{hex_float(x)}][{hex_float(x, upper=True)}]", (v, got[k])
        k += 1
    for b in bits:
        x = struct.unpack("<f", struct.pack("<I", b))[0]
        assert got[k] == f"[{hex_float(x)}][{hex_float(x, upper=True)}]", (hex(b), got[k])
        k += 1
    for v in (3.14159, 255.0, 0.1):
        for p in (0, 1, 2, 3, 5, 6, 8):
            assert got[k] == f"[{hex_float(f32(v), p)}]", (v, p, got[k])
            k += 1
    assert got[k] == ("[    0x1.8p+0][0x1.8p+0    ][0x00001.8p+0]"
                      "[+0x1.8p+0][ 0x1.8p+0][0x1.p+0]")


def test_hex_float_inf_and_nan(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    volatile float z = 0.0f;
    float inf = 1.0f / z, nan = z / z;
    printf("[%a][%A][%8a][%-8a][%08a][%+a]\n", inf, inf, nan, nan, inf, inf);
    return 0;
}
""")
    assert out.split("\n")[0] == "[inf][INF][     nan][nan     ][     inf][+inf]"


def test_long_long_octal_and_upper_hex(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    unsigned long long v = 0xdeadbeefcafebabeULL;
    printf("[%llo][%#llo][%25llo][%-25llo|][%.3llo][%llo]\n", v, v, v, v, 1ULL, 0ULL);
    return 0;
}
""")
    assert out.split("\n")[0] == (
        "[1572555756771277535276][01572555756771277535276]"
        "[   1572555756771277535276][1572555756771277535276   |][001][0]")


def test_rare_conversions_link_nothing_for_a_plain_program(tmp_path):
    """The parts of printf that few programs use are linked only when a
    format asks for them (printf_hook.py)."""
    import subprocess
    import sys
    from ._clib import LIB_DIR, REPO
    def symbols(source):
        (tmp_path / "p.c").write_text(source)
        r = subprocess.run([sys.executable, "-m", "uc80.main",
                            str(tmp_path / "p.c"), "-o", str(tmp_path / "p.mac")],
                           capture_output=True, text=True, cwd=str(REPO))
        assert r.returncode == 0, r.stderr
        subprocess.run(["um80", str(tmp_path / "p.mac"), "-o",
                        str(tmp_path / "p.rel")], check=True, capture_output=True)
        subprocess.run(["ul80", str(tmp_path / "p.rel"), str(LIB_DIR / "libc.lib"),
                        str(LIB_DIR / "runtime.lib"), "-o", str(tmp_path / "p.com"),
                        "-S", str(tmp_path / "p.sym")], check=True,
                       capture_output=True)
        return {line.split()[1] for line in
                (tmp_path / "p.sym").read_text().splitlines() if len(line.split()) == 2}
    plain = symbols('#include <stdio.h>\nint main(void){ int x = 1; printf("%d %lf\\n", x, 1.5); return 0; }\n')
    for name in ("__PRINTF_HHS", "__PRINTF_HANDLE_N", "__PRINTF_HANDLE_A",
                 "__PRINTF_HANDLE_LLO", "__PRINTF_HANDLE_LLXU"):
        assert name not in plain, name
    assert "__PRINTF_HANDLE_SKIP" in plain          # %lf needs the skip entry
    rare = symbols('#include <stdio.h>\nint main(void){ int x = 1, n; printf("%hhd %n %a %llo\\n", x, &n, 1.0, 5ULL); return 0; }\n')
    for name in ("__PRINTF_HHS", "__PRINTF_HANDLE_N", "__PRINTF_HANDLE_A",
                 "__PRINTF_HANDLE_LLO"):
        assert name in rare, name


def compile_warnings(tmp_path, source):
    import subprocess
    import sys
    from ._clib import REPO
    (tmp_path / "w.c").write_text(source)
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(tmp_path / "w.c"),
                        "-o", str(tmp_path / "w.mac")], capture_output=True,
                       text=True, cwd=str(REPO))
    assert r.returncode == 0, r.stderr
    return r.stderr


def test_no_warning_for_a_conversion_that_the_library_handles(tmp_path):
    err = compile_warnings(tmp_path, r"""
#include <stdio.h>
#include <stddef.h>
int main(void)
{
    int n; ptrdiff_t d = 3; unsigned long long v = 5;
    printf("%td %lf %n %a %llo %llX %*d %.*f\n", d, 1.5, &n, 1.0, v, v, 4, 5, 2, 1.5);
    return 0;
}
""")
    assert "warning" not in err, err


def test_a_conversion_without_a_handler_still_warns(tmp_path):
    err = compile_warnings(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    printf("%jd\n", (long long)5);
    return 0;
}
""")
    assert "'%jd'" in err


def test_printf_hook_adds_table_entries_only_for_what_the_formats_use():
    from uc80 import printf_hook
    code = ("; Printf format dispatch tables (#pragma printf)\n\n"
            "__printf_format_table:\n\tdb\t'd'\n\tdw\t__printf_handle_d\n"
            "\tdb\t0\t\t; sentinel\n\n"
            "__printf_long_table:\n\tdb\t0\t\t; sentinel\n\n"
            "__printf_ll_table:\n\tdb\t0\t\t; sentinel\n"
            "__printf_ll_entry:\n\tret\n")
    assert printf_hook.patch_tables(code, set()) == code
    out = printf_hook.patch_tables(code, {"n", "t", "hh"})
    assert "db\t'n'\n\tdw\t__printf_handle_n" in out
    assert "db\t't'\n\tdw\t__printf_handle_skip" in out
    assert "db\t'd'\n\tdw\t__pfhh_64" in out and "call\t__printf_hhs" in out
    assert "\textrn\t__printf_handle_n\n" in out
    # a unit without a table is not changed
    assert printf_hook.patch_tables("int x;", {"n"}) == "int x;"
    assert printf_hook.needs_of_format("%hhd %td %lf %n %a %llo %llX %5.2f") == {
        "hh", "t", "lf", "n", "a", "llo", "llX"}
    assert printf_hook.needs_of_format("%d %s %%n %ld") == set()
