import os
import sys
from unittest.mock import patch

import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.dependencies import ensure_dependencies, is_pipx_install


def test_ensure_dependencies_list_conversion():
    """Covers lines 26-27: converting list inputs to dicts."""
    with patch("importlib.util.find_spec") as mock_find:
        mock_find.return_value = True  # Simulate found
        # Pass list, should convert internally and succeed
        ensure_dependencies("test", ["os", "sys"], "extra")


def test_ensure_dependencies_missing_detection():
    """Covers lines 29-32: missing dependency logic."""
    with patch("importlib.util.find_spec") as mock_find:
        mock_find.return_value = None  # Simulate NOT found

        # Expect an error (ImportError/RuntimeError depending on impl)
        # Assuming the function raises when deps are missing
        with pytest.raises(Exception):
            ensure_dependencies("test", {"fake_module": "Fake Display"}, "extra")


# -----------------------------------------------------------------------------
# Tests for is_pipx_install()
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mock_prefix, expected",
    [
        # Simulate standard pipx paths using native OS separators
        (os.path.join(os.sep, "Users", "user", ".local", "share", "pipx", "venvs", "pdftl"), True),
        (os.path.join(os.sep, "home", "user", "pipx", "venvs", "pdftl"), True),
        # Simulate standard pip / venv paths (Not pipx)
        (os.path.join(os.sep, "Users", "user", "projects", "pdftl", ".venv"), False),
        (os.path.join(os.sep, "opt", "homebrew", "opt", "python", "Frameworks"), False),
        (os.path.join(os.sep, "usr", "local", "bin", "python"), False),
    ],
)
def test_is_pipx_install(monkeypatch, mock_prefix, expected):
    """Test that the path heuristic correctly identifies 'pipx' in sys.prefix."""
    monkeypatch.setattr(sys, "prefix", mock_prefix)
    assert is_pipx_install() is expected


# -----------------------------------------------------------------------------
# Tests for ensure_dependencies()
# -----------------------------------------------------------------------------


@patch("pdftl.utils.dependencies.importlib.util.find_spec")
def test_ensure_dependencies_all_present(mock_find_spec):
    """Test that no exception is raised when dependencies are found."""
    # Simulate that find_spec successfully finds the module
    mock_find_spec.return_value = True

    # Should execute silently without raising InvalidArgumentError
    ensure_dependencies("My Feature", ["some_lib"], "my-feature")


@patch("pdftl.utils.dependencies.is_pipx_install")
@patch("pdftl.utils.dependencies.importlib.util.find_spec")
def test_ensure_dependencies_missing_standard_pip(mock_find_spec, mock_is_pipx):
    """Test the exception message when a user is in a standard pip environment."""
    # Simulate module not found
    mock_find_spec.return_value = None
    mock_is_pipx.return_value = False

    expected_msg = r"Please install with: pip install pdftl\[extra-tag\]"

    with pytest.raises(InvalidArgumentError, match=expected_msg):
        ensure_dependencies("My Feature", ["missing_lib"], "extra-tag")


@patch("pdftl.utils.dependencies.is_pipx_install")
@patch("pdftl.utils.dependencies.importlib.util.find_spec")
def test_ensure_dependencies_missing_pipx(mock_find_spec, mock_is_pipx):
    """Test the exception message when a user is in a pipx environment."""
    # Simulate module not found
    mock_find_spec.return_value = None
    mock_is_pipx.return_value = True

    expected_msg = r"Please install with: pipx inject pdftl pdftl\[extra-tag\]"

    with pytest.raises(InvalidArgumentError, match=expected_msg):
        ensure_dependencies("My Feature", ["missing_lib"], "extra-tag")


@patch("pdftl.utils.dependencies.is_pipx_install")
@patch("pdftl.utils.dependencies.importlib.util.find_spec")
def test_ensure_dependencies_multiple_missing(mock_find_spec, mock_is_pipx):
    """Test that the exception correctly lists multiple missing dependencies."""
    mock_find_spec.return_value = None
    mock_is_pipx.return_value = False

    # Regex matches the "and" joiner logic in deps_str
    expected_msg = r"The 'Advanced' feature requires lib_a and lib_b"

    with pytest.raises(InvalidArgumentError, match=expected_msg):
        ensure_dependencies("Advanced", ["lib_a", "lib_b"], "adv")
