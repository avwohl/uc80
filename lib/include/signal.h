/* signal.h - Signal handling stubs for uc80/CP-M */
#ifndef _SIGNAL_H
#define _SIGNAL_H

/* Signal handler function type */
typedef void (*sig_handler_t)(int);

/* Special signal handler values */
#define SIG_DFL ((sig_handler_t)0)  /* Default signal handling */
#define SIG_IGN ((sig_handler_t)1)  /* Ignore signal */
#define SIG_ERR ((sig_handler_t)-1) /* Error return */

/* Signal numbers (CP/M doesn't support signals, but define for compatibility) */
#define SIGABRT 1  /* Abnormal termination */
#define SIGFPE  2  /* Floating-point exception */
#define SIGILL  3  /* Illegal instruction */
#define SIGINT  4  /* Interrupt */
#define SIGSEGV 5  /* Segmentation violation */
#define SIGTERM 6  /* Termination request */

/* Number of signals */
#define _NSIG 7

/* Signal functions (stubs - always succeed but do nothing on CP/M) */
sig_handler_t signal(int sig, sig_handler_t handler);
int raise(int sig);

#endif /* _SIGNAL_H */
