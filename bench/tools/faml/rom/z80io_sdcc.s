	.module z80io
	.area	_CODE
	.globl	_out
	.globl	_in
; void out(uint8_t port, uint8_t value): sdcccall(1): port in A, value in L
_out::
	ld	c, a
	ld	b, #0
	ld	a, l
	out	(c), a
	ret
; uint8_t in(uint8_t port): port in A, result in A
_in::
	ld	c, a
	ld	b, #0
	in	a, (c)
	ret
