"""PEP 517 build backend: assemble the CP/M libraries before packaging.

``libc.lib``, ``runtime.lib`` and ``crt0.rel`` are what a uc80 program links
against, and all three are assembler output.  They are gitignored build
artifacts, so they never exist in a fresh checkout and could never reach a
wheel on their own -- ``.github/workflows/publish.yml`` builds from a clean
``actions/checkout``.  The result was that ``pip install uc80`` shipped a
compiler that could not link anything, and every consumer went looking for
the libraries on its own; the one that guessed a repo-relative path found a
three-month-old shadow copy and silently linked against it.

So build them here, at wheel-build time, and let the package-data globs pick
them up.  That needs an assembler, which is why ``um80`` is in
``[build-system].requires``: the um80 wheel installs the ``um80``, ``ul80``
and ``ulib80`` console scripts this uses.

Deliberately NOT done here:

* The libraries are not committed to git.  ``libc.lib`` is not byte
  reproducible - ulib80's per-module symbol index follows Python set
  iteration order, so two builds of identical inputs differ in ~700 bytes
  unless PYTHONHASHSEED is pinned - so a committed binary would churn on
  every rebuild, and worse, could drift out of step with ``lc/*.mac``
  without anything noticing.  Each release just builds its own.
* ``build_editable`` is left alone.  ``pip install -e .`` stays fast, and a
  developer runs ``uc80 --build-libs`` once instead.
* ``build_sdist`` is left alone.  An sdist ships sources; a wheel built from
  it comes back through ``build_wheel`` and assembles them there.
"""

import subprocess
import sys
from pathlib import Path

from setuptools import build_meta as _orig

LIB = Path(__file__).parent / "src" / "uc80" / "lib"


def _assemble_libs():
    """Produce libc.lib, runtime.lib and crt0.rel inside the package tree.

    Failures are fatal (check=True).  A wheel that silently lacks its
    libraries is exactly the bug this exists to fix, so a broken assembler
    must break the release loudly rather than ship a compiler that cannot
    link.
    """
    # build_libs.py resolves its inputs relative to its own location, but run
    # it from LIB anyway so any relative path it grows later still works.
    subprocess.run([sys.executable, str(LIB / "build_libs.py")],
                   check=True, cwd=str(LIB))
    # build_libs.py does not build the startup module.
    subprocess.run(["um80", str(LIB / "crt0.mac"), "-o", str(LIB / "crt0.rel")],
                   check=True)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _assemble_libs()
    return _orig.build_wheel(wheel_directory, config_settings, metadata_directory)


# Everything else delegates unchanged.  The editable hooks must be re-exported
# explicitly: a backend that does not define build_editable makes pip reject
# `pip install -e .` outright.
get_requires_for_build_wheel = _orig.get_requires_for_build_wheel
get_requires_for_build_sdist = _orig.get_requires_for_build_sdist
get_requires_for_build_editable = _orig.get_requires_for_build_editable
prepare_metadata_for_build_wheel = _orig.prepare_metadata_for_build_wheel
prepare_metadata_for_build_editable = _orig.prepare_metadata_for_build_editable
build_sdist = _orig.build_sdist
build_editable = _orig.build_editable
