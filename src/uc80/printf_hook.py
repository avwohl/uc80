"""Link the rarely used parts of printf only for a program that needs them.

The printf core (lib/lc/lc_printf_core) finds a conversion in a dispatch
table that the code generator writes beside main().  The generator writes
entries for d i u o x X s c p, f F e E g G, the long forms %ld %lu %lo %lx
%lX and the long long forms %lld %llu %llx.  The conversions below need more,
and each costs code, so the library holds each in a module of its own and
this module adds the table entry when a format string of the program asks for
it:

    %td, %lf             an entry that skips the length modifier
    %n                   lc_printf_n
    %a, %A               lc_printf_a
    %llo, %llX           lc_printf_llo, lc_printf_llxu
    %hhd %hhu %hhx ...   a glue routine in front of the handler that calls
                         lc_printf_hh to narrow the argument

The table is text in the assembly that uc80 writes, so this module works on
that text, after the code generator and before the assembler.  A format that
is not a string literal needs all of them.  Under ``--no-whole-program``
only the unit with main() has a table; give that unit ``--printf all`` or
compile it with a format that uses the conversion.
"""

import re

from uc_core import ast
from uc_core.volatility import children

# the index of the format argument of each printf function
PRINTF_FORMAT_ARG = {
    "printf": 0, "vprintf": 0,
    "fprintf": 1, "vfprintf": 1,
    "sprintf": 1, "vsprintf": 1,
    "snprintf": 2,
    "__builtin_printf": 0, "__builtin_vprintf": 0,
    "__builtin_fprintf": 1, "__builtin_vfprintf": 1,
    "__builtin_sprintf": 1, "__builtin_vsprintf": 1,
    "__builtin_snprintf": 2,
}

# % flags width .precision length conversion
_SPEC = re.compile(
    r"%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*)?)?(hh|h|ll|l|L|j|z|t|q)?([A-Za-z%])")

# A format that is not a literal could need any part.  %a is large (about
# 600 bytes), so only --printf all (or a literal) links it.
ALL = frozenset({"hh", "t", "lf", "n", "llo", "llX"})
ALL_EXPLICIT = ALL | {"a"}

# the 16-bit handlers that "hh" narrows: handler -> signed or unsigned
_HH_HANDLERS = {"__printf_handle_d": "s", "__printf_handle_u": "u",
                "__printf_handle_o": "u", "__printf_handle_x": "u",
                "__printf_handle_xu": "u"}


def _literal_text(arg):
    """The source text of a string literal argument, or None."""
    pieces = arg if isinstance(arg, list) else [arg]
    if pieces and all(isinstance(p, ast.StringLiteral) for p in pieces):
        return "".join(p.value.text for p in pieces)
    return None


def needs_of_format(text):
    """The set of optional parts that one format string needs."""
    need = set()
    for length, conv in _SPEC.findall(text):
        if length == "hh" and conv in "diuoxX":
            need.add("hh")
        if length == "t":
            need.add("t")
        if length == "l" and conv in "fFeEgGaA":
            need.add("lf")
        if conv in "aA":
            need.add("a")
        if conv == "n":
            need.add("n")
        if length == "ll" and conv == "o":
            need.add("llo")
        if length == "ll" and conv == "X":
            need.add("llX")
    return need


def printf_needs(unit, features=None):
    """The set of optional printf parts the program needs.

    FEATURES is the set from ``#pragma printf`` or ``--printf``, or None."""
    if features and "all" in features:
        return set(ALL_EXPLICIT)
    need = set()
    unknown = False          # a format that is not a literal
    stack = list(unit.items)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.Call, ast.CallNoArgs)):
            func = node.func
            name = func.name.text if isinstance(func, ast.Identifier) else None
            if name in PRINTF_FORMAT_ARG:
                args = node.args if isinstance(node, ast.Call) else []
                index = PRINTF_FORMAT_ARG[name]
                text = (_literal_text(args[index]) if index < len(args)
                        else None)
                if text is None:
                    unknown = True
                else:
                    need |= needs_of_format(text)
                for arg in args:
                    if isinstance(arg, list):
                        stack.extend(a for a in arg
                                     if hasattr(a, "__dataclass_fields__"))
                    else:
                        stack.append(arg)
                continue
        elif isinstance(node, ast.Identifier):
            if node.name.text in PRINTF_FORMAT_ARG:
                unknown = True           # the address of a printf function
        stack.extend(children(node))
    return need | ALL if unknown else need


