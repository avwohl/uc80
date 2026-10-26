/* m.h - shared parts of the math library modules.
 *
 * double, float and long double are one 32-bit IEEE single type here, so
 * each function is written once and gets the f and l names as aliases (see
 * gen_c_modules.py).  The runtime has no subnormal numbers: float
 * arithmetic treats a subnormal operand as zero and flushes a subnormal
 * result.  The functions that only move bits (frexp, ldexp, ilogb, ...) do
 * handle subnormal numbers.
 */
#ifndef M_H
#define M_H

typedef union {
    float f;
    unsigned long u;
    unsigned char b[4];
} m_u;

/* Parts of a value in a union.  The tests use the bytes: a 32-bit shift or
 * mask costs several times more code on the Z80 than a byte test. */
#define M_EXP(v) ((int)((((v).b[3] & 0x7F) << 1) | ((v).b[2] >> 7)))
#define M_NEG(v) ((v).b[3] & 0x80)
#define M_MANT(v) ((v).u & 0x7FFFFFUL)
#define M_ABSBITS(v) ((v).u & 0x7FFFFFFFUL)
/* exponent 255: an infinity or a NaN */
#define M_SPEC(v) (((v).b[3] & 0x7F) == 0x7F && ((v).b[2] & 0x80))
/* not zero if the mantissa bits are not zero */
#define M_NANM(v) (((v).b[2] & 0x7F) | (v).b[1] | (v).b[0])
#define M_ISNAN(v) (M_SPEC(v) && M_NANM(v))
#define M_ISZERO(v) (!(((v).b[3] & 0x7F) | (v).b[2] | (v).b[1] | (v).b[0]))
/* |x| < 2^-13, and |x| < 2^-25 */
#define M_TINY(v) (((v).b[3] & 0x7F) < 57)
#define M_TINY25(v) (((v).b[3] & 0x7F) < 51)

#define M_INF 0x7F800000UL
#define M_NAN 0x7FC00000UL

/* The value (-1)^neg * mant * 2^(e-150) for a normalized mant (2^23 to
 * 2^24-1): rounds to nearest even into a subnormal, gives inf on overflow. */
float _m_pack(int neg, unsigned long mant, int e);
/* The normalized mantissa (bit 23 set) of a finite x and its biased
 * exponent e (zero or less for a subnormal x); 0 for x = 0. */
unsigned long _m_unpack(float x, int *e);
/* round x to an integer value: mode 0 trunc, 1 floor, 2 ceil,
 * 3 round (half away from zero), 4 rint (half to even) */
float _m_round(float x, int mode);
/* an integer-valued float as a long long (saturates) */
long long _m_toll(float r);
/* Error-free transformation: p + *e = a * b exactly.  Only cbrt uses it;
 * the other math functions are programs of the float interpreter
 * (docs/libm_vm.md). */
float _m_2prod(float a, float b, float *e);

/* Veltkamp split of the float A into HI + LO, each with 12 bits: the
 * product of two such parts is exact.  The compiler does not reassociate
 * float expressions, so these macros keep their meaning. */
#define M_SPLIT(a, hi, lo) \
    do { \
        float c_ = 4097.0f * (a); \
        (hi) = c_ - (c_ - (a)); \
        (lo) = (a) - (hi); \
    } while (0)

#endif
