#!/usr/bin/env python3
"""Run c-testsuite tests for uc80."""

import subprocess
import sys
from pathlib import Path
import argparse

UC80_DIR = Path(__file__).parent
LIB_DIR = UC80_DIR / "lib"
TEST_SUITE_DIR = Path("../external/c-testsuite/tests/single-exec")

CRT0 = LIB_DIR / "crt0.rel"
LIBC = LIB_DIR / "libc.rel"
RUNTIME = LIB_DIR / "runtime.rel"
CPMEMU = Path("../cpmemu/src/cpmemu")


def run_test(c_file: Path, verbose: bool = False) -> tuple[str, str]:
    """Run a single test. Returns (status, message)."""
    mac_file = Path("/tmp") / c_file.with_suffix(".mac").name
    rel_file = mac_file.with_suffix(".rel")
    com_file = mac_file.with_suffix(".com")
    expected_file = c_file.with_suffix(".c.expected")

    # Compile - use --no-whole-program to avoid ul80 linker bug with DSEG relocations
    result = subprocess.run(
        [sys.executable, "-m", "src.main", str(c_file), "-o", str(mac_file), "--no-whole-program"],
        capture_output=True, text=True, cwd=UC80_DIR
    )
    if result.returncode != 0:
        return "compile", result.stderr.strip()

    # Assemble
    result = subprocess.run(
        ["um80", str(mac_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return "asm", result.stderr.strip()

    # Link - include crt0.rel since we use --no-whole-program
    result = subprocess.run(
        ["ul80", str(CRT0), str(rel_file), str(LIBC), str(RUNTIME), "-o", str(com_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return "link", result.stderr.strip()

    # Run
    try:
        result = subprocess.run(
            [str(CPMEMU), str(com_file)],
            capture_output=True, text=True, timeout=5
        )
    except subprocess.TimeoutExpired:
        return "timeout", "Execution timed out"

    # Parse output - remove cpmemu header/footer
    output_lines = result.stdout.strip().split('\n')
    actual_lines = []
    for line in output_lines:
        if line.startswith("CPU mode:") or line.startswith("Loaded "):
            continue
        if line.startswith("Program exit"):
            continue
        actual_lines.append(line)
    actual = '\n'.join(actual_lines).strip()

    # Compare with expected
    if expected_file.exists():
        expected = expected_file.read_text().strip()
        if actual == expected:
            return "pass", ""
        else:
            return "output", f"Expected:\n{expected}\nGot:\n{actual}"
    else:
        # No expected file - check return code
        # cpmemu prints "Program exit via JMP 0" for exit(0)
        if "exit via JMP 0" in result.stdout:
            return "pass", ""
        return "unknown", f"No expected file, output: {actual}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tests", nargs="*", help="Specific test numbers (e.g., 00166)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--start", type=int, default=1, help="Start test number")
    parser.add_argument("--end", type=int, default=220, help="End test number")
    args = parser.parse_args()

    if args.tests:
        test_nums = args.tests
    else:
        test_nums = [f"{i:05d}" for i in range(args.start, args.end + 1)]

    results = {"pass": [], "compile": [], "asm": [], "link": [], "output": [], "timeout": [], "unknown": []}

    for num in test_nums:
        c_file = TEST_SUITE_DIR / f"{num}.c"
        if not c_file.exists():
            continue

        status, msg = run_test(c_file, args.verbose)
        results[status].append(num)

        if args.verbose or status != "pass":
            print(f"{num}: {status.upper()}")
            if args.verbose and msg:
                for line in msg.split('\n')[:5]:  # First 5 lines of message
                    print(f"  {line}")

    # Summary
    print("\n" + "=" * 50)
    total = sum(len(v) for v in results.values())
    print(f"Total: {total} tests")
    print(f"Pass: {len(results['pass'])}")
    print(f"Compile: {len(results['compile'])} - {results['compile']}")
    print(f"ASM: {len(results['asm'])} - {results['asm']}")
    print(f"Link: {len(results['link'])} - {results['link']}")
    print(f"Output: {len(results['output'])} - {results['output']}")
    print(f"Timeout: {len(results['timeout'])} - {results['timeout']}")
    print(f"Unknown: {len(results['unknown'])} - {results['unknown']}")


if __name__ == "__main__":
    main()
