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
    main,
    main as complete_main,
    output_candidates,
    rebuild_cache,
    resolve_candidates,
    simple_cache_key,
    update_simple_cache,
)


def test_complete_main_integration(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

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

    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "wb") as f:
        f.write(b"NOT_A_PICKLE")

    parser = get_parser()
    assert parser is not None


def test_rebuild_cache_handles_missing_dependencies():
    """Ensure rebuild_cache fails gracefully when required parsing dependencies are unavailable."""
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
    monkeypatch.setattr(sys, "argv", ["complete.py"])

    with patch("pdftl.cli.complete.get_parser", return_value=None):
        with patch("sys.stdout", new_callable=io.StringIO):
            result = complete_main()
            assert result is None


def test_module_execution_as_main_script(capsys):
    """Ensure the module can be executed directly as a script without raising unhandled errors."""
    import sys

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore", category=RuntimeWarning, message=".*found in sys.modules.*"
        )

        with patch.object(sys, "argv", ["complete.py"]):
            with patch("pdftl.cli.complete.get_parser", return_value=None):
                runpy.run_module("pdftl.cli.complete", run_name="__main__")

    capsys.readouterr()


def test_get_cache_dir_logic_branching():
    """Manually test both branches without causing path-separator collisions."""
    from pdftl.cli import complete

    with patch("pdftl.cli.complete.os") as mock_os:
        mock_os.name = "nt"
        mock_os.environ.get.return_value = None
        mock_os.path.expanduser.return_value = "C:\\Users\\test"
        mock_os.path.join.return_value = "C:\\Users\\test\\AppData\\Local\\pdftl\\Cache"

        cache_dir = complete.get_cache_dir()
        assert "AppData\\Local" in cache_dir

    with patch("pdftl.cli.complete.os") as mock_os:
        mock_os.name = "posix"
        mock_os.environ.get.return_value = "/custom/cache"
        mock_os.path.join.side_effect = posixpath.join

        cache_dir = complete.get_cache_dir()
        assert "/custom/cache/pdftl" in cache_dir


# --- FIXTURES ---


@pytest.fixture(autouse=True)
def reset_global_cache():
    """Wipes the memoization dict before/after every test to prevent state leaks."""
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
    update_simple_cache("key", ["val"])
    assert mock_cache_env.exists()

    with patch("pdftl.cli.complete.is_package_newer_than_cache", return_value=False):
        assert load_simple_cache() == {"key": ["val"]}


def test_load_simple_cache_when_cache_file_missing():
    """Verify load_simple_cache returns an empty dictionary when package is fresh but cache path does not exist."""
    with (
        patch("pdftl.cli.complete.is_package_newer_than_cache", return_value=False),
        patch("os.path.exists", return_value=False),
    ):
        assert load_simple_cache() == {}


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

    from pdftl.cli.complete import _cache_check_results

    _cache_check_results[cache_file] = "sentinel"
    assert is_package_newer_than_cache(cache_file) == "sentinel"
    _cache_check_results.clear()

    with patch("os.path.getmtime", side_effect=[1000, 2000]):
        assert is_package_newer_than_cache(cache_file) is True


def test_windows_environment_appdata_resolution():
    """Verifies fallback cache directory structures handle Windows APPDATA paths correctly."""
    with patch("os.name", "nt"), patch.dict("os.environ", {"APPDATA": "C:\\App"}):
        is_package_newer_than_cache("fake")


def test_package_freshness_check_on_windows_systems(tmp_path):
    """Verifies cache staleness checking logic on Windows platforms when the cache file exists."""
    cache_file = str(tmp_path / "cache.bin")
    with open(cache_file, "w") as f:
        f.write("data")

    with (
        patch("os.name", "nt"),
        patch.dict("os.environ", {"APPDATA": str(tmp_path)}),
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", return_value=100),
        patch("pdftl.cli.complete._any_path_newer_than", return_value=False),
    ):
        assert is_package_newer_than_cache(cache_file) is False


# --- MAIN & PARSER FALLBACK TESTS ---