_ENTRY = re.compile(r"\tdb\t'(.)'\n\tdw\t(\S+)\n")


def _table_span(code, label):
    """(start, end) of the entries of a table: from the line after LABEL: to
    the line with the sentinel.  None if the table is not in CODE."""
    m = re.search(r"^" + label + r":\n", code, re.M)
    if not m:
        return None
    end = code.find("\tdb\t0\t\t; sentinel", m.end())
    if end < 0:
        return None
    return m.end(), end


def patch_tables(code, need):
    """Return CODE with the dispatch table entries for the parts in NEED.

    A unit without a table (under --no-whole-program, a unit that does not
    define main) is returned unchanged."""
    base = _table_span(code, "__printf_format_table")
    if not need or base is None:
        return code
    extrn = []
    glue = []

    # the long long table first: it is after the base table in the text
    ll = _table_span(code, "__printf_ll_table")
    base_chars = {c for c, _ in _ENTRY.findall(code[base[0]:base[1]])}
    if ll is not None and "l" in base_chars:
        add = ""
        if "llo" in need:
            add += "\tdb\t'o'\n\tdw\t__printf_handle_llo\n"
            extrn.append("__printf_handle_llo")
        if "llX" in need:
            add += "\tdb\t'X'\n\tdw\t__printf_handle_llxu\n"
            extrn.append("__printf_handle_llxu")
        code = code[:ll[1]] + add + code[ll[1]:]

    base = _table_span(code, "__printf_format_table")
    body = code[base[0]:base[1]]
    if "hh" in need:
        def wrap(m):
            char, handler = m.group(1), m.group(2)
            kind = _HH_HANDLERS.get(handler)
            if kind is None:
                return m.group(0)
            label = f"__pfhh_{ord(char):02x}"
            glue.append((label, kind, handler))
            return f"\tdb\t'{char}'\n\tdw\t{label}\n"
        body = _ENTRY.sub(wrap, body)
    add = ""
    if "t" in need and "t" not in base_chars:
        add += "\tdb\t't'\n\tdw\t__printf_handle_skip\n"
    if "lf" in need and "l" not in base_chars:
        add += "\tdb\t'l'\n\tdw\t__printf_handle_skip\n"
    if "t" in need or "lf" in need:
        if add:
            extrn.append("__printf_handle_skip")
    if "n" in need and "n" not in base_chars:
        add += "\tdb\t'n'\n\tdw\t__printf_handle_n\n"
        extrn.append("__printf_handle_n")
    if "a" in need:
        add += ("\tdb\t'a'\n\tdw\t__printf_handle_a\n"
                "\tdb\t'A'\n\tdw\t__printf_handle_au\n")
        extrn += ["__printf_handle_a", "__printf_handle_au"]
    code = code[:base[0]] + body + add + code[base[1]:]

    if glue:
        text = "\n; printf: narrow the argument of %hh conversions\n\tcseg\n"
        for label, kind, handler in glue:
            routine = "__printf_hhs" if kind == "s" else "__printf_hhu"
            text += (f"\tpublic\t{label}\n{label}:\n\tcall\t{routine}\n"
                     f"\tjp\t{handler}\n")
        extrn += ["__printf_hhs", "__printf_hhu"]
        marker = "__printf_ll_entry:\n\tret\n"
        at = code.find(marker)
        if at < 0:
            return code          # not reached: the generator always writes it
        at += len(marker)
        code = code[:at] + text + code[at:]

    if extrn:
        block = "".join(f"\textrn\t{name}\n"
                        for name in sorted(set(extrn)))
        banner = "; Printf format dispatch tables"
        at = code.find(banner)
        code = code[:at] + block + code[at:]
    return code


_WARNED = re.compile(r"printf conversion '(%[^']+)'")


def filter_warnings(warnings, need):
    """Drop the warnings about conversions that the parts in NEED handle, and
    the warning that %* is not implemented (the core implements it)."""
    out = []
    for w in warnings:
        m = _WARNED.search(w)
        if m:
            text = m.group(1)
            if "takes its width or precision from an argument" in w:
                continue
            if ((text == "%n" and "n" in need)
                    or (re.fullmatch(r"%t[diuoxX]", text) and "t" in need)
                    or (re.fullmatch(r"%l[fFeEgGaA]", text) and "lf" in need)
                    or (re.fullmatch(r"%[aA]", text) and "a" in need)
                    or (text == "%llo" and "llo" in need)
                    or (text == "%llX" and "llX" in need)):
                continue
        out.append(w)
    return out
