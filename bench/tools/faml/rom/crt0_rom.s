	.module crt0_rom
	.globl	_main
	.area	_VECT (ABS)
	.org	0x0000
	di
	im	1
	jp	init
	.org	0x0038
	ei
	reti
	.org	0x0066
	retn
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
	.area	_CODE
init:
	ld	sp, #0x0000
	call	gsinit
	call	_main
	halt
	.area	_GSINIT
gsinit::
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
