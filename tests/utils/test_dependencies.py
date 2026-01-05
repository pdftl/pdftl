from unittest.mock import patch

import pytest

from pdftl.utils.dependencies import ensure_dependencies


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
