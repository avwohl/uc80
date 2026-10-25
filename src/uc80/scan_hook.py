"""Link the floating-point part of scanf only for a program that needs it.

The scanf engine (lib/lc/lc_scan_core) calls ``_sc_float`` for %f, %e, %g
and %a.  Two library modules define that function.  ``lc_scan_fdefault`` is
the default; it fails every float conversion.  ``lc_scan_float`` reads the
number and calls strtod (about 5 KB of code).  The linker takes the second
module only when the program refers to its other entry point,
``__sc_float_on``.

This module decides from the syntax tree whether the program needs the
float conversions: a call of a scanf function whose format string has a
float conversion, or whose format is not a string literal, or a scanf
function that is used without a call.  It adds an EXTRN of that
entry point to the assembly that uc80 writes.
"""

import re

from uc_core import ast
from uc_core.volatility import children

# the index of the format argument of each scanf function
SCANF_FORMAT_ARG = {
    "scanf": 0, "vscanf": 0,
    "fscanf": 1, "vfscanf": 1,
    "sscanf": 1, "vsscanf": 1,
}

# %[*][width][length]conversion, with a float conversion
_FLOAT_CONV = re.compile(r"%\*?\d*(?:hh|h|ll|l|L|j|z|t|q)?[eEfFgGaA]")


def _literal_text(arg):
    """The source text of a string literal argument, or None."""
    pieces = arg if isinstance(arg, list) else [arg]
    if pieces and all(isinstance(p, ast.StringLiteral) for p in pieces):
        return "".join(p.value.text for p in pieces)
    return None


def needs_float_scanf(unit, features=None):
    """True if the program needs the float conversions of scanf.

    FEATURES is the set from ``#pragma scanf`` or ``--scanf``, or None."""
    if features and features & {"float", "all"}:
        return True
    stack = list(unit.items)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Call, ast.CallNoArgs)):
            func = node.func
            name = func.name.text if isinstance(func, ast.Identifier) else None
            if name in SCANF_FORMAT_ARG:
                args = node.args if isinstance(node, ast.Call) else []
                index = SCANF_FORMAT_ARG[name]
                if index >= len(args):
                    return True
                text = _literal_text(args[index])
                if text is None or _FLOAT_CONV.search(text):
                    return True
                for arg in args:
                    if isinstance(arg, list):
                        stack.extend(a for a in arg if hasattr(a, "__dataclass_fields__"))
                    else:
                        stack.append(arg)
                continue
        elif isinstance(node, ast.Identifier):
            if node.name.text in SCANF_FORMAT_ARG:
                return True              # the address of a scanf function
        stack.extend(children(node))
    return False


def add_float_reference(code, module_name, tail_index):
    """Return CODE with a small function that refers to ``__sc_float_on``.

    The assembler writes a request for an external symbol into the object
    file, and the linker takes the library module that defines it.  But
    main.py drops an EXTRN that nothing uses, and the assembly DCE drops a
    data label that no live code uses.  A PUBLIC function is an entry point
    of the DCE, so the reference is the address in one.  TAIL_INDEX finds
    the place before the BSS block (main._tail_insert_index)."""
    lines = code.splitlines()
    at = tail_index(lines)
    label = "__sc_float_ref_" + re.sub(r"\W", "_", module_name)
    block = [
        "",
        "; scanf: a reference that links the floating-point conversions",
        "\textrn\t__sc_float_on",
        "\tcseg",
        f"\tpublic\t{label}",
        f"{label}:",
        "\tld\tHL,__sc_float_on",
        "\tret",
        "",
    ]
    return "\n".join(lines[:at] + block + lines[at:]) + "\n"
