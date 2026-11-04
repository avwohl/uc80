#include <stdio.h>
#define MAXN 1100
int a[MAXN + 4];
int ref[MAXN];
extern void qwtest(int *base, unsigned n, int (*cmp)(const void *, const void *));
int ncmp;
int cmp_int(const void *x, const void *y) { ncmp++; return *(const int *)x - *(const int *)y; }
static unsigned long seed = 12345;
static int rnd(void) { seed = seed * 1103515245UL + 12345UL; return (int)((seed >> 8) & 0x7fff); }
static int fails;
static void run(unsigned n, int pat) {
    unsigned i; long s1 = 0, s2 = 0, x1 = 0;
    a[0] = 0x1234; a[n + 1] = 0x4321;   /* guards around a[1..n] */
    for (i = 0; i < n; i++) {
        int v;
        switch (pat) {
        case 0: v = rnd(); break;
        case 1: v = i; break;
        case 2: v = n - i; break;
        case 3: v = 7; break;
        case 4: v = i < n / 2 ? i : n - i; break;       /* organ pipe */
        case 5: v = i % 5; break;                         /* sawtooth */
        case 6: v = rnd() & 3; break;                     /* few unique */
        case 7: v = (i & 1) ? i : n - i; break;
        case 8: v = n - i + (rnd() & 1); break;           /* nearly reversed */
        default: v = i + (rnd() & 1); break;              /* nearly sorted */
        }
        a[1 + i] = v; s1 += v; s2 += (long)v * v; x1 ^= v;
    }
    ncmp = 0;
    qwtest(&a[1], n, cmp_int);
    {
        long t1 = 0, t2 = 0, y1 = 0;
        for (i = 0; i < n; i++) {
            int v = a[1 + i]; t1 += v; t2 += (long)v * v; y1 ^= v;
            if (i && a[i] > v) { fails++; printf("UNSORTED n=%u pat=%d at %u\n", n, pat, i); break; }
        }
        if (t1 != s1 || t2 != s2 || y1 != x1) { fails++; printf("MULTISET n=%u pat=%d\n", n, pat); }
        if (a[0] != 0x1234 || a[n + 1] != 0x4321) { fails++; printf("GUARD n=%u pat=%d\n", n, pat); }
    }
}
int main(void) {
    unsigned n; int p;
    for (n = 0; n <= 48; n++) for (p = 0; p < 10; p++) run(n, p);
    for (p = 0; p < 10; p++) { run(63, p); run(64, p); run(65, p); run(127, p); run(255, p); run(257, p); run(1000, p); }
    printf(fails ? "FAILED %d\n" : "ALL OK\n", fails);
    return 0;
}
