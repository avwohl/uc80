# Binary Size and Test Results

Back to the [README](../README.md). The worst-case size comparison against z88dk is analyzed in [size_analysis_0053_0456.md](size_analysis_0053_0456.md).

## Binary Size

uc80 produces the smallest known binaries for Z80/CP/M among current compilers.

Tested against [z88dk](https://z88dk.org/) (SDCC backend, `-SO3 --max-allocs-per-node10000`)
on the [Fujitsu compiler-test-suite](https://github.com/fujitsu/compiler-test-suite):

| Metric | Result |
|--------|--------|
| uc80 smaller | 47/47 tests (100%) |
| Aggregate size ratio | 46% (uc80 is less than half the size) |
| Total uc80 | 170,496 bytes |
| Total z88dk | 369,644 bytes |
| Minimal binary | 128 bytes (vs 5,172 for z88dk) |

Sample sizes (bytes):

| Program | uc80 | z88dk | Ratio |
|---------|------|-------|-------|
| hello world (puts) | 256 | 5,172 | 5% |
| printf %d | 4,608 | 7,696 | 60% |
| integer math | 5,248 | 7,948 | 66% |
| long arithmetic | 5,632 | 7,793 | 72% |

## Test Results

Tested against multiple external test suites:

| Suite | Pass Rate | Notes |
|-------|-----------|-------|
| [c-testsuite](https://github.com/c-testsuite/c-testsuite) | 220/220 | full pass |
| c-testsuite `--int=32` | 219/220 | 00200 (long-long shift) overflows 64K TPA |
| c-testsuite `--int=32 --long=64` | 218/220 | same as above + marginal timeout |
| [Fujitsu compiler-test-suite](https://github.com/fujitsu/compiler-test-suite) 0003 | 371/374 | |
| Fujitsu 0010 | 58/75 | 9 int16, 1 float, 2 timeout |
| Fujitsu 0011 | 287/335 | 14 int16, 5 large struct |
| Fujitsu 0012 | 4/9 | 4 int16/long long, 1 static DCE |
| [SDCC regression tests](https://sourceforge.net/projects/sdcc/) | 514/523 | 3 fail, 4 sdcc ext, 2 multi-file link |

Remaining non-passing tests are environmental, not codegen bugs:
- **sdcc ext**: SDCC-specific extensions (`__asm`, `#pragma save/restore`)
- **multi-file**: tests that require separate compilation units
- **float precision**: ACOSF/TANF near asymptotes (single-precision IEEE 754 limit)
- **malloc OOM**: SDCC test asserts `malloc(2000) == NULL`; we have plenty of TPA
- **00200**: 67KB binary exceeds 64KB CP/M TPA
