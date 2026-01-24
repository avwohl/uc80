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
            print(f"  Generated {len(code.splitlines())} lines of assembly")

        # Embed runtime library functions if requested
        runtime_funcs_embedded = 0
        if embed_runtime and gen.ctx.runtime_used:
            if args.verbose:
                print(f"  Embedding runtime library...")

            # Load runtime library
            if args.runtime_lib:
                runtime_lib = RuntimeLibrary()
                runtime_lib.load_file(Path(args.runtime_lib))
            else:
                runtime_lib = load_runtime_library()

            # Get required functions
            needed = gen.ctx.runtime_used
            funcs = runtime_lib.get_required_functions(needed)
            runtime_funcs_embedded = len(funcs)

            if funcs:
                # Insert runtime functions before END directive
                lines = code.splitlines()
                end_idx = None
                for i, line in enumerate(lines):
                    if line.strip().upper() == 'END':
                        end_idx = i
                        break

                runtime_code = ["\n; Embedded runtime library functions"]
                for func in funcs:
                    runtime_code.append(func.source)

                # Add data section if needed
                data_section = runtime_lib.get_data_section(funcs)
                if data_section:
                    runtime_code.append("\n\tDSEG")
                    runtime_code.append(data_section)

                if end_idx is not None:
                    lines = lines[:end_idx] + runtime_code + ["\n\tEND"]
                else:
                    lines.extend(runtime_code)
                    lines.append("\n\tEND")

                code = '\n'.join(lines)

            if args.verbose:
                print(f"  Embedded {runtime_funcs_embedded} runtime function(s)")

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
                code_lines.extend(['', '; Included assembly file'])
                code_lines.extend(filtered)

            code_lines.append('\n\tEND')
            code = '\n'.join(code_lines)

            if args.verbose:
                print(f"  Appended {len(mac_files)} assembly file(s)")

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
