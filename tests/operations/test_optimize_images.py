# tests/operations/test_optimize_images_complete.py

import sys
from unittest.mock import MagicMock, patch

import pytest

# Import the optimize_images module for testing
import pdftl.operations.optimize_images as optimize_images_module
from pdftl.exceptions import InvalidArgumentError, PackageError

# --- 1. Parameter Parsing Tests ---


def test_optimize_args_keywords():
    """Test standard keyword aliases."""
    # optimize, jpeg, png, jbig2, jobs
    assert optimize_images_module._parse_args_to_options(["low"]) == (1, 0, 0, False, 0)
    assert optimize_images_module._parse_args_to_options(["medium"]) == (2, 0, 0, False, 0)
    assert optimize_images_module._parse_args_to_options(["high"]) == (3, 0, 0, False, 0)
    # 'all' implies max optimize + jbig2
    assert optimize_images_module._parse_args_to_options(["all"]) == (3, 0, 0, True, 0)


def test_optimize_args_jbig2_alias():
    """Test JBIG2 aliases."""
    # jbig2_lossy sets boolean to True, leaves optimize at default (2)
    assert optimize_images_module._parse_args_to_options(["jbig2_lossy"]) == (2, 0, 0, True, 0)
    assert optimize_images_module._parse_args_to_options(["jb2lossy"]) == (2, 0, 0, True, 0)


def test_optimize_args_quality_specific():
    """Test specific jpeg/png quality flags."""
    # jpeg_quality
    opts = optimize_images_module._parse_args_to_options(["jpeg_quality=50"])
    assert opts[1] == 50
    # png_quality
    opts = optimize_images_module._parse_args_to_options(["png_quality=60"])
    assert opts[2] == 60


def test_optimize_args_quality_general():
    """Test generic 'quality' flag."""
    # Should set both JPEG and PNG
    opts = optimize_images_module._parse_args_to_options(["quality=75"])
    assert opts[1] == 75
    assert opts[2] == 75


def test_optimize_args_jobs():
    """Test jobs flag."""
    opts = optimize_images_module._parse_args_to_options(["jobs=4"])
    assert opts[4] == 4


def test_optimize_args_errors():
    """Test invalid inputs."""
    # 1. Invalid Key
    with pytest.raises(InvalidArgumentError, match="Unrecognized keyword"):
        optimize_images_module._parse_args_to_options(["not_a_valid_flag=10"])

    # 2. Invalid Key Value (Garbage)
    with pytest.raises(InvalidArgumentError, match="Unrecognized keyword"):
        optimize_images_module._parse_args_to_options(["garbage"])

    # 3. Negative Jobs
    with pytest.raises(InvalidArgumentError, match="cannot be negative"):
        optimize_images_module._parse_args_to_options(["jobs=-1"])

    # 4. Invalid Quality Range
    with pytest.raises(InvalidArgumentError, match="integer between 0 and 100"):
        optimize_images_module._parse_args_to_options(["quality=150"])

    # 5. Non-integer value
    with pytest.raises(InvalidArgumentError, match="Could not convert"):
        optimize_images_module._parse_args_to_options(["quality=high"])


# --- 2. Import Error Logic  ---


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
            optimize_images_module.optimize_images_pdf(mock_pdf, [], "dummy_out.pdf")


# --- 3. Success Logic (Mocked) ---


def test_optimize_images_success(two_page_pdf):
    """Test the success path by mocking the installed library."""
    mock_lib = MagicMock()
    mock_lib.DEFAULT_JPEG_QUALITY = 0
    mock_lib.DEFAULT_PNG_QUALITY = 0
    mock_lib.extract_images_generic.return_value = ([], [])

    # optimize_images_pdf() imports everything from ocrmypdf.optimize inside its
    # own function body on every call, so patching sys.modules is enough on its
    # own - no reload of optimize_images_module needed.
    with patch.dict(sys.modules, {"ocrmypdf": MagicMock(), "ocrmypdf.optimize": mock_lib}):
        import pikepdf

        with pikepdf.open(two_page_pdf) as pdf:
            # Call the function (args: pdf, operation_args, output_filename)
            optimize_images_module.optimize_images_pdf(pdf, ["medium"], "out.pdf")

            # Check that it called the library functions
            mock_lib.extract_images_generic.assert_called()
