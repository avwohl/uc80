# Configurable Integer Type Sizes

Design research for adding compiler switches to configure integer type sizes,
allowing tests that assume 32-bit int to pass.

## Current Sizes (hardcoded)

	Type		Size	Registers
	char		1	A/L
	short		2	HL
	int		2	HL
	long		4	DEHL
	long long	8	__acc64/__tmp64
	pointer		2	HL
	float/double	4	DEHL (IEEE 754)

## Proposed Switches

	--int-size 32	Make int 4 bytes (default: 16-bit)
	--long-size 64	Make long 8 bytes (default: 32-bit)

Constraints enforced: short <= int <= long <= long long.

## Naming Convention: is_int4

Replace type-name-based dispatch with byte-width-based dispatch:

	Current			New		Meaning
	_is_long_type()		_is_int4()	4-byte integer type
	_is_long_long_type()	_is_int8()	8-byte integer type
	(implicit 16-bit)	_is_int2()	2-byte integer type

With --int-size 32, int matches _is_int4() instead of _is_int2().
Both int and long become 4-byte, so DEHL register pair is used for both.
This matches the existing code pattern with minimal conceptual change.

## TypeConfig Dataclass

```python
@dataclass
class TypeConfig:
    char_size: int = 1       # always 1
    short_size: int = 2      # always 2
    int_size: int = 2        # 2 or 4
    long_size: int = 4       # 4 or 8
    long_long_size: int = 8  # always 8
    ptr_size: int = 2        # always 2 (Z80)
    float_size: int = 4      # always 4

    @property
    def int_max(self):
        return (1 << (self.int_size * 8 - 1)) - 1

    @property
    def uint_max(self):
        return (1 << (self.int_size * 8)) - 1

    # similar for long, short, long_long
```

Constructed from CLI switches and threaded through CodeGenerator and
ASTOptimizer constructors.

## Call Sites to Refactor

The codegen dispatches on type name in ~150 places in codegen.py.
Key functions that must change:

	Function		Location	Change
	_type_size()		codegen.py:8732	Use TypeConfig lookup
	_is_long_type()		codegen.py:8184	Becomes _is_int4()
	_is_long_long_type()	codegen.py:8190	Becomes _is_int8()
	_is_long_expr()		codegen.py:8295	Check width >= 4 bytes
	_is_long_long_expr()	codegen.py:8242	Check width >= 8 bytes
	_get_expr_type()	codegen.py:7764	Literal thresholds from TypeConfig
	_is_promoted_unsigned()	codegen.py:8164	Promotion rules change

Each _is_long_expr() call site must be audited: some mean "needs DEHL
registers" (becomes _is_int4), others mean "is C long" (different
semantics). With --int-size 32 both int and long are 4 bytes so _is_int4
handles both cases naturally.

### AST Optimizer (ast_optimizer.py)

	_sizeof_type()		line 1677	Use TypeConfig
	_literal_mask()		line 1564	0xFFFF -> config-based
	_simplify_full_mask()	line 330	Int-width mask checks
	constant folding	lines 450-667	Overflow thresholds

## Preprocessor: Predefined Macros

Add GCC-compatible macros so headers can adapt:

	__SIZEOF_INT__		2 or 4
	__SIZEOF_LONG__		4 or 8
	__SIZEOF_POINTER__	2
	__INT_WIDTH__		16 or 32
	__LONG_WIDTH__		32 or 64

## Header File Changes

Use conditional compilation in limits.h, stdint.h, stddef.h, inttypes.h:

```c
/* limits.h */
#if __SIZEOF_INT__ == 4
#define INT_MIN  (-2147483647-1)
#define INT_MAX  2147483647
#define UINT_MAX 4294967295U
#else
#define INT_MIN  (-32768)
#define INT_MAX  32767
#define UINT_MAX 65535U
#endif
```

## Printf/Library Impact

The printf handlers are hand-written Z80 assembly that read arguments
at fixed byte widths:

	%d handler (lc_printf_d.mac)	reads 2 bytes, advances offset by 2
	%ld handler (lc_printf_ld.mac)	reads 4 bytes, advances offset by 4

With --int-size 32, %d must read 4 bytes. Two approaches:

	Approach		Pros			Cons
	Separate .lib files	Simple assembly,	Two library builds,
	(libc_int16.lib,	no runtime cost		linker flag selection
	libc_int32.lib)

	Conditional assembly	Single source,		Requires um80 IF/ENDIF
	(IF INT_SIZE EQ 4)	build-time selection	support verification

Affected assembly files: lc_printf_d, lc_printf_u, lc_printf_x,
lc_printf_o, lc_printf_c, lc_scanf, lc_atoi.

The 32-bit handlers (lc_printf_ld etc.) do not change. With --int-size 32,
the %d handler effectively becomes the current %ld handler.

Runtime arithmetic routines (rt_arith16, rt_arith32, rt_arith64) are
already width-specific and need no changes. The codegen dispatches to the
correct width via _is_int4/_is_int8.

## Performance Impact

With --int-size 32, all int operations become 32-bit runtime calls instead
of inline 16-bit Z80 instructions. Approximate impact:

	int add:  2 cycles (inline ADD HL,DE) -> ~50 cycles (CALL __add32)
	Code size: roughly 2x larger
	Stack use: doubled for int locals and arguments

The IX+d displacement limit (-128 to +127) becomes more constraining
with 4-byte locals, limiting stack frames to ~30 variables.

Fine for test compatibility; not recommended for production Z80 code.

## Implementation Phases

Phase 1 - Infrastructure (low risk):
	- TypeConfig dataclass
	- CLI switches in main.py
	- Thread TypeConfig through CodeGenerator and ASTOptimizer
	- Predefined macros in preprocessor.py

Phase 2 - Core codegen refactor (high risk):
	- _type_size() uses TypeConfig
	- _is_long_type -> _is_int4, _is_long_long_type -> _is_int8
	- _is_long_expr -> checks byte width, not type name
	- _get_expr_type literal thresholds from TypeConfig
	- Audit all ~150 call sites

Phase 3 - AST optimizer (medium risk):
	- _sizeof_type uses TypeConfig
	- _literal_mask, _simplify_full_mask use config widths

Phase 4 - Headers (low risk):
	- limits.h, stdint.h, stddef.h, inttypes.h with #if guards

Phase 5 - Library variants (medium risk):
	- Printf handlers for int32 mode
	- Build system for library variants
	- Linker flag selection in main.py

Phase 6 - Testing:
	- Verify no regression with default (int16) settings
	- Run c-testsuite with --int-size 32 (expect 00174, 00200 to pass)
	- Run Fujitsu 0010/0011/0012 with --int-size 32

## Test Impact Estimate

Tests currently failing due to 16-bit int:

	Suite		Failing (int16)		Expected to pass with --int-size 32
	c-testsuite	2 (00174, 00200)	2
	Fujitsu 0010	9			most
	Fujitsu 0011	14			most
	Fujitsu 0012	4			4
