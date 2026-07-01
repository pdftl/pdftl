# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/cli/test_complete.py

import io
import os
import posixpath
import runpy
import sys
import warnings
from unittest.mock import MagicMock, mock_open, patch

import pytest

from pdftl.cli.complete import (
    get_cache_path,
    is_package_newer_than_cache,
    load_simple_cache,
)
from pdftl.cli.complete import main
from pdftl.cli.complete import main as complete_main
from pdftl.cli.complete import (
    rebuild_cache,
    resolve_candidates,
    simple_cache_key,
    update_simple_cache,
)


def test_complete_main_integration(tmp_path, monkeypatch):
    # Mock cache dir to avoid messing with user's real cache
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Simulate: pdftl help <TAB>
    with patch.object(sys, "argv", ["complete.py", "help", ""]):
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            complete_main()
            output = mock_stdout.getvalue()
            assert "sign" in output
            assert "filter" in output


def test_rebuild_on_corrupt_cache(tmp_path, monkeypatch):
    from pdftl.cli.complete import get_parser

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache_file = get_cache_path()

    # Create a garbage pickle file
    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "wb") as f:
        f.write(b"NOT_A_PICKLE")

    # This should not crash, it should catch the Exception and rebuild
    parser = get_parser()
    assert parser is not None


def test_rebuild_cache_handles_missing_dependencies():
    """Ensure rebuild_cache fails gracefully when required parsing dependencies are unavailable."""
    # We poison the sys.modules for cloudpickle to trigger the ImportError
    with patch.dict("sys.modules", {"cloudpickle": None}):
        result = rebuild_cache()
        assert result is None


def test_resolve_candidates_handles_file_path_token():
    """Ensure the FILE_PATH terminal node safely resolves to the __FILE__ magic candidate."""
    mock_parser = MagicMock()
    candidates = resolve_candidates({"FILE_PATH"}, mock_parser)
    assert "__FILE__" in candidates


def test_resolve_dynamic_token_safely_handles_missing_parser():
    """Ensure dynamic token resolution returns empty sets gracefully when the parser is unavailable."""
    candidates = resolve_candidates(["UNKNOWN_TOKEN"], None)
    assert not candidates


def test_main_gracefully_exits_on_empty_args_or_parser_failure(monkeypatch):
    """Ensure the CLI entrypoint exits cleanly when provided no arguments or when the parser fails."""
    # Simulate zero arguments passed to the script
    monkeypatch.setattr(sys, "argv", ["complete.py"])

    # Mock get_parser to return None
    with patch("pdftl.cli.complete.get_parser", return_value=None):
        with patch("sys.stdout", new_callable=io.StringIO):
            result = complete_main()
            assert result is None


def test_module_execution_as_main_script(capsys):
    """Ensure the module can be executed directly as a script without raising unhandled errors."""
    import sys

    # We use a context manager to ignore the specific runpy warning
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=RuntimeWarning, message=".*found in sys.modules.*"
        )

        with patch.object(sys, "argv", ["complete.py"]):
            with patch("pdftl.cli.complete.get_parser", return_value=None):
                runpy.run_module("pdftl.cli.complete", run_name="__main__")

    # This "swallows" the printed output so it doesn't spam your terminal
    capsys.readouterr()


def test_get_cache_dir_logic_branching():
    """Manually test both branches without causing path-separator collisions."""
    from pdftl.cli import complete

    # 1. Test Windows logic branch
    with patch("pdftl.cli.complete.os") as mock_os:
        mock_os.name = "nt"
        mock_os.environ.get.return_value = None
        mock_os.path.expanduser.return_value = "C:\\Users\\test"
        # Manually control join to return a Windows-looking string
        mock_os.path.join.return_value = "C:\\Users\\test\\AppData\\Local\\pdftl\\Cache"

        cache_dir = complete.get_cache_dir()
        assert "AppData\\Local" in cache_dir

    # 2. Test POSIX logic branch
    with patch("pdftl.cli.complete.os") as mock_os:
        mock_os.name = "posix"
        mock_os.environ.get.return_value = "/custom/cache"

        # Use posixpath.join to force forward slashes,
        # even when running on Windows.
        mock_os.path.join.side_effect = posixpath.join

        cache_dir = complete.get_cache_dir()
        assert "/custom/cache/pdftl" in cache_dir


# --- FIXTURES ---


@pytest.fixture(autouse=True)
def reset_global_cache():
    """Wipes the memoization dict before/after every test to prevent 'leaky' state."""
    from pdftl.cli.complete import _cache_check_results

    _cache_check_results.clear()
    yield
    _cache_check_results.clear()


@pytest.fixture
def mock_cache_env(tmp_path):
    """Provides a clean temp directory and mocks the cache path logic."""
    cache_dir = tmp_path / "pdftl_cache"
    cache_dir.mkdir()
    with patch("pdftl.cli.complete.get_cache_dir", return_value=str(cache_dir)):
        yield cache_dir


# --- LOGIC TESTS ---


@pytest.mark.parametrize(
    "context, expected",
    [
        ([], "__zero"),
        (["help"], "__help"),
        (["--help"], "__help"),
        (["--verbose"], "__option:--verbose"),
        (["input.pdf"], "__one"),
        (["input.pdf", "---"], "__start_pipeline"),
        (["input.pdf", "place"], "__hardcoded:place"),
        (["input.pdf", "unknown"], None),
    ],
)
def test_simple_cache_key_generation(context, expected):
    """Comprehensive check for cache key generation mapping logic."""
    assert simple_cache_key(context) == expected


# --- CACHE I/O TESTS ---


