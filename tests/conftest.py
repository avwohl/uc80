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

import uc80

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
