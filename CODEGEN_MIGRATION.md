# uc80 codegen migration — uplox auto-AST

## Status

In progress. The branch flips imports off `uc_core.frontend_legacy` /
`uc_core.ast_optimizer_legacy` / `uc_core.ast_legacy` back to the
primary modules (which expose the uplox v3 auto-AST). Tests currently
fail because `uc80/codegen.py` (10 504 lines) walks the legacy
resolved-type tree.

## What changed

* `src/uc80/codegen.py` — `from uc_core import ast_legacy as ast`
  → `from uc_core import ast`.
* `src/uc80/main.py` — `frontend_legacy` / `ast_optimizer_legacy` /
  `ast_legacy as ast_module` → `frontend` / `ast_optimizer` /
  `ast as ast_module`.
* `tests/test_codegen.py`, `tests/test_type_config_codegen.py` — same.

Once those imports point at the auto-AST, every `isinstance(..., ast.X)`
in codegen.py needs the same migration uc_core's
`_convert_translation_unit` was doing internally — only now done lazily
at use sites instead of up-front.

## Migration shape

The auto-AST is **declarator-shaped**. Legacy shapes don't exist:

```
legacy                          auto-AST equivalent
──────────────────────────────  ────────────────────────────────────────
ast.TranslationUnit.declarations  ast.TranslationUnit.items
ast.FunctionDecl(name, body,      ast.FunctionDef(decl_specs, declarator,
  return_type, params, ...)         body, pos)
                                  + walk declarator chain for name +
                                    inner FnDeclarator for params
ast.VarDecl(name, var_type,       ast.Declaration(decl_specs, declarators=
  init, storage_class)              [InitDeclarator / InitDeclaratorWithInit])
                                  + each init_decl carries declarator + opt init
ast.DeclarationList               gone — Declaration directly carries N declarators
ast.BasicType(name, is_signed,    walk decl_specs for BasicTypeSpec.kw.text
  is_const, is_volatile)            ("int" / "char" / ...) + signed / unsigned /
                                    const / volatile spec instances
ast.PointerType(base_type,        outermost layer is PointerDeclarator wrapping
  is_const, is_volatile)            an inner declarator; PointerOne / PointerNested
                                    in the .pointer field carry qualifiers
ast.ArrayType(base_type, size)    ArrayDeclarator / ArrayDeclaratorUnsized /
                                    ArrayDeclaratorStatic / etc.
ast.FunctionType(return_type,     declarator's outermost FnDeclarator (.params
  param_types, is_variadic)         is a list[ParamDecl] or a VariadicParams
                                    wrapping that list)
ast.StructType / ast.StructDecl   StructDef (full def) / StructAnon (no tag) /
                                    StructRef (fwd) / StructEmpty / StructAnonEmpty
ast.EnumType / ast.EnumDecl       EnumDef / EnumAnon / EnumRef
ast.IntLiteral.value (int)        ast.IntLiteral.value (Token) — parse via
                                    `uc_core._const.int_value(lit)`
ast.IfStmt(then, else=None)       ast.IfStmt (no else) vs ast.IfStmtElse
ast.ReturnStmt(value=None)        ast.ReturnStmt vs ast.ReturnStmtValue
```

## Migration order

CallGraphAnalyzer first (smaller, fewer call sites), then CodeGenerator:

1. `CallGraphAnalyzer.build_call_graph` + the four collector methods.
2. `_var_size`, `_calc_locals_size`, `_type_signature` (the type
   resolution surface — used everywhere).
3. `_analyze_function_body` + `_analyze_stmt` + `_analyze_expr` (call
   graph walk).
4. `CodeGenerator.generate` entry path.
5. Per-statement codegen handlers: `_gen_function`, `_gen_compound`,
   `_gen_if`, `_gen_while`, `_gen_for`, `_gen_switch`, `_gen_return`,
   `_gen_expr_stmt`, `_gen_var_decl`.
6. Expression codegen: `_gen_expr`, `_gen_binary`, `_gen_unary`,
   `_gen_call`, `_gen_index`, `_gen_member`, `_gen_cast`, `_gen_sizeof`.
7. Initializer codegen.
8. Tests in `tests/test_codegen.py` and
   `tests/test_type_config_codegen.py` get updated as types change.

## Helpers worth writing once

```python
def _resolve_type(decl_specs, declarator) -> ResolvedType:
    """Walk decl_specs + declarator chain → a uc80-internal type info."""

def _iter_declarations(unit):
    """Yield (kind, name, type_info, init_expr_or_body) for each item."""

def _ident_of(declarator) -> str | None:
    """Innermost IDENT name of a declarator chain."""
```

These are similar to the helpers already added to
`uc_core.ast_optimizer` (`_declarator_ident`,
`_outermost_fn_declarator`, `_function_param_names`). Reuse those
where possible; keep uc80-specific resolution local to codegen.py.