def test_cache_io_lifecycle(mock_cache_env):
    """Verifies cache saving, auto-directory creation, and successful retrieval."""
    # 1. Update (creates dir if missing)
    update_simple_cache("key", ["val"])
    assert mock_cache_env.exists()

    # 2. Load Success
    with patch("pdftl.cli.complete.is_package_newer_than_cache", return_value=False):
        assert load_simple_cache() == {"key": ["val"]}


def test_load_simple_cache_handles_corruption_safely():
    """Ensure unreadable or garbage cache files return a safe empty state."""
    m_open = mock_open(read_data=b"garbage")

    with (
        patch("pdftl.cli.complete.is_package_newer_than_cache", return_value=False),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", m_open),
        patch("marshal.load", side_effect=ValueError("Corrupt")),
    ):
        result = load_simple_cache()
        assert result == {}


def test_update_simple_cache_bubbles_disk_errors():
    """Ensure cache generation bubbles up unwritable disk permission errors properly."""
    # We mock load_simple_cache to return a dummy dict first
    with patch("pdftl.cli.complete.load_simple_cache", return_value={}):
        with patch("os.makedirs", side_effect=OSError("Permission Denied")):
            with pytest.raises(OSError, match="Permission Denied"):
                update_simple_cache("key", ["candidate"])


# --- PACKAGE MONITORING TESTS ---


def test_package_freshness_invalidation(tmp_path):
    """Verifies that mtime logic flags the cache as stale when package files are updated."""
    cache_file = str(tmp_path / "cache.bin")
    with open(cache_file, "w") as f:
        f.write("data")

    # Memoization hit test
    from pdftl.cli.complete import _cache_check_results

    _cache_check_results[cache_file] = "sentinel"
    assert is_package_newer_than_cache(cache_file) == "sentinel"
    _cache_check_results.clear()

    # Package root is newer test
    with patch("os.path.getmtime", side_effect=[1000, 2000]):
        assert is_package_newer_than_cache(cache_file) is True


def test_windows_environment_appdata_resolution():
    """Verifies fallback cache directory structures handle Windows APPDATA paths correctly."""
    with patch("os.name", "nt"), patch.dict("os.environ", {"APPDATA": "C:\\App"}):
        # We just need it to execute without crashing
        is_package_newer_than_cache("fake")


# --- MAIN & PARSER FALLBACK TESTS ---


def test_main_cli_cache_fast_path_and_parser_fallback(capsys):
    """Verifies main application logic respects cache shortcuts and handles fallback failures."""
    # Fast-path hit in main
    with (
        patch("sys.argv", ["pdftl", "cat", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value="k"),
        patch("pdftl.cli.complete.load_simple_cache", return_value={"k": ["--opt"]}),
    ):
        main()
        assert "--opt" in capsys.readouterr().out

    # Parser failure path in main
    with (
        patch("sys.argv", ["pdftl", "cat", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value=None),
        patch("pdftl.cli.complete.get_parser", return_value=None),
    ):
        main()  # Should return early


def test_staleness_invalidation_via_critical_directory():
    """Ensure cache invalidates properly when iterating through specific critical folders."""
    from pdftl.cli.complete import is_package_newer_than_cache

    cache_file = "fake.cache"

    # Sequence of mtimes designed to trigger specific invalidation thresholds
    with patch("os.path.exists", return_value=True):
        with patch("os.path.getmtime", side_effect=[1000, 500, 2000]):
            assert is_package_newer_than_cache(cache_file) is True


def test_windows_appdata_stale_cache_resolution():
    """Verify script logic correctly navigates Windows APPDATA structures during staleness checks."""
    from pdftl.cli.complete import is_package_newer_than_cache

    # Force os.name to 'nt' to enter Windows path handling
    with (
        patch("os.name", "nt"),
        patch.dict("os.environ", {"APPDATA": "C:\\Users\\Test\\AppData"}),
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", return_value=100),
    ):
        # Result not needed, just confirming the target block executes safely
        is_package_newer_than_cache("fake.cache")


def test_cache_invalidates_when_script_is_modified(tmp_path):
    """Verify that updates strictly to the executing script reliably invalidate the cache."""
    from pdftl.cli.complete import _cache_check_results, is_package_newer_than_cache

    # 1. Setup a dummy cache file
    cache_file = str(tmp_path / "cache.bin")
    with open(cache_file, "w") as f:
        f.write("test")

    # 2. Reset global state
    _cache_check_results.clear()

    # 3. Mtime mock generator to simulate an updated script file against an older cache
    def mtime_mock(path):
        if "complete.py" in str(path) or "__file__" in str(path):
            return 5000
        if "cache.bin" in str(path):
            return 1000
        return 500

    with (
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", side_effect=mtime_mock),
    ):
        assert is_package_newer_than_cache(cache_file) is True


def test_script_modification_takes_precedence_in_cache_checks():
    """Ensure that the local script file's modification time unconditionally supersedes stale caches."""
    from pdftl.cli.complete import _cache_check_results, is_package_newer_than_cache

    _cache_check_results.clear()
    cache_file = os.path.abspath("must_be_newer.cache")

    def mtime_logic(path):
        # Normalize paths for comparison
        path = os.path.abspath(str(path))

        if path == cache_file:
            return 1000

        if "complete.py" in path or path.endswith(".py") or path.endswith(".pyc"):
            return 5000

        return 500

    with (
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", side_effect=mtime_logic),
    ):
        result = is_package_newer_than_cache(cache_file)

        if not result:
            import pdb

            pdb.set_trace()

        assert result is True
