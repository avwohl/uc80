"""Helper of the family L tests: run one C program with uc80 under cpmemu
and with the host compiler, and return both outputs and the .mac text."""

import shutil
import subprocess
import sys

import pytest

from .test_struct_return_abi import CPMEMU, LIB_DIR, REPO, _toolchain_available

needs_toolchain = pytest.mark.skipif(
    not _toolchain_available() or not shutil.which("cc"),
    reason="um80/ul80/cpmemu, the built libraries or cc are not available")


def host_output(tmp_path, source, cc_flags=("-O1",)):
    src = tmp_path / "h.c"
    src.write_text(source)
    host = subprocess.run(["cc", *cc_flags, "-w", "-o", str(tmp_path / "host"),
                           str(src)], capture_output=True, text=True,
                          timeout=120)
    assert host.returncode == 0, host.stderr
    return subprocess.run([str(tmp_path / "host")], capture_output=True,
                          text=True, timeout=60).stdout.strip()


def uc80_output(tmp_path, source, *flags, name="t", timeout=600):
    """Compile with uc80, assemble, link, run: (output, .mac text)."""
    src = tmp_path / (name + ".c")
    src.write_text(source)
    mac = tmp_path / (name + ".mac")
    r = subprocess.run([sys.executable, "-m", "uc80.main", str(src), "-o",
                        str(mac), *flags], capture_output=True, text=True,
                       cwd=str(REPO), timeout=300)
    assert r.returncode == 0, r.stderr
    subprocess.run(["um80", str(mac), "-o", str(tmp_path / (name + ".rel"))],
                   check=True, capture_output=True, timeout=120)
    subprocess.run(["ul80", str(tmp_path / (name + ".rel")),
                    str(LIB_DIR / "libc.lib"), str(LIB_DIR / "runtime.lib"),
                    "-o", str(tmp_path / (name + ".com"))],
                   check=True, capture_output=True, timeout=120)
    run = subprocess.run([str(CPMEMU), str(tmp_path / (name + ".com"))],
                         capture_output=True, timeout=timeout)
    got = run.stdout.decode("latin-1").replace("\r", "")
    got = "\n".join(ln for ln in got.split("\n")
                    if not ln.startswith(("CPU mode", "Loaded ",
                                          "Program exit", "Note:")))
    return got.strip(), mac.read_text()


def run_both(tmp_path, source, *flags, cc_flags=("-O1",)):
    """(host output, uc80 output, .mac text)"""
    want = host_output(tmp_path, source, cc_flags)
    got, text = uc80_output(tmp_path, source, *flags)
    return want, got, text
