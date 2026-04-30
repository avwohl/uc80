#!/usr/bin/env python3
"""Run c-testsuite tests for uc80.

Platform-specific test notes (Z80 16-bit):
- 00166: Uses int for values >32767. Test expects 32-bit int.
         With long variables and %ld format, values print correctly.
- 00168: Factorial overflow - 8! exceeds 16-bit int range.
- 00174, 00175, 00178, 00195: Float tests requiring %f format support.
- 00186: Printf width formatting (%02d) not implemented.
- 00187: File I/O - CP/M text mode differences (CR+LF, ^Z EOF).
- 00189: Fixed - was DSEG relocation bug in ul80 linker.
- 00200: 64-bit shift operations (long long).
- 00204: ARM64 calling convention test.
- 00205: Large long values exceed test assumptions.
- 00206: Pragma push_macro/pop_macro not implemented.
- 00212: Platform defines (__ILP32__, etc.) - we're ILP16.
- 00216: Range designators [1...5], anonymous unions, complex init.
- 00218: Bit-fields in enums (now passes).
- 00220: Wide characters (wchar_t, L"...").
- 00040: Timeout - 8-queens algorithm complexity.
"""

import subprocess
import sys
import shutil
from pathlib import Path
import argparse

UC80_DIR = Path(__file__).parent
LIB_DIR = UC80_DIR / "lib"
TEST_SUITE_DIR = Path("../external/c-testsuite/tests/single-exec")
PATCH_DIR = UC80_DIR / "tests" / "c-testsuite-patches"
Z80_DIR = UC80_DIR / "tests" / "c-testsuite-z80"

CRT0 = LIB_DIR / "crt0.rel"
# Use .lib archives (not monolithic .rel): the .rel concat bakes in a
# hardcoded __printf_format_table, which can't be overridden by the
# compiler-emitted table under --int=32 / --long=64.  With .lib the linker
# only pulls modules that satisfy unresolved symbols, so the user's table
# wins.  This matches the workflow documented in README.
LIBC = LIB_DIR / "libc.lib"
RUNTIME = LIB_DIR / "runtime.lib"
CPMEMU = Path("../cpmemu/src/cpmemu")

# Tests that need longer timeouts (in seconds)
# These are computationally expensive but correct.
# Budgets are set for the worst-case config: --int=32 makes each
# arithmetic op ~2x slower (DEHL instead of HL), and --long=64 doubles
# again on top.  At --int=16 the same tests finish in a fraction of these.
SLOW_TESTS = {
    "00040": 600,  # 8-queens algorithm - O(n!) complexity
    "00041": 120,  # Prime sieve to 5000 - many multiplications and modulos
    "00200": 120,  # 64-bit shift operations - many test cases
    "00216": 30,   # Lots of struct init + per-byte print loops -
                   # default 5s flakes on a busy host even though the
                   # test finishes in well under 30s on its own.
}

# Tests to skip with reason
SKIP_TESTS = {
}

# Maximum .com file size - .com includes code, data, and BSS.
# cpmemu truncates the load at TPA boundary but BSS beyond that
# is fine since programs initialize it at runtime.
# Set generously: reject only files where code segment alone is too large.
MAX_COM_SIZE = 128000


def apply_patch(c_file: Path, test_num: str) -> Path:
    """Apply platform-specific adaptation if available. Returns path to use."""
    # First check for pre-adapted Z80 version
    z80_file = Z80_DIR / f"{test_num}.c"
    if z80_file.exists():
        return z80_file

    # Then try patch file
    patch_file = PATCH_DIR / f"{test_num}.patch"
    if not patch_file.exists():
        return c_file

    # Create patched copy in /tmp
    patched_file = Path("/tmp") / f"{test_num}_patched.c"
    shutil.copy(c_file, patched_file)

    # Apply patch using patch -p0 with input from file
    result = subprocess.run(
        ["patch", "-p0", "-i", str(patch_file), str(patched_file)],
        capture_output=True, text=True,
        cwd=patched_file.parent
    )
    if result.returncode == 0:
        return patched_file
    else:
        # Patch failed, use original
        return c_file


