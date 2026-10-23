/* sdcc-lib.h for llvm-z80 (bench/cc_llvmz80.py).
 *
 * llvm-z80 compiles with the SDCC 4.6.0 headers (stdio.h, stdlib.h,
 * string.h, ...) and links with the SDCC z80.lib.  The SDCC header
 * sdcc-lib.h picks the port features from __SDCC_z80, which clang does
 * not define, so it falls back to asm/default/features.h, which has
 * "#define _REENTRANT __reentrant", an SDCC keyword.  This file is found
 * before the SDCC copy and takes the z80 features, the same features
 * that SDCC itself uses for the z80 library.
 */
#ifndef __SDCC_SDCC_LIB_H
#define __SDCC_SDCC_LIB_H 1
#include <asm/z80/features.h>
#endif
