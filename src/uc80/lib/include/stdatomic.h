/* stdatomic.h - Atomic operations for uc80/CP-M
 *
 * This implementation uses DI/EI (disable/enable interrupts) to provide
 * atomicity on the Z80, which has no hardware atomic instructions.
 *
 * Memory ordering: Since Z80 is single-core and has no cache coherency
 * issues, all memory orders are effectively sequential consistency.
 */
#ifndef _STDATOMIC_H
#define _STDATOMIC_H

/* Memory order types (all equivalent on single-core Z80) */
typedef enum {
    memory_order_relaxed,
    memory_order_consume,
    memory_order_acquire,
    memory_order_release,
    memory_order_acq_rel,
    memory_order_seq_cst
} memory_order;

/* Atomic flag type - lock-free boolean flag */
typedef struct {
    unsigned char value;
} atomic_flag;

/* Initial value for atomic_flag */
#define ATOMIC_FLAG_INIT { 0 }

/* Atomic flag operations */
_Bool atomic_flag_test_and_set(volatile atomic_flag *obj);
_Bool atomic_flag_test_and_set_explicit(volatile atomic_flag *obj, memory_order order);
void atomic_flag_clear(volatile atomic_flag *obj);
void atomic_flag_clear_explicit(volatile atomic_flag *obj, memory_order order);

/* Atomic integer types */
typedef struct { _Bool value; } atomic_bool;
typedef struct { char value; } atomic_char;
typedef struct { signed char value; } atomic_schar;
typedef struct { unsigned char value; } atomic_uchar;
typedef struct { short value; } atomic_short;
typedef struct { unsigned short value; } atomic_ushort;
typedef struct { int value; } atomic_int;
typedef struct { unsigned int value; } atomic_uint;
typedef struct { long value; } atomic_long;
typedef struct { unsigned long value; } atomic_ulong;

/* Size type atomic */
typedef struct { unsigned int value; } atomic_size_t;
typedef struct { int value; } atomic_ptrdiff_t;
typedef struct { int value; } atomic_intptr_t;
typedef struct { unsigned int value; } atomic_uintptr_t;

/* Initialize atomic object (simple assignment is safe before use) */
#define ATOMIC_VAR_INIT(value) { value }

/* Atomic initialization */
#define atomic_init(obj, val) ((void)((obj)->value = (val)))

/* Generic atomic store */
void __atomic_store_1(volatile void *obj, unsigned char val);
void __atomic_store_2(volatile void *obj, unsigned int val);
void __atomic_store_4(volatile void *obj, unsigned long val);

#define atomic_store(obj, val) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_store_1, \
        volatile atomic_char*: __atomic_store_1, \
        volatile atomic_schar*: __atomic_store_1, \
        volatile atomic_uchar*: __atomic_store_1, \
        volatile atomic_short*: __atomic_store_2, \
        volatile atomic_ushort*: __atomic_store_2, \
        volatile atomic_int*: __atomic_store_2, \
        volatile atomic_uint*: __atomic_store_2, \
        volatile atomic_long*: __atomic_store_4, \
        volatile atomic_ulong*: __atomic_store_4, \
        default: __atomic_store_2 \
    )((void*)(obj), (val))

#define atomic_store_explicit(obj, val, order) atomic_store(obj, val)

/* Generic atomic load */
unsigned char __atomic_load_1(volatile void *obj);
unsigned int __atomic_load_2(volatile void *obj);
unsigned long __atomic_load_4(volatile void *obj);

#define atomic_load(obj) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_load_1, \
        volatile atomic_char*: __atomic_load_1, \
        volatile atomic_schar*: __atomic_load_1, \
        volatile atomic_uchar*: __atomic_load_1, \
        volatile atomic_short*: __atomic_load_2, \
        volatile atomic_ushort*: __atomic_load_2, \
        volatile atomic_int*: __atomic_load_2, \
        volatile atomic_uint*: __atomic_load_2, \
        volatile atomic_long*: __atomic_load_4, \
        volatile atomic_ulong*: __atomic_load_4, \
        default: __atomic_load_2 \
    )((void*)(obj))

#define atomic_load_explicit(obj, order) atomic_load(obj)

/* Generic atomic exchange */
unsigned char __atomic_exchange_1(volatile void *obj, unsigned char val);
unsigned int __atomic_exchange_2(volatile void *obj, unsigned int val);
unsigned long __atomic_exchange_4(volatile void *obj, unsigned long val);

