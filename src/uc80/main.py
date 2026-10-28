#!/usr/bin/env python3
"""uc80 - ANSI C compiler for Z80.

Compiles C source to Z80 assembly compatible with um80 assembler.
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from uc_core.frontend import parse as _frontend_parse
from uc_core.frontend import UnsupportedConstructError
from uc_core import ast as ast_module
from uc_core.preprocessor import Preprocessor, PreprocessorError, Macro
from uc_core.type_config import TypeConfig, Z80_CPM

from . import lib_dir, lib_file
from .codegen import (generate, CodeGenerator, CodegenError, BSS_BANNER_PREFIX,
                      drop_unreferenced_externs, function_name)
from .runtime import (RuntimeLibrary, load_lib_modules, load_runtime_library,
                      leave_to_library)
from .asm_dce import eliminate_dead_code as asm_eliminate_dead_code
from .asm_dce import (ASM_BEGIN_FILE_MARKER, ASM_END_MARKER,
                      is_asm_begin, is_asm_end)
from . import volatile_fence
from . import regcc
from . import sdcc_compat
from . import sdas
from . import isr_check
from .z80_intrinsics import BARRIER_BEGIN, BARRIER_END
from . import rom as rom_module
from . import printf_hook
from . import scan_hook
from . import file_hook
from .multi_unit import (file_scope_names, uniquify_enumerators, uniquify_statics_map,
                         uniquify_types)

from . import peep_local
from . import iyptr, reglocals

# Import peephole optimizer from upeepz80 library
from upeepz80 import PeepholeOptimizer

from .ast_opt import Uc80ASTOptimizer

# Target-specific predefined macros supplied to the shared uc_core preprocessor.
# __UC80_VERSION__ tracks uc80 (the driver), not uc_core.
Z80_CPM_PREDEFINES = {
    "__UC80__": "1",
    "__UC80_VERSION__": "100",
    "__Z80__": "1",
    "__CPM__": "1",
    # Integer-range GCC predefines — values match the Z80/CP/M 16-bit-int
    # data model (uc80 uses int=16, long=32, long long=64; size_t and
    # ptrdiff_t are 16-bit). Source that references e.g. ``__INT_MAX__``
    # directly compiles without including <limits.h>.
    "__SHRT_MAX__": "32767",
    "__INT_MAX__": "32767",
    "__LONG_MAX__": "2147483647L",
    "__LONG_LONG_MAX__": "9223372036854775807LL",
    "__SIZE_MAX__": "65535U",
    "__PTRDIFF_MAX__": "32767",
    "__SIZEOF_INT__": "2",
    "__SIZEOF_LONG__": "4",
    "__SIZEOF_LONG_LONG__": "8",
    "__SIZEOF_POINTER__": "2",
    "__SIZEOF_SIZE_T__": "2",
    "__SIZEOF_PTRDIFF_T__": "2",
    "__SIZEOF_FLOAT__": "4",
    "__SIZEOF_DOUBLE__": "4",
    "__SIZEOF_LONG_DOUBLE__": "4",
    "__SIZEOF_SHORT__": "2",
    # GCC defines ``__builtin_va_list`` as a built-in type; uc80's
    # ``va_list`` is just ``char *`` (see stdarg.h). Map the builtin
    # name to the same so tests that spell their typedef as
    # ``typedef __builtin_va_list va_list;`` compile.
    "__builtin_va_list": "char *",
}


def _is_end_directive(line: str) -> bool:
    """True if `line` is a MACRO-80 END directive.

    END may carry a start address (``END START``), so a plain equality test
    against "END" is not enough.
    """
    stripped = line.strip().upper()
    return (stripped == 'END'
            or stripped.startswith('END\t')
            or stripped.startswith('END '))


def _tail_insert_index(lines: list[str]) -> int:
    """Index in `lines` at which assembly appended to a module must be spliced.

    The code generator emits the COMMON (BSS) block last, immediately before
    END, and crt0 zeroes the whole COMMON region before main() runs.  Anything
    placed after ``common //`` therefore lands in BSS: it is wiped at startup
    and, being uninitialised storage, is not even present in the .com image.

    Re-establishing CSEG after the COMMON directive does NOT help.  um80
    cannot leave a COMMON block: the segment directive is ignored and the
    bytes that follow are dropped from the object file (um80 0.3.43,
    um80.py:2266-2303).  Appended code must therefore go *before* the COMMON
    directive, not after it.

    Returns the index of the COMMON directive - or of the comment banner
    introducing it - when the module has a BSS block, the index of the END
    directive when it does not, and len(lines) when it has neither.
    """
    for i, line in enumerate(lines):
        # The banner introduces the block in both spellings -- COMMON for a
        # whole-program module, DSEG for a separately compiled one, where
        # the linker would overlay every module's blank COMMON.
        if line.strip().startswith(BSS_BANNER_PREFIX):
            return i
        if re.match(r'\s*COMMON\b', line, re.IGNORECASE):
            # Keep the "; BSS - ..." banner attached to its directive.
            while i > 0 and lines[i - 1].strip().startswith(';'):
                i -= 1
            return i
    for i, line in enumerate(lines):
        if _is_end_directive(line):
            return i
    return len(lines)


def _bss_block_name(path) -> str:
    """Name of the BSS block of a separately compiled module: the file stem
    and a hash of the full path, so two files `a.c` in two directories do
    not share (overlay) one block.  The hash uses the path relative to the
    working directory when the file is below it, so the same tree gives the
    same object in any checkout."""
    import zlib
    p = Path(path).resolve()
    try:
        key = p.relative_to(Path.cwd().resolve())
    except ValueError:
        key = p
    stem = re.sub(r'[^A-Za-z0-9_]', '_', p.stem)[:12]
    return f"uc_{stem}_{zlib.crc32(str(key).encode()) & 0xFFFF:04x}"


VEC_REF_LABEL = "__vec_ref"


def _add_vector_labels(code: str, labels: dict, ref: bool) -> str:
    """Define the vector labels ({handler label: [vector labels]}) at the
    handler: PUBLIC lines and a label line in front of the label of the
    function.  With ``ref`` the module also refers to ``__crt0_rom_vec``
    (a word in CSEG: 2 bytes), which only crt0_rom_vec.rel defines."""
    for handler, names in labels.items():
        pat = re.compile(r"^(" + re.escape(handler) + r"::?(?:\s|$))",
                         re.M)
        add = "".join(f"\tPUBLIC\t{n}\n{n}:\n" for n in names)
        code, n = pat.subn(lambda m: add + m.group(1), code, count=1)
        if not n:
            raise CodegenError(f"no label {handler} to bind to the vector")
    if ref:
        lines = code.split("\n")
        at = _tail_insert_index(lines)
        lines[at:at] = ["", f"\tEXTRN\t{rom_module.VEC_REF}", "\tCSEG",
                        f"{VEC_REF_LABEL}:", f"\tDW\t{rom_module.VEC_REF}"]
        code = "\n".join(lines)
    return code


def _filter_hand_written_asm(asm_text: str):
    """Prepare one hand-written assembly module for appending to the output.

    Only two things are dropped: ``.Z80`` (already emitted at the top of the
    generated module) and END (a single END is appended after everything).
    Segment directives are deliberately KEPT - hand-written assembly must
    control its own CSEG/DSEG placement.  Stripping them makes the appended
    code inherit whichever segment the generated code happened to end in,
    which is how it used to land in BSS and get zeroed by crt0.

    Returns (lines, publics, defined_labels):
        lines           the filtered assembly
        publics         names the module declares PUBLIC
        defined_labels  every label the module defines
    """
    lines = []
    publics = set()
    defined_labels = set()
    for line in asm_text.splitlines():
        if line.strip().upper() == '.Z80' or _is_end_directive(line):
            continue
        lines.append(line)
        match = re.match(r'\s*PUBLIC\s+(.+)', line, re.IGNORECASE)
        if match:
            for label in match.group(1).split(','):
                publics.add(label.strip())
        match = re.match(r'^(\@?\?*\w+):', line)
        if match:
            defined_labels.add(match.group(1))
    return lines, publics, defined_labels


class _Peephole:
    """upeepz80, then the few rules of peep_local (``stats`` is
    upeepz80's)."""

    def __init__(self, inner):
        self.inner = inner

    @property
    def stats(self):
        return self.inner.stats

    def optimize(self, code: str) -> str:
        return peep_local.optimize(self.inner.optimize(code))


def _optimize_outside_asm(peephole, code: str) -> str:
    """Run the peephole optimizer on everything except inline-asm regions.

    Hand-written assembly must reach the assembler byte for byte, and no
    peephole pattern may match across its boundary - inline asm is an
    optimization barrier, exactly as it is in GCC.  A comment alone is not
    a barrier: upeepz80's matcher skips comment lines while matching a
    multi-line pattern (upeepz80/peephole.py:606-618), so a pattern would
    happily fuse the instruction before an asm block with the one after
    it.  Splitting the text at the markers and optimizing each non-asm run
    on its own is what actually enforces the barrier, and it needs no
    change to the external upeepz80 package.
    """
    lines = code.split("\n")

    def opens(line):
        return is_asm_begin(line) or line.strip() == BARRIER_BEGIN

    def closes(line):
        return is_asm_end(line) or line.strip() == BARRIER_END

    # Port I/O, interrupt control and the interrupt prologue and epilogue
    # (z80_intrinsics.py, codegen.py) are fenced the same way: the lines
    # between BARRIER_BEGIN and BARRIER_END reach the assembler as written.
    if not any(opens(line) for line in lines):
        return peephole.optimize(code)
    out: list[str] = []
    buf: list[str] = []
    in_asm = False
    for line in lines:
        if not in_asm and opens(line):
            out.append(peephole.optimize("\n".join(buf)))
            buf = []
            in_asm = True
            out.append(line)
            continue
        if in_asm:
            out.append(line)
            if closes(line):
                in_asm = False
            continue
        buf.append(line)
    out.append(peephole.optimize("\n".join(buf)))
    return "\n".join(out)


def _close_format_features(feats):
    """Close a --printf/--scanf feature set over the features it implies.

    ``float``, ``long`` and ``llong`` all imply ``int``: the dispatch table
    is one flat list, so leaving ``int`` out drops %d/%u/%s/%c entirely, and
    a dropped conversion does not just print nothing -- only a handler
    advances the vararg offset, so every later conversion in the same call
    reads the wrong argument.  ``llong`` additionally implies ``long``
    because %lld is reached by walking the 'l' entry into the long table.

    The compiler's own auto-detector has always assumed this closure
    (_extract_printf_specifiers adds 'int' alongside 'float'/'long'), so
    without it the explicit path behaves differently from the implicit one.
    """
    f = set(feats)
    if 'all' in f:
        return f
    if 'llong' in f:
        f.add('long')
    if f & {'float', 'long', 'llong'}:
        f.add('int')
    return f


class _PrintLibDirAction(argparse.Action):
    """``--print-lib-dir``: print the library directory, then exit 0.

    ``nargs=0`` plus the immediate ``parser.exit()`` make argparse run this
    the moment the flag is seen - before it checks that the required
    ``input`` positional was supplied - exactly the way ``-h`` already
    behaves.  Without that, ``uc80 --print-lib-dir`` would die with "the
    following arguments are required: input" and exit 2, which is the whole
    reason consumers had to guess the path instead of asking for it.

    The output is one line and nothing else, so ``LIB=$(uc80
    --print-lib-dir)`` is safe.  It is a path query, not a health check: it
    succeeds whether or not libc.lib has been built yet, and the caller
    tests for the file it needs.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        print(lib_dir())
        parser.exit(0)


class _BuildLibsAction(argparse.Action):
    """``--build-libs``: assemble libc.lib, runtime.lib and crt0.rel, then exit.

    Wheels built since 0.6.0 already ship those three, so this is for a git
    checkout (where they are gitignored build output) and for rebuilding
    after editing lc/*.mac or rt/*.mac.  Needs um80/ulib80 on PATH.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest,
                         default=default, nargs=0, help=help)

    @staticmethod
    def _read_only_advice(target):
        """Explain how to build the libraries out of tree."""
        print(f"uc80: error: cannot write to {target}", file=sys.stderr)
        print("The library directory is read-only, which usually means uc80 "
              "was installed into a system-owned site-packages.",
              file=sys.stderr)
        print("Build the libraries somewhere writable and point UC80_LIB_DIR "
              "at them:", file=sys.stderr)
        print(f"  cp -r {target} ~/uc80lib", file=sys.stderr)
        print("  (cd ~/uc80lib && python3 build_libs.py "
              "&& um80 crt0.mac -o crt0.rel)", file=sys.stderr)
        print("  export UC80_LIB_DIR=~/uc80lib", file=sys.stderr)

    def __call__(self, parser, namespace, values, option_string=None):
        import os
        import subprocess
        from .lib import build_libs

        # build_libs.py reads and writes beside itself, i.e. inside the
        # packaged tree - it has no output-directory option - so this cannot
        # honour UC80_LIB_DIR as a destination.
        target = Path(build_libs.SCRIPT_DIR)
        # Check writability up front. Without this the failure surfaces as
        # one "Permission denied" line per module - 89 of them - because
        # build_libs runs um80 as a subprocess and reports each assembly as
        # a plain failure.
        if not os.access(target, os.W_OK):
            self._read_only_advice(target)
            parser.exit(1)
        try:
            # Pass the targets explicitly: build_libs.main() otherwise reads
            # sys.argv, which here holds uc80's own flags and would make it
            # report "Unknown target: --build-libs".
            build_libs.main(["runtime", "libc"])
            # build_libs.py builds the libraries but not the startup module.
            for stem in ("crt0", "crt0_rom", "crt0_rom_vec"):
                crt0_mac, crt0_rel = target / f"{stem}.mac", target / f"{stem}.rel"
                if crt0_mac.exists():
                    result = subprocess.run(
                        ["um80", str(crt0_mac), "-o", str(crt0_rel)],
                        capture_output=True, text=True)
                    if result.returncode:
                        print(result.stdout + result.stderr, file=sys.stderr)
                        parser.exit(1)
                    print(f"  Assembled {stem}.rel")
        except SystemExit as e:
            if e.code:
                parser.exit(int(e.code))
        except PermissionError:
            # Backstop for a directory that is writable but holds a
            # read-only file, which the os.access() check above cannot see.
            self._read_only_advice(target)
            parser.exit(1)
        except FileNotFoundError as e:
            print(f"uc80: error: {e.filename or e}: not found", file=sys.stderr)
            print("Building the libraries needs um80 and ulib80 on PATH: "
                  "pip install um80", file=sys.stderr)
            parser.exit(1)
        parser.exit(0)


def _defines_label(code: str, mac_files: list, label: str) -> bool:
    """True if the generated code or an appended .mac file has PUBLIC label."""
    pat = re.compile(r"^\s*PUBLIC\s+(?:[^;\n]*,\s*)?" + re.escape(label)
                     + r"\b", re.I | re.M)
    return bool(pat.search(code) or any(pat.search(m) for m in mac_files))


@dataclass
class _Roots:
    keep: set        # function and object names to keep
    public: set      # names that get a PUBLIC label even if static
    vectors: dict    # {0x38: name, 0x66: name}
    isrs: dict = None  # interrupt functions: name -> "int", "int_crit", "nmi"
    rst: dict = None   # {0x08..0x30: name}: RST vectors (docs/rom_hardware.md)
    im2: dict = None   # {slot 0..127: name}: IM 2 vector table slots
    im2_table: int | None = None  # address of the IM 2 table, or None
    im2_on: bool = False          # IM 2 is in use
    where: dict = None            # {("rst", 8) | ("vec", 0x38): "file:line"}
    save_wa: set = None           # interrupt functions that save work areas


_VECTORS = {0x38: "INT (38h)", 0x66: "NMI (66h)"}
_RST_ADDRS = (0x08, 0x10, 0x18, 0x20, 0x28, 0x30)
IM2_DEFAULT_TABLE = 0x100


def _parse_address(text: str):
    """38h, 0x38, 038H or decimal -> int, or None."""
    t = text.strip().lower()
    try:
        if t.startswith("0x"):
            return int(t[2:], 16)
        if t.endswith("h"):
            return int(t[:-1], 16)
        return int(t, 10)
    except ValueError:
        return None


def _ambiguous_functions(asts: list, paths: list) -> dict:
    """{name: [file, ...]} for each function that more than one input file
    defines.  A vector pragma names a function by its name alone, so such a
    name cannot be bound (two static functions of one name in two files)."""
    seen = {}
    for ast, path in zip(asts, paths):
        for item in ast.items or []:
            if isinstance(item, ast_module.FunctionDef):
                name = function_name(item)
                if name and path not in seen.setdefault(name, []):
                    seen[name].append(path)
    return {n: f for n, f in seen.items() if len(f) > 1}


_NAME_WORDS = {"rst": (1,), "im2": (1,), "vector": (1,),
               "interrupt": (0,), "nmi": (0,)}
_ALL_NAMES = ("keep", "public", "isr_save_workareas")


def source_name(name: str) -> str:
    """The C name of a name that the merge renamed (h__s0 -> h)."""
    return re.sub(r"__s\d+$", "", name)


def resolve_pragma_names(pragmas: list, units: list, maps: list,
                         names: list = None) -> list:
    """Replace the function name of each pragma with the name that the
    merge gave it in the file of the pragma (a renamed static).  NAMES is
    file_scope_names() of the units before the renaming: a vector pragma
    that names a function which only another file has as a static is an
    error, because that static is not visible in the file of the pragma."""
    out = []
    for (verb, words, fname, line), u in zip(pragmas, units):
        m = maps[u] if u < len(maps) else {}
        if names and verb in _NAME_WORDS and u < len(names):
            for i in _NAME_WORDS[verb]:
                w = words[i] if i < len(words) else None
                if not w or w in names[u][0]:
                    continue
                elsewhere = [j for j, (ev, st) in enumerate(names)
                             if j != u and w in ev]
                if elsewhere and all(w in names[j][1] for j in elsewhere):
                    raise CodegenError(
                        f"{fname}:{line}: #pragma uc80 {verb}: '{w}' is not "
                        f"defined in this file; it is a static function of "
                        f"another file, which this file cannot name")
        if m and (verb in _ALL_NAMES or verb in _NAME_WORDS):
            idx = (range(len(words)) if verb in _ALL_NAMES
                   else _NAME_WORDS[verb])
            words = [m.get(w, w) if i in idx else w
                     for i, w in enumerate(words)]
        out.append((verb, words, fname, line))
    return out


def resolve_found_names(found, paths: list, maps: list) -> None:
    """The same for the __interrupt and __naked marks of the SDCC syntax,
    which carry the path of their file."""
    where = {p: i for i, p in enumerate(paths)}

    def mapped(name, path):
        i = where.get(path)
        return maps[i].get(name, name) if i is not None and i < len(maps) \
            else name
    found.isrs[:] = [(k, mapped(n, f), f, l) for k, n, f, l in found.isrs]
    found.naked[:] = [(mapped(n, f), f, l) for n, f, l in found.naked]
    found.naked_protos[:] = [(mapped(n, f), f, l)
                             for n, f, l in found.naked_protos]


def _roots_from_options(entries: list, pragmas: list,
                        sdcc_isrs: list = (), ambiguous: dict = None) -> _Roots:
    """The names --entry and #pragma uc80 keep/public/vector pin down.
    Raises CodegenError for a pragma it does not understand: a vector
    that is silently dropped is a ROM that does not start."""
    roots = _Roots(keep=set(entries), public=set(), vectors={}, isrs={},
                   rst={}, im2={}, where={}, save_wa=set())
    ident = re.compile(r"^[A-Za-z_]\w*$")
    ambiguous = ambiguous or {}

    def unambiguous(name, where):
        if name in ambiguous:
            raise CodegenError(
                f"{where}: '{name}' is defined in more than one file ("
                f"{', '.join(ambiguous[name])}); a vector names a function "
                f"by its name alone, so uc80 cannot tell which one is "
                f"meant. Give each function its own name")

    def remember(key, where):
        m = re.match(r"(.*?:\d+):", where)
        roots.where.setdefault(key, m.group(1) + ": " if m else "")

    def add_rst(addr, name, kind, where):
        unambiguous(name, where)
        if addr not in _RST_ADDRS:
            hint = (" (0000h is the reset vector; use '#pragma uc80 "
                    "interrupt NAME' for 0038h)" if addr in (0, 0x38) else "")
            raise CodegenError(
                f"{where}: the RST address must be 08h, 10h, 18h, 20h, 28h "
                f"or 30h{hint}")
        if addr in roots.rst:
            raise CodegenError(
                f"{where}: the RST {addr:02X}h vector is already set to "
                f"{source_name(roots.rst[addr][0])}")
        if kind is not None:
            if roots.isrs.get(name, kind) != kind:
                raise CodegenError(
                    f"{where}: '{name}' is already an interrupt function of "
                    f"another kind")
            roots.isrs[name] = kind
        roots.rst[addr] = (name, kind)
        remember(("rst", addr), where)
        roots.keep.add(name)
        roots.public.add(name)

    def add_im2(slot, name, kind, where):
        unambiguous(name, where)
        if not 0 <= slot <= 127:
            raise CodegenError(
                f"{where}: the IM 2 slot must be 0 to 127 (the data bus "
                f"byte is 2 times the slot)")
        if slot in roots.im2:
            raise CodegenError(
                f"{where}: IM 2 slot {slot} is already set to "
                f"{source_name(roots.im2[slot])}")
        if roots.isrs.get(name, kind) != kind:
            raise CodegenError(
                f"{where}: '{name}' is already an interrupt function of "
                f"another kind")
        roots.isrs[name] = kind
        roots.im2[slot] = name
        roots.im2_on = True
        remember(("im2", 0), where)
        roots.keep.add(name)
        roots.public.add(name)

    def add_isr(kind, name, where):
        unambiguous(name, where)
        addr = 0x66 if kind == "nmi" else 0x38
        if roots.isrs.get(name, kind) != kind:
            raise CodegenError(
                f"{where}: '{name}' is already an interrupt function of "
                f"another kind")
        if addr in roots.vectors and roots.vectors[addr] != name:
            raise CodegenError(
                f"{where}: the {_VECTORS[addr]} vector is already set to "
                f"{source_name(roots.vectors[addr])}")
        roots.isrs[name] = kind
        roots.vectors[addr] = name
        remember(("vec", addr), where)
        roots.keep.add(name)
        roots.public.add(name)

    save_where = {}
    for kind, name, fname, line in sdcc_isrs:
        add_isr(kind, name, f"{fname}:{line}: __interrupt")
    for verb, words, fname, line in pragmas:
        where = f"{fname}:{line}: #pragma uc80 {verb}"
        if verb in ("keep", "public"):
            if not words or not all(ident.match(w) for w in words):
                raise CodegenError(f"{where}: expected one or more names")
            roots.keep.update(words)
            if verb == "public":
                roots.public.update(words)
        elif verb == "rst":
            addr = _parse_address(words[0]) if words else None
            ok = (len(words) in (2, 3, 4) and addr is not None
                  and ident.match(words[1])
                  and (len(words) == 2 or words[2] == "interrupt")
                  and (len(words) < 4 or words[3] == "critical"))
            if not ok:
                raise CodegenError(
                    f"{where}: expected '#pragma uc80 rst 08h NAME' or "
                    f"'#pragma uc80 rst 08h NAME interrupt [critical]'")
            kind = (None if len(words) == 2 else
                    "int_crit" if len(words) == 4 else "int")
            add_rst(addr, words[1], kind, where)
        elif verb == "im2table":
            addr = _parse_address(words[0]) if len(words) == 1 else None
            if addr is None or addr % 256 or not 0x100 <= addr <= 0xFE00:
                raise CodegenError(
                    f"{where}: expected '#pragma uc80 im2table ADDR' with "
                    f"ADDR a multiple of 100h from 0100h to FE00h "
                    f"(the program code follows the table, 256 bytes long, "
                    f"and must start below 10000h)")
            if roots.im2_table not in (None, addr):
                raise CodegenError(
                    f"{where}: the IM 2 table is already at "
                    f"{roots.im2_table:04X}h")
            roots.im2_table = addr
            roots.im2_on = True
            remember(("im2", 0), where)
        elif verb == "im2":
            slot = (_parse_address(words[0])
                    if len(words) in (2, 3) else None)
            if (slot is None or not ident.match(words[1])
                    or (len(words) == 3 and words[2] != "critical")):
                raise CodegenError(
                    f"{where}: expected '#pragma uc80 im2 SLOT NAME' or "
                    f"'#pragma uc80 im2 SLOT NAME critical'")
            add_im2(slot, words[1], "int_crit" if len(words) == 3 else "int",
                    where)
        elif verb == "vector":
            addr = _parse_address(words[0]) if words else None
            if (len(words) == 2 and addr in _RST_ADDRS
                    and ident.match(words[1])):
                add_rst(addr, words[1], None, where)
                continue
            if len(words) != 2 or addr not in _VECTORS or not ident.match(words[1]):
                raise CodegenError(
                    f"{where}: expected '#pragma uc80 vector 38h NAME' or "
                    f"'#pragma uc80 vector 66h NAME'")
            if addr in roots.vectors and roots.vectors[addr] != words[1]:
                raise CodegenError(
                    f"{where}: the {_VECTORS[addr]} vector is already set "
                    f"to {source_name(roots.vectors[addr])}")
            unambiguous(words[1], where)
            roots.vectors[addr] = words[1]
            remember(("vec", addr), where)
            roots.keep.add(words[1])
        elif verb == "isr_save_workareas":
            if not words or not all(ident.match(w) for w in words):
                raise CodegenError(
                    f"{where}: expected one or more names of interrupt "
                    f"functions")
            for w in words:
                unambiguous(w, where)
            roots.save_wa.update(words)
            save_where.update({w: where for w in words})
        elif verb in ("interrupt", "nmi"):
            ok = (len(words) == 1 or (verb == "interrupt" and len(words) == 2
                                      and words[1] == "critical"))
            if not ok or not ident.match(words[0]):
                raise CodegenError(
                    f"{where}: expected '#pragma uc80 interrupt NAME', "
                    f"'#pragma uc80 interrupt NAME critical' or "
                    f"'#pragma uc80 nmi NAME'")
            kind = ("nmi" if verb == "nmi" else
                    "int_crit" if len(words) == 2 else "int")
            add_isr(kind, words[0], where)
        else:
            raise CodegenError(
                f"{where}: unknown verb; uc80 knows keep, public, vector, "
                f"interrupt, nmi, rst, im2, im2table and isr_save_workareas")
    for name in sorted(roots.save_wa):
        if name not in roots.isrs:
            raise CodegenError(
                f"{save_where[name]}: '{source_name(name)}' is not an "
                f"interrupt function; name it in #pragma uc80 interrupt, "
                f"nmi, im2 or rst ... interrupt")
    return roots


def _rom_problems(gen, code: str) -> list:
    """Error messages for what a ROM cannot do: main(argc, argv), and calls
    to library functions that are not in libc_rom.lib."""
    problems = []
    main_sym = gen.ctx.globals.get("main")
    ptypes = getattr(getattr(main_sym, "sym_type", None), "param_types", None)
    if ptypes:
        problems.append(
            "--target=rom: main() has parameters; a ROM has no command "
            "line, so declare main(void)")
    names = rom_module.undefined_in_rom(lib_dir(),
                                        drop_unreferenced_externs(code))
    if names:
        problems.append(
            "--target=rom: the program calls " + ", ".join(names)
            + ", which libc_rom.lib does not have. Console and file "
            "I/O, exit, abort, the heap, the clock and the environment "
            "need the CP/M BDOS or an operating system. See docs/rom.md")
    return problems


UNSUPPORTED_MESSAGE = ("uc80 does not support '{name}' yet; "
                       "see docs/rom_target.md")


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="uc80",
        description="C24 compiler for Z80"
    )
    parser.add_argument(
        "input",
        nargs='*',
        help="Input C source file(s) or .mac assembly file(s)"
    )
    # Query/maintenance flags. These act the moment argparse sees them and
    # exit, so they work without an input file despite `input` being
    # required.
    parser.add_argument(
        "--print-lib-dir",
        action=_PrintLibDirAction,
        help="Print the directory holding crt0.rel, libc.lib and "
             "runtime.lib, then exit"
    )
    parser.add_argument(
        "--build-libs",
        action=_BuildLibsAction,
        help="Assemble libc.lib, runtime.lib and crt0.rel into that "
             "directory, then exit"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output assembly file (default: input.mac)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "-I", "--include",
        action="append",
        default=[],
        metavar="DIR",
        help="Add directory to include search path"
    )
    parser.add_argument(
        "-D", "--define",
        action="append",
        default=[],
        metavar="NAME[=VALUE]",
        help="Define preprocessor macro"
    )
    parser.add_argument(
        "-E", "--preprocess-only",
        action="store_true",
        help="Preprocess only, output to stdout"
    )
    parser.add_argument(
        "-P", "--no-preprocess",
        action="store_true",
        help="Skip preprocessing"
    )
    parser.add_argument(
        "-O0", "--no-optimize",
        action="store_true",
        help="Disable peephole optimization"
    )
    parser.add_argument(
        "--no-shared-storage",
        action="store_true",
        help="Disable shared storage optimization for non-recursive functions"
    )
    parser.add_argument(
        "--no-dead-elimination",
        action="store_true",
        help="Disable dead function elimination"
    )
    parser.add_argument(
        "--no-inlining",
        action="store_true",
        help="Disable inline expansion of small functions"
    )
    parser.add_argument(
        "--no-const-propagation",
        action="store_true",
        help="Disable interprocedural constant propagation"
    )
    parser.add_argument(
        "--no-whole-program",
        action="store_true",
        help="Assume other C files may be linked (disables some optimizations on PUBLIC functions)"
    )
    parser.add_argument(
        "--no-register-cc",
        action="store_true",
        help="Pass every argument on the stack (docs/calling_convention.md)"
    )
    parser.add_argument(
        "--no-register-locals",
        action="store_true",
        help="Keep every local in memory (docs/register_locals.md)"
    )
    parser.add_argument(
        "--no-module-bss",
        action="store_true",
        help="With --no-whole-program, put the zero-start data of the module "
             "in DSEG (in the image) and not in a BSS block of its own"
    )
    parser.add_argument(
        "--no-direct-pointers",
        action="store_true",
        help="Keep every access through a pointer that holds a constant "
             "address as an access through the pointer (docs/rom_hardware.md)"
    )
    parser.add_argument(
        "--no-embed-runtime",
        action="store_true",
        help="Don't embed runtime library (use EXTRN references instead)"
    )
    parser.add_argument(
        "--runtime-lib",
        metavar="FILE",
        help="Runtime library .mac file (default: lib/runtime.mac)"
    )
    parser.add_argument(
        "--embed-lib",
        action="append",
        default=[],
        metavar="FILE",
        help="Additional .mac library to embed (can specify multiple times)"
    )
    parser.add_argument(
        "--no-asm-dce",
        action="store_true",
        help="Disable assembly-level dead code elimination"
    )
    parser.add_argument(
        "--no-ast-optimize",
        action="store_true",
        help="Disable AST-level expression optimization"
    )
    parser.add_argument(
        "--no-embed-startup",
        action="store_true",
        help="Don't embed startup code (crt0) in whole-program mode"
    )
    parser.add_argument(
        "--startup-lib",
        metavar="FILE",
        help="Startup code .mac file (default: lib/crt0.mac)"
    )
    parser.add_argument(
        "--target", choices=["cpm", "rom"], default="cpm",
        help="cpm (default): a CP/M program. rom: a freestanding Z80 ROM "
             "image. Selects lib/crt0_rom.mac (vectors, SP, data copy, BSS "
             "clear), no BDOS, and refuses exit, abort, console and file "
             "I/O and main(argc, argv). See docs/rom.md"
    )
    parser.add_argument(
        "--rom-size", metavar="HEX", default="8000",
        help="--target=rom with --asm-syntax=sdas: the ROM size in hex "
             "(default 8000, as in the ul80 command of docs/rom.md; 1 to "
             "10000). With um80 and ul80 the option only changes the "
             "ul80 command that -v prints: give the size to ul80. uc80 "
             "refuses a program whose code starts at or above the end of "
             "the ROM (an IM 2 table near the top). ul80 checks the size "
             "of the image by itself"
    )
    parser.add_argument(
        "-ffreestanding", dest="freestanding", action="store_true",
        help="Same as --target=rom"
    )
    parser.add_argument(
        "--stack-top", metavar="EXPR",
        help="--target=rom: the initial SP, an assembler expression "
             "(default: __RAM_END, which ul80 defines from --ram-end or "
             "--ram-size)"
    )
    parser.add_argument(
        "--asm-syntax", choices=["m80", "sdas"], default="m80",
        help="m80 (default): MACRO-80 text for um80 and ul80. sdas: write "
             "sdas text (a .s file) for sdasz80 and sdldz80, to link with "
             "SDCC code or with nullsound. See docs/rom_interop.md"
    )
    parser.add_argument(
        "--sdas-style", choices=sorted(sdas.STYLES), default="sdcc",
        help="--asm-syntax=sdas: the area names. sdcc (default): _CODE, "
             "_INITIALIZER, _INITIALIZED, _DATA. ngdevkit: CODE, "
             "INITIALIZER, INITIALIZED, DATA (the names of nullsound)"
    )
    parser.add_argument(
        "--sdas-areas", metavar="ROLE=NAME,...", default="",
        help="--asm-syntax=sdas: change single area names. Roles: code, "
             "rom_init, ram_init, bss, absolute"
    )
    parser.add_argument(
        "--build-sdas-libs", metavar="DIR",
        help="Translate and assemble runtime and libc with sdasz80 into "
             "DIR/uc80_rt.lib and DIR/uc80_lc.lib, then exit. Uses "
             "--sdas-style, and --target (the CP/M libraries differ from "
             "the ROM libraries in the place of the initial data)"
    )
    parser.add_argument(
        "--entry", metavar="NAME", action="append", default=[],
        help="Keep function NAME and everything it calls, even if no code "
             "calls NAME (repeatable; the same as #pragma uc80 keep NAME). "
             "With --target=rom and no main(), the first --entry is the "
             "function the start-up code calls"
    )
    parser.add_argument(
        "--isr-safe", action="store_true",
        help="Functions that an interrupt function (#pragma uc80 interrupt, "
             "nmi, or SDCC __interrupt) reaches keep their locals on the "
             "stack, never in the shared ??AUTO area. Use it when main "
             "code and an interrupt function call the same function. "
             "See docs/rom.md"
    )
    parser.add_argument(
        "--isr-save-workareas", action="store_true",
        help="Every interrupt function saves and restores the fixed work "
             "areas of the runtime that its call graph can reach "
             "(__tmp32, __div32_dvd, __acc64 and so on), so main code that "
             "uses the same routines is not corrupted. One handler: "
             "#pragma uc80 isr_save_workareas NAME. See docs/rom_hardware.md"
    )
    parser.add_argument(
        "--with-nullsound", action="store_true",
        help="The program links with nullsound (the Neo Geo sound "
             "driver): warn for every function and runtime helper on a "
             "path from --entry, main or an interrupt function that uses "
             "EXX or EX AF,AF'. The ngdevkit style with --entry has the "
             "check without the option. See docs/rom_interop.md"
    )
    parser.add_argument(
        "--no-crlf",
        action="store_true",
        help="Do not translate '\\n' to CR LF on console output. The default "
             "translates, which is what a real CP/M terminal needs. Only the "
             "translation unit containing main() honours this flag; a program "
             "can also flip it at run time with <stdio.h>'s __crlf_mode. "
             "File streams are never translated either way."
    )
    parser.add_argument(
        "--printf",
        action="append",
        default=[],
        choices=["int", "long", "llong", "float", "all"],
        help="Printf conversions to support; ADDITIVE, repeat the flag "
             "(--printf int --printf float). float/long/llong imply int, and "
             "llong implies long. A conversion outside the selected set is "
             "echoed verbatim at run time and misaligns the rest of that call. "
             "Omit the flag to let the compiler detect what the literal format "
             "strings need."
    )
    parser.add_argument(
        "--scanf",
        action="append",
        default=[],
        choices=["int", "long", "llong", "float", "text", "all"],
        help="Scanf format support level; ADDITIVE, same closure rules as "
             "--printf. float links the %%f conversions and text links the "
             "%%c, %%s and %%[ conversions; the compiler finds them in the "
             "format strings, so these options only force them"
    )
    parser.add_argument(
        "--int", dest="int_bits", type=int, choices=[16, 32],
        help="int width in bits (default: 16)"
    )
    parser.add_argument(
        "--long", dest="long_bits", type=int, choices=[32, 64],
        help="long width in bits (default: 32)"
    )
    parser.add_argument(
        "--long-long", dest="long_long_bits", type=int, choices=[64],
        help="long long width in bits (default: 64)"
    )
    parser.add_argument(
        "--ptr", dest="ptr_bits", type=int, choices=[16],
        help="pointer width in bits (default: 16 — Z80 only supports 16-bit pointers)"
    )

    args = parser.parse_args()
    if not args.input and not args.build_sdas_libs:
        parser.error("the following arguments are required: input")
    if args.build_sdas_libs:
        try:
            names = sdas.area_names(args.sdas_style, args.sdas_areas)
        except ValueError as e:
            parser.error(str(e))
        if not sdas.tools_available():
            print("uc80: error: --build-sdas-libs needs sdasz80 and sdar "
                  "(SDCC) on PATH", file=sys.stderr)
            return 1
        res = sdas.build_library(
            args.build_sdas_libs, names,
            flat_data=not (args.target == "rom" or args.freestanding))
        for lib in res["libs"]:
            print(f"  Built {lib}")
        for name, msg in res["failed"]:
            print(f"uc80: error: {name}: {msg}", file=sys.stderr)
        return 1 if res["failed"] else 0
    rom = args.target == "rom" or args.freestanding
    if args.asm_syntax == "sdas":
        try:
            sdas.area_names(args.sdas_style, args.sdas_areas)
        except ValueError as e:
            parser.error(str(e))
    if args.stack_top and not rom:
        parser.error("--stack-top needs --target=rom")

    # Build TypeConfig from CLI overrides (defaults = Z80_CPM: int16/long32/ptr16)
    type_config = TypeConfig(
        char_size=Z80_CPM.char_size,
        short_size=Z80_CPM.short_size,
        int_size=(args.int_bits // 8) if args.int_bits else Z80_CPM.int_size,
        long_size=(args.long_bits // 8) if args.long_bits else Z80_CPM.long_size,
        long_long_size=(args.long_long_bits // 8) if args.long_long_bits else Z80_CPM.long_long_size,
        ptr_size=(args.ptr_bits // 8) if args.ptr_bits else Z80_CPM.ptr_size,
        float_size=Z80_CPM.float_size,
        double_size=Z80_CPM.double_size,
        long_double_size=Z80_CPM.long_double_size,
    )

    # Validate all input files exist
    input_paths = [Path(f) for f in args.input]
    for input_path in input_paths:
        if not input_path.exists():
            print(f"uc80: error: {input_path}: No such file", file=sys.stderr)
            return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        # Use first input file's name for output
        output_path = input_paths[0].with_suffix(
            ".s" if args.asm_syntax == "sdas" else ".mac")

    # Set up include paths
    include_paths = list(args.include)
    # Add lib/include as default include path
    lib_include = lib_file("include")
    if lib_include.exists():
        include_paths.append(str(lib_include))

    # Compile
    try:
        asts = []
        sdcc_found = sdcc_compat.Found()
        mac_files = []  # Assembly files to append
        printf_features: set[str] | None = None  # From #pragma printf
        scanf_features: set[str] | None = None   # From #pragma scanf
        target_pragmas = []                      # From #pragma uc80 ...
        pragma_units = []                        # unit index of each pragma

        for input_path in input_paths:
            # Handle .mac assembly files - pass through
            if input_path.suffix.lower() == '.mac':
                if args.verbose:
                    print(f"Including assembly file {input_path}...")
                try:
                    mac_content = input_path.read_text()
                    mac_files.append(mac_content)
                except Exception as e:
                    print(f"uc80: error: Cannot read {input_path}: {e}", file=sys.stderr)
                    return 1
                continue

            if args.verbose:
                print(f"Compiling {input_path}...")

            # Read source.  C source isn't strictly UTF-8 — string literals
            # can contain arbitrary bytes (e.g. embedded \xff).  Fall back to
            # latin-1 if utf-8 decoding fails so the lexer sees the file
            # verbatim and the bytes survive into the output unchanged.
            try:
                source = input_path.read_text()
            except UnicodeDecodeError:
                try:
                    source = input_path.read_text(encoding='latin-1')
                except Exception as e:
                    print(f"uc80: error: Cannot read {input_path}: {e}", file=sys.stderr)
                    return 1
            except Exception as e:
                print(f"uc80: error: Cannot read {input_path}: {e}", file=sys.stderr)
                return 1

            # Preprocessing
            if not args.no_preprocess:
                if args.verbose:
                    print(f"  Preprocessing...")

                pp_predefines = {**Z80_CPM_PREDEFINES, **type_config.predefined_macros()}
                if rom:
                    del pp_predefines["__CPM__"]
                    pp_predefines["__UC80_ROM__"] = "1"
                pp = Preprocessor(include_paths, target_predefines=pp_predefines)

                # Add command-line defines
                for define in args.define:
                    if '=' in define:
                        name, value = define.split('=', 1)
                        pp.macros[name] = pp.macros.get(name) or Macro(name, body=value)
                    else:
                        pp.macros[define] = Macro(define, body="1")

                source = pp.preprocess(source, str(input_path))

                target_pragmas.extend(pp.target_pragmas)
                pragma_units.extend([len(asts)] * len(pp.target_pragmas))

                # Collect #pragma printf/scanf features
                if pp.printf_features:
                    if printf_features is None:
                        printf_features = set()
                    printf_features |= pp.printf_features
                if pp.scanf_features:
                    if scanf_features is None:
                        scanf_features = set()
                    scanf_features |= pp.scanf_features

                if args.verbose:
                    print(f"  Preprocessed to {len(source.splitlines())} lines")

                # If -E, just output preprocessed source
                if args.preprocess_only:
                    print(source)
                    continue

            # Front-end: lex + parse via plox-driven c23 grammar.
            # Strict: the interrupt and fastcall markers mean something on a
            # Z80, so the frontend refuses them instead of erasing them.
            # SDCC spellings uc80 accepts (docs/rom.md): the prepass turns
            # them into the uc80 forms and records them.  Anything else
            # that looks like SDCC stays an error.
            source = sdcc_compat.prepass(source, str(input_path), sdcc_found)
            ast = _frontend_parse(
                source, str(input_path),
                strict_unsupported=True,
                unsupported_message=UNSUPPORTED_MESSAGE)
            sdcc_compat.rewrite_sfr(ast, sdcc_found.sfrs, str(input_path))
            asts.append(ast)

            if args.verbose:
                print(f"  Parsed {len(ast.items)} declarations")

        # If preprocess-only mode, we're done
        if args.preprocess_only:
            return 0

        # Merge ASTs into single TranslationUnit
        static_maps = [{} for _ in asts]
        scope_names = None
        if len(asts) == 1:
            merged_ast = asts[0]
        else:
            # A static of one file must stay apart from a same-named
            # static (or global) of another file.
            uniquify_enumerators(asts)
            uniquify_types(asts)
            scope_names = file_scope_names(asts)
            static_maps = uniquify_statics_map(asts)
            merged_ast = ast_module.TranslationUnit(items=[])
            for unit in asts:
                merged_ast.items.extend(unit.items)
            if args.verbose:
                print(f"Merged {len(asts)} files into {len(merged_ast.items)} declarations")

        # AST-level expression optimization
        if not args.no_ast_optimize:
            opt_level = 3
            ast_opt = Uc80ASTOptimizer(opt_level, type_config=type_config)
            merged_ast = ast_opt.optimize(merged_ast)
            if args.verbose and ast_opt.stats:
                print(f"  AST optimizations:")
                for name, count in sorted(ast_opt.stats.items()):
                    print(f"    {name}: {count}")

        # Determine module name from first input file
        module_name = input_paths[0].stem

        # Code generation with optional optimizations
        enable_shared_storage = not args.no_shared_storage
        enable_dead_elimination = not args.no_dead_elimination
        enable_inlining = not args.no_inlining
        enable_const_propagation = not args.no_const_propagation
        whole_program = not args.no_whole_program
        # Embed runtime by default when whole_program is enabled
        embed_runtime = whole_program and not args.no_embed_runtime
        # Embed startup code (crt0) by default when whole_program is enabled
        embed_startup = whole_program and not args.no_embed_startup

        # Command-line --printf overrides #pragma printf
        if args.printf:
            printf_features = set(args.printf)
        if args.scanf:
            scanf_features = set(args.scanf)
        # vsnprintf is not in the format scan of the code generator, so a
        # program that calls it needs every conversion, as vsprintf does
        # (its format is not a literal).
        if printf_features is None and printf_hook.uses_unscanned(merged_ast):
            printf_features = {"all"}
        # ...and either way, close the set over its implied features.
        if printf_features is not None:
            printf_features = _close_format_features(printf_features)
        if scanf_features is not None:
            scanf_features = _close_format_features(scanf_features)

        # A pragma, __interrupt or __naked names a function of its own
        # file: a static that the merge renamed (h -> h__s0) is found
        # through the map of that file.
        c_paths = [str(p) for p in input_paths if p.suffix.lower() != ".mac"]
        target_pragmas = resolve_pragma_names(
            target_pragmas, pragma_units, static_maps, scope_names)
        resolve_found_names(sdcc_found, c_paths, static_maps)
        for e in args.entry:
            if sum(e in m for m in static_maps) > 1:
                raise CodegenError(
                    f"--entry {e}: '{e}' is a static function or variable "
                    f"in more than one file, so the name does not tell "
                    f"which one is meant")
        roots = _roots_from_options(
            args.entry, target_pragmas, sdcc_found.isrs,
            _ambiguous_functions(asts, [str(p) for p in input_paths
                                        if p.suffix.lower() != ".mac"]))
        gen = CodeGenerator(module_name, enable_shared_storage, enable_dead_elimination,
                           enable_inlining, enable_const_propagation, whole_program,
                           embed_runtime=embed_runtime,
                           printf_features=printf_features,
                           scanf_features=scanf_features,
                           type_config=type_config,
                           crlf_console=not args.no_crlf or rom,
                           rom_data=rom,
                           keep_names=roots.keep,
                           public_names=roots.public,
                           isr_funcs=roots.isrs,
                           isr_safe=args.isr_safe,
                           absolute=sdcc_found.absolute,
                           # Hand-written assembly and extra libraries may
                           # call a C function with the stack convention.
                           register_cc=not (args.no_register_cc
                                            or args.embed_lib
                                            or args.runtime_lib
                                            or args.startup_lib),
                           foreign_asm_names=regcc.asm_text_names(mac_files),
                           register_locals=not args.no_register_locals,
                           naked_funcs={n for n, _f, _l in sdcc_found.naked})
        gen.direct_pointers = not args.no_direct_pointers
        gen.naked_file = {n: f for n, f, _l in sdcc_found.naked}
        for msg in sdcc_compat.naked_mismatches(
                asts, [str(p) for p in input_paths
                       if p.suffix.lower() != ".mac"], sdcc_found):
            print(f"uc80: warning: {msg}", file=sys.stderr)
        if not whole_program and not args.no_module_bss:
            gen.bss_block = _bss_block_name(input_paths[0])
        code = gen.generate(merged_ast)
        printf_need = printf_hook.printf_needs(merged_ast, printf_features)
        code = printf_hook.patch_tables(code, printf_need)
        gen.warnings = printf_hook.filter_warnings(gen.warnings, printf_need)
        scanf_need = scan_hook.scanf_needs(merged_ast, scanf_features)
        if scanf_need:
            code = scan_hook.add_scanf_references(code, module_name,
                                                  _tail_insert_index,
                                                  scanf_need)
        # Short file code for a program that opens no file in an update or
        # append mode.  Only when this unit is the whole program: another
        # unit or hand-written assembly could open one.
        if (whole_program and not rom and not mac_files
                and not (args.embed_lib or args.runtime_lib
                         or args.startup_lib)
                and file_hook.needs_lite(merged_ast)):
            code = file_hook.add_lite_reference(code, module_name,
                                                _tail_insert_index)

        def label_of(name):
            sym = gen.ctx.globals.get(name)
            return sym.label() if sym else f"_{name}"

        keep_labels = {label_of(n) for n in roots.keep}
        # The vectors, the IM 2 table and the I register are set by the
        # start-up code of --target=rom only.
        no_vec = (not rom or not embed_startup or args.startup_lib)
        why_rst = ("only --target=rom writes the vectors" if not rom else
                   "only the start-up code that uc80 embeds (whole-program "
                   "mode, no --startup-lib) sets the vectors")
        # Vectors in a module without the start-up code of uc80 (separate
        # compilation, or --no-embed-startup): the handler gets the label
        # that its vector jumps to (rom.VEC_LABELS).  crt0_rom_vec.rel has
        # the vectors.  A module of a separate compilation also refers to
        # that file, so the link with crt0_rom.rel fails on an undefined
        # symbol instead of making a ROM that never takes the interrupt.
        alias_vec = rom and not embed_startup
        via_vec_crt0 = (alias_vec and not whole_program
                        and not args.no_embed_startup)
        vec_names = {}                     # handler name -> [vector labels]
        if alias_vec:
            for _addr, _name in roots.vectors.items():
                vec_names.setdefault(_name, []).append(
                    rom_module.VEC_LABELS[_addr])
            for _addr, (_name, _k) in roots.rst.items():
                vec_names.setdefault(_name, []).append(
                    rom_module.VEC_LABELS[_addr])
        if via_vec_crt0 and roots.im2_on:
            raise CodegenError(
                roots.where.get(("im2", 0), "") + "IM 2 in separate "
                "compilation (--no-whole-program) is not supported: the "
                "vector table and the start-up code that loads I come from "
                "the whole-program mode. Compile the program in whole-program "
                "mode, or write the table in assembly and use "
                "--no-embed-startup")
        if no_vec:
            for _addr, (_name, _k) in sorted(roots.rst.items()):
                if via_vec_crt0:
                    continue
                print(f"uc80: warning: {roots.where.get(('rst', _addr), '')}"
                      f"the RST {_addr:02X}h vector is not "
                      f"set to '{_name}': {why_rst}; make your own start-up "
                      f"code jump to {label_of(_name)}"
                      + (f" (the label {rom_module.VEC_LABELS[_addr]} is "
                         f"there too)" if alias_vec else ""),
                      file=sys.stderr)
            if roots.im2_on:
                print("uc80: warning: " + roots.where.get(("im2", 0), "")
                      + "the IM 2 vector table is not written: " + ("only --target=rom writes the table "
                      if not rom else "only the start-up code that uc80 "
                      "embeds writes the table ") + "and sets the I "
                      "register and IM 2", file=sys.stderr)
            for _addr, _name in sorted(roots.vectors.items()):
                if via_vec_crt0:
                    continue
                print(f"uc80: warning: {roots.where.get(('vec', _addr), '')}"
                      f"the {_VECTORS[_addr]} vector is not "
                      f"set to '{_name}': {why_rst}; make your own start-up "
                      f"code jump to {label_of(_name)}"
                      + (f" (the label {rom_module.VEC_LABELS[_addr]} is "
                         f"there too)" if alias_vec else ""),
                      file=sys.stderr)
        if via_vec_crt0:
            for _addr, _name in sorted(roots.vectors.items()):
                print(f"uc80: note: {roots.where.get(('vec', _addr), '')}"
                      f"the {_VECTORS[_addr]} vector jumps to "
                      f"{rom_module.VEC_LABELS[_addr]}, the label of "
                      f"'{source_name(_name)}'; link crt0_rom_vec.rel in "
                      f"place of crt0_rom.rel", file=sys.stderr)
            for _addr, (_name, _k) in sorted(roots.rst.items()):
                print(f"uc80: note: {roots.where.get(('rst', _addr), '')}"
                      f"the RST {_addr:02X}h vector jumps to "
                      f"{rom_module.VEC_LABELS[_addr]}, the label of "
                      f"'{source_name(_name)}'; link crt0_rom_vec.rel in "
                      f"place of crt0_rom.rel", file=sys.stderr)
        for n in sorted(gen.asm_isrs):
            if not _defines_label(code, mac_files, label_of(n)):
                raise CodegenError(
                    f"'{source_name(n)}' is named as an interrupt function and declared "
                    f"here, but no assembly file defines {label_of(n)}")
        for n in sorted(roots.keep):
            if (n not in gen.ctx.globals
                    and not _defines_label(code, mac_files, label_of(n))):
                raise CodegenError(
                    f"'{source_name(n)}' is named by --entry or #pragma uc80 "
                    f"but the program does not define it")

        if rom:
            problems = _rom_problems(gen, code)
            if problems:
                for msg in problems:
                    print(f"uc80: error: {msg}", file=sys.stderr)
                return 1
            code = rom_module.strip_empty_printf_tables(code)

        for w in gen.warnings:
            print(f"uc80: warning: {w}", file=sys.stderr)

        if args.verbose:
            if gen.inlined_calls > 0:
                print(f"  Inlined {gen.inlined_calls} call(s)")
            if gen.constants_propagated > 0:
                print(f"  Propagated {gen.constants_propagated} constant(s)")
            if gen.dead_functions_removed > 0:
                print(f"  Eliminated {gen.dead_functions_removed} dead function(s)")
            if gen._regcc:
                print(f"  Register calling convention: {len(gen._regcc)} "
                      f"function(s): {', '.join(sorted(gen._regcc))}")
            if gen.call_graph_analyzer and gen.call_graph_analyzer.total_shared_storage > 0:
                cga = gen.call_graph_analyzer
                shared_count = len(cga.storage_offsets)
                individual_total = sum(cga.func_storage.get(f, 0) for f in cga.storage_offsets)
                print(f"  Shared storage: {shared_count} function(s), "
                      f"{individual_total} bytes reduced to {cga.total_shared_storage} bytes")
            print(f"  Generated {len(code.splitlines())} lines of assembly")

        # Embed startup code (crt0) if requested - at beginning of code
        if embed_startup:
            if args.verbose:
                print(f"  Embedding startup code...")

            # Load startup code
            if args.startup_lib:
                startup_path = Path(args.startup_lib)
            elif rom:
                startup_path = lib_file("crt0_rom.mac")
            else:
                # Default: crt0.mac from the package's library directory
                startup_path = lib_file("crt0.mac")

            if startup_path.exists():
                startup_content = startup_path.read_text()

                # Parse and filter startup code
                startup_lines = []
                if rom and not args.startup_lib:
                    int_handler = (label_of(roots.vectors[0x38])
                                   if 0x38 in roots.vectors else
                                   "__int_handler" if _defines_label(
                                       code, mac_files, "__int_handler")
                                   else None)
                    nmi_handler = (label_of(roots.vectors[0x66])
                                   if 0x66 in roots.vectors else
                                   "__nmi_handler" if _defines_label(
                                       code, mac_files, "__nmi_handler")
                                   else None)
                    if (0x38 in roots.vectors and _defines_label(
                            code, mac_files, "__int_handler")) or (
                            0x66 in roots.vectors and _defines_label(
                                code, mac_files, "__nmi_handler")):
                        raise CodegenError(
                            "#pragma uc80 vector and an assembly "
                            "__int_handler or __nmi_handler both set the "
                            "same vector")
                    for _addr, _name in sorted(roots.vectors.items()):
                        if _name in roots.isrs:
                            continue
                        print(f"uc80: warning: #pragma uc80 vector {_addr:02X}h "
                              f"{_name}: a C function ends with RET, not "
                              f"RETI, and saves no registers; use it only "
                              f"for a handler that never returns",
                              file=sys.stderr)
                    start = None
                    if not (gen.ctx.globals.get("main") is not None
                            or _defines_label(code, mac_files, "_main")):
                        if not args.entry:
                            raise CodegenError(
                                "--target=rom: the program has no main() "
                                "and no --entry; name the function that "
                                "the start-up code calls with --entry NAME")
                        start = label_of(args.entry[0])
                    startup_lines = rom_module.startup_text(
                        startup_path, int_handler, nmi_handler,
                        args.stack_top, start,
                        _defines_label(code, mac_files, "__watchdog_kick"),
                        rst={a: label_of(n) for a, (n, _k)
                             in roots.rst.items()},
                        im2=({s: label_of(n) for s, n in roots.im2.items()},
                             roots.im2_table or IM2_DEFAULT_TABLE)
                        if roots.im2_on else None)
                    startup_content = ""
                for line in startup_content.splitlines():
                    stripped = line.strip().upper()
                    # Skip directives that are already in main output
                    if stripped in {'.Z80', 'CSEG', 'DSEG'}:
                        continue
                    # Skip END directive
                    if stripped.startswith('END'):
                        continue
                    # Skip EXTRN _main since we define it
                    if 'EXTRN' in stripped and '_MAIN' in stripped:
                        continue
                    startup_lines.append(line)

                # Insert startup code after header but before first function
                lines = code.splitlines()
                insert_idx = 0
                for i, line in enumerate(lines):
                    stripped = line.strip().upper()
                    # Find first PUBLIC or actual code (after header comments)
                    if stripped.startswith('PUBLIC') or (stripped and not stripped.startswith(';') and not stripped.startswith('.')):
                        insert_idx = i
                        break

                # Insert startup code.  The ROM start-up code opens with
                # the vectors (CSEG with ORG), so it must come first.
                after = ['']
                lines = lines[:insert_idx] + ['\n; Embedded startup code (crt0)'] + startup_lines + after + lines[insert_idx:]
                code = '\n'.join(lines)

                if args.verbose:
                    print(f"    Embedded from {startup_path}")
            else:
                if args.verbose:
                    print(f"    Warning: startup file not found: {startup_path}")

        # Collect program's own PUBLIC labels before embedding libraries.
        # These become the entry points for assembly DCE in whole-program mode,
        # allowing unreachable library functions to be trimmed.
        program_public_labels = set()
        # Labels defined by hand-written assembly (appended .mac files).  These
        # are always assembly-DCE entry points: uc80 cannot see how user
        # assembly reaches its own labels (computed jumps, address-taken
        # labels), so it must not assume an unreferenced label is dead.  Any
        # future source of hand-written assembly should add its labels here.
        asm_entry_labels = set(keep_labels)
        for line in code.splitlines():
            match = re.match(r'\s*PUBLIC\s+(.+)', line, re.IGNORECASE)
            if match:
                for label in match.group(1).split(','):
                    program_public_labels.add(label.strip())

        # Embed runtime library functions if requested
        runtime_funcs_embedded = 0
        if embed_runtime:
            if args.verbose:
                print(f"  Embedding runtime library...")

            # Load runtime library
            if args.runtime_lib:
                runtime_lib = RuntimeLibrary()
                runtime_lib.load_file(Path(args.runtime_lib))
            else:
                runtime_lib = load_runtime_library()

            # Get required functions from codegen AND from EXTRN references
            needed = set(gen.ctx.runtime_used)

            # Scan for EXTRN references to libc functions
            for line in code.splitlines():
                match = re.match(r'\s*EXTRN\s+(.+)', line, re.IGNORECASE)
                if match:
                    labels = [l.strip() for l in match.group(1).split(',')]
                    # Only add if the runtime library has this function
                    for label in labels:
                        if label in runtime_lib.functions:
                            needed.add(label)

            funcs = runtime_lib.get_required_functions(needed) if needed else []

            # A runtime module that libc needs comes into the link as a whole,
            # so a function embedded here that it also defines would be in the
            # image twice (and ul80 would warn "%Mult. Def. Global").  Leave
            # those functions, and the data they use, to the library.
            lib_provided: set[str] = set()
            lib_externs = ''
            if funcs and not args.runtime_lib:
                lib_mods = load_lib_modules()
                if lib_mods:
                    all_funcs = funcs
                    # What the link will need is what asm DCE leaves, not
                    # what the compiler wrote: a dead function that calls
                    # printf must not predict the printf modules.
                    live_code = code
                    if whole_program and not args.no_asm_dce:
                        entry = set(program_public_labels) | asm_entry_labels
                        if gen.call_graph_analyzer:
                            entry |= {f"_{n}" for n in
                                      gen.call_graph_analyzer.address_taken}
                        try:
                            live_code = asm_eliminate_dead_code(
                                code, entry_points=entry,
                                keep_labels=keep_labels, export_common=rom)
                        except Exception:
                            live_code = code
                    funcs, lib_provided = leave_to_library(
                        runtime_lib, lib_mods, funcs, live_code,
                        program_public_labels, set(gen.ctx.runtime_used))
                    left = sorted({n for f in all_funcs if f not in funcs
                                   for n in f.publics})
                    if left:
                        lib_externs = '\n\textrn\t' + ','.join(left)
            runtime_funcs_embedded = len(funcs)

            # Even if no runtime functions are embedded, we may need data EXTRN
            # declarations for symbols like __tmp32, __sret_buf referenced by
            # generated code
            data_section = runtime_lib.get_data_section(
                funcs, gen.ctx.runtime_used,
                lib_provided) if (funcs or gen.ctx.runtime_used) else ''
            data_section = lib_externs + data_section

            if funcs or data_section:
                # The module's own END is dropped; a single END is appended
                # after everything below.
                lines = [l for l in code.splitlines() if not _is_end_directive(l)]

                runtime_code = []

                if funcs:
                    # Add CP/M BDOS constants if any I/O functions are used
                    io_funcs = {'_printf', '_putchar', '_getchar', '_puts', '_gets'}
                    needs_bdos = any(f.name in io_funcs for f in funcs)

                    runtime_code.append("\n\tcseg\n; Embedded runtime library functions")
                    if needs_bdos:
                        runtime_code.append("; CP/M BDOS constants")
                        runtime_code.append("BDOS\tequ\t5")
                        runtime_code.append("CONOUT\tequ\t2")
                        runtime_code.append("CONIN\tequ\t1")

                    # Add EXTRN declarations for external symbols needed by runtime
                    required_externs = runtime_lib.get_required_externs(funcs)
                    # Functions this code calls that were left to the library
                    kept_names = {n for f in funcs for n in f.publics}
                    required_externs |= {d for f in funcs
                                         for d in f.dependencies} - kept_names
                    if required_externs:
                        runtime_code.append("; External symbols needed by runtime")
                        for ext in sorted(required_externs):
                            runtime_code.append(f"\textrn\t{ext}")

                    for func in funcs:
                        # Add PUBLIC declaration for the function
                        if func.publics:
                            runtime_code.append(f"\tpublic\t{','.join(func.publics)}")
                        runtime_code.append(func.source)

                # Add data section if needed (pass runtime_used for generated code refs)
                # Note: get_data_section returns EXTRN lines (for CSEG) followed
                # by optional DSEG content - don't wrap in additional DSEG.
                # We must ensure EXTRN lines are in CSEG context since the
                # compiler output may end in DSEG (for shared auto storage).
                runtime_bss = ""
                if data_section:
                    if not funcs:
                        # No runtime functions were emitted, so we haven't
                        # switched to CSEG yet - do it now for the EXTRNs
                        runtime_code.append("\n\tcseg")
                    # The runtime's uninitialized data joins the program's
                    # COMMON block (below).  A second ``common //`` of its
                    # own would overlay the first at the same address: the
                    # accumulator __acc64 would share its bytes with the
                    # first global variable of the program.
                    bss_marker = "\n\tcommon\t//"
                    if (bss_marker in data_section and any(
                            re.match(r'\s*COMMON\b', ln, re.IGNORECASE)
                            for ln in lines)):
                        data_section, _, runtime_bss = (
                            data_section.partition(bss_marker))
                    runtime_code.append(data_section)

                # Splice the runtime in *before* the COMMON (BSS) block - see
                # _tail_insert_index.  Emitting it after the COMMON directive
                # puts it in BSS, where crt0 zeroes it before main() runs; the
                # `cseg` above cannot rescue it because um80 cannot leave a
                # COMMON block.  With assembly DCE on this was masked, because
                # DCE re-sorts the segments; under --no-asm-dce it miscompiled.
                insert_at = _tail_insert_index(lines)
                lines[insert_at:insert_at] = runtime_code
                if runtime_bss:
                    for i, ln in enumerate(lines):
                        if re.match(r'\s*COMMON\b', ln, re.IGNORECASE):
                            lines[i + 1:i + 1] = runtime_bss.split("\n")
                            break
                lines.append("\n\tend")

                code = '\n'.join(lines)

                # Remove EXTRN declarations for embedded functions
                embedded_names = set()
                for func in funcs:
                    embedded_names.update(func.publics)
                if embedded_names:
                    lines = code.splitlines()
                    filtered_lines = []
                    for line in lines:
                        match = re.match(r'\s*EXTRN\s+(.+)', line, re.IGNORECASE)
                        if match:
                            labels = [l.strip() for l in match.group(1).split(',')]
                            # Keep only labels that weren't embedded
                            remaining = [l for l in labels if l not in embedded_names]
                            if remaining:
                                filtered_lines.append(f"\textrn\t{','.join(remaining)}")
                            # else skip the line entirely
                        else:
                            filtered_lines.append(line)
                    code = '\n'.join(filtered_lines)

            if args.verbose:
                print(f"  Embedded {runtime_funcs_embedded} runtime function(s)")

        # Embed additional libraries if specified
        additional_funcs_embedded = 0
        if args.embed_lib and embed_runtime:
            # Find all EXTRN references in the current code
            extrn_refs = set()
            for line in code.splitlines():
                match = re.match(r'\s*EXTRN\s+(.+)', line, re.IGNORECASE)
                if match:
                    labels = [l.strip() for l in match.group(1).split(',')]
                    extrn_refs.update(labels)
                # Also check for CALL instructions to undefined functions
                match = re.search(r'\bCALL\s+(\w+)', line, re.IGNORECASE)
                if match:
                    extrn_refs.add(match.group(1))

            # Load each additional library and try to resolve references
            for lib_path in args.embed_lib:
                if args.verbose:
                    print(f"  Loading library {lib_path}...")

                lib = RuntimeLibrary()
                lib.load_file(Path(lib_path))

                # Find functions from this library that are referenced
                needed_from_lib = extrn_refs & set(lib.functions.keys())
                if needed_from_lib:
                    funcs = lib.get_required_functions(needed_from_lib)
                    additional_funcs_embedded += len(funcs)

                    if funcs:
                        # Insert functions before END directive
                        lines = code.splitlines()
                        end_idx = None
                        for i, line in enumerate(lines):
                            if line.strip().upper() == 'END':
                                end_idx = i
                                break

                        lib_code = [f"\n; Embedded from {lib_path}"]

                        # Add CP/M BDOS constants if any I/O functions are used
                        io_funcs = {'_printf', '_putchar', '_getchar', '_puts', '_gets'}
                        needs_bdos = any(f.name in io_funcs for f in funcs)
                        # Check if BDOS is already defined in the code
                        bdos_defined = any('BDOS' in line and 'EQU' in line.upper() for line in lines)
                        if needs_bdos and not bdos_defined:
                            lib_code.append("; CP/M BDOS constants")
                            lib_code.append("BDOS\tequ\t5")
                            lib_code.append("CONOUT\tequ\t2")
                            lib_code.append("CONIN\tequ\t1")

                        for func in funcs:
                            lib_code.append(func.source)

                        # Add data section if needed
                        data_section = lib.get_data_section(funcs)
                        if data_section:
                            lib_code.append("\n\tdseg")
                            lib_code.append(data_section)

                        if end_idx is not None:
                            lines = lines[:end_idx] + lib_code + ["\n\tend"]
                        else:
                            lines.extend(lib_code)
                            lines.append("\n\tend")

                        code = '\n'.join(lines)

                        # Remove EXTRN declarations for embedded functions
                        embedded_names = set()
                        for func in funcs:
                            embedded_names.add(func.name)
                            if hasattr(func, 'publics'):
                                embedded_names.update(func.publics)
                        if embedded_names:
                            lines = code.splitlines()
                            filtered_lines = []
                            for line in lines:
                                match = re.match(r'\s*EXTRN\s+(.+)', line, re.IGNORECASE)
                                if match:
                                    labels = [l.strip() for l in match.group(1).split(',')]
                                    # Keep only labels that weren't embedded
                                    remaining = [l for l in labels if l not in embedded_names]
                                    if remaining:
                                        filtered_lines.append(f"\textrn\t{','.join(remaining)}")
                                    # else skip the line entirely
                                else:
                                    filtered_lines.append(line)
                            code = '\n'.join(filtered_lines)

                        # Also remove from extrn_refs set for tracking
                        for name in embedded_names:
                            extrn_refs.discard(name)

                    if args.verbose:
                        print(f"    Embedded {len(funcs)} function(s) from {Path(lib_path).name}")

            if args.verbose and additional_funcs_embedded > 0:
                print(f"  Total additional functions embedded: {additional_funcs_embedded}")

        # Append any .mac files from input
        if mac_files:
            # The module's own END is dropped (the .mac's END goes with it, in
            # _filter_hand_written_asm); a single END is appended below.
            code_lines = [l for l in code.splitlines() if not _is_end_directive(l)]

            mac_block = []
            for mac_content in mac_files:
                filtered, publics, defined_labels = _filter_hand_written_asm(mac_content)
                program_public_labels.update(publics)
                asm_entry_labels.update(defined_labels)
                # Start in CSEG: the splice point below is not guaranteed to be
                # in any particular segment.
                #
                # Fence it exactly as inline asm() is fenced.  A .mac file is
                # hand-written assembly and must reach the assembler byte for
                # byte, but only asm() was bracketed, so the peephole ran over
                # an appended file and rewrote it: `LD A,(HL)` followed by
                # `LD C,A` fused to `LD C,(HL)` with A still live afterwards,
                # which is a silent wrong answer, and when a label sat between
                # the two fused instructions the label went with them and any
                # jump to it failed to assemble.
                mac_block.extend(['', '; Included assembly file',
                                  ASM_BEGIN_FILE_MARKER, '\tcseg'])
                mac_block.extend(filtered)
                mac_block.append(ASM_END_MARKER)

            # Splice the assembly in *before* the COMMON (BSS) block - see
            # _tail_insert_index.  Appending it at the end of the module puts
            # it inside COMMON, where crt0 zeroes it before main() runs.
            insert_at = _tail_insert_index(code_lines)
            code_lines[insert_at:insert_at] = mac_block

            code_lines.append('\n\tEND')
            code = '\n'.join(code_lines)

            if args.verbose:
                print(f"  Appended {len(mac_files)} assembly file(s)")

        if vec_names:
            code = _add_vector_labels(
                code, {label_of(n): v for n, v in vec_names.items()},
                ref=via_vec_crt0)
            if via_vec_crt0:
                keep_labels.add(VEC_REF_LABEL)

        # Interrupt functions: do they reach code that uses the alternate
        # registers, or a routine with work areas at fixed addresses?
        if roots.isrs:
            isr_labels = {n: label_of(n) for n in roots.isrs}
            cga = gen.call_graph_analyzer
            taken_labels = ({label_of(n) for n in cga.address_taken}
                            if cga is not None else set())
            reports, graph = isr_check.analyze(
                code, isr_labels, lib_dir() if lib_dir().is_dir() else None,
                taken_labels, with_graph=True)
            # a naked handler saves its own registers: no marker, no note
            for r in reports:
                if r.name in gen.naked:
                    r.alt_chain = None
            # --isr-save-workareas and the pragma: the prologue saves the
            # work areas of the runtime that the handler can reach
            wa_plan, wa_saved = {}, {}
            for r in reports:
                if r.name in roots.save_wa and r.name in gen.naked:
                    raise CodegenError(
                        f"#pragma uc80 isr_save_workareas {source_name(r.name)}: "
                        f"a naked function saves nothing; save the work "
                        f"areas in its assembly text")
                if not (args.isr_save_workareas or r.name in roots.save_wa) \
                        or r.name in gen.naked:
                    continue
                if not whole_program:
                    raise CodegenError(
                        f"interrupt function '{source_name(r.name)}': "
                        f"saving the work areas needs the whole program "
                        f"(the labels of the runtime work areas are private "
                        f"to the library, and a routine of another module "
                        f"is invisible); drop --no-whole-program, or save "
                        f"the areas in a naked handler")
                plan, _skipped, saved = isr_check.save_plan(graph, r)
                if plan:
                    wa_plan[r.name] = plan
                    wa_saved[r.name] = saved
                    nbytes, tstates = isr_check.wa_cost(plan)
                    print(f"uc80: note: interrupt function "
                          f"'{source_name(r.name)}' saves and restores the "
                          f"work areas of "
                          f"{', '.join(sorted(saved))} "
                          f"({sum(n for _b, _l, n in plan)} bytes of RAM, "
                          f"{nbytes} bytes of code, {tstates} T-states for "
                          f"each interrupt)", file=sys.stderr)
            code = isr_check.resolve_alt_markers(
                code, {r.name for r in reports if r.alt_chain}, wa_plan)
            for msg in isr_check.format_notes(reports):
                print(f"uc80: note: {msg}", file=sys.stderr)
            for msg in isr_check.format_reports(reports, wa_saved):
                print(f"uc80: warning: {msg}", file=sys.stderr)

        # C code linked next to nullsound (docs/rom_interop.md): its NMI
        # handler owns the alternate registers, so no function that the
        # nullsound code calls may reach code that uses EXX or EX AF,AF'.
        # The check runs with --with-nullsound, and for --entry functions
        # in the ngdevkit style (the build of docs/rom_interop.md).
        ns_style = (getattr(args, "asm_syntax", None) == "sdas"
                    and args.sdas_style == "ngdevkit" and args.entry)
        if args.with_nullsound or ns_style:
            ns_roots = {f"entry function '{n}'": label_of(n)
                        for n in args.entry}
            for n in roots.isrs:
                ns_roots[f"interrupt function '{source_name(n)}'"] = \
                    label_of(n)
            if gen.ctx.globals.get("main") is not None:
                ns_roots["'main'"] = label_of("main")
            cga = gen.call_graph_analyzer
            ns_taken = ({label_of(n) for n in cga.address_taken}
                        if cga is not None else set())
            ns_reports = isr_check.analyze(
                code, {k: v for k, v in ns_roots.items()},
                lib_dir() if lib_dir().is_dir() else None, ns_taken)
            seen_users = set()
            for rep in ns_reports:
                if rep.alt_chain:
                    print(f"uc80: warning: {rep.name} "
                          f"reaches code that uses the alternate registers "
                          f"({' -> '.join(rep.alt_chain)}); nullsound's NMI "
                          f"handler keeps its state there",
                          file=sys.stderr)
                for lab, chain in sorted(rep.alt_users.items()):
                    user = chain[-1]
                    if user in seen_users:
                        continue
                    seen_users.add(user)
                    kind = ("runtime helper" if user.startswith("__")
                            else "function")
                    print(f"uc80: warning: {kind} '{user}' uses EXX or "
                          f"EX AF,AF' (the alternate registers); the NMI "
                          f"handler of nullsound uses them at any time, so "
                          f"the code can corrupt the state of nullsound "
                          f"(reached from {rep.name}: "
                          f"{' -> '.join(chain)})", file=sys.stderr)

        # Assembly-level dead code elimination (after runtime embedding, before peephole)
        if not args.no_asm_dce and (embed_runtime or mac_files):
            if args.verbose:
                print(f"  Assembly dead code elimination...")

            lines_before = len(code.splitlines())
            if whole_program:
                # In whole-program mode, only the program's own PUBLIC labels
                # are entry points. Library functions are kept only if reachable.
                # Also include address-taken static functions (they aren't PUBLIC
                # but their addresses are used, so DCE must not eliminate them)
                # and every label defined by hand-written assembly.
                asm_entry_points = set(program_public_labels) | asm_entry_labels
                if gen.call_graph_analyzer:
                    for func_name in gen.call_graph_analyzer.address_taken:
                        asm_entry_points.add(f"_{func_name}")
                code = asm_eliminate_dead_code(code, entry_points=asm_entry_points,
                                               keep_labels=keep_labels,
                                               export_common=rom)
            else:
                # extra_entry_points, not entry_points: passing entry_points
                # would switch asm_dce into whole-program mode and drop
                # unreferenced PUBLIC data.
                code = asm_eliminate_dead_code(code, extra_entry_points=asm_entry_labels,
                                               keep_labels=keep_labels,
                                               export_common=rom)
            lines_after = len(code.splitlines())

            if args.verbose and lines_before != lines_after:
                print(f"    Removed {lines_before - lines_after} unreachable lines")

        # Peephole optimization (enabled by default)
        if not args.no_optimize:
            if args.verbose:
                print(f"  Peephole optimization...")

            peephole = _Peephole(PeepholeOptimizer())
            # Not peephole.optimize(code): inline asm is an optimization
            # barrier and its bytes are never rewritten.
            # Volatile accesses are fenced with labels the matcher cannot
            # see across (volatile_fence.py); the labels go again after.
            code = _optimize_outside_asm(
                peephole, volatile_fence.fence_regions(code))

            if args.verbose:
                for pattern, count in peephole.stats.items():
                    if count > 0:
                        print(f"    {pattern}: {count} applied")
                print(f"  Optimized to {len(code.splitlines())} lines of assembly")

        # The register-locals wraps that protect nothing (the peephole
        # pass has run: the callees are final).
        code = reglocals.prune(code)
        code = iyptr.prune_iy(code)

        # The volatile markers and fence labels are for the passes above only.
        code = volatile_fence.strip(code)
        if BARRIER_BEGIN in code:
            code = "\n".join(ln for ln in code.split("\n")
                             if ln.strip() not in (BARRIER_BEGIN, BARRIER_END))

        # Drop EXTRNs nothing uses: ul80 links the library module for each
        # one.  Done last, so appended .mac files count as uses and names
        # whose last use asm DCE or the peephole pass deleted go too.
        code = drop_unreferenced_externs(code)

        # Write output
        rom_end = _parse_address(
            "0x" + args.rom_size.lower().removeprefix("0x").rstrip("h"))
        if rom_end is None or not 0 < rom_end <= 0x10000:
            print(f"uc80: error: --rom-size {args.rom_size}: expected a "
                  f"size in hex, 1 to 10000h (the Z80 has 64 kilobytes of "
                  f"address space)", file=sys.stderr)
            return 1
        sdas_origin = None
        if args.asm_syntax == "sdas":
            try:
                code = sdas.translate(
                    code, style=sdas.area_names(args.sdas_style,
                                                args.sdas_areas),
                    module=module_name, filename=str(input_paths[0].name),
                    flat_data=not rom)
            except sdas.SdasError as e:
                # keep the MACRO-80 text: the line number is a line of it
                keep = output_path.with_suffix(".mac")
                keep.write_text(code)
                print(f"uc80: error: --asm-syntax=sdas: {e}\n"
                      f"uc80: the MACRO-80 text is in {keep}", file=sys.stderr)
                return 1
            sdas_origin = sdas.code_origin_of(code)
            if rom and sdas_origin is not None and sdas_origin >= rom_end:
                print(f"uc80: error: ROM overflow: the program code starts "
                      f"at {sdas_origin:04X}h" + (
                          f", after the IM 2 table at "
                          f"{roots.im2_table or IM2_DEFAULT_TABLE:04X}h"
                          if roots.im2_on else "")
                      + f", but the ROM is {rom_end:04X}h bytes and ends "
                      f"at {rom_end:04X}h; put the table lower, or give "
                      f"--rom-size", file=sys.stderr)
                return 1
            if args.sdas_style == "ngdevkit" and rom:
                nm = sdas.area_names(args.sdas_style, args.sdas_areas)
                cur, init_data = None, False
                for ln in code.splitlines():
                    f = ln.split()
                    if len(f) >= 2 and f[0] == ".area":
                        cur = f[1]
                    elif cur == nm.rom_init and f and f[0] in (
                            ".db", ".dw", ".ds"):
                        init_data = True
                if init_data:
                    print("uc80: warning: initialised global data in the "
                          "ngdevkit style: nothing next to nullsound copies "
                          f"the {nm.rom_init} image to {nm.ram_init}; the "
                          "variables start with undefined values (set them "
                          "in a command, or see docs/rom_interop.md)",
                          file=sys.stderr)
        output_path.write_text(code)

        if args.verbose and args.asm_syntax == "sdas":
            print(f"  Wrote {output_path}")
            print(sdas.build_help(output_path.name, args.sdas_style,
                                  args.sdas_areas, sdas_origin, rom))
        elif args.verbose:
            print(f"  Wrote {output_path}")
            if rom:
                stem = output_path.with_suffix("").name
                print("  ROM link (adjust -d, --rom-size, --ram-size):")
                print("    um80 " + output_path.name)
                print("    " + rom_module.link_command(stem, rom_end))

        return 0

    except PreprocessorError as e:
        print(f"uc80: {e}", file=sys.stderr)
        return 1

    except (UnsupportedConstructError, sdcc_compat.SdccCompatError) as e:
        print(f"uc80: error: {e}", file=sys.stderr)
        return 1

    except CodegenError as e:
        # A fault in the user's source, not in uc80 — no traceback and
        # no "internal error:" prefix.
        print(f"uc80: error: {e}", file=sys.stderr)
        return 1

    except Exception as e:
        print(f"uc80: internal error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
