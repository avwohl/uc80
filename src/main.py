#!/usr/bin/env python3
"""uc80 - ANSI C compiler for Z80.

Compiles C source to Z80 assembly compatible with um80 assembler.
"""

import argparse
import sys
from pathlib import Path

from .lexer import Lexer, LexerError
from .parser import Parser, ParseError
from .codegen import generate
from .preprocessor import Preprocessor, PreprocessorError

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
        help="Input C source file"
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

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"uc80: error: {args.input}: No such file", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_suffix(".mac")

    # Read source
    try:
        source = input_path.read_text()
    except Exception as e:
        print(f"uc80: error: Cannot read {args.input}: {e}", file=sys.stderr)
        return 1

    # Compile
    try:
        if args.verbose:
            print(f"Compiling {input_path}...")

        # Set up include paths
        include_paths = list(args.include)
        # Add lib/include as default include path
        lib_include = Path(__file__).parent.parent / "lib" / "include"
        if lib_include.exists():
            include_paths.append(str(lib_include))

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

            if args.verbose:
                print(f"  Preprocessed to {len(source.splitlines())} lines")

            # If -E, just output preprocessed source
            if args.preprocess_only:
                print(source)
                return 0

        # Lexical analysis
        lexer = Lexer(source, str(input_path))
        tokens = list(lexer.tokenize())

        if args.verbose:
            print(f"  Lexed {len(tokens)} tokens")

        # Parsing
        p = Parser(tokens)
        ast = p.parse()

        if args.verbose:
            print(f"  Parsed {len(ast.declarations)} declarations")

        # Code generation
        code = generate(ast, input_path.stem)

        if args.verbose:
            print(f"  Generated {len(code.splitlines())} lines of assembly")

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
