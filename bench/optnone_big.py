"""A program of N functions for the size test of optnone (docs/optnone.md).

    python3 bench/optnone_big.py 400 > big.c

Each function has a loop, an update of an array and a call, about 115
bytes of code.  main calls all of them and prints a checksum.  With N = 400
the program fits in 64K, and the same program with --optnone=all does not.
"""
import sys
n=int(sys.argv[1])
out=["#include <stdio.h>","int g[8];","int sink(int v) { g[v & 7] += v; return g[(v + 1) & 7]; }"]
for i in range(n):
    out.append(f"""int f{i}(int a, int b)
{{
    int s = {i}, k;
    for (k = 0; k < a; k++) {{
        s += b * k + (k ^ {i * 7 + 3});
        if (s > {1000 + i}) s -= {i + 5};
    }}
    g[{i % 8}] += s;
    return sink(s + {i}) + b;
}}""")
out.append("int main(void)\n{\n    int t = 0;")
for i in range(n):
    out.append(f"    t += f{i}({i % 5 + 1}, {i % 7 + 2});")
out.append("    printf(\"%d\\n\", t);\n    return 0;\n}")
print("\n".join(out))
