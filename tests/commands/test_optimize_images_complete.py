import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Import the logic functions directly for easier testing
from pdftl.commands.optimize_images import _parse_args_to_options, optimize_images_pdf
from pdftl.exceptions import InvalidArgumentError, PackageError

# --- 1. Parameter Parsing Tests (Lines 207-278) ---


def test_optimize_args_keywords():
    """Test standard keyword aliases."""
    # optimize, jpeg, png, jbig2, jobs
    assert _parse_args_to_options(["low"]) == (1, 0, 0, False, 0)
    assert _parse_args_to_options(["medium"]) == (2, 0, 0, False, 0)
    assert _parse_args_to_options(["high"]) == (3, 0, 0, False, 0)
    # 'all' implies max optimize + jbig2
    assert _parse_args_to_options(["all"]) == (3, 0, 0, True, 0)


def test_optimize_args_jbig2_alias():
    """Test JBIG2 aliases (Lines 227-228)."""
    # jbig2_lossy sets boolean to True, leaves optimize at default (2)
    assert _parse_args_to_options(["jbig2_lossy"]) == (2, 0, 0, True, 0)
    assert _parse_args_to_options(["jb2lossy"]) == (2, 0, 0, True, 0)


def test_optimize_args_quality_specific():
    """Test specific jpeg/png quality flags (Lines 239, etc)."""
    # jpeg_quality
    opts = _parse_args_to_options(["jpeg_quality=50"])
    assert opts[1] == 50
    # png_quality (Line 239)
    opts = _parse_args_to_options(["png_quality=60"])
    assert opts[2] == 60


def test_optimize_args_quality_general():
    """Test generic 'quality' flag (Lines 241-242)."""
    # Should set both JPEG and PNG
    opts = _parse_args_to_options(["quality=75"])
    assert opts[1] == 75
    assert opts[2] == 75


def test_optimize_args_jobs():
    """Test jobs flag."""
    opts = _parse_args_to_options(["jobs=4"])
    assert opts[4] == 4


def test_optimize_args_errors():
    """Test invalid inputs (Lines 266, 270)."""
    # 1. Invalid Key (Line 270)
    with pytest.raises(InvalidArgumentError, match="Unrecognized keyword"):
        _parse_args_to_options(["not_a_valid_flag=10"])

    # 2. Invalid Key Value (Garbage)
    with pytest.raises(InvalidArgumentError, match="Unrecognized keyword"):
        _parse_args_to_options(["garbage"])

    # 3. Negative Jobs (Line 266)
    with pytest.raises(InvalidArgumentError, match="cannot be negative"):
        _parse_args_to_options(["jobs=-1"])

    # 4. Invalid Quality Range
    with pytest.raises(InvalidArgumentError, match="integer between 0 and 100"):
        _parse_args_to_options(["quality=150"])

    # 5. Non-integer value
    with pytest.raises(InvalidArgumentError, match="Could not convert"):
        _parse_args_to_options(["quality=high"])


# --- 2. Import Error Logic (Lines 35-40, 125-130) ---


def test_optimize_images_import_failure():
    """
    Test that a proper PackageError is raised when ocrmypdf is missing.
    """
    # Force import failure for 'ocrmypdf'
    with patch.dict(sys.modules, {"ocrmypdf": None, "ocrmypdf.optimize": None}):

        # We also need to mock the inputs since we are calling the function directly
        mock_pdf = MagicMock()

        # The assertion: Does calling the function raise the expected error?
        with pytest.raises(PackageError, match="Loading OCRmyPDF failed"):
            optimize_images_pdf(mock_pdf, [], "dummy_out.pdf")


# --- 3. Success Logic (Mocked) ---


def test_optimize_images_success(two_page_pdf):
    """Test the success path by mocking the installed library."""
    mock_lib = MagicMock()
    mock_lib.DEFAULT_JPEG_QUALITY = 0
    mock_lib.DEFAULT_PNG_QUALITY = 0
    mock_lib.extract_images_generic.return_value = ([], [])

    with patch.dict(sys.modules, {"ocrmypdf": MagicMock(), "ocrmypdf.optimize": mock_lib}):
        # Reload to hit the 'try' block successfully
        import pdftl.commands.optimize_images

        importlib.reload(pdftl.commands.optimize_images)

        import pikepdf

        with pikepdf.open(two_page_pdf) as pdf:
            # Call the function (args: pdf, operation_args, output_filename)
            pdftl.commands.optimize_images.optimize_images_pdf(pdf, ["medium"], "out.pdf")

            # Check that it called the library functions
            mock_lib.extract_images_generic.assert_called()
