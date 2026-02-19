#!/usr/bin/env python3
"""uc80 - ANSI C compiler for Z80.

Compiles C source to Z80 assembly compatible with um80 assembler.
"""

import argparse
import sys
from pathlib import Path

from .lexer import Lexer, LexerError
from .parser import Parser, ParseError
from .codegen import generate, CodeGenerator
from . import ast as ast_module
from .preprocessor import Preprocessor, PreprocessorError
from .runtime import RuntimeLibrary, load_runtime_library
from .asm_dce import eliminate_dead_code as asm_eliminate_dead_code
from .ast_optimizer import ASTOptimizer

# Import peephole optimizer from upeepz80 library
from upeepz80 import PeepholeOptimizer


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
        "-O3", "--aggressive-optimize",
        action="store_true",
        help="Enable aggressive AST optimizations (CSE, copy propagation, dead stores, loop opts)"
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

    args = parser.parse_args()

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
    lib_include = Path(__file__).parent.parent / "lib" / "include"
    if lib_include.exists():
        include_paths.append(str(lib_include))

    # Compile
    try:
        asts = []
        mac_files = []  # Assembly files to append
        total_tokens = 0
        total_preprocessed_lines = 0

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

            # Read source
            try:
                source = input_path.read_text()
            except Exception as e:
                print(f"uc80: error: Cannot read {input_path}: {e}", file=sys.stderr)
                return 1

            # Preprocessing
            if not args.no_preprocess:
                if args.verbose:
                    print(f"  Preprocessing...")

                pp = Preprocessor(include_paths)

                # Add command-line defines
                for define in args.define:
                    if '=' in define:
                        name, value = define.split('=', 1)
                        pp.macros[name] = pp.macros.get(name) or type(pp.macros["__UC80__"])(name, body=value)
                    else:
                        pp.macros[define] = type(pp.macros["__UC80__"])(define, body="1")

                source = pp.preprocess(source, str(input_path))
                total_preprocessed_lines += len(source.splitlines())

                if args.verbose:
                    print(f"  Preprocessed to {len(source.splitlines())} lines")

                # If -E, just output preprocessed source
                if args.preprocess_only:
                    print(source)
                    continue

            # Lexical analysis
            lexer = Lexer(source, str(input_path))
            tokens = list(lexer.tokenize())
            total_tokens += len(tokens)

            if args.verbose:
                print(f"  Lexed {len(tokens)} tokens")

            # Parsing
            p = Parser(tokens)
            ast = p.parse()
            asts.append(ast)

            if args.verbose:
                print(f"  Parsed {len(ast.declarations)} declarations")

        # If preprocess-only mode, we're done
        if args.preprocess_only:
            return 0

        # Merge ASTs into single TranslationUnit
        if len(asts) == 1:
            merged_ast = asts[0]
        else:
            merged_ast = ast_module.TranslationUnit(declarations=[])
            for unit in asts:
                merged_ast.declarations.extend(unit.declarations)
            if args.verbose:
                print(f"Merged {len(asts)} files into {len(merged_ast.declarations)} declarations")

        # AST-level expression optimization
        if not args.no_ast_optimize:
            opt_level = 3
            ast_opt = ASTOptimizer(opt_level)
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

        gen = CodeGenerator(module_name, enable_shared_storage, enable_dead_elimination,
                           enable_inlining, enable_const_propagation, whole_program,
                           embed_runtime=embed_runtime)
        code = gen.generate(merged_ast)

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
                startup_path = Path(__file__).parent.parent / "lib" / "crt0.mac"

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
        import re
        program_public_labels = set()
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

            if funcs:
                # Insert runtime functions before END directive
                lines = code.splitlines()
                end_idx = None
                for i, line in enumerate(lines):
                    if line.strip().upper() == 'END':
                        end_idx = i
                        break

                # Add CP/M BDOS constants if any I/O functions are used
                io_funcs = {'_printf', '_putchar', '_getchar', '_puts', '_gets'}
                needs_bdos = any(f.name in io_funcs for f in funcs)

                runtime_code = ["\n\tCSEG\n; Embedded runtime library functions"]
                if needs_bdos:
                    runtime_code.append("; CP/M BDOS constants")
                    runtime_code.append("BDOS\tEQU\t5")
                    runtime_code.append("CONOUT\tEQU\t2")
                    runtime_code.append("CONIN\tEQU\t1")

                # Add EXTRN declarations for external symbols needed by runtime
                required_externs = runtime_lib.get_required_externs(funcs)
                if required_externs:
                    runtime_code.append("; External symbols needed by runtime")
                    for ext in sorted(required_externs):
                        runtime_code.append(f"\tEXTRN\t{ext}")

                for func in funcs:
                    # Add PUBLIC declaration for the function
                    if func.publics:
                        runtime_code.append(f"\tPUBLIC\t{','.join(func.publics)}")
                    runtime_code.append(func.source)

                # Add data section if needed (pass runtime_used for generated code refs)
                data_section = runtime_lib.get_data_section(funcs, gen.ctx.runtime_used)
                if data_section:
                    runtime_code.append("\n\tDSEG")
                    runtime_code.append(data_section)

                if end_idx is not None:
                    lines = lines[:end_idx] + runtime_code + ["\n\tEND"]
                else:
                    lines.extend(runtime_code)
                    lines.append("\n\tEND")

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
                                filtered_lines.append(f"\tEXTRN\t{','.join(remaining)}")
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
                            lib_code.append("BDOS\tEQU\t5")
                            lib_code.append("CONOUT\tEQU\t2")
                            lib_code.append("CONIN\tEQU\t1")

                        for func in funcs:
                            lib_code.append(func.source)

                        # Add data section if needed
                        data_section = lib.get_data_section(funcs)
                        if data_section:
                            lib_code.append("\n\tDSEG")
                            lib_code.append(data_section)

                        if end_idx is not None:
                            lines = lines[:end_idx] + lib_code + ["\n\tEND"]
                        else:
                            lines.extend(lib_code)
                            lines.append("\n\tEND")

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
                                        filtered_lines.append(f"\tEXTRN\t{','.join(remaining)}")
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
            # Strip END directives from main code and mac files, add single END at end
            code_lines = code.splitlines()
            code_lines = [l for l in code_lines if l.strip().upper() != 'END']

            for mac_content in mac_files:
                mac_lines = mac_content.splitlines()
                # Skip header directives that are already in main output
                skip_headers = {'.Z80', 'CSEG', 'DSEG'}
                filtered = []
                for line in mac_lines:
                    stripped = line.strip().upper()
                    if stripped in skip_headers:
                        continue
                    if stripped == 'END':
                        continue
                    filtered.append(line)
                    # Also collect PUBLIC labels from appended .mac files
                    match = re.match(r'\s*PUBLIC\s+(.+)', line, re.IGNORECASE)
                    if match:
                        for label in match.group(1).split(','):
                            program_public_labels.add(label.strip())
                code_lines.extend(['', '; Included assembly file'])
                code_lines.extend(filtered)

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
                code = asm_eliminate_dead_code(code, entry_points=program_public_labels)
            else:
                code = asm_eliminate_dead_code(code)
            lines_after = len(code.splitlines())

            if args.verbose and lines_before != lines_after:
                print(f"    Removed {lines_before - lines_after} unreachable lines")

        # Peephole optimization (enabled by default)
        if not args.no_optimize:
            if args.verbose:
                print(f"  Peephole optimization...")

            peephole = PeepholeOptimizer()
            code = peephole.optimize(code)

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

    except LexerError as e:
        print(f"uc80: {e}", file=sys.stderr)
        return 1

    except ParseError as e:
        print(f"uc80: {e.location}: {e.message}", file=sys.stderr)
        return 1

    except Exception as e:
        print(f"uc80: internal error: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
