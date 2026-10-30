# 1 << n in line

`1 << n` with a variable `n` called `__shl16`. A call needs 17 T-states, and the routine tests the count twice before the loop. A code such as `c & (1 << i)` in a loop paid this for each pass.

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change.

## The change

For a shift whose left operand is the literal 1 (an `int`, not a `long`), the code generator emits the shift in line:

```
	ld	A,L		; the count
	ld	HL,1
	or	A
	jr	z,done
	cp	16
	jr	c,loop
	ld	L,H		; a count of 16 or more gives 0, as in __shl16
	jr	done
loop:	add	HL,HL
	dec	A
	jr	nz,loop
done:
```

The program `bsd-ppt` needs about 7000 T-states less.

## Switch

`--no-shl-inline` calls `__shl16`. The change is for 16-bit `int` only. With `--int=32` the shift stays a call.

## Tests

`tests/test_famj_kernels.py` computes `1 << n` for `n` in 0 to 14. It does this in a function, as a mask in a loop, and with an `unsigned` count. It also prints the bits of a byte. The output must be the same as the host output for the default build, `--no-shl-inline`, `-O0` and `--no-register-locals`.
