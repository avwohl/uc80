/* signal.h - Signal handling for uc80/CP-M */
#ifndef _SIGNAL_H
#define _SIGNAL_H

/* Signal handler function type */
typedef void (*sig_handler_t)(int);

/* Special signal handler values */
#define SIG_DFL ((sig_handler_t)0)  /* Default signal handling */
#define SIG_IGN ((sig_handler_t)1)  /* Ignore signal */
#define SIG_ERR ((sig_handler_t)-1) /* Error return */

/* Signal numbers */
#define SIGABRT 1  /* Abnormal termination */
#define SIGFPE  2  /* Floating-point exception */
#define SIGILL  3  /* Illegal instruction */
#define SIGINT  4  /* Interrupt */
#define SIGSEGV 5  /* Segmentation violation */
#define SIGTERM 6  /* Termination request */

/* Number of signals (internal) */
#define _NSIG 7

/* Set signal handler - returns previous handler or SIG_ERR on error */
sig_handler_t signal(int sig, sig_handler_t handler);

/* Raise a signal - returns 0 on success, non-zero on error */
int raise(int sig);

#endif /* _SIGNAL_H */
