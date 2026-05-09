#!/usr/bin/env python3
"""Split libc.mac into separate modules in lib/lc/

For Phase 1, printf stays monolithic (all format handlers in one module).
Printf uses table-driven dispatch with separate handler modules.
"""
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(SCRIPT_DIR, "libc.mac")
OUT_DIR = os.path.join(SCRIPT_DIR, "lc")

os.makedirs(OUT_DIR, exist_ok=True)

with open(SRC, "r") as f:
    lines = f.readlines()

HEADER = """\t.Z80

; CP/M BDOS function numbers
BDOS\tEQU\t5
CONOUT\tEQU\t2
CONIN\tEQU\t1
PRTSTR\tEQU\t9

"""

def extract(start_line, end_line):
    """Extract lines (1-based inclusive)"""
    return "".join(lines[start_line-1:end_line])

def write_module(name, content):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w") as f:
        f.write(content)
    print(f"  wrote {name} ({len(content)} bytes)")

# =====================================================================
# lc_putchar.mac: putchar (lines 16-37)
# =====================================================================
print("Creating lc_putchar.mac...")
write_module("lc_putchar.mac", f"""; lc_putchar.mac - putchar
{HEADER}\tCSEG

{extract(16, 37)}
\tEND
""")

# =====================================================================
# lc_getchar.mac: getchar (lines 41-53)
# =====================================================================
print("Creating lc_getchar.mac...")
write_module("lc_getchar.mac", f"""; lc_getchar.mac - getchar
{HEADER}\tCSEG

{extract(41, 53)}
\tEND
""")

# =====================================================================
# lc_puts.mac: puts + cputs (lines 54-131)
# =====================================================================
print("Creating lc_puts.mac...")
write_module("lc_puts.mac", f"""; lc_puts.mac - puts, cputs
{HEADER}\tCSEG

{extract(54, 131)}
\tEND
""")

# =====================================================================
# lc_gets.mac: gets (lines 133-167)
# =====================================================================
print("Creating lc_gets.mac...")
write_module("lc_gets.mac", f"""; lc_gets.mac - gets
{HEADER}\tCSEG

{extract(133, 167)}
\tEND
""")

# =====================================================================
# lc_file.mac: file I/O (fopen through perror, lines 170-2099)
# This is a large group - all file operations are tightly coupled
# =====================================================================
print("Creating lc_file.mac...")
write_module("lc_file.mac", f"""; lc_file.mac - file I/O functions
{HEADER}
\tEXTRN\t__div16
\tEXTRN\t_putchar

\tCSEG

{extract(170, 2099)}
; File I/O working storage
_fprintf_stream:\tDW\t0
_vprintf_stream:\tDW\t0
_vsprintf_ptr:\tDW\t0
_vsprintf_end:\tDW\t0

; File slot storage
_file_slots:\tDS\t680
_fread_ptr:\tDS\t2
_fread_cnt:\tDS\t2
_fwrite_ptr:\tDS\t2
_fwrite_cnt:\tDS\t2

\tEND
""")

# =====================================================================
# lc_printf.mac: printf + all format handlers + helpers (monolithic for now)
# Lines 2100-4082 (printf core, fprintf, vprintf etc.)
# Plus helper functions: _prt_dec, _prt_div16, _prt_float, _prt_hex,
# _prt_dec32, _div32_10 (lines 4083-4808)
# =====================================================================
print("Creating lc_printf.mac...")
write_module("lc_printf.mac", f"""; lc_printf.mac - printf family (monolithic, will be split in Phase 2)
{HEADER}
\tEXTRN\t__fmul
\tEXTRN\t__fsub
\tEXTRN\t__ftoi
\tEXTRN\t__itof
\tEXTRN\t__tmp32
\tEXTRN\t__fadd
\tEXTRN\t_fputc

\tCSEG

{extract(2100, 4808)}
; Printf internal data
_prt32_val:\tDS\t4

\tEND
""")

