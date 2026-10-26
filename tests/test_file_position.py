"""ftell and fseek on files.

ftell read the random record field of the FCB, which sequential reads never
set, so it was wrong after any read, and it did not add the position in the
buffer.  fseek took only SEEK_SET, only the low 16 bits of the offset, and
a write stream lost its buffer.  A file is now read and written with the
random access BDOS calls (33 and 34), with the number of the next record in
r0 r1 of the FCB, so the position is exact and every whence works.
Carriage returns count, because fgetc skips them but the file holds them.
"""

from ._clib import build_and_run, skip_without_tools

pytestmark = skip_without_tools

WRITE300 = r"""
#include <stdio.h>
static void make(void)
{
    FILE *f = fopen("data.txt", "w");
    int i;
    for (i = 0; i < 300; i++) fputc('A' + (i % 26), f);
    for (i = 0; i < 10; i++) fputc('0' + i, f);
    fclose(f);
}
"""


def letter(i):
    return chr(ord("A") + i % 26)


def test_ftell_follows_reads_and_writes(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f = fopen("a.txt", "w");
    int i; long t0, t1, t2, t3, t4, t5, t6, t7;
    t0 = ftell(f);
    for (i = 0; i < 300; i++) fputc('x', f);
    t1 = ftell(f);
    fflush(f);
    t2 = ftell(f);
    for (i = 0; i < 10; i++) fputc('y', f);
    t3 = ftell(f);
    fclose(f);
    f = fopen("a.txt", "r");
    t4 = ftell(f);
    fgetc(f);
    t5 = ftell(f);
    for (i = 0; i < 199; i++) fgetc(f);
    t6 = ftell(f);
    for (i = 0; i < 110; i++) fgetc(f);
    t7 = ftell(f);
    fclose(f);
    printf("%ld %ld %ld %ld %ld %ld %ld %ld\n", t0, t1, t2, t3, t4, t5, t6, t7);
    return 0;
}
""")
    assert out.split("\n")[0] == "0 300 300 310 0 1 200 310"
    data = (tmp_path / "a.txt").read_bytes()
    # the emulator takes the ^Z padding off; a ^Z inside would stay
    assert data == b"x" * 300 + b"y" * 10


def test_fseek_set_cur_end(tmp_path):
    out = build_and_run(tmp_path, WRITE300 + r"""
int main(void)
{
    FILE *f;
    int r[12], c[12], k = 0;
    long t[12];
    make();
    f = fopen("data.txt", "r");
    r[k] = fseek(f, 5, SEEK_SET);   c[k] = fgetc(f); t[k] = ftell(f); k++;
    r[k] = fseek(f, 128, SEEK_SET); c[k] = fgetc(f); t[k] = ftell(f); k++;
    r[k] = fseek(f, 255, SEEK_SET); c[k] = fgetc(f); t[k] = ftell(f); k++;
    r[k] = fseek(f, 2, SEEK_CUR);   c[k] = fgetc(f); t[k] = ftell(f); k++;
    r[k] = fseek(f, -10, SEEK_CUR); c[k] = fgetc(f); t[k] = ftell(f); k++;
    r[k] = fseek(f, 0, SEEK_END);   c[k] = fgetc(f); t[k] = ftell(f); k++;
    r[k] = fseek(f, -4, SEEK_END);  c[k] = fgetc(f); t[k] = ftell(f); k++;
    r[k] = fseek(f, -1, SEEK_SET);  c[k] = 0;        t[k] = 0;        k++;
    r[k] = fseek(f, 0, 7);          c[k] = 0;        t[k] = 0;        k++;
    rewind(f);                      c[k] = fgetc(f); t[k] = ftell(f); r[k] = 0; k++;
    fclose(f);
    for (k = 0; k < 10; k++) printf("%d %d %ld\n", r[k], c[k], t[k]);
    return 0;
}
""")
    got = [tuple(int(x) for x in ln.split()) for ln in out.split("\n")[:10]]
    assert got == [
        (0, ord(letter(5)), 6), (0, ord(letter(128)), 129),
        (0, ord(letter(255)), 256), (0, ord(letter(258)), 259),
        (0, ord(letter(249)), 250), (0, -1, 310),
        (0, ord("6"), 307), (-1, 0, 0), (-1, 0, 0), (0, ord("A"), 1)]


def test_seek_and_read_again_matches_the_file(tmp_path):
    out = build_and_run(tmp_path, WRITE300 + r"""
int main(void)
{
    FILE *f;
    long p; int i, bad = 0;
    make();
    f = fopen("data.txt", "r");
    for (p = 0; p < 310; p += 37) {
        fseek(f, p, SEEK_SET);
        for (i = 0; i < 20 && p + i < 310; i++) {
            int want = p + i < 300 ? 'A' + ((p + i) % 26) : '0' + (int)(p + i - 300);
            if (fgetc(f) != want) bad++;
        }
        if (ftell(f) != p + i) bad++;
    }
    fclose(f);
    printf("bad=%d\n", bad);
    return 0;
}
""")
    assert out.split("\n")[0] == "bad=0"


def test_ftell_after_ungetc_and_at_end_of_file(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f = fopen("b.txt", "w");
    long a, b, c, d;
    int ch;
    fputs("hello", f);
    fclose(f);
    f = fopen("b.txt", "r");
    ch = fgetc(f); ch = fgetc(f);
    a = ftell(f);
    ungetc(ch, f);
    b = ftell(f);
    fgetc(f);
    while ((ch = fgetc(f)) != EOF) ;
    c = ftell(f);
    d = feof(f) != 0;
    fclose(f);
    printf("%ld %ld %ld %ld\n", a, b, c, d);
    return 0;
}
""")
    assert out.split("\n")[0] == "2 1 5 1"


def test_write_stream_can_seek_back_and_end(tmp_path):
    out = build_and_run(tmp_path, r"""
#include <stdio.h>
int main(void)
{
    FILE *f = fopen("c.txt", "w");
    char buf[16];
    long e;
    fputs("0123456789", f);
    fseek(f, 3, SEEK_SET);
    fputc('X', f);
    fseek(f, 0, SEEK_END);
    e = ftell(f);
    fputs("!", f);
    fclose(f);
    f = fopen("c.txt", "r");
    fgets(buf, 16, f);
    fclose(f);
    printf("%ld [%s]\n", e, buf);
    return 0;
}
""")
    assert out.split("\n")[0] == "10 [012X456789!]"


def test_fgetpos_and_fsetpos_round_trip(tmp_path):
    out = build_and_run(tmp_path, WRITE300 + r"""
int main(void)
{
    FILE *f;
    fpos_t pos;
    int a, b;
    make();
    f = fopen("data.txt", "r");
    fseek(f, 200, SEEK_SET);
    fgetpos(f, &pos);
    a = fgetc(f);
    fseek(f, 0, SEEK_SET);
    fsetpos(f, &pos);
    b = fgetc(f);
    fclose(f);
    printf("%d %d\n", a == b, a);
    return 0;
}
""")
    assert out.split("\n")[0] == "1 %d" % ord(letter(200))
