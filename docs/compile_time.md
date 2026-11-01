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

Times for `int f(int i) { return (a[i]+b[0]*2)+(a[i+1]+b[1]*2)+...; }`
on one machine, in seconds:

```
terms   time
80      0.4
160     0.7
320     1.6
640     4.4
```

The memo of the code generator lives for the whole `gen_expr` call of one
expression. The code generator clears the memo at a declaration or a statement
inside a statement expression. Such a declaration can change which
name an identifier means. The float memo of `uc_core`
(`FloatInfo.maybe_float`) lives for one statement. Each statement
clears the float memo. The time now grows a little faster than
linear (the lists of the tree walks), not with the square of the size.

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

## Byte registers (family J, step 4)

The allocator of register locals tries more configurations when byte
registers are on. The compile time of `embench-huffbench` rises by about
38 percent. Other programs rise by less. `--no-byte-regs` gives the
earlier time. Use `UC80_RL_SKIPFN=name` to keep the locals of one
function in memory.
