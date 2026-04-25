#!/usr/bin/env python3
"""Run SDCC regression tests for uc80.

The SDCC test suite uses a test framework (testfwk.c/testfwk.h) with ASSERT()
macros. Some tests are templates with {placeholder} substitutions that generate
multiple test instances. This runner handles template expansion internally
(the original generate-cases.py requires Python 2's HTMLgen).

Test output format: "--- Summary: F/T/C: F failed of T tests in C cases."
Pass = F (failures) is 0.

For tests that do file I/O, cpmemu needs correct .cfg settings for ^Z CP/M
EOF and CR/LF line endings. See cpmemu docs for eol_convert and default_mode.
Do not exclude/skip any tests without a human checking first.
"""

import subprocess
import sys
import re
import itertools
from pathlib import Path
import argparse

UC80_DIR = Path(__file__).parent
LIB_DIR = UC80_DIR / "lib"
SDCC_DIR = Path("../external/sdcc-regression")
TESTS_DIR = SDCC_DIR / "tests"
FWK_DIR = SDCC_DIR / "fwk"

CRT0 = LIB_DIR / "crt0.rel"
# Use .lib archives (not monolithic .rel): see run_ctests.py for rationale.
LIBC = LIB_DIR / "libc.lib"
RUNTIME = LIB_DIR / "runtime.lib"
CPMEMU = Path("../cpmemu/src/cpmemu")

MAX_COM_SIZE = 128000
DEFAULT_TIMEOUT = 10

# Working directory for generated files
WORK_DIR = Path("/tmp/sdcc-tests")


def parse_template(c_file: Path):
    """Parse an SDCC test template, extracting substitution keys and test functions.

    Returns (replacements_dict, function_list, lines).
    replacements_dict: {key: [value1, value2, ...]}
    function_list: [funcname1, funcname2, ...]
    lines: all lines of the file
    """
    lines = c_file.read_text().splitlines(keepends=True)
    replacements = {}
    functions = []
    in_header = True

    for line in lines:
        stripped = line.strip()

        if in_header:
            if '*/' in stripped:
                in_header = False
                continue
            if ':' in stripped and not stripped.startswith('/*') and not stripped.startswith('*'):
                # Could be "key: val1, val2, val3" or a false positive
                # Only match within the /** ... */ comment block
                m = re.match(r'^[\s*]*(\w+)\s*:\s*(.*)', stripped)
                if m:
                    key = m.group(1).strip()
                    raw_values = m.group(2).strip()
                    values = [v.strip() for v in raw_values.split(',')]
                    # Filter empty trailing values but keep empty string as a valid value
                    # (e.g., "storage: static, " means ["static", ""])
                    while len(values) > 1 and values[-1] == '':
                        # Keep one empty string if it's intentional (trailing comma)
                        # "storage: static, " -> ["static", ""]
                        break
                    # Actually, trailing comma means empty is a valid variant
                    replacements[key] = values
        else:
            # Look for test function declarations
            m = re.match(r'^(?:\W*void\W+)?\W*(test\w*)\W*\(\W*void\W*\)', stripped)
            if m:
                functions.append(m.group(1))

    return replacements, functions, lines


def generate_framework_suffix(functions, test_name):
    """Generate the t_runSuite, t_numCases, and t_getSuiteName functions."""
    lines = []
    lines.append("\nvoid\nt_runSuite(void)\n{\n")
    for func in functions:
        lines.append(f'  t_prints("Running {func}\\n");\n')
        lines.append(f"  {func}();\n")
    lines.append("}\n")
    lines.append(f"\nconst int t_numCases = {len(functions)};\n")
    # Escape backslashes in path for C string
    escaped_name = test_name.replace('\\', '\\\\')
    lines.append(f'\nconst char *\nt_getSuiteName(void)\n{{\n  return "{escaped_name}";\n}}\n')
    return ''.join(lines)


def expand_template(text, replacements):
    """Replace {key} placeholders with values. Only replaces known keys."""
    for key, value in replacements.items():
        text = text.replace('{' + key + '}', value)
    return text


