/* Test memory and string functions */
#include <stdio.h>
#include <string.h>

int main() {
    char buf[64];
    char *p;

    puts("Memory/String Test");

    /* Test strcpy and strcmp */
    strcpy(buf, "Hello");
    if (strcmp(buf, "Hello") == 0) puts("strcpy/strcmp: OK");
    else puts("strcpy/strcmp: FAIL");

    /* Test strcat */
    strcat(buf, " World");
    if (strcmp(buf, "Hello World") == 0) puts("strcat: OK");
    else puts("strcat: FAIL");

    /* Test strlen */
    if (strlen(buf) == 11) puts("strlen: OK");
    else puts("strlen: FAIL");

    /* Test strchr */
    p = strchr(buf, 'W');
    if (p && *p == 'W') puts("strchr: OK");
    else puts("strchr: FAIL");

    /* Test strrchr */
    strcpy(buf, "a/b/c");
    p = strrchr(buf, '/');
    if (p && p[1] == 'c') puts("strrchr: OK");
    else puts("strrchr: FAIL");

    /* Test strstr */
    strcpy(buf, "Hello World");
    p = strstr(buf, "Wor");
    if (p && strcmp(p, "World") == 0) puts("strstr: OK");
    else puts("strstr: FAIL");

    /* Test memcpy */
    memcpy(buf, "ABCDEF", 6);
    buf[6] = 0;
    if (strcmp(buf, "ABCDEF") == 0) puts("memcpy: OK");
    else puts("memcpy: FAIL");

    /* Test memset */
    memset(buf, 'X', 5);
    buf[5] = 0;
    if (strcmp(buf, "XXXXX") == 0) puts("memset: OK");
    else puts("memset: FAIL");

    /* Test memchr */
    strcpy(buf, "Find the X here");
    p = memchr(buf, 'X', 15);
    if (p && *p == 'X') puts("memchr: OK");
    else puts("memchr: FAIL");

    /* Test memcmp */
    if (memcmp("abc", "abc", 3) == 0) puts("memcmp equal: OK");
    else puts("memcmp equal: FAIL");

    if (memcmp("abc", "abd", 3) < 0) puts("memcmp less: OK");
    else puts("memcmp less: FAIL");

    puts("Done!");
    return 0;
}
