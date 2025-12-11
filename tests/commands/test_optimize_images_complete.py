import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# Import the logic functions directly for easier testing
from pdftl.commands.optimize_images import _parse_args_to_options
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
    Test the behavior when ocrmypdf cannot be imported.
    We must force an ImportError during module reload.
    """
    # 1. Mock sys.modules to raise ImportError when 'ocrmypdf' is accessed
    # We remove it from modules so importlib tries to load it,
    # and use a side_effect on the loader or just patch the import mechanism?
    # Easier: Patch sys.modules with an object that raises ImportError on access?
    # Actually, simpler: patch sys.modules['ocrmypdf'] to None usually indicates 'not found'
    # but the code does `from ocrmypdf.optimize import ...`.

    with patch.dict(sys.modules, {"ocrmypdf.optimize": None, "ocrmypdf": None}):
        # Use a side_effect to force the specific import failure logic
        # We need to target the `from` import statement.
        # The cleanest way in pytest is often to just ensure it's NOT in sys.modules
        # and let the real import fail (if not installed).
        # IF IT IS INSTALLED, we need to break it.

        # We will wrap the builtin __import__ to fail for this specific module
        real_import = __import__

        def fail_import(name, *args, **kwargs):
            if "ocrmypdf" in name:
                raise ImportError("Mocked failure")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fail_import):
            # Reload the module under test so it hits the `except ImportError` block
            import pdftl.commands.optimize_images

            importlib.reload(pdftl.commands.optimize_images)

            # Check if the flag was set (Line 37)
            assert pdftl.commands.optimize_images.OCRMYPDF_IMPORT_FAILED is True

            # Now verify that calling the function raises PackageError (Lines 125-130)
            with pytest.raises(PackageError, match="Loading OCRmyPDF failed"):
                # We call the function that was registered when import failed
                pdftl.commands.optimize_images.optimize_images_pdf()


# --- 3. Success Logic (Mocked) ---


def test_optimize_images_success(two_page_pdf):
    """Test the success path by mocking the installed library."""
    mock_lib = MagicMock()
    mock_lib.DEFAULT_JPEG_QUALITY = 0
    mock_lib.DEFAULT_PNG_QUALITY = 0
    mock_lib.extract_images_generic.return_value = ([], [])

    with patch.dict(
        sys.modules, {"ocrmypdf": MagicMock(), "ocrmypdf.optimize": mock_lib}
    ):
        # Reload to hit the 'try' block successfully
        import pdftl.commands.optimize_images

        importlib.reload(pdftl.commands.optimize_images)

        # Verify flag
        assert pdftl.commands.optimize_images.OCRMYPDF_IMPORT_FAILED is False

        import pikepdf

        with pikepdf.open(two_page_pdf) as pdf:
            # Call the function (args: pdf, operation_args, output_filename)
            pdftl.commands.optimize_images.optimize_images_pdf(
                pdf, ["medium"], "out.pdf"
            )

            # Check that it called the library functions
            mock_lib.extract_images_generic.assert_called()
