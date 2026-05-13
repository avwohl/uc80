/* sdcc-lib.h shim — uc80 isn't SDCC, but the SDCC regression tests
 * include this for SDCC's memory-space qualifiers (_AUTOMEM, _STATMEM,
 * _CODE, etc.).  We define them all to nothing. */
#ifndef SDCC_LIB_H
#define SDCC_LIB_H

#define _AUTOMEM
#define _STATMEM
#define _CODE
#define _XDATA
#define _IDATA
#define _PDATA
#define _NEAR
#define _FAR

#endif
