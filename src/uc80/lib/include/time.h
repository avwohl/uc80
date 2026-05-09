/* time.h - Time types and functions for uc80 */
#ifndef _TIME_H
#define _TIME_H

/* Size type — pointer-width so libc stack offsets stay stable under --int=32 */
#ifndef _SIZE_T_DEFINED
#define _SIZE_T_DEFINED
#if __SIZEOF_POINTER__ == __SIZEOF_INT__
typedef unsigned int size_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_LONG__
typedef unsigned long size_t;
#elif __SIZEOF_POINTER__ == __SIZEOF_SHORT__
typedef unsigned short size_t;
#endif
#endif

/* NULL pointer */
#ifndef NULL
#define NULL ((void *)0)
#endif

/* Clock ticks per second (CP/M doesn't have real clock) */
#define CLOCKS_PER_SEC 100

/* Time types */
typedef long time_t;        /* Seconds since epoch */
typedef long clock_t;       /* Clock ticks */

/* Time structure */
struct tm {
    int tm_sec;     /* Seconds (0-60) */
    int tm_min;     /* Minutes (0-59) */
    int tm_hour;    /* Hours (0-23) */
    int tm_mday;    /* Day of month (1-31) */
    int tm_mon;     /* Month (0-11) */
    int tm_year;    /* Year - 1900 */
    int tm_wday;    /* Day of week (0-6, Sunday=0) */
    int tm_yday;    /* Day of year (0-365) */
    int tm_isdst;   /* Daylight saving flag */
};

/* High-resolution time structure (C11) */
struct timespec {
    time_t tv_sec;  /* Seconds */
    long tv_nsec;   /* Nanoseconds (0-999999999) */
};

/* Time base values for timespec_get */
#define TIME_UTC 1

/* Time functions (stubs - return dummy values on CP/M) */
time_t time(time_t *tloc);
clock_t clock(void);
double difftime(time_t time1, time_t time0);

/* Conversion functions */
struct tm *localtime(const time_t *timep);
struct tm *gmtime(const time_t *timep);
time_t mktime(struct tm *tm);

/* String functions */
char *asctime(const struct tm *tm);
char *ctime(const time_t *timep);
size_t strftime(char *s, size_t max, const char *format, const struct tm *tm);

#endif /* _TIME_H */
