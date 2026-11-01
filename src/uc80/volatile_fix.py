"""Keep ``volatile`` on a typedef name and on a struct type.

``uc_core.ast.resolved_to_legacy`` turns the resolved type of a cast
target or declaration into the legacy type.  For a typedef name (not yet
resolved) and for a struct or union type, the conversion drops the
``volatile`` qualifier.  So ``(volatile u8 *) 0x4300`` and
``(volatile struct dev *) 0x4300`` named a plain type, and the passes that
must keep a volatile access (dead store, dead test, device memset) did not
see the access.  This module wraps the conversion and sets the flag on the
result.  The fix belongs in uc_core; this wrapper is harmless when uc_core
keeps the flag itself.  ``install`` runs once, at import of ``uc80``.
"""

_KINDS = ("typedef", "struct", "enum", "complex")


def install() -> None:
    from uc_core import ast as core_ast
    from uc_core.codegen_helpers import ResolvedType

    orig = core_ast.resolved_to_legacy
    if getattr(orig, "_uc80_keeps_volatile", False):
        return

    def resolved_to_legacy(rt):
        out = orig(rt)
        if (isinstance(rt, ResolvedType) and rt.is_volatile
                and rt.kind in _KINDS and out is not None
                and hasattr(out, "is_volatile") and not out.is_volatile):
            out.is_volatile = True
        return out

    resolved_to_legacy._uc80_keeps_volatile = True
    core_ast.resolved_to_legacy = resolved_to_legacy
