#!/usr/bin/env python3
"""uc80 - ANSI C compiler for Z80.

Compiles C source to Z80 assembly compatible with um80 assembler.
"""

import argparse
import re
import sys
from pathlib import Path

from uc_core.frontend import parse as _frontend_parse
from uc_core import ast as ast_module
from uc_core.preprocessor import Preprocessor, PreprocessorError, Macro
from uc_core.ast_optimizer import ASTOptimizer
from uc_core.type_config import TypeConfig, Z80_CPM

from .codegen import generate, CodeGenerator, CodegenError
from .runtime import RuntimeLibrary, load_runtime_library
from .asm_dce import eliminate_dead_code as asm_eliminate_dead_code
from .asm_dce import is_asm_begin, is_asm_end

# Import peephole optimizer from upeepz80 library
from upeepz80 import PeepholeOptimizer

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
        if re.match(r'\s*COMMON\b', line, re.IGNORECASE):
            # Keep the "; BSS - ..." banner attached to its directive.
            while i > 0 and lines[i - 1].strip().startswith(';'):
                i -= 1
            return i
    for i, line in enumerate(lines):
        if _is_end_directive(line):
            return i
    return len(lines)


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
    if not any(is_asm_begin(line) for line in lines):
        return peephole.optimize(code)
    out: list[str] = []
    buf: list[str] = []
    in_asm = False
    for line in lines:
        if not in_asm and is_asm_begin(line):
            out.append(peephole.optimize("\n".join(buf)))
            buf = []
            in_asm = True
            out.append(line)
            continue
        if in_asm:
            out.append(line)
            if is_asm_end(line):
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


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="uc80",
        description="C24 compiler for Z80"
    )
    parser.add_argument(
        "input",
        nargs='+',
        help="Input C source file(s) or .mac assembly file(s)"
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
        choices=["int", "long", "llong", "float", "all"],
        help="Scanf format support level; ADDITIVE, same closure rules as "
             "--printf (currently advisory: nothing consumes scanf features yet)"
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
        output_path = input_paths[0].with_suffix(".mac")

    # Set up include paths
    include_paths = list(args.include)
    # Add lib/include as default include path
    lib_include = Path(__file__).parent / "lib" / "include"
    if lib_include.exists():
        include_paths.append(str(lib_include))

    # Compile
    try:
        asts = []
        mac_files = []  # Assembly files to append
        printf_features: set[str] | None = None  # From #pragma printf
        scanf_features: set[str] | None = None   # From #pragma scanf

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
                pp = Preprocessor(include_paths, target_predefines=pp_predefines)

                # Add command-line defines
                for define in args.define:
                    if '=' in define:
                        name, value = define.split('=', 1)
                        pp.macros[name] = pp.macros.get(name) or Macro(name, body=value)
                    else:
                        pp.macros[define] = Macro(define, body="1")

                source = pp.preprocess(source, str(input_path))

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
            ast = _frontend_parse(source, str(input_path))
            asts.append(ast)

            if args.verbose:
                print(f"  Parsed {len(ast.items)} declarations")

        # If preprocess-only mode, we're done
        if args.preprocess_only:
            return 0

        # Merge ASTs into single TranslationUnit
        if len(asts) == 1:
            merged_ast = asts[0]
        else:
            merged_ast = ast_module.TranslationUnit(items=[])
            for unit in asts:
                merged_ast.items.extend(unit.items)
            if args.verbose:
                print(f"Merged {len(asts)} files into {len(merged_ast.items)} declarations")

        # AST-level expression optimization
        if not args.no_ast_optimize:
            opt_level = 3
            ast_opt = ASTOptimizer(opt_level, type_config=type_config)
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
        # ...and either way, close the set over its implied features.
        if printf_features is not None:
            printf_features = _close_format_features(printf_features)
        if scanf_features is not None:
            scanf_features = _close_format_features(scanf_features)

        gen = CodeGenerator(module_name, enable_shared_storage, enable_dead_elimination,
                           enable_inlining, enable_const_propagation, whole_program,
                           embed_runtime=embed_runtime,
                           printf_features=printf_features,
                           scanf_features=scanf_features,
                           type_config=type_config)
        code = gen.generate(merged_ast)

        for w in gen.warnings:
            print(f"uc80: warning: {w}", file=sys.stderr)

        if args.verbose:
            if gen.inlined_calls > 0:
                print(f"  Inlined {gen.inlined_calls} call(s)")
            if gen.constants_propagated > 0:
                print(f"  Propagated {gen.constants_propagated} constant(s)")
            if gen.dead_functions_removed > 0:
                print(f"  Eliminated {gen.dead_functions_removed} dead function(s)")
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
            else:
                # Default: lib/crt0.mac relative to package
                startup_path = Path(__file__).parent / "lib" / "crt0.mac"

            if startup_path.exists():
                startup_content = startup_path.read_text()

                # Parse and filter startup code
                startup_lines = []
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

                # Insert startup code
                lines = lines[:insert_idx] + ['\n; Embedded startup code (crt0)'] + startup_lines + [''] + lines[insert_idx:]
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
        asm_entry_labels = set()
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
            runtime_funcs_embedded = len(funcs)

            # Even if no runtime functions are embedded, we may need data EXTRN
            # declarations for symbols like __tmp32, __sret_buf referenced by
            # generated code
            data_section = runtime_lib.get_data_section(
                funcs, gen.ctx.runtime_used) if (funcs or gen.ctx.runtime_used) else ''

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
                if data_section:
                    if not funcs:
                        # No runtime functions were emitted, so we haven't
                        # switched to CSEG yet - do it now for the EXTRNs
                        runtime_code.append("\n\tcseg")
                    runtime_code.append(data_section)

                # Splice the runtime in *before* the COMMON (BSS) block - see
                # _tail_insert_index.  Emitting it after the COMMON directive
                # puts it in BSS, where crt0 zeroes it before main() runs; the
                # `cseg` above cannot rescue it because um80 cannot leave a
                # COMMON block.  With assembly DCE on this was masked, because
                # DCE re-sorts the segments; under --no-asm-dce it miscompiled.
                insert_at = _tail_insert_index(lines)
                lines[insert_at:insert_at] = runtime_code
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
                mac_block.extend(['', '; Included assembly file', '\tcseg'])
                mac_block.extend(filtered)

            # Splice the assembly in *before* the COMMON (BSS) block - see
            # _tail_insert_index.  Appending it at the end of the module puts
            # it inside COMMON, where crt0 zeroes it before main() runs.
            insert_at = _tail_insert_index(code_lines)
            code_lines[insert_at:insert_at] = mac_block

            code_lines.append('\n\tEND')
            code = '\n'.join(code_lines)

            if args.verbose:
                print(f"  Appended {len(mac_files)} assembly file(s)")

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
                code = asm_eliminate_dead_code(code, entry_points=asm_entry_points)
            else:
                # extra_entry_points, not entry_points: passing entry_points
                # would switch asm_dce into whole-program mode and drop
                # unreferenced PUBLIC data.
                code = asm_eliminate_dead_code(code, extra_entry_points=asm_entry_labels)
            lines_after = len(code.splitlines())

            if args.verbose and lines_before != lines_after:
                print(f"    Removed {lines_before - lines_after} unreachable lines")

        # Peephole optimization (enabled by default)
        if not args.no_optimize:
            if args.verbose:
                print(f"  Peephole optimization...")

            peephole = PeepholeOptimizer()
            # Not peephole.optimize(code): inline asm is an optimization
            # barrier and its bytes are never rewritten.
            code = _optimize_outside_asm(peephole, code)

            if args.verbose:
                for pattern, count in peephole.stats.items():
                    if count > 0:
                        print(f"    {pattern}: {count} applied")
                print(f"  Optimized to {len(code.splitlines())} lines of assembly")

        # Write output
        output_path.write_text(code)

        if args.verbose:
            print(f"  Wrote {output_path}")

        return 0

    except PreprocessorError as e:
        print(f"uc80: {e}", file=sys.stderr)
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
