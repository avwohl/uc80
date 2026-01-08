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

        # Write output
        output_path.write_text(code)

        if args.verbose:
            print(f"  Wrote {output_path}")

        return 0

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