#define atomic_exchange(obj, val) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_exchange_1, \
        volatile atomic_char*: __atomic_exchange_1, \
        volatile atomic_schar*: __atomic_exchange_1, \
        volatile atomic_uchar*: __atomic_exchange_1, \
        volatile atomic_short*: __atomic_exchange_2, \
        volatile atomic_ushort*: __atomic_exchange_2, \
        volatile atomic_int*: __atomic_exchange_2, \
        volatile atomic_uint*: __atomic_exchange_2, \
        volatile atomic_long*: __atomic_exchange_4, \
        volatile atomic_ulong*: __atomic_exchange_4, \
        default: __atomic_exchange_2 \
    )((void*)(obj), (val))

#define atomic_exchange_explicit(obj, val, order) atomic_exchange(obj, val)

/* Generic atomic compare-exchange */
_Bool __atomic_compare_exchange_1(volatile void *obj, void *expected, unsigned char desired);
_Bool __atomic_compare_exchange_2(volatile void *obj, void *expected, unsigned int desired);
_Bool __atomic_compare_exchange_4(volatile void *obj, void *expected, unsigned long desired);

#define atomic_compare_exchange_strong(obj, expected, desired) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_compare_exchange_1, \
        volatile atomic_char*: __atomic_compare_exchange_1, \
        volatile atomic_schar*: __atomic_compare_exchange_1, \
        volatile atomic_uchar*: __atomic_compare_exchange_1, \
        volatile atomic_short*: __atomic_compare_exchange_2, \
        volatile atomic_ushort*: __atomic_compare_exchange_2, \
        volatile atomic_int*: __atomic_compare_exchange_2, \
        volatile atomic_uint*: __atomic_compare_exchange_2, \
        volatile atomic_long*: __atomic_compare_exchange_4, \
        volatile atomic_ulong*: __atomic_compare_exchange_4, \
        default: __atomic_compare_exchange_2 \
    )((void*)(obj), (void*)(expected), (desired))

/* weak is same as strong on Z80 */
#define atomic_compare_exchange_weak(obj, expected, desired) \
    atomic_compare_exchange_strong(obj, expected, desired)

#define atomic_compare_exchange_strong_explicit(obj, exp, des, succ, fail) \
    atomic_compare_exchange_strong(obj, exp, des)
#define atomic_compare_exchange_weak_explicit(obj, exp, des, succ, fail) \
    atomic_compare_exchange_weak(obj, exp, des)

/* Atomic fetch-and-add operations */
unsigned char __atomic_fetch_add_1(volatile void *obj, unsigned char val);
unsigned int __atomic_fetch_add_2(volatile void *obj, unsigned int val);
unsigned long __atomic_fetch_add_4(volatile void *obj, unsigned long val);

#define atomic_fetch_add(obj, val) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_fetch_add_1, \
        volatile atomic_char*: __atomic_fetch_add_1, \
        volatile atomic_schar*: __atomic_fetch_add_1, \
        volatile atomic_uchar*: __atomic_fetch_add_1, \
        volatile atomic_short*: __atomic_fetch_add_2, \
        volatile atomic_ushort*: __atomic_fetch_add_2, \
        volatile atomic_int*: __atomic_fetch_add_2, \
        volatile atomic_uint*: __atomic_fetch_add_2, \
        volatile atomic_long*: __atomic_fetch_add_4, \
        volatile atomic_ulong*: __atomic_fetch_add_4, \
        default: __atomic_fetch_add_2 \
    )((void*)(obj), (val))

#define atomic_fetch_add_explicit(obj, val, order) atomic_fetch_add(obj, val)

/* Atomic fetch-and-subtract */
unsigned char __atomic_fetch_sub_1(volatile void *obj, unsigned char val);
unsigned int __atomic_fetch_sub_2(volatile void *obj, unsigned int val);
unsigned long __atomic_fetch_sub_4(volatile void *obj, unsigned long val);

#define atomic_fetch_sub(obj, val) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_fetch_sub_1, \
        volatile atomic_char*: __atomic_fetch_sub_1, \
        volatile atomic_schar*: __atomic_fetch_sub_1, \
        volatile atomic_uchar*: __atomic_fetch_sub_1, \
        volatile atomic_short*: __atomic_fetch_sub_2, \
        volatile atomic_ushort*: __atomic_fetch_sub_2, \
        volatile atomic_int*: __atomic_fetch_sub_2, \
        volatile atomic_uint*: __atomic_fetch_sub_2, \
        volatile atomic_long*: __atomic_fetch_sub_4, \
        volatile atomic_ulong*: __atomic_fetch_sub_4, \
        default: __atomic_fetch_sub_2 \
    )((void*)(obj), (val))

#define atomic_fetch_sub_explicit(obj, val, order) atomic_fetch_sub(obj, val)

