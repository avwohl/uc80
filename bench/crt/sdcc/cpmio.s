;; Console I/O for standalone SDCC on CP/M (sdcccall(1): int arg in HL,
;; int result in DE).  putchar sends CR before LF, as uc80 and z88dk do.
	.module cpmio
;; No .optsdcc line: SDCC 4.6 modules say "-mz80 sdcccall(1)" and SDCC 4.4
;; modules say "-mz80", and sdldz80 refuses a mix.  With no line, this
;; module links with both.  Both versions use sdcccall(1) by default.
	.area	_CODE

_putchar::
	push	hl
	ld	a, l
	cp	a, #10
	jr	NZ, 1$
	ld	e, #13
	ld	c, #2
	call	5
1$:
	pop	hl
	push	hl
	ld	e, l
	ld	c, #2
	call	5
	pop	de
	ret

_getchar::
	ld	c, #1
	call	5
	cp	a, #26
	jr	Z, 2$
	cp	a, #13
	jr	NZ, 3$
	ld	a, #10
3$:
	ld	e, a
	ld	d, #0
	ret
2$:
	ld	de, #-1
	ret
