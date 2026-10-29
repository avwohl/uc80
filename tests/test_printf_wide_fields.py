"""printf takes a field width or precision above 255.

Both were bytes in the runtime, so ``%300d`` printed 255 characters.  They
are 16-bit now and limited to 32767.  The expected output of the program
below is what a host compiler prints for it.  A float precision above 255
is still 255 (docs/printf.md).
"""

from tests.test_struct_return_abi import build_and_run, pytestmark  # noqa: F401

PROGRAM = '#include <stdio.h>\n#include <string.h>\nstatic char big[700];\nstatic char buf[2000];\nstatic int count(const char *s, char c) { int n = 0; while (*s) { if (*s == c) n++; s++; } return n; }\nint main(void){\n  int n;\n  memset(big, \'x\', 699);\n  n = sprintf(buf, "%300d", 7); printf("%d %c %c %d\\n", n, buf[0], buf[299], count(buf,\' \'));\n  n = sprintf(buf, "%.300s", big); printf("%d %d\\n", n, (int)strlen(buf));\n  n = sprintf(buf, "%*d", 300, 7); printf("%d %c %d\\n", n, buf[299], count(buf,\' \'));\n  n = sprintf(buf, "%-300d|", 7); printf("%d %c %c\\n", n, buf[0], buf[300]);\n  n = sprintf(buf, "%.300d", 7); printf("%d %c %c %d\\n", n, buf[0], buf[299], count(buf,\'0\'));\n  n = sprintf(buf, "%0300d", 7); printf("%d %c %c %d\\n", n, buf[0], buf[299], count(buf,\'0\'));\n  n = sprintf(buf, "%260s", "ab"); printf("%d %c %d\\n", n, buf[259], count(buf,\' \'));\n  n = sprintf(buf, "%.*s", 300, big); printf("%d\\n", n);\n  n = sprintf(buf, "%300c", \'q\'); printf("%d %c %d\\n", n, buf[299], count(buf,\' \'));\n  n = sprintf(buf, "%256d", 7); printf("%d\\n", n);\n  n = sprintf(buf, "%1000d", 7); printf("%d\\n", n);\n  n = sprintf(buf, "%300ld", 7L); printf("%d\\n", n);\n  n = sprintf(buf, "%300x", 255); printf("%d %c %c\\n", n, buf[298], buf[299]);\n  n = sprintf(buf, "%#300o", 8); printf("%d %c%c%c\\n", n, buf[297], buf[298], buf[299]);\n  n = sprintf(buf, "%300.3f", 1.5); printf("%d %c\\n", n, buf[299]);\n  n = sprintf(buf, "%-300.3f|", 1.5); printf("%d %c\\n", n, buf[0]);\n  n = sprintf(buf, "%*d", -300, 7); printf("%d %c %c\\n", n, buf[0], buf[299]);\n  n = sprintf(buf, "%.0d|", 0); printf("%d\\n", n);\n  n = sprintf(buf, "%5.3d|%-6d|%06d|%+d", 7, 8, -9, 5); printf("%s\\n", buf);\n  n = sprintf(buf, "%.300x", 255); printf("%d %c\\n", n, buf[0]);\n  n = sprintf(buf, "%+.300d", 7); printf("%d %c\\n", n, buf[0]);\n  n = sprintf(buf, "%-+310.300d|", 7); printf("%d %c %c\\n", n, buf[0], buf[301]);\n  n = sprintf(buf, "%#0300x", 255); printf("%d %c%c %c\\n", n, buf[0], buf[1], buf[299]);\n  n = sprintf(buf, "%300.100s|", "hello"); printf("%d %c\\n", n, buf[299]);\n  n = sprintf(buf, "%-300.2s|", "hello"); printf("%d %c%c%c\\n", n, buf[0], buf[1], buf[2]);\n  n = sprintf(buf, "%300a", 1.0); printf("%d\\n", n);\n  n = sprintf(buf, "%300e", 1.0); printf("%d\\n", n);\n  n = sprintf(buf, "%300g", 1.0); printf("%d\\n", n);\n  n = sprintf(buf, "%.300s", "short"); printf("%d\\n", n);\n  n = snprintf(buf, 100, "%300d", 7); printf("%d %d\\n", n, (int)strlen(buf));\n  return 0;\n}\n'

EXPECTED = '300   7 299\n300 300\n300 7 299\n301 7 |\n300 0 7 299\n300 0 7 299\n260 b 258\n300\n300 q 299\n256\n1000\n300\n300 f f\n300 010\n300 0\n301 1\n300 7  \n1\n  007|8     |-00009|+5\n300 0\n301 +\n311 +  \n300 0x f\n301 o\n301 he \n300\n300\n300\n5\n300 99\n'


def test_widths_and_precisions_above_255(tmp_path):
    assert build_and_run(tmp_path, PROGRAM) == EXPECTED