def run_test(c_file: Path, verbose: bool = False, test_num: str = "",
             extra_cflags: list[str] | None = None,
             variant: str = "") -> tuple[str, str]:
    """Run a single test. Returns (status, message)."""
    # Apply platform-specific patch if available
    source_file = apply_patch(c_file, test_num) if test_num else c_file

    mac_file = Path("/tmp") / c_file.with_suffix(".mac").name
    rel_file = mac_file.with_suffix(".rel")
    com_file = mac_file.with_suffix(".com")

    # Look up the expected output file.  Prefer (in order):
    # 1. Most specific Z80 variant (e.g. 00178.int32.long64.c.expected)
    # 2. Less specific variant (00178.int32.c.expected) — sizeof results
    #    differ between --int=16 and --int=32, so each width has its own file
    # 3. Z80 default (00178.c.expected)
    # 4. Upstream (../external/c-testsuite/.../00178.c.expected)
    expected_file = None
    if test_num and variant:
        # Try progressively shorter variant prefixes: "int32.long64", "int32"
        parts = variant.split(".")
        for i in range(len(parts), 0, -1):
            v = Z80_DIR / f"{test_num}.{'.'.join(parts[:i])}.c.expected"
            if v.exists():
                expected_file = v
                break
    if expected_file is None and test_num:
        z = Z80_DIR / f"{test_num}.c.expected"
        if z.exists():
            expected_file = z
    if expected_file is None:
        expected_file = c_file.with_suffix(".c.expected")

    # Compile - use --no-whole-program to avoid ul80 linker bug with DSEG relocations
    cc_cmd = [sys.executable, "-m", "src.main", str(source_file),
              "-o", str(mac_file), "--no-whole-program"]
    if extra_cflags:
        cc_cmd.extend(extra_cflags)
    result = subprocess.run(
        cc_cmd, capture_output=True, text=True, cwd=UC80_DIR
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

    # Check .com file size - reject if obviously too large.
    # Note: .com files may exceed 64K when DSEG/BSS is appended but
    # cpmemu truncates the load at TPA boundary and programs still work
    # as long as code+initialized data fits within the loaded area.
    com_size = com_file.stat().st_size
    if com_size > MAX_COM_SIZE:
        return "skip", f"code size {com_size} bytes exceeds CP/M TPA limit"

    # Run with per-test timeout
    timeout = SLOW_TESTS.get(test_num, 5)
    try:
        result = subprocess.run(
            [str(CPMEMU), str(com_file)],
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return "timeout", f"Execution timed out after {timeout}s"

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
    parser.add_argument("--int", dest="int_bits", type=int, choices=[16, 32],
                        help="Compile with --int=<bits> (default: compiler default = 16)")
    parser.add_argument("--long", dest="long_bits", type=int, choices=[32, 64],
                        help="Compile with --long=<bits> (default: compiler default = 32)")
    args = parser.parse_args()

    extra_cflags = []
    variant_parts = []
    if args.int_bits:
        extra_cflags.append(f"--int={args.int_bits}")
        if args.int_bits != 16:
            variant_parts.append(f"int{args.int_bits}")
    if args.long_bits:
        extra_cflags.append(f"--long={args.long_bits}")
        if args.long_bits != 32:
            variant_parts.append(f"long{args.long_bits}")
    variant = ".".join(variant_parts)

    if args.tests:
        test_nums = args.tests
    else:
        test_nums = [f"{i:05d}" for i in range(args.start, args.end + 1)]

    results = {"pass": [], "compile": [], "asm": [], "link": [], "output": [], "timeout": [], "skip": [], "unknown": []}

    for num in test_nums:
        c_file = TEST_SUITE_DIR / f"{num}.c"
        if not c_file.exists():
            continue

        # Check for skipped tests
        if num in SKIP_TESTS:
            status, msg = "skip", SKIP_TESTS[num]
        else:
            status, msg = run_test(c_file, args.verbose, num, extra_cflags,
                                   variant=variant)
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
    print(f"Skip: {len(results['skip'])} - {results['skip']}")
    print(f"Unknown: {len(results['unknown'])} - {results['unknown']}")


if __name__ == "__main__":
    main()