def generate_instances(c_file: Path, out_dir: Path):
    """Generate test instances from a template file.

    Returns list of (generated_c_file, test_name) tuples.
    """
    replacements, functions, lines = parse_template(c_file)

    if not functions:
        return []

    base_text = ''.join(lines)
    basename = c_file.stem
    instances = []

    if not replacements:
        # No template variables - single instance
        suffix = generate_framework_suffix(functions, basename)
        out_file = out_dir / f"{basename}.c"
        out_file.write_text(base_text + suffix)
        instances.append((out_file, basename))
    else:
        # Generate all permutations
        keys = list(replacements.keys())
        value_lists = [replacements[k] for k in keys]

        for combo in itertools.product(*value_lists):
            trans = dict(zip(keys, combo))
            # Build filename suffix from key-value pairs
            parts = []
            for k, v in zip(keys, combo):
                vname = v.strip() if v.strip() else 'none'
                vname = re.sub(r'\s+', '_', vname)
                # Remove SDCC-specific prefixes for cleaner names
                vname = vname.replace('__', '')
                parts.append(f"{k}_{vname}")
            instance_name = f"{basename}_{'_'.join(parts)}"

            expanded = expand_template(base_text, trans)
            suffix = generate_framework_suffix(functions, instance_name)
            out_file = out_dir / f"{instance_name}.c"
            out_file.write_text(expanded + suffix)
            instances.append((out_file, instance_name))

    return instances


def create_combined_test(test_c: Path, test_name: str, work_dir: Path) -> Path:
    """Create a single combined .c file with shim + framework + test.

    We combine everything into one compilation unit because uc80 doesn't
    generate PUBLIC declarations for global variables, so cross-module
    references to globals like t_numTests fail at link time.
    """
    combined = work_dir / f"{test_name}_combined.c"
    test_source = test_c.read_text()

    # Strip the #include <testfwk.h> from the test since we inline it
    test_source = test_source.replace('#include <testfwk.h>', '/* testfwk.h inlined below */')
    # Also handle quoted include
    test_source = test_source.replace('#include "testfwk.h"', '/* testfwk.h inlined below */')

    combined.write_text(f"""\
/* Combined test file for uc80: {test_name} */
#include <stdio.h>
#include <stdarg.h>

/* === testfwk.h (inlined) === */
/* Memory space qualifiers - all empty for uc80 */
#define __data
#define __idata
#define __pdata
#define __xdata
#define __code
#define __near
#define __far
#define __at(x)
#define __reentrant
#define _AUTOMEM
#define _STATMEM
#define REENTRANT
#define code
#define data
#define xdata
#define idata
#define pdata
#define near
#define far

int t_numTests = 0;
static int t_numFailures = 0;

void t_fail(const char *szMsg, const char *szCond, const char *szFile, int line);
void t_prints(const char *s);
void t_printn(int n);
void t_printf(const char *szFormat, ...);
const char *t_getSuiteName(void);
void t_runSuite(void);

#define ASSERT(_a)  (++t_numTests, (_a) ? (void)0 : t_fail("Assertion failed", #_a, __FILE__, __LINE__))
#define ASSERT_FAILED(_a)  (++t_numTests, (_a) ? 0 : (t_fail("Assertion failed", #_a, __FILE__, __LINE__), 1))
#define FAIL()      FAILM("Failure")
#define FAILM(_a)   t_fail(_a, #_a, __FILE__, __LINE__)
#define LOG(_a)     t_printf _a
#define UNUSED(_a)  if (_a) {{ }}

/* === shim === */
void _putchar(char c) {{ putchar(c); }}
void _initEmu(void) {{ }}
void _exitEmu(void) {{ }}

/* === testfwk.c (inlined) === */
void t_prints(const char *s) {{
    char c;
    while ('\\0' != (c = *s)) {{ _putchar(c); ++s; }}
}}

void t_printn(int n) {{
    if (0 == n) {{ _putchar('0'); }}
    else {{
        char buf[6];
        char *p = &buf[sizeof(buf) - 1];
        char neg = 0;
        buf[sizeof(buf) - 1] = '\\0';
        if (0 > n) {{ n = -n; neg = 1; }}
        while (0 != n) {{ *--p = '0' + (n % 10); n = n / 10; }}
        if (neg) _putchar('-');
        t_prints(p);
    }}
}}

void t_printf(const char *szFormat, ...) {{
    va_list ap;
    va_start(ap, szFormat);
    while (*szFormat) {{
        if (*szFormat == '%') {{
            switch (*++szFormat) {{
            case 's': {{ char *sz = va_arg(ap, char *); t_prints(sz); break; }}
            case 'u': {{ int i = va_arg(ap, int); t_printn(i); break; }}
            case '%': _putchar('%'); break;
            default: break;
            }}
        }} else {{ _putchar(*szFormat); }}
        szFormat++;
    }}
    va_end(ap);
}}

void t_fail(const char *szMsg, const char *szCond, const char *szFile, int line) {{
    t_printf("--- FAIL: \\"%s\\" on %s at %s:%u\\n", szMsg, szCond, szFile, line);
    t_numFailures++;
}}

/* === test source === */
{test_source}

/* === main === */
int main(void) {{
    _initEmu();
    t_prints("--- Running: ");
    t_prints(t_getSuiteName());
    t_prints("\\n");
    t_runSuite();
    t_prints("--- Summary: ");
    t_printn(t_numFailures);
    _putchar('/');
    t_printn(t_numTests);
    _putchar('/');
    t_printn(t_numCases);
    t_prints(": ");
    t_printn(t_numFailures);
    t_prints(" failed of ");
    t_printn(t_numTests);
    t_prints(" tests in ");
    t_printn(t_numCases);
    t_prints(" cases.\\n");
    _exitEmu();
    return 0;
}}
""")
    return combined