# =====================================================================
# lc_string.mac: string functions (lines 4810-5122 strlen through strcat,
#   5346-5376 strchr, and later string funcs)
# =====================================================================
print("Creating lc_string.mac...")
write_module("lc_string.mac", f"""; lc_string.mac - string functions
{HEADER}\tCSEG

{extract(4810, 5376)}
; Additional string functions
{extract(7085, 7575)}
{extract(7636, 7693)}
; strtok data
_strtok_next:\tDW\t0

\tEND
""")

# =====================================================================
# lc_mem.mac: memory functions (lines 5374-5508)
# memcpy, memset, memcmp + later: memmove, memchr
# =====================================================================
print("Creating lc_mem.mac...")
write_module("lc_mem.mac", f"""; lc_mem.mac - memory functions
{HEADER}\tCSEG

{extract(5374, 5508)}
{extract(7691, 7809)}
\tEND
""")

# =====================================================================
# lc_malloc.mac: malloc, calloc, free, realloc (lines 5509-5723)
# =====================================================================
print("Creating lc_malloc.mac...")
write_module("lc_malloc.mac", f"""; lc_malloc.mac - dynamic memory allocation
{HEADER}\tCSEG

{extract(5509, 5723)}

\tDSEG
_heap_ptr:\tDW\t0
_heap_start:\tDW\t0

\tEND
""")

# =====================================================================
# lc_stdlib.mac: exit, abort, atexit, rand, srand, abs, labs, div, ldiv
# (lines 4837-5122)
# =====================================================================
print("Creating lc_stdlib.mac...")
write_module("lc_stdlib.mac", f"""; lc_stdlib.mac - standard library functions
{HEADER}
\tEXTRN\t__mul16
\tEXTRN\t__sdiv16
\tEXTRN\t__smod16
\tEXTRN\t__sdiv32
\tEXTRN\t__smod32
\tEXTRN\t__tmp32

\tCSEG

{extract(4837, 5122)}

\tDSEG
; Atexit handler table
_atexit_table:\tDS\t64
_atexit_cnt:\tDB\t0
_rand_seed:\tDW\t1

\tEND
""")

# =====================================================================
# lc_atoi.mac: atoi, atol, strtol, strtoul (lines 5723-6278)
# =====================================================================
print("Creating lc_atoi.mac...")
write_module("lc_atoi.mac", f"""; lc_atoi.mac - string to integer conversion
{HEADER}\tCSEG

{extract(5723, 6278)}
\tEND
""")

# =====================================================================
# lc_atof.mac: atof, strtod, strtof, strtold (lines 6280-6770)
# =====================================================================
print("Creating lc_atof.mac...")
write_module("lc_atof.mac", f"""; lc_atof.mac - string to float conversion
{HEADER}
\tEXTRN\t__ltof
\tEXTRN\t__fadd
\tEXTRN\t__fsub
\tEXTRN\t__fmul
\tEXTRN\t__fdiv
\tEXTRN\t__tmp32

\tCSEG

{extract(6280, 6770)}
\tEND
""")

# =====================================================================
# lc_wchar.mac: mblen, mbtowc, wctomb, mbstowcs, wcstombs (lines 6764-7087)
# =====================================================================
print("Creating lc_wchar.mac...")
write_module("lc_wchar.mac", f"""; lc_wchar.mac - multibyte/wide character functions
{HEADER}\tCSEG

{extract(6764, 7087)}
\tEND
""")

# =====================================================================
# lc_locale.mac: setlocale, localeconv (lines 7575-7639)
# =====================================================================
print("Creating lc_locale.mac...")
write_module("lc_locale.mac", f"""; lc_locale.mac - locale functions
{HEADER}\tCSEG

{extract(7575, 7639)}

\tDSEG
; Locale data
_locale_str_c:\tDB\t'C',0
_locale_str_posix:\tDB\t'POSIX',0
_locale_str_dot:\tDB\t'.',0
_locale_str_empty:\tDB\t0
_lconv_data:\tDS\t32

\tEND
""")

