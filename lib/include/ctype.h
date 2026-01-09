/* ctype.h - Character classification for uc80 */
#ifndef _CTYPE_H
#define _CTYPE_H

/* Character classification macros
 * These are implemented as macros for efficiency on Z80
 */

/* Check for alphanumeric character */
#define isalnum(c) (isalpha(c) || isdigit(c))

/* Check for alphabetic character */
#define isalpha(c) (((c) >= 'A' && (c) <= 'Z') || ((c) >= 'a' && (c) <= 'z'))

/* Check for blank character (space or tab) */
#define isblank(c) ((c) == ' ' || (c) == '\t')

/* Check for control character */
#define iscntrl(c) (((c) >= 0 && (c) <= 31) || (c) == 127)

/* Check for digit */
#define isdigit(c) ((c) >= '0' && (c) <= '9')

/* Check for printable character excluding space */
#define isgraph(c) ((c) > ' ' && (c) <= '~')

/* Check for lowercase letter */
#define islower(c) ((c) >= 'a' && (c) <= 'z')

/* Check for printable character including space */
#define isprint(c) ((c) >= ' ' && (c) <= '~')

/* Check for punctuation character */
#define ispunct(c) (isgraph(c) && !isalnum(c))

/* Check for whitespace character */
#define isspace(c) ((c) == ' ' || (c) == '\t' || (c) == '\n' || \
                    (c) == '\r' || (c) == '\f' || (c) == '\v')

/* Check for uppercase letter */
#define isupper(c) ((c) >= 'A' && (c) <= 'Z')

/* Check for hexadecimal digit */
#define isxdigit(c) (isdigit(c) || \
                     ((c) >= 'A' && (c) <= 'F') || \
                     ((c) >= 'a' && (c) <= 'f'))

/* Convert to lowercase */
#define tolower(c) (isupper(c) ? ((c) + ('a' - 'A')) : (c))

/* Convert to uppercase */
#define toupper(c) (islower(c) ? ((c) - ('a' - 'A')) : (c))

/* Function versions (for use as function pointers)
 * Parentheses around names prevent macro expansion */
int (isdigit)(int c);
int (isalpha)(int c);
int (isalnum)(int c);
int (isspace)(int c);
int (isupper)(int c);
int (islower)(int c);
int (isprint)(int c);
int (isxdigit)(int c);
int (toupper)(int c);
int (tolower)(int c);

#endif /* _CTYPE_H */
