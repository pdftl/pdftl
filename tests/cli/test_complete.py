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


def test_coverage_gap_import_errors():
    """Targets lines 37-38: Handling missing dependencies during rebuild."""
    # We poison the sys.modules for cloudpickle to trigger the ImportError
    with patch.dict("sys.modules", {"cloudpickle": None}):
        result = rebuild_cache()
        assert result is None


def test_coverage_gap_resolve_file_path():
    """Targets line 104: The generic FILE_PATH candidate."""
    mock_parser = MagicMock()
    candidates = resolve_candidates({"FILE_PATH"}, mock_parser)
    assert "__FILE__" in candidates


def test_coverage_gap_main_edge_cases(monkeypatch):
    """Targets lines 128-129 (no args) and 134 (parser failure)."""
    # Simulate zero arguments passed to the script
    monkeypatch.setattr(sys, "argv", ["complete.py"])

    # Mock get_parser to return None to hit line 134
    with patch("pdftl.cli.complete.get_parser", return_value=None):
        with patch("sys.stdout", new_callable=io.StringIO):
            result = complete_main()
            assert result is None


def test_coverage_gap_module_entrypoint(capsys):
    """Targets line 166: The __main__ execution block quietly."""
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

        # FIX: Use posixpath.join to force forward slashes,
        # even when running on Windows.
        mock_os.path.join.side_effect = posixpath.join

        cache_dir = complete.get_cache_dir()
        assert "/custom/cache/pdftl" in cache_dir


# --- merged from test_complete_coverage.py ---

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
        (["input.pdf", "unknown"], None),  # Hit line 332
    ],
)
def test_simple_cache_key_logic(context, expected):
    """Comprehensive check for cache key branches (Lines 318-332)."""
    assert simple_cache_key(context) == expected


# --- CACHE I/O TESTS ---


def test_cache_io_flow(mock_cache_env):
    """Tests loading, updating, and directory auto-creation (Lines 161-186)."""
    # 1. Update (creates dir if missing)
    update_simple_cache("key", ["val"])
    assert mock_cache_env.exists()

    # 2. Load Success
    with patch("pdftl.cli.complete.is_package_newer_than_cache", return_value=False):
        assert load_simple_cache() == {"key": ["val"]}


def test_load_simple_cache_handles_corruption():
    """Hits lines 159-160: Returns {} on marshal/read errors."""
    m_open = mock_open(read_data=b"garbage")

    with (
        patch("pdftl.cli.complete.is_package_newer_than_cache", return_value=False),
        patch("os.path.exists", return_value=True),
        patch("builtins.open", m_open),
        patch("marshal.load", side_effect=ValueError("Corrupt")),
    ):
        # Now that you changed 'raise' to 'return {}', this passes!
        result = load_simple_cache()
        assert result == {}


def test_update_simple_cache_write_error():
    """Hits lines 177-178: Re-raises if disk is unwritable."""
    # We mock load_simple_cache to return a dummy dict first
    with patch("pdftl.cli.complete.load_simple_cache", return_value={}):
        with patch("os.makedirs", side_effect=OSError("Permission Denied")):
            with pytest.raises(OSError, match="Permission Denied"):
                update_simple_cache("key", ["candidate"])


# --- PACKAGE MONITORING TESTS ---


def test_package_freshness(tmp_path):
    """Tests mtime logic and the memoization shortcut (Lines 199, 210, 238)."""
    cache_file = str(tmp_path / "cache.bin")
    with open(cache_file, "w") as f:
        f.write("data")

    # Line 199: Memoization hit
    from pdftl.cli.complete import _cache_check_results

    _cache_check_results[cache_file] = "sentinel"
    assert is_package_newer_than_cache(cache_file) == "sentinel"
    _cache_check_results.clear()

    # Line 210: Package root is newer
    with patch("os.path.getmtime", side_effect=[1000, 2000]):
        assert is_package_newer_than_cache(cache_file) is True


