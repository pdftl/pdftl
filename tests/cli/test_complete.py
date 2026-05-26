import io
import os
import posixpath
import runpy
import sys
import warnings
from unittest.mock import MagicMock, patch

from pdftl.cli.complete import get_cache_path, rebuild_cache, resolve_candidates
from pdftl.cli.complete import main as complete_main


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
