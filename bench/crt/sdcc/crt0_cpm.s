;; CP/M startup for standalone SDCC (z80 port, sdcccall(1)).
;; Adapted from SDCC 4.6.0 lib/src/z80/crt0.s: no reset vectors, code
;; starts at 0100h, the stack starts at the BDOS entry, exit is JP 0.
	.module crt0_cpm
;; No .optsdcc line: SDCC 4.6 modules say "-mz80 sdcccall(1)" and SDCC 4.4
;; modules say "-mz80", and sdldz80 refuses a mix.  With no line, this
;; module links with both.  Both versions use sdcccall(1) by default.
	.globl	_main

	.area	_CODE
init:
	ld	sp, (6)
	call	gsinit
	;; main(argc, argv) with argc = 0 and argv = {NULL} (sdcccall(1):
	;; first argument in HL, second in DE).  gsinit leaves junk there.
	ld	hl, #0
	ld	de, #argv0
	call	_main
	jp	0
argv0:
	.dw	0

	;; Ordering of segments for the linker.
	.area	_HOME
	.area	_CODE
	.area	_INITIALIZER
	.area	_GSINIT
	.area	_GSFINAL
	.area	_DATA
	.area	_INITIALIZED
	.area	_BSEG
	.area	_BSS
	.area	_HEAP
	;; The link places _HEAP_END at a fixed address (SDCC_HEAP_TOP in
	;; bench/compilers.py), so the z80.lib heap runs up to it.  The area
	;; is named here so that the link works when no heap is linked.
	.area	_HEAP_END

	.area	_CODE
_exit::
	jp	0

	.area	_GSINIT
gsinit::
	; Default-initialized global variables.
	ld	bc, #l__DATA
	ld	a, b
	or	a, c
	jr	Z, zeroed_data
	ld	hl, #s__DATA
	ld	(hl), #0x00
	dec	bc
	ld	a, b
	or	a, c
	jr	Z, zeroed_data
	ld	e, l
	ld	d, h
	inc	de
	ldir
zeroed_data:
	; Explicitly initialized global variables.
	ld	bc, #l__INITIALIZER
	ld	a, b
	or	a, c
	jr	Z, gsinit_next
	ld	de, #s__INITIALIZED
	ld	hl, #s__INITIALIZER
	ldir
gsinit_next:

	.area	_GSFINAL
	ret