# =====================================================================
# lc_sprintf.mac: sprintf (lines 7810-8233)
# =====================================================================
print("Creating lc_sprintf.mac...")
write_module("lc_sprintf.mac", f"""; lc_sprintf.mac - sprintf
{HEADER}
\tEXTRN\t_printf

\tCSEG

{extract(7805, 8233)}
\tEND
""")

# =====================================================================
# lc_snprintf.mac: snprintf (lines 8234-8579)
# =====================================================================
print("Creating lc_snprintf.mac...")
write_module("lc_snprintf.mac", f"""; lc_snprintf.mac - snprintf
{HEADER}
\tEXTRN\t_printf

\tCSEG

{extract(8234, 8579)}
\tEND
""")

# =====================================================================
# lc_scanf.mac: scanf, sscanf, fscanf (lines 8580-9310)
# =====================================================================
print("Creating lc_scanf.mac...")
write_module("lc_scanf.mac", f"""; lc_scanf.mac - scanf family
{HEADER}
\tEXTRN\t_fgetc
\tEXTRN\t_ungetc

\tCSEG

{extract(8575, 9310)}

\tDSEG
_scanf_buf:\tDS\t32
_scanf_cnt:\tDW\t0

\tEND
""")

# =====================================================================
# lc_assert.mac: __assert_fail (lines 9311-9416)
# =====================================================================
print("Creating lc_assert.mac...")
write_module("lc_assert.mac", f"""; lc_assert.mac - assert support
{HEADER}
\tEXTRN\t_puts
\tEXTRN\t_abort

\tCSEG

{extract(9307, 9416)}
\tEND
""")

# =====================================================================
# lc_time.mac: time, clock (lines 9416-9458)
# =====================================================================
print("Creating lc_time.mac...")
write_module("lc_time.mac", f"""; lc_time.mac - time functions
{HEADER}\tCSEG

{extract(9416, 9458)}
\tEND
""")

# =====================================================================
# lc_misc.mac: getenv, system (lines 9458-9476)
# =====================================================================
print("Creating lc_misc.mac...")
write_module("lc_misc.mac", f"""; lc_misc.mac - getenv, system
{HEADER}\tCSEG

{extract(9458, 9476)}
\tEND
""")

# =====================================================================
# lc_qsort.mac: qsort, bsearch (lines 9476-9737)
# =====================================================================
print("Creating lc_qsort.mac...")
write_module("lc_qsort.mac", f"""; lc_qsort.mac - qsort, bsearch
{HEADER}
\tEXTRN\t__callhl

\tCSEG

{extract(9476, 9737)}

\tDSEG
; qsort working storage
_qsort_i:\tDS\t2
_qsort_j:\tDS\t2
_qsort_min:\tDS\t2
_qsort_ptr1:\tDS\t2
_qsort_ptr2:\tDS\t2
; bsearch working storage
_bsearch_low:\tDS\t2
_bsearch_high:\tDS\t2
_bsearch_mid:\tDS\t2

\tEND
""")

# =====================================================================
# lc_ctype.mac: character classification (lines 9764-10018)
# =====================================================================
print("Creating lc_ctype.mac...")
write_module("lc_ctype.mac", f"""; lc_ctype.mac - character classification functions
{HEADER}\tCSEG

{extract(9764, 10018)}
\tEND
""")

# =====================================================================
# lc_signal.mac: signal, raise (lines 10001-10184)
# =====================================================================
print("Creating lc_signal.mac...")
write_module("lc_signal.mac", f"""; lc_signal.mac - signal handling
{HEADER}\tCSEG

{extract(10001, 10184)}

\tDSEG
_signal_table:\tDS\t14

\tEND
""")

