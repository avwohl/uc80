"""uc80 - C compiler targeting Z80 / CP/M."""

import os
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("uc80")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["__version__", "lib_dir", "lib_file"]

# The library tree that ships inside the package: crt0.mac, runtime.mac,
# libc.mac, include/, lc/, rt/, build_libs.py, and - once they have been
# built - crt0.rel, libc.lib and runtime.lib.
#
# This is uc80's single source of truth for library assets.  There is
# deliberately no second candidate directory to probe: a repo-relative
# fallback is what let a stale, git-ignored build tree shadow the real one
# and silently link a three-month-old libc (printf returning 0 instead of
# the character count, with no diagnostic from anything).
_PKG_LIB = Path(__file__).resolve().parent / "lib"


def lib_dir() -> Path:
    """Directory that holds uc80's linkable libraries and assembly sources.

    Linking a uc80 program needs ``crt0.rel``, ``libc.lib`` and
    ``runtime.lib`` from this directory.  Wheels built since 0.6.0 ship all
    three; in a git checkout they are build artifacts (``.gitignore`` covers
    ``*.lib`` and ``*.rel``) that exist only after ``uc80 --build-libs`` has
    run.  Always test for the file you need before using it - this function
    answers "where", never "is it there".

    Set ``UC80_LIB_DIR`` to use a library tree built somewhere else, which is
    what makes uc80 usable when it is installed into a read-only
    site-packages.  The override is honoured only if it names an existing
    directory, and :func:`lib_file` applies it per file, so a directory
    holding nothing but the two built ``.lib`` files is a valid override and
    cannot hide ``crt0.mac`` or ``include/``.

    Also available from the shell as ``uc80 --print-lib-dir``, which prints
    exactly this path and nothing else::

        LIB=$(uc80 --print-lib-dir)
    """
    env = os.environ.get("UC80_LIB_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
    return _PKG_LIB


def lib_file(name: str) -> Path:
    """Resolve one library asset, honouring UC80_LIB_DIR with a fallback.

    ``UC80_LIB_DIR`` wins only when it actually contains *name*; otherwise
    the packaged copy is used.  That per-file fallback is the safety property
    that makes the override usable: pointing it at a directory holding just
    ``libc.lib`` and ``runtime.lib`` - the normal case, since those are the
    only two files anyone rebuilds - cannot half-hijack the compiler's own
    inputs (``crt0.mac``, ``runtime.mac``, ``include/``), which are version-
    locked to this compiler's code generator.

    The returned path is not guaranteed to exist; callers must check.
    """
    p = lib_dir() / name
    return p if p.exists() else _PKG_LIB / name
