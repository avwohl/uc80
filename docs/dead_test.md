# An if with nothing to do

A program can have a statement `if (c) ;` or `if (c) { }`. A macro that is empty in a timed build makes such a statement, for example `if (!flags[i]) PRINTF("%u ", i);`. The code loads the value, tests it, and then jumps to the next statement.

Family J step 2 in [codegen_gap.md](codegen_gap.md) lists the change.

## The change

`Uc80ASTOptimizer` in `src/uc80/ast_opt.py` deletes an `if` statement when:

- the then-branch and the else-branch (if any) are empty (`;`, or blocks with only such statements).
- the condition is on a list of node types that have no side effect: names, literals, unary and binary operators (no `++`, `--`, assignment or comma), `?:`, casts, indexes and member accesses. Other node types stay. The test of `uc_core` knows no comma expression, so `if ((g = 5, 0)) ;` lost the assignment.
- the condition reads no volatile object (`_expr_is_impure` of `uc_core` answers no).

A call, an assignment, `++`, `--` and a volatile access stay. A loop body that is such an `if` becomes an empty statement. The loop itself stays.

The programs `sieve` and `sieve+static` need 347000 T-states less.

## Switch

`--no-dead-test` keeps these statements. `-O0` keeps them too.

## Tests

`tests/test_famj_kernels.py` runs a program with empty ifs. The conditions are pure, or have a call, or have `n++`. One if has an empty block. The output must be the same as the host output for the default build, `--no-dead-test`, `-O0` and `--no-ast-optimize`. A second test shows that a volatile read stays in the code. A third test runs conditions with a comma (`(g = 5, 0)`, `(f(), 0)`, `(i++, a)`), also inside loop bodies.