# =====================================================================
# lc_math.mac: math functions (lines 10185-11959)
# =====================================================================
print("Creating lc_math.mac...")
write_module("lc_math.mac", f"""; lc_math.mac - math functions
{HEADER}
\tEXTRN\t__fadd
\tEXTRN\t__fsub
\tEXTRN\t__fmul
\tEXTRN\t__fdiv
\tEXTRN\t__fcmp
\tEXTRN\t__itof
\tEXTRN\t__ftoi
\tEXTRN\t__tmp32

\tCSEG

{extract(10185, 11959)}

\tDSEG
; Math working storage
_sqrt_x:\tDS\t4
_sqrt_result:\tDS\t4
_sin_x:\tDS\t4
_cos_x:\tDS\t4
_exp_x:\tDS\t4
_log_x:\tDS\t4
_pow_x:\tDS\t4
_pow_y:\tDS\t4
_atan_x:\tDS\t4
_cbrt_x:\tDS\t4
_hypot_a:\tDS\t4
_hypot_b:\tDS\t4
_ceil_x:\tDS\t4
_fmod_x:\tDS\t4
_fmod_y:\tDS\t4
_modf_x:\tDS\t4

\tEND
""")

# =====================================================================
# lc_complex.mac: complex number functions (lines 11960-12820)
# =====================================================================
print("Creating lc_complex.mac...")
write_module("lc_complex.mac", f"""; lc_complex.mac - complex number functions
{HEADER}
\tEXTRN\t__fadd
\tEXTRN\t__fsub
\tEXTRN\t__fmul
\tEXTRN\t__fdiv
\tEXTRN\t__fcmp
\tEXTRN\t__itof
\tEXTRN\t__ftoi
\tEXTRN\t__tmp32
\tEXTRN\t__cadd
\tEXTRN\t__csub
\tEXTRN\t__cmul
\tEXTRN\t__cdiv
\tEXTRN\t__cplx_l
\tEXTRN\t__cplx_r
\tEXTRN\t__cplx_result
\tEXTRN\t__cplx_tmp
\tEXTRN\t_sqrt
\tEXTRN\t_atan2
\tEXTRN\t_sin
\tEXTRN\t_cos
\tEXTRN\t_exp
\tEXTRN\t_log
\tEXTRN\t_pow

\tCSEG

{extract(11960, 12820)}

\tDSEG
_cplx_z:\tDS\t8
_cplx_w:\tDS\t8
_cplx_res:\tDS\t8

\tEND
""")

# =====================================================================
# lc_data.mac: global data (stdin, stdout, stderr, errno, sret_buf)
# =====================================================================
print("Creating lc_data.mac...")
write_module("lc_data.mac", f"""; lc_data.mac - global data
\t.Z80

\tDSEG

; Standard I/O streams
\tPUBLIC\t_stdin_data
_stdin_data:
\tDW\t0,0
\tPUBLIC\t_stdout_data
_stdout_data:
\tDW\t0,0
\tPUBLIC\t_stderr_data
_stderr_data:
\tDW\t0,0

\tPUBLIC\t_stdin
_stdin:\tDW\t_stdin_data
\tPUBLIC\t_stdout
_stdout:\tDW\t_stdout_data
\tPUBLIC\t_stderr
_stderr:\tDW\t_stderr_data

\tPUBLIC\t_errno
_errno:\tDW\t0

; Static return buffer for struct-by-value returns
\tPUBLIC\t__sret_buf
__sret_buf:\tDS\t64

; Float printing working storage (used by printf)
\tPUBLIC\t__ftmp
__ftmp:\tDS\t4
__fman:\tDS\t4
__fwork:\tDS\t4

\tEND
""")

# =====================================================================
# lc_thread.mac: threading + mutexes + condition vars + TLS
# =====================================================================
print("Creating lc_thread.mac...")
# Find exact range - threading starts after complex data
write_module("lc_thread.mac", f"""; lc_thread.mac - threading support
{HEADER}\tCSEG

{extract(12960, 13817)}

\tDSEG
_thread_mode:\tDB\t0
_thread_current:\tDB\t0
_tss_slots:\tDS\t16
_tss_dtors:\tDS\t16

\tEND
""")

# =====================================================================
# lc_atomic.mac: atomic operations
# =====================================================================
print("Creating lc_atomic.mac...")
write_module("lc_atomic.mac", f"""; lc_atomic.mac - atomic operations
{HEADER}\tCSEG

{extract(13819, 14897)}
\tEND
""")

print("\nDone! Created modules in", OUT_DIR)
print("Note: Some modules may need EXTRN adjustments after assembly testing.")
