"""Tests for library discovery and packaging.

``libc.lib`` and ``runtime.lib`` are assembler output, gitignored, and were
produced only by a manual step, so a wheel could never contain them and
uc80 offered no way to ask where they were.  Every consumer therefore
guessed at the path, and a consumer that guessed a repo-relative one found
a stale build tree and linked against it silently -- printf returning 0
instead of the character count, with no diagnostic from the compiler, the
assembler or the linker.

The fix has three parts, all covered here:
  * ``uc80.lib_dir()`` / ``uc80.lib_file()`` -- a real API to ask.
  * ``uc80 --print-lib-dir`` -- the same answer from the shell, which must
    work with no input file even though ``input`` is a required positional.
  * ``uc80 --build-libs`` -- produce the artifacts on demand.
The wheel-build hook that ships the artifacts is covered by
``TestPackaging``, which reads pyproject.toml rather than building a wheel
(a wheel build needs an assembler and takes ~45 s).
"""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import uc80

REPO = Path(__file__).resolve().parent.parent
PKG_LIB = REPO / "src" / "uc80" / "lib"
PYPROJECT = REPO / "pyproject.toml"


def run_uc80(*args, env=None):
    """Invoke the compiler as a subprocess, the way a consumer does."""
    return subprocess.run(
        [sys.executable, "-m", "uc80.main", *args],
        capture_output=True, text=True, cwd=str(REPO), env=env)


