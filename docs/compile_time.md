# Compile time

This file lists the known causes of long compile times and how uc80 avoids
them.

## Expression trees

The code generator asks for the type of an expression in many places. The
type of a binary operation depends on the type of both operands. A sum of
many terms is a deep tree, so the code generator asked for the type of the
same subtree again at each level.

The methods `_get_expr_type`, `_arith_operand_type`, `_is_float_expr`,
`_is_pointer_arith`, `_is_long_long_expr` and `_is_long_expr` of
`src/uc80/codegen.py` have the decorator `_scoped_memo`. During one
outermost call, the decorator keeps one result for each method and node.
The outermost call deletes the table when it returns. The scope and the
tree do not change during that call, so the results are the same as
without the table.

Times for `int f(int i) { return a[i]+a[i+1]+...; }` on one machine, in
seconds:

```
terms   before   after
20      0.8      0.4
40      5        0.6
80      62       1.1
160     -        2.9
320     -        15
```

The growth that remains is quadratic with a small factor. The rule check
`maybe_float` in `uc_core/ast_optimizer.py` walks a whole subtree at each
level of the tree, and the code generator asks once for each level.

## Dead code and dead data

`src/uc80/asm_dce.py` removes the blocks that no live block reaches. To
find the data that live code uses, the pass splits each line into names
once. Then it looks each name up in the table of data labels. The pass
scans each live data object once. The cost is linear in the size of the
assembly text.

The old pass matched one regular expression for each label against each
line. `battlestar-size` and `rogue36-size` did not finish in 1800 s. The
time was in `uc80`, not in `um80` or `ul80`.

## Large programs

Times for the whole-program build of the size-only programs of the
benchmark, in seconds:

```
program           uc80   um80   ul80
battlestar-size   37     7      1
rogue36-size      82     7      1
```

Many passes share the compiler time. Each pass walks the whole AST for
each function: the call graph (built 14 times), the inliner, the
table of volatile objects, the register-local allocator and the peephole
pass. `uc_core/_dcfast.py` keeps the answers of `is_dataclass` and
`fields` for each class. These walks asked for them some 20 million times.
