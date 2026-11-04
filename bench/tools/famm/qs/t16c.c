#include <stdio.h>
typedef struct { const char *name; int (*fn)(int); } Op;
typedef struct { int pad; Op ops[32]; } St;
static St st;
static St *G = &st;
static int f1(int x){return x+1;} static int f2(int x){return x*2;} static int f3(int x){return x-3;}
static int f4(int x){return x^5;} static int f5(int x){return -x;}
#define OP(n,nm,f) ops[n].name = nm; ops[n].fn = f
#define OPN(n,nm) ops[n].name = nm
static void setup(void) {
    Op *ops = G->ops;
    OP(1,"one",f1); OP(2,"two",f2); OP(3,"three",f3); OPN(4,"four"); OP(5,"five",f4); OP(6,"six",f5); OP(8,"eight",f1);
    OPN(9,"nine"); OP(10,"ten",f2); OP(11,"eleven",f3); OP(12,"twelve",f4);
}
int main(void) {
    int i, s = 0;
    setup();
    for (i = 0; i < 16; i++) {
        printf("%d:%s:", i, G->ops[i].name ? G->ops[i].name : "-");
        if (G->ops[i].fn) { s += G->ops[i].fn(i); printf("%d", s); }
        printf("\n");
    }
    return 0;
}
