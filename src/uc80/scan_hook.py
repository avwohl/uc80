"""Link the larger parts of scanf only for a program that needs them.

The scanf engine (lib/lc/lc_scan_core) does the integer conversions, the
literal characters and %%, %n.  It calls ``_sc_float`` for %f, %e, %g and
%a, and ``_sc_text`` for %c, %s and %[.  Two library modules define each of
these functions.  ``lc_scan_fdefault`` and ``lc_scan_tdefault`` are the
defaults; they fail every conversion of their kind.  ``lc_scan_float`` reads
the number and calls strtod (about 5 KB of code).  ``lc_scan_text`` reads
characters and sets.  The linker takes the real module only when the program
refers to its other entry point, ``__sc_float_on`` or ``__sc_text_on``.

This module decides from the syntax tree what the program needs: a call of a
scanf function whose format string has a conversion of the kind, or whose
format is not a string literal, or a scanf function that is used without a
call.  It adds an EXTRN of each entry point to the assembly that uc80 writes.
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

# %[*][width][length]conversion, with a float conversion or a text conversion
_FLOAT_CONV = re.compile(r"%\*?\d*(?:hh|h|ll|l|L|j|z|t|q)?[eEfFgGaA]")
_TEXT_CONV = re.compile(r"%\*?\d*(?:hh|h|ll|l|L|j|z|t|q)?[cs\[]")


def _literal_text(arg):
    """The source text of a string literal argument, or None."""
    pieces = arg if isinstance(arg, list) else [arg]
    if pieces and all(isinstance(p, ast.StringLiteral) for p in pieces):
        return "".join(p.value.text for p in pieces)
    return None


def scanf_needs(unit, features=None):
    """The set of optional scanf parts the program needs: "float", "text".

    FEATURES is the set from ``#pragma scanf`` or ``--scanf``, or None."""
    need = set()
    if features:
        if features & {"float", "all"}:
            need.add("float")
        if features & {"text", "all"}:
            need.add("text")
    stack = list(unit.items)
    while stack and need != {"float", "text"}:
        node = stack.pop()
        if isinstance(node, (ast.Call, ast.CallNoArgs)):
            func = node.func
            name = func.name.text if isinstance(func, ast.Identifier) else None
            if name in SCANF_FORMAT_ARG:
                args = node.args if isinstance(node, ast.Call) else []
                index = SCANF_FORMAT_ARG[name]
                if index >= len(args):
                    return {"float", "text"}
                text = _literal_text(args[index])
                if text is None:
                    return {"float", "text"}
                if _FLOAT_CONV.search(text):
                    need.add("float")
                if _TEXT_CONV.search(text):
                    need.add("text")
                for arg in args:
                    if isinstance(arg, list):
                        stack.extend(a for a in arg if hasattr(a, "__dataclass_fields__"))
                    else:
                        stack.append(arg)
                continue
        elif isinstance(node, ast.Identifier):
            if node.name.text in SCANF_FORMAT_ARG:
                return {"float", "text"}     # the address of a scanf function
        stack.extend(children(node))
    return need


def needs_float_scanf(unit, features=None):
    """True if the program needs the float conversions of scanf."""
    return "float" in scanf_needs(unit, features)


_ENTRY = {"float": "__sc_float_on", "text": "__sc_text_on"}


def add_scanf_references(code, module_name, tail_index, need):
    """Return CODE with a small function that refers to the entry points of
    the optional scanf parts in NEED (a set of "float" and "text").

    The assembler writes a request for an external symbol into the object
    file, and the linker takes the library module that defines it.  But
    main.py drops an EXTRN that nothing uses, and the assembly DCE drops a
    data label that no live code uses.  A PUBLIC function is an entry point
    of the DCE, so the reference is the address in one.  TAIL_INDEX finds
    the place before the BSS block (main._tail_insert_index)."""
    lines = code.splitlines()
    at = tail_index(lines)
    label = "__sc_opt_ref_" + re.sub(r"\W", "_", module_name)
    block = ["", "; scanf: a reference that links the optional conversions"]
    block += [f"\textrn\t{_ENTRY[k]}" for k in sorted(need)]
    block += ["\tcseg", f"\tpublic\t{label}", f"{label}:"]
    block += [f"\tld\tHL,{_ENTRY[k]}" for k in sorted(need)]
    block += ["\tret", ""]
    return "\n".join(lines[:at] + block + lines[at:]) + "\n"


def add_float_reference(code, module_name, tail_index):
    """Return CODE with a reference that links the float conversions."""
    return add_scanf_references(code, module_name, tail_index, {"float"})