class TestLibDir:
    """uc80.lib_dir() - the Python half of the API."""

    def test_returns_the_packaged_lib_directory(self):
        assert uc80.lib_dir() == PKG_LIB

    def test_is_a_path_not_a_string(self):
        # Consumers do lib_dir() / "libc.lib"; a str would break that.
        assert isinstance(uc80.lib_dir(), Path)

    def test_directory_exists_and_holds_the_assembly_sources(self):
        d = uc80.lib_dir()
        assert d.is_dir()
        assert (d / "crt0.mac").is_file()
        assert (d / "include").is_dir()

    def test_is_absolute(self):
        # It is handed to a linker run from some other cwd.
        assert uc80.lib_dir().is_absolute()

    def test_env_override_redirects(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UC80_LIB_DIR", str(tmp_path))
        assert uc80.lib_dir() == tmp_path

    def test_env_override_is_made_absolute(self, tmp_path, monkeypatch):
        """A relative override used to come back verbatim, so
        ``LIB=$(uc80 --print-lib-dir)`` captured in one directory named a
        different tree when used from another -- or fell back to the
        packaged one.  Either way, silently."""
        (tmp_path / "mylib").mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("UC80_LIB_DIR", "mylib")
        got = uc80.lib_dir()
        assert got.is_absolute(), got
        assert got == (tmp_path / "mylib").resolve()

    def test_env_override_ignored_when_not_a_directory(self, monkeypatch):
        monkeypatch.setenv("UC80_LIB_DIR", "/no/such/directory/anywhere")
        assert uc80.lib_dir() == PKG_LIB

    def test_env_override_ignored_when_empty(self, monkeypatch):
        monkeypatch.setenv("UC80_LIB_DIR", "")
        assert uc80.lib_dir() == PKG_LIB

    def test_env_override_ignored_when_a_file(self, tmp_path, monkeypatch):
        f = tmp_path / "notadir"
        f.write_text("x")
        monkeypatch.setenv("UC80_LIB_DIR", str(f))
        assert uc80.lib_dir() == PKG_LIB


class TestLibFile:
    """uc80.lib_file() - per-file resolution with fallback."""

    def test_resolves_within_the_lib_directory(self):
        assert uc80.lib_file("crt0.mac") == PKG_LIB / "crt0.mac"

    def test_resolves_a_subdirectory(self):
        assert uc80.lib_file("include") == PKG_LIB / "include"

    def test_override_wins_for_a_file_it_actually_has(self, tmp_path, monkeypatch):
        (tmp_path / "libc.lib").write_bytes(b"stub")
        monkeypatch.setenv("UC80_LIB_DIR", str(tmp_path))
        assert uc80.lib_file("libc.lib") == tmp_path / "libc.lib"

    def test_partial_override_cannot_hijack_the_compilers_own_inputs(
            self, tmp_path, monkeypatch):
        """The safety property that makes UC80_LIB_DIR usable.

        The realistic override holds only the two rebuilt .lib files.  If it
        also captured crt0.mac / runtime.mac / include - which are version
        locked to this compiler's code generator - a directory holding two
        files would silently break every compile.
        """
        (tmp_path / "libc.lib").write_bytes(b"stub")
        monkeypatch.setenv("UC80_LIB_DIR", str(tmp_path))
        assert uc80.lib_file("libc.lib") == tmp_path / "libc.lib"
        for asset in ("crt0.mac", "runtime.mac", "include"):
            assert uc80.lib_file(asset) == PKG_LIB / asset

    def test_missing_everywhere_returns_the_packaged_path(self, monkeypatch):
        # Not an error: callers test for existence themselves.
        monkeypatch.delenv("UC80_LIB_DIR", raising=False)
        assert uc80.lib_file("nope.lib") == PKG_LIB / "nope.lib"

    def test_exports(self):
        assert set(uc80.__all__) == {"__version__", "lib_dir", "lib_file"}


class TestPrintLibDirFlag:
    """uc80 --print-lib-dir - the shell half of the API."""

    def test_works_without_an_input_file(self):
        """The whole point: `input` is nargs='+' and required.

        Before the fix this exited 2 with "the following arguments are
        required: input" before any option body could run, which is why
        consumers had nothing to call.
        """
        r = run_uc80("--print-lib-dir")
        assert r.returncode == 0, r.stderr
        assert "required" not in r.stderr

    def test_prints_exactly_one_line_and_nothing_else(self):
        # Must be safe for LIB=$(uc80 --print-lib-dir).
        r = run_uc80("--print-lib-dir")
        assert r.stdout == str(uc80.lib_dir()) + "\n"
        assert r.stderr == ""

    def test_output_names_a_directory_that_exists(self):
        r = run_uc80("--print-lib-dir")
        assert Path(r.stdout.strip()).is_dir()

    def test_honours_the_env_override(self, tmp_path):
        env = dict(os.environ, UC80_LIB_DIR=str(tmp_path))
        r = run_uc80("--print-lib-dir", env=env)
        assert r.returncode == 0
        assert r.stdout.strip() == str(tmp_path)

    def test_does_not_require_the_libs_to_be_built(self, tmp_path):
        """It answers "where", never "is it there".

        An empty directory is a legitimate answer - it keeps the flag usable
        inside $(...) before --build-libs has run.
        """
        env = dict(os.environ, UC80_LIB_DIR=str(tmp_path))
        r = run_uc80("--print-lib-dir", env=env)
        assert r.returncode == 0
        assert not (tmp_path / "libc.lib").exists()

    def test_listed_in_help(self):
        r = run_uc80("--help")
        assert "--print-lib-dir" in r.stdout
        assert "--build-libs" in r.stdout

    def test_help_still_works(self):
        # A new argparse.Action subclass is an easy way to break --help.
        r = run_uc80("--help")
        assert r.returncode == 0
        assert "usage: uc80" in r.stdout

    def test_missing_input_still_errors_without_the_flag(self):
        r = run_uc80()
        assert r.returncode == 2
        assert "required" in r.stderr


class TestBuildLibsFlag:
    """uc80 --build-libs - argument handling only.

    Actually building takes ~40 s and needs um80/ulib80, so this covers the
    argparse wiring; TestBuiltArtifacts covers the products.
    """

    def test_accepts_no_input_file(self):
        """It must parse without `input`, like --print-lib-dir.

        Checked by asking for both flags at once: --print-lib-dir is
        declared first and exits 0 immediately, which proves argparse got
        past the required positional without --build-libs itself running.
        """
        r = run_uc80("--print-lib-dir", "--build-libs")
        assert r.returncode == 0
        assert "required" not in r.stderr
        assert r.stdout == str(uc80.lib_dir()) + "\n"

    def test_build_libs_main_accepts_an_explicit_target_list(self):
        """Callers must be able to pass targets instead of via sys.argv.

        build_libs.main() used to read sys.argv unconditionally, so calling
        it from uc80 made it report "Unknown target: --build-libs".
        """
        import inspect

        from uc80.lib import build_libs
        params = inspect.signature(build_libs.main).parameters
        assert "targets" in params
        assert params["targets"].default is None

    def test_build_libs_rejects_an_unknown_target(self, capsys):
        from uc80.lib import build_libs
        with pytest.raises(SystemExit) as e:
            build_libs.main(["nosuchtarget"])
        assert e.value.code == 1
        assert "Unknown target" in capsys.readouterr().out

    def test_build_libs_empty_target_list_means_both(self):
        """`main([])` must not silently build nothing."""
        import inspect

        from uc80.lib import build_libs
        src = inspect.getsource(build_libs.main)
        assert 'targets = ["runtime", "libc"]' in src

    def test_read_only_lib_dir_gives_advice_not_89_permission_errors(
            self, tmp_path, monkeypatch, capsys):
        """A read-only install must fail with one actionable message.

        build_libs runs um80 as a subprocess per module and reports each
        failure separately, so without the up-front writability check this
        emits ~95 "Permission denied" lines and never names the cause.
        """
        import argparse

        from uc80.lib import build_libs
        from uc80.main import _BuildLibsAction

        ro = tmp_path / "lib"
        ro.mkdir()
        ro.chmod(0o555)
        monkeypatch.setattr(build_libs, "SCRIPT_DIR", str(ro))
        parser = argparse.ArgumentParser(prog="uc80")
        action = _BuildLibsAction(["--build-libs"])
        try:
            with pytest.raises(SystemExit) as e:
                action(parser, argparse.Namespace(), None)
            err = capsys.readouterr().err
            assert e.value.code == 1
            assert "uc80: error: cannot write to" in err
            assert "UC80_LIB_DIR" in err
            assert err.count("Permission denied") == 0
        finally:
            ro.chmod(0o755)


class TestBuiltArtifacts:
    """What a consumer actually needs to link, once it has been built."""

    @pytest.mark.skipif(not (PKG_LIB / "libc.lib").exists(),
                        reason="libraries not built (run: uc80 --build-libs)")
    @pytest.mark.parametrize("name", ["crt0.rel", "libc.lib", "runtime.lib"])
    def test_link_artifact_is_reachable_through_the_api(self, name):
        p = uc80.lib_file(name)
        assert p.is_file()
        assert p.stat().st_size > 0

    @pytest.mark.skipif(not (PKG_LIB / "libc.lib").exists(),
                        reason="libraries not built (run: uc80 --build-libs)")
    def test_print_lib_dir_points_at_a_directory_holding_libc_lib(self):
        """The consumer contract, exactly as documented in docs/libraries.md."""
        r = run_uc80("--print-lib-dir")
        assert (Path(r.stdout.strip()) / "libc.lib").is_file()


class TestSingleSourceOfTruth:
    """There must be exactly one library directory."""

    def test_no_toplevel_lib_shadow_directory(self):
        """The stale shadow that caused the original silent miscompile.

        /home/wohl/src/uc80/lib was git-ignored, three months old, and
        unreferenced by any code -- but it was the second candidate a
        consumer probed, so it won whenever the real one was empty.
        """
        assert not (REPO / "lib").exists(), (
            "the vestigial top-level lib/ is back; uc80 uses "
            "src/uc80/lib exclusively")

    def test_compiler_resolves_its_assets_through_the_api(self):
        """No module may re-derive the lib path with Path(__file__).

        Three copies of `Path(__file__).parent / "lib"` are what made it
        impossible to expose or override the location in one place.
        """
        for module in ("main.py", "runtime.py", "codegen.py", "asm_dce.py"):
            text = (REPO / "src" / "uc80" / module).read_text()
            assert 'Path(__file__).parent / "lib"' not in text, module

    def test_gitignore_still_excludes_the_build_artifacts(self):
        """They are built, never committed.

        libc.lib is not byte reproducible (ulib80's symbol index follows
        Python set iteration order), so a committed copy would churn on
        every rebuild and could drift from lc/*.mac unnoticed.
        """
        ignore = (REPO / ".gitignore").read_text().splitlines()
        assert "*.lib" in ignore
        assert "*.rel" in ignore

    def test_built_artifacts_are_not_tracked_by_git(self):
        r = subprocess.run(["git", "ls-files", "src/uc80/lib"],
                           capture_output=True, text=True, cwd=str(REPO))
        tracked = r.stdout.split()
        assert not [f for f in tracked if f.endswith((".lib", ".rel"))]


class TestPackaging:
    """pyproject.toml must be able to ship the artifacts it promises."""

    @staticmethod
    def _cfg():
        with open(PYPROJECT, "rb") as f:
            return tomllib.load(f)

    def test_build_backend_is_the_in_tree_shim(self):
        bs = self._cfg()["build-system"]
        assert bs["build-backend"] == "_uc80_build"
        assert bs["backend-path"] == ["."]

    def test_assembler_is_a_build_requirement(self):
        """Without um80 at build time the hook cannot assemble anything."""
        reqs = self._cfg()["build-system"]["requires"]
        assert any(r.startswith("um80") for r in reqs), reqs

    def test_backend_module_exists_and_builds_the_libs(self):
        text = (REPO / "_uc80_build.py").read_text()
        assert "def build_wheel(" in text
        assert "build_libs.py" in text
        assert "crt0.mac" in text

    def test_backend_delegates_the_editable_hooks(self):
        """A backend without build_editable makes pip reject -e installs."""
        text = (REPO / "_uc80_build.py").read_text()
        for hook in ("build_editable", "prepare_metadata_for_build_editable",
                     "get_requires_for_build_editable", "build_sdist"):
            assert f"{hook} = _orig.{hook}" in text, hook

    def test_sdist_carries_the_backend_module(self):
        """`python -m build` builds the wheel from the sdist.

        _uc80_build.py lives at the repo root, so it belongs to no package
        and setuptools would drop it; then the wheel step would fail with
        "Cannot import '_uc80_build'".
        """
        assert "_uc80_build.py" in (REPO / "MANIFEST.in").read_text()

    def test_package_data_ships_the_link_artifacts(self):
        data = self._cfg()["tool"]["setuptools"]["package-data"]["uc80"]
        assert "lib/*.lib" in data
        assert "lib/crt0.rel" in data

    def test_package_data_excludes_the_intermediate_rel_files(self):
        """lib/**/*.rel would add ~105 KB of per-module objects nothing links."""
        data = self._cfg()["tool"]["setuptools"]["package-data"]["uc80"]
        assert "lib/**/*.rel" not in data

    def test_no_false_py_typed_claim(self):
        """Declared for a file that does not exist -- and still does not.

        uc80 carries inline annotations but has never been type checked
        (mypy reports 74 errors), has no type checker in the dev extra and
        no CI type gate, so PEP 561 would be a promise nothing enforces.
        If py.typed is ever shipped, create the file in the same commit.
        """
        data = self._cfg()["tool"]["setuptools"]["package-data"]["uc80"]
        declared = "py.typed" in data
        exists = (REPO / "src" / "uc80" / "py.typed").exists()
        assert declared == exists, (
            "pyproject declares py.typed but the file does not exist"
            if declared else
            "src/uc80/py.typed exists but pyproject does not ship it")
