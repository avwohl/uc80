/* locale.h - Localization support (minimal C locale) */
#ifndef _LOCALE_H
#define _LOCALE_H

/* Locale categories */
#define LC_ALL      0
#define LC_COLLATE  1
#define LC_CTYPE    2
#define LC_MONETARY 3
#define LC_NUMERIC  4
#define LC_TIME     5

/* Null pointer macro */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* lconv structure - numeric/monetary formatting info */
struct lconv {
    /* Numeric (non-monetary) formatting */
    char *decimal_point;     /* Decimal point character (default ".") */
    char *thousands_sep;     /* Thousands separator (default "") */
    char *grouping;          /* Grouping (default "") */

    /* Monetary formatting (international) */
    char *int_curr_symbol;   /* International currency symbol (default "") */

    /* Monetary formatting (local) */
    char *currency_symbol;   /* Local currency symbol (default "") */
    char *mon_decimal_point; /* Monetary decimal point (default "") */
    char *mon_thousands_sep; /* Monetary thousands separator (default "") */
    char *mon_grouping;      /* Monetary grouping (default "") */
    char *positive_sign;     /* Positive sign (default "") */
    char *negative_sign;     /* Negative sign (default "") */

    /* Monetary format values */
    char int_frac_digits;    /* International fractional digits (default CHAR_MAX) */
    char frac_digits;        /* Local fractional digits (default CHAR_MAX) */
    char p_cs_precedes;      /* 1 if currency symbol precedes positive (default CHAR_MAX) */
    char p_sep_by_space;     /* 1 if space between currency and positive (default CHAR_MAX) */
    char n_cs_precedes;      /* 1 if currency symbol precedes negative (default CHAR_MAX) */
    char n_sep_by_space;     /* 1 if space between currency and negative (default CHAR_MAX) */
    char p_sign_posn;        /* Position of positive sign (default CHAR_MAX) */
    char n_sign_posn;        /* Position of negative sign (default CHAR_MAX) */
};

/* Set locale - returns pointer to locale string or NULL on failure
 * Only "C" and "" (default) locales are supported
 */
char *setlocale(int category, const char *locale);

/* Get locale conversion structure */
struct lconv *localeconv(void);

#endif /* _LOCALE_H */
