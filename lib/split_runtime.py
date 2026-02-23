#!/usr/bin/env python3
"""Split runtime.mac into separate modules in lib/rt/"""
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(SCRIPT_DIR, "runtime.mac")
OUT_DIR = os.path.join(SCRIPT_DIR, "rt")

os.makedirs(OUT_DIR, exist_ok=True)

with open(SRC, "r") as f:
    lines = f.readlines()

# Line numbers (1-based in editor, 0-based in array)
# arith16: lines 10-297 (callhl at 295-297), data at 98-101
# arith32: lines 299-932, uses data from float dseg (tmp32, mul32_mcd, etc)
# setjmp: lines 934-1047
# float: lines 1049-2221 (code), dseg 2223-2282
# complex: lines 2284-2540 (code), uses data from dseg
# arith64: lines 2542-end

def extract(start_line, end_line):
    """Extract lines (1-based inclusive)"""
    return lines[start_line-1:end_line]

def write_module(name, content):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        f.write(content)
    print(f"  wrote {path} ({len(content)} bytes)")

# =====================================================================
# rt_arith16.mac: 16-bit arithmetic + callhl
# Lines 10-297 of runtime.mac, plus DSEG lines 98-101
# =====================================================================
print("Creating rt_arith16.mac...")
mod = """; rt_arith16.mac - 16-bit arithmetic routines
; Split from runtime.mac

\t.Z80

\tCSEG

"""
# Lines 12-297 (skipping the original CSEG at line 10, we emit our own)
for line in extract(12, 297):
    mod += line
# The DSEG for __divrem is already embedded (lines 98-101)
write_module("rt_arith16.mac", mod)

# =====================================================================
# rt_arith32.mac: 32-bit arithmetic
# Lines 299-932 code, needs its own data section
# =====================================================================
print("Creating rt_arith32.mac...")
mod = """; rt_arith32.mac - 32-bit arithmetic routines
; Split from runtime.mac

\t.Z80

\tCSEG

"""
for line in extract(299, 932):
    mod += line

# Add data segment for arith32 work areas
mod += """
\tDSEG
\tPUBLIC\t__tmp32
__tmp32:
\tDW\t0,0
__mul32_mcd:
\tDW\t0,0
__mul32_mpr:
\tDW\t0,0
__div32_dvd:
\tDW\t0,0
__rem32:
\tDW\t0,0
\tEND
"""
write_module("rt_arith32.mac", mod)

# =====================================================================
# rt_setjmp.mac: setjmp/longjmp
# Lines 934-1047
# =====================================================================
print("Creating rt_setjmp.mac...")
mod = """; rt_setjmp.mac - setjmp/longjmp
; Split from runtime.mac

\t.Z80

\tCSEG

"""
for line in extract(934, 1047):
    mod += line
mod += "\n\tEND\n"
write_module("rt_setjmp.mac", mod)

# =====================================================================
# rt_float.mac: IEEE 754 single-precision float
# Lines 1049-2221 (code)
# Data: float work area (lines 2236-2282, minus complex and arith32 parts)
# =====================================================================
print("Creating rt_float.mac...")
mod = """; rt_float.mac - IEEE 754 single-precision floating point
; Split from runtime.mac

\t.Z80

\tEXTRN\t__tmp32

\tCSEG

"""
for line in extract(1049, 2221):
    mod += line

# Float-specific data segment
mod += """
\tDSEG
\tPUBLIC\t__fop1,__fop2,__fsgn1,__fsgn2,__fexp1,__fexp2,__fman1,__fman2
__fop1:
\tDW\t0,0
__fop2:
\tDW\t0,0
__fsgn1:
\tDB\t0
__fsgn2:
\tDB\t0
__fexp1:
\tDW\t0
__fexp2:
\tDW\t0
__fman1:
\tDW\t0,0
__fman2:
\tDW\t0,0
__fdiv_rem:
\tDW\t0,0

; Multiplication work area
__mul_mcand:
\tDW\t0,0

; Temporary storage for partial products in float multiplication
__fmul_hh:
\tDW\t0,0
__fmul_hl:
\tDW\t0,0
__fmul_lh:
\tDW\t0,0
__fmul_ll:
\tDW\t0,0
\tEND
"""
write_module("rt_float.mac", mod)

# =====================================================================
# rt_complex.mac: Complex number arithmetic
# Lines 2284-2540 (code)
# Data: complex work area
# =====================================================================
print("Creating rt_complex.mac...")
mod = """; rt_complex.mac - Complex number arithmetic
; Split from runtime.mac

\t.Z80

\tEXTRN\t__tmp32
\tEXTRN\t__fadd
\tEXTRN\t__fsub
\tEXTRN\t__fmul
\tEXTRN\t__fdiv

\tCSEG

"""
for line in extract(2284, 2540):
    mod += line

# Complex-specific data segment
mod += """
\tDSEG
\tPUBLIC\t__cplx_l,__cplx_r,__cplx_result,__cplx_tmp
__cplx_l:
\tDW\t0,0,0,0
__cplx_r:
\tDW\t0,0,0,0
__cplx_result:
\tDW\t0,0,0,0
__cplx_tmp:
\tDW\t0,0,0,0
__cplx_work:
\tDW\t0,0,0,0
\tEND
"""
write_module("rt_complex.mac", mod)

# =====================================================================
# rt_arith64.mac: 64-bit arithmetic
# Lines 2542-end
# =====================================================================
print("Creating rt_arith64.mac...")
mod = """; rt_arith64.mac - 64-bit arithmetic routines
; Split from runtime.mac

\t.Z80

\tCSEG

"""
# Start from the DSEG that defines __acc64/__tmp64 (line 2548)
# and continue to end of file
for line in extract(2542, len(lines)):
    mod += line

# Make sure it ends with END
if not mod.rstrip().endswith("END"):
    mod += "\n\tEND\n"

write_module("rt_arith64.mac", mod)

print("Done! Created modules in", OUT_DIR)
