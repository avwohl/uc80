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
    %*d, %.*s ...        lc_printf_star (the '*' entry; an int argument of 4
                         bytes for --int 32)
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
    "snprintf": 2, "vsnprintf": 2,
    "__builtin_printf": 0, "__builtin_vprintf": 0,
    "__builtin_fprintf": 1, "__builtin_vfprintf": 1,
    "__builtin_sprintf": 1, "__builtin_vsprintf": 1,
    "__builtin_snprintf": 2, "__builtin_vsnprintf": 2,
}

# printf functions that the format scan of codegen.py does not know.  A
# program that uses one of them gets every conversion, as for a format
# that is not a literal.
UNSCANNED = frozenset({"vsnprintf", "__builtin_vsnprintf"})


def uses_unscanned(unit):
    """True if the program calls (or takes the address of) a function in
    UNSCANNED."""
    stack = list(unit.items)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.Identifier) and node.name.text in UNSCANNED:
            return True
        stack.extend(children(node))
    return False

# % flags width .precision length conversion
_SPEC = re.compile(
    r"%[-+ #0]*(?:\d+|\*)?(?:\.(?:\d+|\*)?)?(hh|h|ll|l|L|j|z|t|q)?([A-Za-z%])")

# A format that is not a literal could need any part.  %a is large (about
# 600 bytes), so only --printf all (or a literal) links it.
ALL = frozenset({"hh", "t", "lf", "n", "llo", "llX", "star"})
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
    if re.search(r"%[-+ #0]*(?:\d+|\*)?\.?\*", text):
        need.add("star")
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


def _all_literal_texts(unit):
    """The decoded text of every string literal of the program (adjacent
    literals are joined)."""
    from uc_core._dcfast import fields as _fields, is_dataclass as _is_dc
    from .codegen import _decode_string_literal

    def decode(pieces):
        try:
            return "".join(_decode_string_literal(p.value.text)
                           for p in pieces)
        except Exception:
            return None

    texts = []
    seen = set()

    def walk(node):
        if isinstance(node, (list, tuple)):
            if node and all(isinstance(p, ast.StringLiteral) for p in node):
                text = decode(node)
                if text is not None:
                    texts.append(text)
                return
            for item in node:
                walk(item)
            return
        if not _is_dc(node) or id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, ast.StringLiteral):
            if node.value.text.startswith('"'):
                text = decode([node])
                if text is not None:
                    texts.append(text)
            return
        for f in _fields(node):
            walk(getattr(node, f.name, None))

    for item in unit.items:
        walk(item)
    return texts


_CONVERSION = re.compile(r"%[^%]")


def _decl_name(decl):
    """The identifier that a declarator declares, or None."""
    while decl is not None:
        name = getattr(decl, "name", None)
        if name is not None:
            return getattr(name, "text", None)
        decl = getattr(decl, "inner", None) or getattr(decl, "declarator", None)
    return None


def _function_info(fdef):
    """(name, [parameter names]) of a FunctionDef."""
    decl = fdef.declarator
    name = _decl_name(decl)
    params = getattr(decl, "params", None)
    plist = getattr(params, "params", params)
    if not isinstance(plist, (list, tuple)):
        plist = []
    names = [_decl_name(getattr(p, "declarator", None)) for p in plist]
    return name, names


def _strip_casts(e):
    while isinstance(e, ast.Cast):
        e = e.expr
    return e


