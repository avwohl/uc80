/* Test setjmp/longjmp functionality */
#include <stdio.h>
#include <setjmp.h>

jmp_buf env;
int counter;

void second() {
    puts("second: calling longjmp");
    longjmp(env, 2);
    puts("second: after longjmp - should not print");
}

void first() {
    puts("first: calling second");
    second();
    puts("first: after second - should not print");
}

int main() {
    int val;

    puts("Test setjmp/longjmp");

    counter = 0;
    val = setjmp(env);

    if (val == 0) {
        puts("setjmp returned 0 (direct call)");
        first();
        puts("after first - should not print");
    } else {
        puts("setjmp returned non-zero (from longjmp)");
        /* Print the return value */
        putchar('v');
        putchar('a');
        putchar('l');
        putchar('=');
        putchar('0' + val);
        putchar(10);
    }

    /* Test longjmp with val=0 (should become 1) */
    counter++;
    if (counter == 1) {
        puts("Testing longjmp(env, 0)");
        val = setjmp(env);
        if (val == 0) {
            longjmp(env, 0);  /* Should return 1 */
        } else {
            puts("longjmp(env,0) returned:");
            putchar('0' + val);
            putchar(10);
        }
    }

    puts("Test complete");
    return 0;
}
