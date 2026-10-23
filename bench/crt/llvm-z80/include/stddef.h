/* stddef.h for llvm-z80 (bench/cc_llvmz80.py): clang's own stddef.h,
 * then tell the SDCC headers that wchar_t exists.  clang's wchar_t is
 * the target type (16-bit unsigned int); the SDCC stdlib.h, stddef.h and
 * wchar.h would declare it again as unsigned long, which clang rejects
 * as a conflicting typedef.  The benchmarks use no wide characters.
 */
#include_next <stddef.h>
#ifndef __WCHAR_T_DEFINED
#define __WCHAR_T_DEFINED
#endif