def test_nt_path_logic():
    """Hits Windows-specific config pathing (Line 217)."""
    with patch("os.name", "nt"), patch.dict("os.environ", {"APPDATA": "C:\\App"}):
        # We just need it to execute without crashing
        is_package_newer_than_cache("fake")


# --- MAIN & PARSER FALLBACK TESTS ---


def test_main_and_parser_failures(capsys):
    """Hits the tricky exit points in main and get_parser (Lines 365, 372)."""
    # Line 365-367: Fast-path hit in main
    with (
        patch("sys.argv", ["pdftl", "cat", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value="k"),
        patch("pdftl.cli.complete.load_simple_cache", return_value={"k": ["--opt"]}),
    ):
        main()
        assert "--opt" in capsys.readouterr().out

    # Line 372: Parser failure path in main
    with (
        patch("sys.argv", ["pdftl", "cat", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value=None),
        patch("pdftl.cli.complete.get_parser", return_value=None),
    ):
        main()  # Should return early at line 372


def test_directory_iteration_hit():
    """Hits lines 233-234: Finding a newer critical directory."""
    from pdftl.cli.complete import is_package_newer_than_cache

    cache_file = "fake.cache"

    # We need a sequence of mtimes:
    # 1. cache_file (1000)
    # 2. package_root (500)
    # 3. First critical dir (2000) -> This triggers lines 233-234
    with patch("os.path.exists", return_value=True):
        with patch("os.path.getmtime", side_effect=[1000, 500, 2000]):
            assert is_package_newer_than_cache(cache_file) is True


def test_nt_path_handling_hit():
    """Hits line 217: Windows APPDATA path resolution."""
    from pdftl.cli.complete import is_package_newer_than_cache

    # Force os.name to 'nt' to enter the branch at 217
    with (
        patch("os.name", "nt"),
        patch.dict("os.environ", {"APPDATA": "C:\\Users\\Test\\AppData"}),
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", return_value=100),
    ):
        # We don't care about the result, just hitting the line
        is_package_newer_than_cache("fake.cache")


def test_is_package_newer_than_cache_logic_extended_final(tmp_path):
    """Hits lines 238-239: Script itself is newer than cache."""
    from pdftl.cli.complete import _cache_check_results, is_package_newer_than_cache

    # 1. Setup a dummy cache file
    cache_file = str(tmp_path / "cache.bin")
    with open(cache_file, "w") as f:
        f.write("test")

    # 2. Reset global state
    _cache_check_results.clear()

    # 3. We use a generator for side_effect to avoid counting exactly
    # how many dirs are in the loop.
    # It returns 1000 (old) forever, UNTIL the very last call, which is 5000 (new).
    def mtime_mock(path):
        # If the path is this test file or the completion script, it's 'new'
        # This ensures line 237 evaluates to True
        if "complete.py" in str(path) or "__file__" in str(path):
            return 5000
        # For the cache file itself (the first call), return a middle value
        if "cache.bin" in str(path):
            return 1000
        # For everything else (root, critical dirs), return 'old'
        return 500

    with (
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", side_effect=mtime_mock),
    ):
        # This will walk through the loop (returning 500),
        # reach line 237, get 5000, and hit 238-239.
        assert is_package_newer_than_cache(cache_file) is True


def test_script_itself_is_newer():
    """Hits lines 238-239 by ensuring the script mtime is always the winner."""

    from pdftl.cli.complete import _cache_check_results, is_package_newer_than_cache

    _cache_check_results.clear()
    cache_file = os.path.abspath("must_be_newer.cache")

    def mtime_logic(path):
        # Normalize paths for comparison
        path = os.path.abspath(str(path))

        if path == cache_file:
            return 1000

        # If the code is checking the script itself (Line 237)
        # We check for 'complete.py' in the path to be safe
        if "complete.py" in path or path.endswith(".py") or path.endswith(".pyc"):
            return 5000

        # Everything else (package root, directories)
        return 500

    with (
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", side_effect=mtime_logic),
    ):
        result = is_package_newer_than_cache(cache_file)

        # If this still fails, the pdb below will show us what paths were checked
        if not result:
            import pdb

            pdb.set_trace()

        assert result is True
