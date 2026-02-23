#!/usr/bin/env python3
"""Run GCC torture tests (execute/) for uc80.

Tests use abort() on failure, exit(0) on success. No stdout comparison needed -
we check whether abort() was called (prints "Aborted" to stdout) or the program
exited cleanly.

For tests that do file I/O, a cpmemu .cfg may be needed for correct ^Z EOF and
CR/LF line ending handling. See cpmemu docs for eol_convert and default_mode.
Do not exclude/skip any tests without a human checking first.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
import argparse

UC80_DIR = Path(__file__).parent
LIB_DIR = UC80_DIR / "lib"
TORTURE_DIR = Path("../external/llvm-test-suite/SingleSource/Regression/C/gcc-c-torture/execute")

CRT0 = LIB_DIR / "crt0.rel"
LIBC = LIB_DIR / "libc.lib"
RUNTIME = LIB_DIR / "runtime.lib"
CPMEMU = Path("../cpmemu/src/cpmemu")

MAX_COM_SIZE = 128000
DEFAULT_TIMEOUT = 10


def find_tests(patterns=None, limit=None):
    """Find test .c files in the execute directory."""
    tests = []
    if patterns:
        for pat in patterns:
            for f in sorted(TORTURE_DIR.glob(pat)):
                if f.suffix == '.c' and f not in tests:
                    tests.append(f)
    else:
        tests = sorted(TORTURE_DIR.glob("*.c"))

    if limit:
        tests = tests[:limit]
    return tests


def run_test(c_file: Path, verbose: bool = False, timeout: int = DEFAULT_TIMEOUT) -> tuple:
    """Run a single test. Returns (status, message).

    Status values:
      pass     - exit(0) without abort
      abort    - test called abort() (assertion failed)
      compile  - uc80 compiler failed
      asm      - um80 assembler failed
      link     - ul80 linker failed
      skip     - binary too large for CP/M
      timeout  - execution timed out
      crash    - unexpected runtime failure
    """
    mac_file = Path("/tmp") / c_file.with_suffix(".mac").name
    rel_file = mac_file.with_suffix(".rel")
    com_file = mac_file.with_suffix(".com")

    # Compile
    result = subprocess.run(
        [sys.executable, "-m", "src.main", str(c_file), "-o", str(mac_file), "--no-whole-program"],
        capture_output=True, text=True, cwd=UC80_DIR
    )
    if result.returncode != 0:
        return "compile", result.stderr.strip()[:200]

    # Assemble
    result = subprocess.run(
        ["um80", str(mac_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return "asm", result.stderr.strip()[:200]

    # Link
    result = subprocess.run(
        ["ul80", str(CRT0), str(rel_file), str(LIBC), str(RUNTIME), "-o", str(com_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return "link", result.stderr.strip()[:200]

    # Check size
    com_size = com_file.stat().st_size
    if com_size > MAX_COM_SIZE:
        return "skip", f"code size {com_size} bytes too large"

    # Run
    try:
        result = subprocess.run(
            [str(CPMEMU), str(com_file)],
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"timed out after {timeout}s"

    # Parse output
    stdout = result.stdout
    stderr = result.stderr

    # abort() prints "Aborted" to stdout before JP 0
    if "Aborted" in stdout:
        return "abort", "test called abort()"

    # Clean exit(0) -> cpmemu prints "Program exit via JMP 0" to stderr
    if "exit via JMP 0" in stderr:
        return "pass", ""

    # Something else happened
    return "crash", f"stdout: {stdout[:100]}, stderr: {stderr[:100]}"


def main():
    parser = argparse.ArgumentParser(description="Run GCC torture tests for uc80")
    parser.add_argument("patterns", nargs="*", help="Glob patterns for test files (e.g., 2000*.c)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-n", "--limit", type=int, help="Max number of tests to run")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-test timeout in seconds")
    parser.add_argument("--summary-only", action="store_true", help="Only show summary")
    args = parser.parse_args()

    tests = find_tests(args.patterns or None, args.limit)
    print(f"Found {len(tests)} tests")

    results = {"pass": 0, "abort": 0, "compile": 0, "asm": 0, "link": 0,
               "skip": 0, "timeout": 0, "crash": 0}

    for i, c_file in enumerate(tests):
        test_id = c_file.stem
        status, msg = run_test(c_file, args.verbose, args.timeout)
        results[status] += 1

        if not args.summary_only:
            if args.verbose or status not in ("pass", "compile"):
                print(f"  {test_id}: {status.upper()}")
                if args.verbose and msg:
                    for line in msg.split('\n')[:3]:
                        print(f"    {line}")

        if (i + 1) % 100 == 0:
            print(f"  ... {i+1}/{len(tests)} done, {results['pass']} pass so far")

    # Summary
    total = sum(results.values())
    print(f"\n{'=' * 50}")
    print(f"Total:   {total} tests")
    print(f"Pass:    {results['pass']}")
    print(f"Abort:   {results['abort']} (test assertion failed)")
    print(f"Compile: {results['compile']}")
    print(f"ASM:     {results['asm']}")
    print(f"Link:    {results['link']}")
    print(f"Skip:    {results['skip']}")
    print(f"Timeout: {results['timeout']}")
    print(f"Crash:   {results['crash']}")


if __name__ == "__main__":
    main()
