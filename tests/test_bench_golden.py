"""The bench runner verifies the output of every build against a host
build (docs/bench_harness.md): a time build is checked too, and a
time build checks its own result."""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "bench"


def load_run():
    sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location("bench_run", BENCH / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run = load_run()


def test_time_golden_path():
    # a program whose time build prints nothing has its own time file
    p = {"name": "sieve"}
    assert run.golden_path(p, "time").name == "sieve.time.txt"
    assert run.golden_path(p).name == "sieve.txt"
    # a program with the same output in both builds uses the check file
    q = {"name": "embench-huffbench"}
    assert run.golden_path(q, "time").name == "embench-huffbench.txt"
    # a variant takes the time file of its base program
    v = {"name": "sieve+static", "base": "sieve"}
    assert run.golden_path(v, "time").name == "sieve.time.txt"


def test_every_run_program_has_a_golden_file():
    man = run.load_manifest()
    for p in man["program"]:
        if p.get("kind") == "size-only":
            continue
        assert run.golden_path(p).exists(), p["name"]
        assert run.golden_path(p, "time").exists(), p["name"]


def test_time_builds_without_output_check_themselves():
    """Every program whose time build prints nothing has self-check
    edits (BENCH_CHECK in bench.h); the check values give the right
    result on the host."""
    man = run.load_manifest()
    for p in man["program"]:
        if p.get("kind") == "size-only":
            continue
        if run.golden_path(p, "time").name.endswith(".time.txt"):
            assert run.has_selfcheck(p), p["name"]


@pytest.mark.skipif(shutil.which("cc") is None, reason="no host cc")
def test_self_check_stops_a_wrong_result(tmp_path):
    """A BENCH_CHECK_EQ with a wrong value loops for ever, and one with
    the right value does not."""
    src = tmp_path / "t.c"
    src.write_text('#include "bench.h"\nint v = 7;\n'
                   'int main(void){ BENCH_CHECK_EQ(v, EXPECT);'
                   ' BENCH_CHECK_NEAR(v * 0.5, 3.5, 0.1);'
                   ' BENCH_CHECK(v == 7); return 0; }\n')
    for expect, ok in ((7, True), (8, False)):
        exe = tmp_path / f"t{expect}"
        subprocess.run(["cc", "-w", f"-I{BENCH}", f"-DEXPECT={expect}",
                        str(src), "-o", str(exe)], check=True)
        try:
            r = subprocess.run([str(exe)], timeout=3)
            assert ok and r.returncode == 0
        except subprocess.TimeoutExpired:
            assert not ok


@pytest.mark.skipif(shutil.which("cc") is None, reason="no host cc")
def test_time_build_of_sieve_loops_on_a_wrong_result(tmp_path):
    """sieve with a flag set wrong in the time build: the self-check
    of the count of primes loops for ever."""
    man = run.load_manifest()
    prog = next(p for p in man["program"] if p["name"] == "sieve")
    try:
        srcs = run.prepare_sources(prog, man["repo"], tmp_path)
    except (SystemExit, OSError):
        pytest.skip("z88dk sources are not in bench/_cache")
    src = srcs[0]
    text = src.read_text(encoding="latin-1")
    assert "         for (k = i_sq; k < SIZE; k += i)" in text
    exe = tmp_path / "sieve"

    def build(extra):
        src.write_text(extra, encoding="latin-1")
        subprocess.run(["cc", "-w", f"-I{BENCH}", f"-I{tmp_path}", "-DTIMER",
                        str(src), "-o", str(exe)], check=True)

    build(text)
    assert subprocess.run([str(exe)], timeout=20).returncode == 0
    build(text.replace("flags[k] = 1;", "flags[k] = (k < 7000);"))
    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run([str(exe)], timeout=3)


@pytest.mark.skipif(shutil.which("cc") is None, reason="no host cc")
def test_huffbench_verify_sees_a_missing_decoder(tmp_path):
    """verify_benchmark compared test_data with orig_data while test_data
    still held the original bytes: a run that never decoded printed PASS.
    The edit that clears the buffer makes that run print FAIL."""
    man = run.load_manifest()
    prog = next(p for p in man["program"] if p["name"] == "embench-huffbench")
    try:
        srcs = run.prepare_sources(prog, man["repo"], tmp_path / "ok")
    except (SystemExit, OSError):
        pytest.skip("embench sources are not in bench/_cache")

    def build_and_run(work, srcs):
        out = work / "host"
        subprocess.run(["cc", "-w", "-O1", f"-I{BENCH}", f"-I{work}", "-DPRINTF"]
                       + [str(s) for s in srcs] + ["-o", str(out)],
                       check=True)
        return subprocess.run([str(out)], capture_output=True,
                              text=True).stdout.strip()

    assert build_and_run(tmp_path / "ok", srcs) == "PASS"
    work = tmp_path / "cut"
    srcs = run.prepare_sources(prog, man["repo"], work)
    lib = work / "libhuffbench.c"
    text = lib.read_text(encoding="latin-1")
    marker = "  memset (data, 0, data_len);\n"
    assert marker in text
    # a decoder that never runs: return right after the buffer is cleared
    lib.write_text(text.replace(marker, marker + "  free_beebs (comp);\n  return;\n"),
                   encoding="latin-1")
    assert build_and_run(work, srcs) == "FAIL"
