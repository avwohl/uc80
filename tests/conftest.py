"""Build the ROM libraries once when a checkout does not have them.

libc_rom.lib and crt0_rom.rel are build artifacts (.gitignore covers *.lib
and *.rel).  Without this hook the ROM tests would skip in a fresh checkout
and hide a missing library.  A lock file keeps pytest-xdist workers from
building at the same time.
"""
import fcntl
import os
import shutil
import subprocess
import sys


# Test the checkout the tests live in, not an installed copy: every test
# that spawns "python -m uc80.main" inherits this PYTHONPATH.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "src")
os.environ["PYTHONPATH"] = os.pathsep.join(
    [_SRC] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep)
              if p and p != _SRC])
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import uc80  # noqa: E402

NEEDED = ("crt0_rom.rel", "libc_rom.lib", "runtime.lib", "libc.lib", "crt0.rel")


def pytest_configure(config):
    lib = uc80.lib_dir()
    if all((lib / n).exists() for n in NEEDED):
        return
    if not (shutil.which("um80") and shutil.which("ul80")):
        return  # tools missing: the tests that need them skip on their own
    lock = lib.parent / ".build_libs.lock"
    try:
        with open(lock, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            if not all((lib / n).exists() for n in NEEDED):
                subprocess.run([sys.executable, "-m", "uc80.main",
                                "--build-libs"], check=True,
                               env=dict(os.environ))
    except OSError:
        pass


# ---- helpers for the sdas tests ---------------------------------------------

def shared_dir(tmp_path_factory, name):
    """A directory that all pytest-xdist workers of one session share."""
    import pathlib
    base = tmp_path_factory.getbasetemp()
    if os.environ.get("PYTEST_XDIST_WORKER"):
        base = base.parent          # one directory for the whole session
    d = pathlib.Path(base) / name
    d.mkdir(exist_ok=True)
    return d


class SharedLock:
    """Hold an exclusive lock on a file in a directory: build in the
    directory once, whatever the number of workers."""

    def __init__(self, d):
        self.path = d / ".lock"

    def __enter__(self):
        self.fh = open(self.path, "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)

    def __exit__(self, *a):
        self.fh.close()


HAVE_SDAS = all(shutil.which(t) for t in ("sdasz80", "sdldz80", "sdar",
                                          "sdobjcopy"))


def _sdas_libs(tmp_path_factory, name, **kw):
    import pytest
    from uc80 import sdas
    if not HAVE_SDAS:
        pytest.skip("needs SDCC sdas tools")
    d = shared_dir(tmp_path_factory, name)
    with SharedLock(d):
        if not (d / "uc80_lc.lib").exists():
            res = sdas.build_library(d, "sdcc", **kw)
            assert res["failed"] == [], res["failed"][:3]
    return d


import pytest  # noqa: E402


@pytest.fixture(scope="session")
def rom_libs(tmp_path_factory):
    """uc80_rt.lib and uc80_lc.lib for a ROM (sdasz80), built once."""
    return _sdas_libs(tmp_path_factory, "sdaslib_rom")


@pytest.fixture(scope="session")
def cpm_libs(tmp_path_factory):
    """The same for a CP/M program (the data is in the image)."""
    return _sdas_libs(tmp_path_factory, "sdaslib_cpm", flat_data=True)