def union_is_safe(unit):
    """True when every format of the program that is not a literal comes
    from a literal.  The format must be a parameter of a function (a
    wrapper around vprintf or vfprintf), the wrapper must not assign the
    parameter or declare a name that hides it, the address of the wrapper
    must not be taken, and every call of the wrapper must pass a string
    literal, or a parameter that passes this test in turn, at that
    position.  A format in a variable, an array or an expression, an
    address of a printf function, or an unscanned call has no such proof:
    the program keeps every conversion (docs/k1_link_size.md)."""
    funcs = {}        # name -> (FunctionDef, [param names])
    calls = []        # (Call, enclosing function name or None)
    idents = {}       # name -> number of Identifier nodes
    callee_count = {}  # name -> number of Identifier nodes that are callees
    assigned = {}     # (function name, identifier name) -> True
    declared = {}     # (function name, declared name) -> count
    decl_any = {}     # declared name -> count over the whole program
    lit_ptr = set()   # pointer variables initialised from a literal
    addr_taken = set()
    va_amp = set()    # ids of the &last of va_start

    def walk(node, fname):
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, ast.Identifier):
                nm = n.name.text
                idents[nm] = idents.get(nm, 0) + 1
            elif isinstance(n, (ast.Call, ast.CallNoArgs)):
                calls.append((n, fname))
                if isinstance(n.func, ast.Identifier):
                    cn = n.func.name.text
                    callee_count[cn] = callee_count.get(cn, 0) + 1
            elif isinstance(n, ast.BinaryOp):
                op = getattr(n.op, "text", n.op)
                if op == "=" and isinstance(n.right, ast.BinaryOp):
                    # ap = (va_list)&last + k: the va_start of stdarg.h
                    # takes the address of the last named parameter
                    rop = getattr(n.right.op, "text", n.right.op)
                    lft = _strip_casts(n.right.left)
                    if (rop == "+" and isinstance(lft, ast.UnaryOp)
                            and getattr(lft.op, "text", lft.op) == "&"):
                        va_amp.add(id(lft))
                if (isinstance(op, str) and op.endswith("=")
                        and op not in ("==", "!=", "<=", ">=")
                        and isinstance(n.left, ast.Identifier)):
                    assigned[(fname, n.left.name.text)] = True
            elif isinstance(n, ast.InitDeclaratorWithInit):
                d = n.declarator
                if (isinstance(d, ast.PointerDeclarator)
                        and _literal_text(n.init) is not None):
                    nm = _decl_name(d)
                    if nm:
                        lit_ptr.add(nm)
            elif isinstance(n, (ast.UnaryOp, ast.PostfixOp)):
                op = getattr(n.op, "text", n.op)
                if (op == "&" and isinstance(n.operand, ast.Identifier)
                        and id(n) not in va_amp):
                    addr_taken.add(n.operand.name.text)
                if op in ("++", "--") and isinstance(n.operand, ast.Identifier):
                    assigned[(fname, n.operand.name.text)] = True
            elif isinstance(n, ast.Declarator):
                key = (fname, n.name.text)
                declared[key] = declared.get(key, 0) + 1
                decl_any[n.name.text] = decl_any.get(n.name.text, 0) + 1
            stack.extend(children(n))

    for item in unit.items:
        if isinstance(item, ast.FunctionDef):
            name, params = _function_info(item)
            funcs[name] = (item, params)
            walk(item.body, name)
        else:
            walk(item, None)

    # an address of a printf function (not a call of it)
    for nm in PRINTF_FORMAT_ARG:
        if idents.get(nm, 0) > callee_count.get(nm, 0):
            return False

    def literal_arg(arg):
        return _literal_text(arg) is not None

    def param_ok(fname, index, visiting):
        key = (fname, index)
        if key in visiting:
            return True
        info = funcs.get(fname)
        if info is None:
            return False
        _item, params = info
        if index >= len(params) or params[index] is None:
            return False
        pname = params[index]
        if (fname, pname) in assigned or declared.get((fname, pname), 0):
            return False
        if pname in addr_taken:
            return False        # *&param = ... can change the format
        # the address of the wrapper is not taken
        if idents.get(fname, 0) != callee_count.get(fname, 0):
            return False
        visiting = visiting | {key}
        for call, caller in calls:
            if not (isinstance(call.func, ast.Identifier)
                    and call.func.name.text == fname):
                continue
            args = call.args if isinstance(call, ast.Call) else []
            if index >= len(args):
                return False
            if not arg_ok(args[index], caller, visiting):
                return False
        return True

    def arg_ok(arg, fname, visiting):
        if literal_arg(arg):
            return True
        if isinstance(arg, list):
            return False
        e = _strip_casts(arg)
        if not isinstance(e, ast.Identifier):
            return False
        nm = e.name.text
        if (nm in lit_ptr and decl_any.get(nm, 0) == 1
                and nm not in addr_taken
                and not any(k[1] == nm for k in assigned)):
            return True      # a pointer set once from a literal
        if fname not in funcs:
            return False
        params = funcs[fname][1]
        if nm not in params:
            return False
        return param_ok(fname, params.index(nm), visiting)

    for call, fname in calls:
        if not isinstance(call, (ast.Call, ast.CallNoArgs)):
            continue
        if not isinstance(call.func, ast.Identifier):
            continue
        name = call.func.name.text
        if name not in PRINTF_FORMAT_ARG:
            continue
        args = call.args if isinstance(call, ast.Call) else []
        index = PRINTF_FORMAT_ARG[name]
        if index >= len(args):
            return False
        if not arg_ok(args[index], fname, frozenset()):
            return False
    return True


def literal_union_features(unit):
    """The ``#pragma printf`` feature set for a program with a format that
    is not a literal: the conversions of every string literal of the
    program, and always ``int``.  None when no string literal has a
    conversion: the format then comes from outside the program and needs
    every conversion.  docs/k1_link_size.md."""
    from .codegen import CodeGenerator
    if not union_is_safe(unit):
        return None
    features = {"int"}
    texts = _all_literal_texts(unit)
    if not any(_CONVERSION.search(t) for t in texts):
        return None      # no literal has a conversion: the format is input
    for text in texts:
        CodeGenerator._extract_printf_specifiers(text, features)
    return features


def printf_needs(unit, features=None, union=False):
    """The set of optional printf parts the program needs.

    FEATURES is the set from ``#pragma printf`` or ``--printf``, or None.
    UNION: a format that is not a literal needs the parts that any string
    literal of the program asks for, not every part."""
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
    if unknown and union and union_is_safe(unit):
        texts = _all_literal_texts(unit)
        if any(_CONVERSION.search(t) for t in texts):
            for text in texts:
                need |= needs_of_format(text)
            return need
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


def patch_tables(code, need, int_size=2):
    """Return CODE with the dispatch table entries for the parts in NEED.

    INT_SIZE is the size of an int (4 for --int 32).

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
    if "star" in need and "*" not in base_chars:
        handler = ("__printf_handle_star32" if int_size == 4
                   else "__printf_handle_star")
        add += f"\tdb\t'*'\n\tdw\t{handler}\n"
        extrn.append(handler)
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
    """Drop the warnings about conversions that the parts in NEED handle."""
    out = []
    for w in warnings:
        m = _WARNED.search(w)
        if m:
            text = m.group(1)
            if ((text == "%n" and "n" in need)
                    or (re.fullmatch(r"%t[diuoxX]", text) and "t" in need)
                    or (re.fullmatch(r"%l[fFeEgGaA]", text) and "lf" in need)
                    or (re.fullmatch(r"%[aA]", text) and "a" in need)
                    or (text == "%llo" and "llo" in need)
                    or (text == "%llX" and "llX" in need)):
                continue
        out.append(w)
    return out
