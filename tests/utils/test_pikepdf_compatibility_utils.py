# tests/utils/test_pikepdf_compatibility_utils.py

import sys
from unittest.mock import MagicMock, patch

from pdftl.utils.pikepdf_compatibility_utils import (
    as_pil_image_compat,
    pikepdf_version,
    pikepdf_version_at_least,
)


def test_pikepdf_version_standard():
    """Test standard semantic version parsing."""
    mock_pikepdf = MagicMock()
    mock_pikepdf.__version__ = "10.10.1"

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        assert pikepdf_version() == [10, 10, 1]


def test_pikepdf_version_with_string_suffix():
    """Test version parsing stops gracefully at non-integer strings (e.g. dev versions)."""
    mock_pikepdf = MagicMock()
    mock_pikepdf.__version__ = "10.0.dev4"

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        assert pikepdf_version() == [10, 0]


def test_pikepdf_version_completely_invalid():
    """Test fallback when the version string contains no parseable integers."""
    mock_pikepdf = MagicMock()
    mock_pikepdf.__version__ = "invalid_version"

    with patch.dict(sys.modules, {"pikepdf": mock_pikepdf}):
        assert pikepdf_version() == [0, 0, 0]


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version")
def test_pikepdf_version_at_least_int(mock_version):
    """Test version comparison when an integer is passed."""
    mock_version.return_value = [9, 0, 0]

    assert pikepdf_version_at_least(9) is True
    assert pikepdf_version_at_least(10) is False


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version")
def test_pikepdf_version_at_least_list(mock_version):
    """Test version comparison when a list is passed."""
    mock_version.return_value = [10, 1, 0]

    assert pikepdf_version_at_least([10, 0, 0]) is True
    assert pikepdf_version_at_least([10, 2, 0]) is False


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version")
def test_pikepdf_version_at_least_invalid_type(mock_version):
    """Test version comparison returns False for unhandled types like strings."""
    mock_version.return_value = [10, 1, 0]

    assert pikepdf_version_at_least("10.0.0") is False


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_as_pil_image_compat_new_pikepdf(mock_version_at_least):
    """Test PIL extraction applies the mask flag for pikepdf >= 10.10.0."""
    mock_version_at_least.return_value = True
    mock_image = MagicMock()

    as_pil_image_compat(mock_image)

    mock_image.as_pil_image.assert_called_once_with(apply_mask=False)


@patch("pdftl.utils.pikepdf_compatibility_utils.pikepdf_version_at_least")
def test_as_pil_image_compat_old_pikepdf(mock_version_at_least):
    """Test PIL extraction skips the mask flag for older pikepdf versions."""
    mock_version_at_least.return_value = False
    mock_image = MagicMock()

    as_pil_image_compat(mock_image)

    mock_image.as_pil_image.assert_called_once_with()
