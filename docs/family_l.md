# Family L: the last gaps to SDCC

Family L is the work that follows [family K](k1_link_size.md). The aim has two parts. The image of each benchmark program must not be bigger than the image of SDCC 4.6.0 in its size mode. The program must run faster than SDCC in its speed mode. The plan with the measured numbers is in [family_l_plan.json](family_l_plan.json). The tools that measured the plan are in `bench/tools/faml`.

Each change has a switch that turns the change off. Each change has a test that runs code with uc80 and with the host compiler and compares the output.

Step LA (link, library and tree level):

- [Static pointer to a fixed address](#static-pointer-to-a-fixed-address): `--no-ptr-const`.
- [qsort of 16-bit elements](#qsort-of-16-bit-elements): `--no-qsort-wr`.
- [Calls of empty functions](#calls-of-empty-functions): `--no-empty-call`.
- [One divide for x / y and x % y](#one-divide-for-x--y-and-x--y): `--no-divmod-pair`.
- [An empty counted loop](#an-empty-counted-loop): `--no-empty-loop-fold`.
- [A short multiply in a loop with calls](#a-short-multiply-in-a-loop-with-calls): `--no-mul16c-loop`.

## Static pointer to a fixed address

Some programs keep their state in one object and reach the object through a pointer variable:

```c
static State state_object;
static State *g = NULL;
int main(void) { g = &state_object; /* ... */ g->field = 1; }
```

Each `g->field` loads the pointer from memory and adds the offset of the member. The program `mojozork-size` has 390 such accesses.

The compiler replaces `g` by `&state_object`. It then changes `(&state_object)->field` to `state_object.field`, a fixed address. The compiler removes the pointer variable.

The change is made only when all of these are true:

- The pointer has internal linkage (`static`), is not volatile, and no assembly names the pointer.
- The program never takes the address of the pointer. Only `=` assigns the pointer, and `++` and `+=` do not occur.
- The pointer points to one object of the same type. The object has file scope, or is a `static` variable of a function. The compiler moves a `static` variable of a function to file scope under the name `__ls_<function>_<name>`.
- One of two cases is true. In the first case, the declaration reads `static T *p = &obj;` and no statement assigns the pointer. In the second case, the declaration has no initializer (or a null one) and the only assignment is the statement `p = &obj;` among the first items of `main`. Each item before the assignment is a declaration that makes no call and does not use the pointer.
- No function calls `main`, and `main` is the only entry. The build is a CP/M whole-program build with no interrupt function and no assembly file.

In the second case no code reads the pointer before the assignment. So the pointer is never null when the program reads the pointer.

The change saves 3348 bytes in `mojozork-size`.

## qsort of 16-bit elements

A call `qsort(base, n, 2, cmp)` calls the library routine `__qsort_wr` (`lc_qsort_w.mac`, 332 bytes) in place of `qsort` (855 bytes). The routine takes `HL` = base, `DE` = number of elements and `BC` = comparator. The comparator takes its two pointers in `HL` and `DE` and returns the result in `HL`.

The routine checks first if the array is one run. A run in increasing order needs no work. The routine reverses a run in strictly decreasing order. Other arrays use a quicksort. The pivot is the element at one quarter of the part, because the middle element of an array in organ-pipe order is the largest one and gives a number of compares in the order of n squared. The recursion goes into the smaller part, so the stack depth is at most log2(n) levels.

The comparator gets the register convention for calls only when all of these are true:

- Every use of the name of the comparator is the fourth argument of a call `qsort(b, n, 2, name)`.
- The comparator has two parameters of one word each, and it returns an `int`.
- The comparator cannot reach `qsort`, `bsearch` or a function that calls back (`raise`, `signal` and the like). The check follows the direct calls. An indirect call or inline assembly in the comparator, or in a function that the comparator calls, turns the change off.
- The program defines no interrupt function, no function named `qsort` and no assembly file. The type `int` has 16 bits.

The routine keeps its state in static storage and is not reentrant. The checks above make sure that no comparator calls a sort. If a check fails, the call keeps the stack convention and the generic `qsort`. A call with a constant count of 32 or less and an element size of 1 to 8 still uses `__qsort_s` when the checks fail.

The eight sort programs of the benchmark shrink and run faster. The program `sort-ran-5000` goes from 36.0 million to 26.6 million T-states.

## Calls of empty functions

A debug function such as `static void dbg(const char *fmt, ...) { }` has no effect when the debug switch is off, but each call still pushes the arguments. The compiler deletes a call when all of these are true:

- The call is a whole expression statement, or a cast of a call to `void`.
- The function has one definition, and the body has no code. A body of empty statements and of expressions without side effects counts as empty.
- Each argument has no call, assignment, `++`, `--`, statement expression or compound literal, and does not access a volatile object.
- The function that contains the call has no local variable or parameter with the name of the called function.

The function stays when the program takes its address. Otherwise dead function removal deletes the function and the string literals that only its calls used. The change saves 1259 bytes in `mojozork-size`.

## One divide for x / y and x % y

Two statements often divide the same operands, one for the quotient and one for the remainder:

```c
a[n] = x % n;
x = 10 * a[n - 1] + x / n;
```

The routine for division computes both results. `__div16` returns the quotient in `HL` and the remainder in `DE`. The new routine `__sdivmod16` (`rt_sdqr16.mac`, 58 bytes) does the same for signed values.

The compiler finds a pair of operations when the operands are variables of 16 bits or constants, and the two operations are in the same block. The compiler divides at the first operation and stores the other result in a hidden local variable. The second operation reads the hidden variable. A pair needs all of these:

- The operands are not volatile and the program does not take their address.
- The first operation always runs when its statement runs. It is not on the right of `&&` or `||` and not in an arm of `?:`.
- No statement between the two operations changes an operand. A call between the two operations is possible only when the operands are local variables. The second statement can be the assignment `x = ... x / n ...` to an operand.
- Between the two statements there is no label, no loop, no `if` and no other control statement.

If the first operation does not end in a divide call (a constant divisor that is a power of two, for example), the compiler computes the second operation after the first. The result is correct in all cases.

The change gives a gain of 38% in T-states for `davidly-e`.

## An empty counted loop

A loop `for (v = c1; v < c2; v += c) ;` with constant bounds and an empty body only sets the counter. The compiler changes the loop to `v = final;`. The counter must be a non-volatile variable of an integer type of 16 bits or more, and the final value must be 32767 or less. The loop test uses `<`, `<=` or `!=`.

The sort programs have such a loop in `main`, because the print statements of the loop are empty in a timed build. The change saves 35 bytes in each sort program.

## A short multiply in a loop with calls

Family K step 2 uses `__mul16c` (25 bytes) in place of `__mul16` and its helpers (139 bytes) when each multiplication stands in `main` outside every loop. The new rule also allows a multiplication in a loop of depth 1 when the loop has three calls or more. The calls cost much more than the multiply. So the slower short routine does not change the run time in a measurable way. A loop with fewer calls keeps `__mul16`.

The change saves 116 bytes in each of the three `dhrystone21` programs.