def run_test(test_c: Path, test_name: str, work_dir: Path,
             verbose: bool = False, timeout: int = DEFAULT_TIMEOUT) -> tuple:
    """Compile and run a single SDCC test instance. Returns (status, message).

    Uses single-file compilation: framework + shim + test are combined into
    one .c file because uc80 doesn't export global variables across modules.

    Status values:
      pass     - 0 failures in summary
      fail     - non-zero failures in summary
      compile  - uc80 compiler failed
      asm      - um80 assembler failed
      link     - ul80 linker failed
      skip     - binary too large
      timeout  - execution timed out
      crash    - unexpected runtime error
    """
    # Create combined single-file source
    combined_c = create_combined_test(test_c, test_name, work_dir)
    combined_name = f"{test_name}_combined"

    mac_file = work_dir / f"{combined_name}.mac"
    rel_file = work_dir / f"{combined_name}.rel"
    com_file = work_dir / f"{combined_name}.com"

    # Compile.  We deliberately don't pre-define SDCC=1 even though some
    # tests have SDCC-specific branches: defining it pulls a few tests
    # into code that uses other SDCC-only intrinsics (memory-space
    # qualifiers, snprintf signatures) and produces more regressions
    # than it fixes.  swap.c specifically loses (its non-SDCC uint32
    # typedef is `unsigned int` = 16-bit on Z80) but that's a single
    # test vs ~4 elsewhere.
    cmd = [sys.executable, "-m", "src.main", str(combined_c), "-o", str(mac_file), "--no-whole-program",
           "-I", str(work_dir)]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=UC80_DIR)
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

    # Parse output - look for summary line
    stdout = result.stdout
    output_lines = stdout.strip().split('\n')

    # Filter cpmemu header/footer
    actual_lines = []
    for line in output_lines:
        if line.startswith("CPU mode:") or line.startswith("Loaded "):
            continue
        if line.startswith("Program exit"):
            continue
        actual_lines.append(line)

    actual = '\n'.join(actual_lines).strip()

    # Look for summary line: "--- Summary: F/T/C: F failed of T tests in C cases."
    summary_match = re.search(r'--- Summary:\s*(\d+)/(\d+)/(\d+)', actual)
    if summary_match:
        failures = int(summary_match.group(1))
        total = int(summary_match.group(2))
        cases = int(summary_match.group(3))
        if failures == 0:
            return "pass", f"{total} tests in {cases} cases"
        else:
            # Extract FAIL lines for detail
            fail_lines = [l for l in actual_lines if '--- FAIL' in l]
            detail = '\n'.join(fail_lines[:3])
            return "fail", f"{failures}/{total} failed: {detail}"

    # Partial summary (e.g., "--- Summary: 0/6" without the /C part)
    partial_match = re.search(r'--- Summary:\s*(\d+)/(\d+)', actual)
    if partial_match:
        failures = int(partial_match.group(1))
        total = int(partial_match.group(2))
        if failures == 0:
            return "pass", f"{total} tests"
        else:
            fail_lines = [l for l in actual_lines if '--- FAIL' in l]
            detail = '\n'.join(fail_lines[:3])
            return "fail", f"{failures}/{total} failed: {detail}"

    # No summary but FAIL lines present
    fail_lines = [l for l in actual_lines if '--- FAIL' in l]
    if fail_lines:
        detail = '\n'.join(fail_lines[:3])
        return "fail", f"failures detected (no summary): {detail}"

    # No summary found at all
    if "Aborted" in stdout:
        return "crash", "abort() called"

    return "crash", f"no summary line found. Output: {actual[:200]}"


