/* Test ctype functions */
#include <stdio.h>
#include <ctype.h>

int main() {
    puts("ctype Test");

    /* Test isdigit */
    if (isdigit('5')) puts("isdigit('5'): OK");
    else puts("isdigit('5'): FAIL");

    if (!isdigit('a')) puts("!isdigit('a'): OK");
    else puts("!isdigit('a'): FAIL");

    /* Test isalpha */
    if (isalpha('A')) puts("isalpha('A'): OK");
    else puts("isalpha('A'): FAIL");

    if (isalpha('z')) puts("isalpha('z'): OK");
    else puts("isalpha('z'): FAIL");

    if (!isalpha('5')) puts("!isalpha('5'): OK");
    else puts("!isalpha('5'): FAIL");

    /* Test toupper/tolower */
    if (toupper('a') == 'A') puts("toupper('a'): OK");
    else puts("toupper('a'): FAIL");

    if (tolower('Z') == 'z') puts("tolower('Z'): OK");
    else puts("tolower('Z'): FAIL");

    /* Test isspace */
    if (isspace(' ')) puts("isspace(' '): OK");
    else puts("isspace(' '): FAIL");

    if (isspace('\t')) puts("isspace('\\t'): OK");
    else puts("isspace('\\t'): FAIL");

    if (!isspace('x')) puts("!isspace('x'): OK");
    else puts("!isspace('x'): FAIL");

    /* Test isxdigit */
    if (isxdigit('F')) puts("isxdigit('F'): OK");
    else puts("isxdigit('F'): FAIL");

    if (isxdigit('a')) puts("isxdigit('a'): OK");
    else puts("isxdigit('a'): FAIL");

    if (!isxdigit('G')) puts("!isxdigit('G'): OK");
    else puts("!isxdigit('G'): FAIL");

    puts("Done!");
    return 0;
}