/* Atomic fetch-and-or */
unsigned char __atomic_fetch_or_1(volatile void *obj, unsigned char val);
unsigned int __atomic_fetch_or_2(volatile void *obj, unsigned int val);
unsigned long __atomic_fetch_or_4(volatile void *obj, unsigned long val);

#define atomic_fetch_or(obj, val) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_fetch_or_1, \
        volatile atomic_char*: __atomic_fetch_or_1, \
        volatile atomic_schar*: __atomic_fetch_or_1, \
        volatile atomic_uchar*: __atomic_fetch_or_1, \
        volatile atomic_short*: __atomic_fetch_or_2, \
        volatile atomic_ushort*: __atomic_fetch_or_2, \
        volatile atomic_int*: __atomic_fetch_or_2, \
        volatile atomic_uint*: __atomic_fetch_or_2, \
        volatile atomic_long*: __atomic_fetch_or_4, \
        volatile atomic_ulong*: __atomic_fetch_or_4, \
        default: __atomic_fetch_or_2 \
    )((void*)(obj), (val))

#define atomic_fetch_or_explicit(obj, val, order) atomic_fetch_or(obj, val)

/* Atomic fetch-and-xor */
unsigned char __atomic_fetch_xor_1(volatile void *obj, unsigned char val);
unsigned int __atomic_fetch_xor_2(volatile void *obj, unsigned int val);
unsigned long __atomic_fetch_xor_4(volatile void *obj, unsigned long val);

#define atomic_fetch_xor(obj, val) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_fetch_xor_1, \
        volatile atomic_char*: __atomic_fetch_xor_1, \
        volatile atomic_schar*: __atomic_fetch_xor_1, \
        volatile atomic_uchar*: __atomic_fetch_xor_1, \
        volatile atomic_short*: __atomic_fetch_xor_2, \
        volatile atomic_ushort*: __atomic_fetch_xor_2, \
        volatile atomic_int*: __atomic_fetch_xor_2, \
        volatile atomic_uint*: __atomic_fetch_xor_2, \
        volatile atomic_long*: __atomic_fetch_xor_4, \
        volatile atomic_ulong*: __atomic_fetch_xor_4, \
        default: __atomic_fetch_xor_2 \
    )((void*)(obj), (val))

#define atomic_fetch_xor_explicit(obj, val, order) atomic_fetch_xor(obj, val)

/* Atomic fetch-and-and */
unsigned char __atomic_fetch_and_1(volatile void *obj, unsigned char val);
unsigned int __atomic_fetch_and_2(volatile void *obj, unsigned int val);
unsigned long __atomic_fetch_and_4(volatile void *obj, unsigned long val);

#define atomic_fetch_and(obj, val) \
    _Generic((obj), \
        volatile atomic_bool*: __atomic_fetch_and_1, \
        volatile atomic_char*: __atomic_fetch_and_1, \
        volatile atomic_schar*: __atomic_fetch_and_1, \
        volatile atomic_uchar*: __atomic_fetch_and_1, \
        volatile atomic_short*: __atomic_fetch_and_2, \
        volatile atomic_ushort*: __atomic_fetch_and_2, \
        volatile atomic_int*: __atomic_fetch_and_2, \
        volatile atomic_uint*: __atomic_fetch_and_2, \
        volatile atomic_long*: __atomic_fetch_and_4, \
        volatile atomic_ulong*: __atomic_fetch_and_4, \
        default: __atomic_fetch_and_2 \
    )((void*)(obj), (val))

#define atomic_fetch_and_explicit(obj, val, order) atomic_fetch_and(obj, val)

/* Thread fence (no-op on single-core Z80) */
#define atomic_thread_fence(order) ((void)0)
#define atomic_signal_fence(order) ((void)0)

/* Lock-free query macros - Z80 uses DI/EI so nothing is truly lock-free */
#define ATOMIC_BOOL_LOCK_FREE     0
#define ATOMIC_CHAR_LOCK_FREE     0
#define ATOMIC_CHAR16_T_LOCK_FREE 0
#define ATOMIC_CHAR32_T_LOCK_FREE 0
#define ATOMIC_WCHAR_T_LOCK_FREE  0
#define ATOMIC_SHORT_LOCK_FREE    0
#define ATOMIC_INT_LOCK_FREE      0
#define ATOMIC_LONG_LOCK_FREE     0
#define ATOMIC_LLONG_LOCK_FREE    0
#define ATOMIC_POINTER_LOCK_FREE  0

/* Check if atomic type is lock-free */
#define atomic_is_lock_free(obj) (0)

/* Kill dependency (no-op) */
#define kill_dependency(y) (y)

#endif /* _STDATOMIC_H */