def main():
    parser = argparse.ArgumentParser(description="Run SDCC regression tests for uc80")
    parser.add_argument("tests", nargs="*", help="Specific test names (e.g., abs addsub)")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("-n", "--limit", type=int, help="Max number of test files to process")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Per-test timeout")
    parser.add_argument("--summary-only", action="store_true", help="Only show summary")
    args = parser.parse_args()

    # Set up working directory
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    # Find test files
    if args.tests:
        test_files = []
        for name in args.tests:
            f = TESTS_DIR / f"{name}.c"
            if f.exists():
                test_files.append(f)
            else:
                print(f"Warning: test {name} not found at {f}")
    else:
        test_files = sorted(TESTS_DIR.glob("*.c"))

    if args.limit:
        test_files = test_files[:args.limit]

    print(f"Found {len(test_files)} test source files")

    results = {"pass": 0, "fail": 0, "compile": 0, "asm": 0, "link": 0,
               "skip": 0, "timeout": 0, "crash": 0}
    total_instances = 0

    for test_file in test_files:
        # Generate instances from template
        instances = generate_instances(test_file, WORK_DIR)

        if not instances:
            if not args.summary_only:
                print(f"  {test_file.stem}: SKIP (no test functions found)")
            continue

        for inst_file, inst_name in instances:
            total_instances += 1
            status, msg = run_test(inst_file, inst_name, WORK_DIR, args.verbose, args.timeout)
            results[status] += 1

            if not args.summary_only:
                if args.verbose or status not in ("pass", "compile"):
                    print(f"  {inst_name}: {status.upper()}")
                    if args.verbose and msg:
                        for line in msg.split('\n')[:3]:
                            print(f"    {line}")

            if total_instances % 50 == 0:
                print(f"  ... {total_instances} instances done, {results['pass']} pass so far")

    # Summary
    total = sum(results.values())
    print(f"\n{'=' * 50}")
    print(f"Total:   {total} test instances (from {len(test_files)} source files)")
    print(f"Pass:    {results['pass']}")
    print(f"Fail:    {results['fail']} (assertion failures)")
    print(f"Compile: {results['compile']}")
    print(f"ASM:     {results['asm']}")
    print(f"Link:    {results['link']}")
    print(f"Skip:    {results['skip']}")
    print(f"Timeout: {results['timeout']}")
    print(f"Crash:   {results['crash']}")


if __name__ == "__main__":
    main()
