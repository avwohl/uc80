"""uc80 - C compiler targeting Z80 / CP/M."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("uc80")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
