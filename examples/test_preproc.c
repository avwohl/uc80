/* Test preprocessor functionality */

#include <stdio.h>
#include <string.h>

/* Simple macro */
#define VERSION 100
#define GREETING "Hello"

/* Function-like macro */
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define MIN(a, b) ((a) < (b) ? (a) : (b))

/* Conditional compilation */
#ifdef __UC80__
#define PLATFORM "UC80"
#else
#define PLATFORM "Unknown"
#endif

/* Stringification */
#define STR(x) #x
#define XSTR(x) STR(x)

/* Token pasting */
#define CONCAT(a, b) a ## b

void print_num(int n) {
    if (n < 0) {
        putchar('-');
        n = -n;
    }
    if (n >= 10) {
        print_num(n / 10);
    }
    putchar('0' + n % 10);
}

int main(void) {
    int a;
    int b;
    int CONCAT(my, var);  /* Creates variable 'myvar' */

    /* Test predefined macros - print date and time */
    puts("Build: " __DATE__ " " __TIME__);

    /* Test simple macros */
    puts(GREETING);
    print_num(VERSION);
    putchar('\n');

    /* Test function-like macros */
    a = 10;
    b = 20;
    print_num(MAX(a, b));
    putchar(' ');
    print_num(MIN(a, b));
    putchar('\n');

    /* Test conditional compilation */
    puts("Platform: " PLATFORM);

    /* Test token pasting */
    myvar = 42;
    print_num(myvar);
    putchar('\n');

    /* Test string functions from header */
    print_num(strlen("Test"));
    putchar('\n');

    return 0;
}
