#!/usr/bin/env python3
"""Run tests from the Fujitsu compiler test suite."""

import subprocess
import sys
import os
from pathlib import Path

UC80_DIR = Path(__file__).parent
LIB_DIR = UC80_DIR / "lib"
TEST_SUITE_DIR = Path("../external/compiler-test-suite/C")

CRT0 = LIB_DIR / "crt0.rel"
LIBC = LIB_DIR / "libc.rel"
RUNTIME = LIB_DIR / "runtime.rel"
CPMEMU = Path("../cpmemu/src/cpmemu")

def run_test(c_file: Path) -> tuple[str, str]:
    """Run a single test. Returns (status, message)."""
    mac_file = c_file.with_suffix(".mac")
    rel_file = c_file.with_suffix(".rel")
    com_file = c_file.with_suffix(".com")
    ref_file = c_file.with_suffix(".reference_output")

    # Compile
    result = subprocess.run(
        [sys.executable, "-m", "src.main", str(c_file), "-o", str(mac_file)],
        capture_output=True, text=True, cwd=UC80_DIR
    )
    if result.returncode != 0:
        return "COMPILE_FAIL", result.stderr.strip()

    # Assemble
    result = subprocess.run(
        ["um80", str(mac_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return "ASM_FAIL", result.stderr.strip()

    # Link
    result = subprocess.run(
        ["ul80", str(CRT0), str(rel_file), str(LIBC), str(RUNTIME), "-o", str(com_file)],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return "LINK_FAIL", result.stderr.strip()

    # Run
    result = subprocess.run(
        [str(CPMEMU), str(com_file)],
        capture_output=True, text=True, timeout=10
    )

    # Check output
    output_lines = result.stdout.strip().split('\n')
    # Remove cpmemu header and footer
    actual_output = []
    for line in output_lines:
        if line.startswith("CPU mode:") or line.startswith("Loaded ") or line.startswith("Program exit"):
            continue
        actual_output.append(line)
    actual = '\n'.join(actual_output).strip()

    if ref_file.exists():
        expected = ref_file.read_text().strip()
        # Remove "exit N" lines from expected
        expected_lines = [l for l in expected.split('\n') if not l.startswith('exit ')]
        expected = '\n'.join(expected_lines).strip()

        if actual == expected:
            return "PASS", ""
        else:
            return "OUTPUT_MISMATCH", f"Expected: {expected!r}, Got: {actual!r}"
    else:
        # No reference, just check it ran
        if "PASS" in actual or "OK" in actual:
            return "PASS", ""
        elif "NG" in actual or "FAIL" in actual:
            return "FAIL", actual
        else:
            return "UNKNOWN", actual

def main():
    if len(sys.argv) > 1:
        test_dirs = [Path(d) for d in sys.argv[1:]]
    else:
        # Get all test directories
        test_dirs = sorted(TEST_SUITE_DIR.iterdir())[:20]  # First 20 dirs

    results = {"PASS": 0, "COMPILE_FAIL": 0, "ASM_FAIL": 0, "LINK_FAIL": 0,
               "OUTPUT_MISMATCH": 0, "FAIL": 0, "UNKNOWN": 0, "TIMEOUT": 0}

    for test_dir in test_dirs:
        if not test_dir.is_dir():
            continue

        c_files = sorted(test_dir.glob("*.c"))
        for c_file in c_files:
            try:
                status, msg = run_test(c_file)
                results[status] += 1

                if status == "PASS":
                    print(f"[PASS] {c_file.name}")
                else:
                    print(f"[{status}] {c_file.name}: {msg[:80]}")
            except subprocess.TimeoutExpired:
                results["TIMEOUT"] += 1
                print(f"[TIMEOUT] {c_file.name}")
            except Exception as e:
                print(f"[ERROR] {c_file.name}: {e}")

    print("\n--- Summary ---")
    total = sum(results.values())
    for status, count in results.items():
        if count > 0:
            print(f"{status}: {count}")
    print(f"Total: {total}")

if __name__ == "__main__":
    main()
