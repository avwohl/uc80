# Size Analysis: Test 0053_0456

Test 0053_0456 is the worst-case size comparison vs z88dk.

	uc80: 9,216 bytes
	z88dk: 6,609 bytes
	ratio: 139% (uc80 is 2,607 bytes larger)

## What the test does

Simple program: assigns unsigned int, long int, unsigned long int, and int
constants (hex and decimal with various suffixes), compares them against
literal values, prints "***** " if equal. Uses printf with NO format
specifiers — only constant strings.

## Binary layout (uc80)

	Section                         Start   End    Size
	crt0 + user code                0100H   0EA5H  3493
	64-bit runtime (ucmp/zext)      0EA5H   0F1AH   117
	printf core                     0F1AH   1097H   381
	float printing (prt_float)      1097H   130BH   628
	printf dispatch tables          130BH   1373H   104
	printf handle_lo (octal)        1373H   1414H   161
	float math library              1414H   1AEBH  1751
	printf handlers (d/u/x/s/c/l)   1AEBH   1F75H  1162
	32-bit runtime (arith)          1F75H   220FH   666
	printf handle_d + handle_u      220FH   2368H   345
	data + BSS                      2368H   2522H   442
	                                               ----
	Total in binary                                9192

## Root causes

### 1. Printf pulls ALL format handlers (2,379 bytes wasted)

The program uses zero format specifiers (only `printf("***** \n")`), but
the default `lc_printf_all.mac` table links every handler:
%d, %u, %o, %x, %s, %c, %p, %f, %ld, %lu, %lo, %lx, %lld, %llu, %llx

The %f handler pulls in the entire float math library:
__fadd, __fsub, __fmul, __fdiv, __fcmp, __itof, __ftoi,
__funpack, __fpack, __fnorm, __fneg = 1,751 bytes

Combined with prt_float (628 bytes) = 2,379 bytes of dead code.

Fix options:
- Auto-detect format specifiers at compile time (scan printf strings)
- Create a "no-float" default table
- Require `#pragma printf` for programs that use printf

### 2. 64-bit comparisons for 32-bit constants (256 bytes — FIXED)

Bug in `_is_long_long_expr()` and `_is_long_expr()` (codegen.py):
only hex literals got the unsigned long (32-bit) exemption. Decimal
constants with `u` suffix like `4294967294u` should also be unsigned long
(32-bit) per C11 6.4.4.1, but the code treated them as long long (64-bit).

Both functions needed the same fix — change `expr.is_hex` to
`(expr.is_hex or expr.is_unsigned)` in the 32-bit range check.

After fix: 9,216 → 8,960 bytes (256 bytes saved, 64-bit runtime eliminated)

### 3. No constant folding for trivial comparisons

Patterns like `a = 0; if (a == 0)` generate full compare-and-branch code
instead of folding to unconditional execution. Lower priority since it
only affects contrived tests.

## Current status

	Fix                             Bytes saved  Status
	64-bit constant type fix         256          DONE
	Printf auto-detect specifiers   ~2,379       DONE

Both fixes are implemented. The compiler now scans printf format strings at
compile time and links only the needed format handlers. Programs that don't
use %f no longer pull in the float math library.
