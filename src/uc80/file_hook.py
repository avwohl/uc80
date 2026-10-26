"""Link the code for update and append modes of ``fopen`` only when needed.

A stream that is open in mode ``r+``, ``w+``, ``a+`` or ``a`` can change
between reading and writing.  The code for the change (``lc_ftowrite`` and
``lc_ftoread``, about 300 bytes) is called by ``fgetc`` and ``fputc``.  A
program that uses only the modes ``r`` and ``w``, or no file at all (it calls
``fputs(s, stdout)``), does not need it.

This module reads the syntax tree.  When the whole program is visible and has
no use of an update mode or of the append mode, ``add_lite_reference`` adds a
reference to ``__fio_lite`` to the assembly that uc80 writes.  The linker then
takes ``lc_f_lite``, which has short versions of the two entry points, and
not the full modules.  Without the reference the library has the full code, so
a program that this module does not understand works too.

The program needs the full code when it
- calls ``fopen`` or ``freopen`` with a mode that is not a string literal,
  or a literal that starts with ``a`` or has a ``+``,
- calls ``tmpfile`` (it opens a file with ``w+``), or
- uses the address of ``fopen``, ``freopen`` or ``tmpfile``.

The caller must not use this module for a unit that is not the whole
program (``--no-whole-program``), for the ROM target, or when hand-written
assembly is linked: those can open a file in a mode that the scan cannot see.
"""

import re

from uc_core import ast
from uc_core.volatility import children

# the index of the mode argument
MODE_ARG = {"fopen": 1, "freopen": 1}
# a call of these means that the program has a use for the file code
FILE_FUNCS = frozenset({
    "fopen", "freopen", "tmpfile", "fclose", "fflush", "fgetc", "fputc",
    "getc", "putc", "fgets", "fputs", "fread", "fwrite", "fprintf",
    "vfprintf", "fscanf", "vfscanf", "fseek", "ftell", "rewind", "fgetpos",
    "fsetpos", "ungetc",
})
UPDATE_FUNCS = frozenset({"tmpfile"})


def _literal_text(arg):
    """The source text of a string literal argument, or None."""
    pieces = arg if isinstance(arg, list) else [arg]
    if pieces and all(isinstance(p, ast.StringLiteral) for p in pieces):
        return "".join(p.value.text for p in pieces)
    return None


def _plain_mode(text):
    """True if the mode string is r or w, with b and t only.  TEXT is the
    source text of the literal, with the quotes."""
    if text is None:
        return False
    text = text.replace('"', "")
    return bool(text) and text[0] in "rw" and "+" not in text


def file_use(unit):
    """(uses_files, needs_update): whether the program calls a file function,
    and whether it can open a file in an update mode or append mode."""
    uses = False
    update = False
    stack = list(unit.items)
    while stack and not update:
        node = stack.pop()
        if isinstance(node, (ast.Call, ast.CallNoArgs)):
            func = node.func
            name = func.name.text if isinstance(func, ast.Identifier) else None
            if name in FILE_FUNCS:
                uses = True
            if name in UPDATE_FUNCS:
                update = True
            elif name in MODE_ARG:
                args = node.args if isinstance(node, ast.Call) else []
                index = MODE_ARG[name]
                text = (_literal_text(args[index]) if index < len(args)
                        else None)
                if not _plain_mode(text):
                    update = True
            if name in FILE_FUNCS:
                # look into the arguments, but not at the name of the function
                for arg in (node.args if isinstance(node, ast.Call) else []):
                    if isinstance(arg, list):
                        stack.extend(a for a in arg
                                     if hasattr(a, "__dataclass_fields__"))
                    else:
                        stack.append(arg)
                continue
        elif isinstance(node, ast.Identifier):
            name = node.name.text
            if name in FILE_FUNCS:
                uses = True
            if name in MODE_ARG or name in UPDATE_FUNCS:
                update = True       # the address of the function
        stack.extend(children(node))
    return uses, update


def needs_lite(unit):
    """True if the program uses files and needs no update or append mode."""
    uses, update = file_use(unit)
    return uses and not update


def add_lite_reference(code, module_name, tail_index):
    """Return CODE with a function that refers to ``__fio_lite``.

    See ``scan_hook.add_scanf_references`` for why the reference is the
    address in a PUBLIC function.  TAIL_INDEX finds the place before the BSS
    block (main._tail_insert_index)."""
    lines = code.splitlines()
    at = tail_index(lines)
    label = "__fio_ref_" + re.sub(r"\W", "_", module_name)
    # cseg first: when the unit has no BSS block, the place is after the
    # data (DSEG) and the assembly DCE would take the extrn for data.
    block = ["", "; files: a reference that links the short file switch",
             "\tcseg", "\textrn\t__fio_lite", f"\tpublic\t{label}",
             f"{label}:", "\tld\tHL,__fio_lite", "\tret", ""]
    return "\n".join(lines[:at] + block + lines[at:]) + "\n"
