"""The bench runner verifies the output of every build against a host
build (docs/bench_harness.md): a time build is checked too, and a time
build that stopped early is WRONG(time)."""
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


def test_stopped_early():
    assert run.stopped_early(2_840_000, 9_800_000)
    assert not run.stopped_early(9_700_000, 9_800_000)
    assert not run.stopped_early(5_000, None)
    assert not run.stopped_early(None, 5_000)
    # the manifest key min_time_ratio: sieve prints a lot in the check build
    assert run.stopped_early(2_856_119, 11_000_000, 0.5)
    assert not run.stopped_early(2_856_119, 11_000_000, 0.15)
    sieve = next(p for p in run.load_manifest()["program"] if p["name"] == "sieve")
    assert sieve["min_time_ratio"] == 0.15


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