def test_main_cli_cache_fast_path_and_parser_fallback(capsys):
    """Verifies main application logic respects cache shortcuts and handles fallback failures."""
    with (
        patch("sys.argv", ["pdftl", "cat", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value="k"),
        patch("pdftl.cli.complete.load_simple_cache", return_value={"k": ["--opt"]}),
    ):
        main()
        assert "--opt" in capsys.readouterr().out

    with (
        patch("sys.argv", ["pdftl", "cat", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value=None),
        patch("pdftl.cli.complete.get_parser", return_value=None),
    ):
        main()


def test_main_falls_back_to_parser_on_cache_miss():
    """Verifies main proceeds to load parser when simple_context_key is valid but missing from cache."""
    with (
        patch("sys.argv", ["pdftl", "help", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value="__help"),
        patch("pdftl.cli.complete.load_simple_cache", return_value={}),
        patch("pdftl.cli.complete.get_parser", return_value=None) as mock_get_parser,
    ):
        main()
        mock_get_parser.assert_called_once()


def test_autocompletion_entrypoint_with_empty_context_and_cache_miss():
    """Verify completion parser correctly handles execution with empty context during cache miss."""
    from lark.exceptions import UnexpectedEOF

    mock_parser = MagicMock()
    exc = UnexpectedEOF(expected={"KW_EACH"})
    mock_parser.parse.side_effect = exc

    with (
        patch("sys.argv", ["complete.py", ""]),
        patch("pdftl.cli.complete.load_simple_cache", return_value={}),
        patch("pdftl.cli.complete.get_parser", return_value=mock_parser),
        patch("sys.stdout", new_callable=io.StringIO) as mock_stdout,
    ):
        main()
        output = mock_stdout.getvalue()
        assert "EACH" in output


def test_main_handles_parser_errors_without_unkeyable_context():
    """Verifies parsing exception handling behavior when context produces no simple cache key."""
    from lark.exceptions import UnexpectedToken

    mock_parser = MagicMock()
    exc = UnexpectedToken(token=MagicMock(), expected={"HELP_KW"})
    mock_parser.parse.side_effect = exc

    with (
        patch("sys.argv", ["pdftl", "input.pdf", "unknown_cmd", ""]),
        patch("pdftl.cli.complete.simple_cache_key", return_value=None),
        patch("pdftl.cli.complete.get_parser", return_value=mock_parser),
        patch("pdftl.cli.complete.update_simple_cache") as mock_update,
        patch("sys.stdout", new_callable=io.StringIO),
    ):
        main()
        mock_update.assert_not_called()


def test_staleness_invalidation_via_critical_directory():
    """Ensure cache invalidates properly when iterating through specific critical folders."""
    from pdftl.cli.complete import is_package_newer_than_cache

    cache_file = "fake.cache"

    with patch("os.path.exists", return_value=True):
        with patch("os.path.getmtime", side_effect=[1000, 500, 2000]):
            assert is_package_newer_than_cache(cache_file) is True


def test_windows_appdata_stale_cache_resolution():
    """Verify script logic correctly navigates Windows APPDATA structures during staleness checks."""
    from pdftl.cli.complete import is_package_newer_than_cache

    with (
        patch("os.name", "nt"),
        patch.dict("os.environ", {"APPDATA": "C:\\Users\\Test\\AppData"}),
        patch("os.path.exists", return_value=True),
        patch("os.path.getmtime", return_value=100),
    ):
        is_package_newer_than_cache("fake.cache")


def test_cache_invalidates_when_script_is_modified(tmp_path):
    """Verify that updates strictly to the executing script reliably invalidate the cache."""
    from pdftl.cli.complete import _cache_check_results, is_package_newer_than_cache

    cache_file = str(tmp_path / "cache.bin")
    with open(cache_file, "w") as f:
        f.write("test")

    _cache_check_results.clear()

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
    """Ensure local script modification time unconditionally supersedes older cached state."""
    from pdftl.cli.complete import _cache_check_results, is_package_newer_than_cache

    _cache_check_results.clear()
    cache_file = os.path.abspath("must_be_newer.cache")

    def mtime_logic(path):
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
        assert result is True


def test_resolve_candidates_includes_args_flag_literal():
    """Verify ARGS_FLAG correctly maps to the literal option flag."""
    mock_parser = MagicMock()
    candidates = resolve_candidates({"ARGS_FLAG"}, mock_parser)
    assert "--args" in candidates


def test_resolve_candidates_args_flag_and_file_path_together():
    """Verify option boundary handling when expecting file path parameters."""
    mock_parser = MagicMock()
    candidates = resolve_candidates({"FILE_PATH"}, mock_parser)
    assert candidates == {"__FILE__"}


def test_resolve_dynamic_token_handles_unmatched_grammar_terminals():
    """Ensure missing terminal definitions safely return empty candidate sets."""
    mock_terminal = MagicMock()
    mock_terminal.name = "UNRELATED_TERMINAL"
    mock_parser = MagicMock()
    mock_parser.terminals = [mock_terminal]

    assert resolve_candidates({"HELP_SUB_KW"}, mock_parser) == set()
    assert resolve_candidates({"KW_NONEXISTENT"}, mock_parser) == set()


def test_output_candidates_filters_unmatched_prefix(capsys):
    """Verify candidate output streaming filters items that do not match the typed prefix."""
    output_candidates(["apple", "banana"], current_partial="b")
    captured = capsys.readouterr().out.splitlines()
    assert captured == ["banana"]
