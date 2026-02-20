/* threads.h - Threading support for uc80/CP-M
 *
 * This implementation supports two modes:
 * 1. MP/M 2 mode: Real multi-processing using MP/M XDOS functions
 * 2. Single-tasking mode: Uses DI/EI for mutual exclusion
 *
 * Note: True threads require MP/M 2. Under plain CP/M, thread
 * functions return error codes or simulate basic behavior.
 */
#ifndef _THREADS_H
#define _THREADS_H

/* C11/C23 thread_local storage class specifier */
#define thread_local _Thread_local

/* Include time.h for timespec if available */
#include <time.h>

/* Thread return values */
#define thrd_success  0
#define thrd_nomem    1
#define thrd_timedout 2
#define thrd_busy     3
#define thrd_error    4

/* Mutex types */
#define mtx_plain     0
#define mtx_recursive 1
#define mtx_timed     2

/* Thread handle type - for MP/M this is a process ID */
typedef unsigned char thrd_t;

/* Thread start function type */
typedef int (*thrd_start_t)(void *);

/* Mutex type */
typedef struct {
    unsigned char locked;      /* 0 = unlocked, 1 = locked */
    unsigned char type;        /* mtx_plain, mtx_recursive, mtx_timed */
    unsigned char owner;       /* Thread ID of owner (for recursive) */
    unsigned char count;       /* Recursion count */
    unsigned char queue[8];    /* MP/M queue name if using MP/M */
} mtx_t;

/* Condition variable type */
typedef struct {
    unsigned char waiters;     /* Number of waiting threads */
    unsigned char signaled;    /* Signal flag */
} cnd_t;

/* Thread-specific storage key */
typedef unsigned char tss_t;

/* Thread-specific storage destructor */
typedef void (*tss_dtor_t)(void *);

/* Once flag type */
typedef unsigned char once_flag;
#define ONCE_FLAG_INIT 0

/* Thread management functions */
int thrd_create(thrd_t *thr, thrd_start_t func, void *arg);
int thrd_equal(thrd_t lhs, thrd_t rhs);
thrd_t thrd_current(void);
int thrd_sleep(const struct timespec *duration, struct timespec *remaining);
void thrd_yield(void);
_Noreturn void thrd_exit(int res);
int thrd_detach(thrd_t thr);
int thrd_join(thrd_t thr, int *res);

/* Mutex functions */
int mtx_init(mtx_t *mtx, int type);
int mtx_lock(mtx_t *mtx);
int mtx_timedlock(mtx_t *mtx, const struct timespec *ts);
int mtx_trylock(mtx_t *mtx);
int mtx_unlock(mtx_t *mtx);
void mtx_destroy(mtx_t *mtx);

/* Condition variable functions */
int cnd_init(cnd_t *cond);
int cnd_signal(cnd_t *cond);
int cnd_broadcast(cnd_t *cond);
int cnd_wait(cnd_t *cond, mtx_t *mtx);
int cnd_timedwait(cnd_t *cond, mtx_t *mtx, const struct timespec *ts);
void cnd_destroy(cnd_t *cond);

/* Thread-specific storage functions */
int tss_create(tss_t *key, tss_dtor_t dtor);
void *tss_get(tss_t key);
int tss_set(tss_t key, void *val);
void tss_delete(tss_t key);

/* call_once */
void call_once(once_flag *flag, void (*func)(void));

/* MP/M detection and mode selection */
int __mpm_available(void);  /* Returns 1 if MP/M detected */
void __threads_use_mpm(int enable);  /* Force MP/M or DI/EI mode */

#endif /* _THREADS_H */
