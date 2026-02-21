#!/usr/bin/env python3
"""Run Fujitsu compiler test suite C tests for uc80.

The Fujitsu suite has ~30K single-source C tests with .reference_output files.
Tests use #ifdef to handle both 32-bit and 64-bit long. We attempt all tests
and report results - do not exclude/skip any without a human checking first.
"""

import subprocess
import sys
import tempfile
from pathlib import Path
import argparse

UC80_DIR = Path(__file__).parent
LIB_DIR = UC80_DIR / "lib"
FUJITSU_DIR = Path("../external/compiler-test-suite/C")

CRT0 = LIB_DIR / "crt0.rel"
LIBC = LIB_DIR / "libc.rel"
RUNTIME = LIB_DIR / "runtime.rel"
CPMEMU = Path("../cpmemu/src/cpmemu")

MAX_COM_SIZE = 128000
DEFAULT_TIMEOUT = 5


def find_tests(dirs=None, limit=None):
    """Find single-source test files with reference output."""
    tests = []
    if dirs:
        test_dirs = [FUJITSU_DIR / d for d in dirs]
    else:
        test_dirs = sorted(FUJITSU_DIR.iterdir())

    for d in test_dirs:
        if not d.is_dir():
            continue
        for c_file in sorted(d.glob("*.c")):
            ref_file = c_file.with_suffix(".reference_output")
            if ref_file.exists():
                tests.append((c_file, ref_file))
            if limit and len(tests) >= limit:
                return tests
    return tests


def run_test(c_file: Path, ref_file: Path, verbose: bool = False) -> tuple[str, str]:
    """Run a single test. Returns (status, message)."""
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
            [str(CPMEMU), "--z80", str(com_file)],
            capture_output=True, text=True, timeout=DEFAULT_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"timed out after {DEFAULT_TIMEOUT}s"

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

    # Parse expected - strip "exit N" suffix (may or may not be on its own line)
    expected_text = ref_file.read_text(errors='replace').strip()
    import re
    # Remove trailing "exit N" whether it's on its own line or appended
    expected_text = re.sub(r'exit \d+\s*$', '', expected_text)
    expected_lines = [l for l in expected_text.split('\n') if not re.match(r'^exit \d+$', l.strip())]
    expected = '\n'.join(expected_lines).strip()

    if actual == expected:
        return "pass", ""
    else:
        return "output", f"Expected:\n{expected[:100]}\nGot:\n{actual[:100]}"


def main():
    parser = argparse.ArgumentParser(description="Run Fujitsu C tests for uc80")
    parser.add_argument("dirs", nargs="*", help="Specific test directories (e.g., 0000 0001)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-n", "--limit", type=int, help="Max number of tests to run")
    parser.add_argument("--summary-only", action="store_true", help="Only show summary")
    args = parser.parse_args()

    tests = find_tests(args.dirs or None, args.limit)
    print(f"Found {len(tests)} tests")

    results = {"pass": 0, "compile": 0, "asm": 0, "link": 0, "output": 0, "timeout": 0, "skip": 0}

    for i, (c_file, ref_file) in enumerate(tests):
        test_id = f"{c_file.parent.name}/{c_file.stem}"
        status, msg = run_test(c_file, ref_file, args.verbose)
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
    print(f"Total: {total} tests")
    print(f"Pass:    {results['pass']}")
    print(f"Compile: {results['compile']}")
    print(f"ASM:     {results['asm']}")
    print(f"Link:    {results['link']}")
    print(f"Output:  {results['output']}")
    print(f"Timeout: {results['timeout']}")
    print(f"Skip:    {results['skip']}")


if __name__ == "__main__":
    main()
