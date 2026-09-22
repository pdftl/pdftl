# tests/operations/test_optimize_images_complete.py

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Import the optimize_images module for testing
import pdftl.operations.optimize_images as optimize_images_module
from pdftl.exceptions import InvalidArgumentError, PackageError, OperationError


# Define genuine exception classes for our mock module to prevent TypeError
class MockMissingDependencyError(Exception):
    pass


class MockSubprocessOutputError(Exception):
    pass


# --- 1. Parameter Parsing Tests ---


def test_optimize_args_keywords():
    """Test standard keyword aliases."""
    # optimize, jpeg, png, jbig2, jobs, jbig2_group_size
    assert optimize_images_module._parse_args_to_options(["low"]) == (1, 0, 0, False, 0, None)
    assert optimize_images_module._parse_args_to_options(["medium"]) == (2, 0, 0, False, 0, None)
    assert optimize_images_module._parse_args_to_options(["high"]) == (3, 0, 0, False, 0, None)
    # 'all' implies max optimize + jbig2
    assert optimize_images_module._parse_args_to_options(["all"]) == (3, 0, 0, True, 0, None)


def test_optimize_args_jbig2_alias():
    """Test JBIG2 aliases."""
    # jbig2_lossy sets boolean to True, leaves optimize at default (2)
    assert optimize_images_module._parse_args_to_options(["jbig2_lossy"]) == (
        2,
        0,
        0,
        True,
        0,
        None,
    )
    assert optimize_images_module._parse_args_to_options(["jb2lossy"]) == (2, 0, 0, True, 0, None)


def test_optimize_args_quality_specific():
    """Test specific jpeg/png quality flags."""
    # jpeg_quality
    opts = optimize_images_module._parse_args_to_options(["jpeg_quality=50"])
    assert opts[1] == 50
    # png_quality
    opts = optimize_images_module._parse_args_to_options(["png_quality=60"])
    assert opts[2] == 60


def test_optimize_args_jpg_quality_alias():
    """Regression: jpg_quality was accepted by the keyval parser but silently dropped."""
    opts = optimize_images_module._parse_args_to_options(["jpg_quality=50"])
    assert opts[1] == 50
    assert opts[2] == 0


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


def test_optimize_args_jbig2_group_size():
    """Explicit integer jbig2_group_size is parsed and returned as-is."""
    opts = optimize_images_module._parse_args_to_options(["jbig2_group_size=5"])
    assert opts[5] == 5


@pytest.mark.parametrize("val", ["all", "infinity", "inf"])
def test_optimize_args_jbig2_group_size_sentinel(val):
    """all/infinity/inf resolve to the sentinel, not an int."""
    opts = optimize_images_module._parse_args_to_options([f"jbig2_group_size={val}"])
    assert opts[5] is optimize_images_module._JBIG2_GROUP_SIZE_ALL


def test_optimize_args_jbig2_group_size_invalid():
    """Non-positive jbig2_group_size is rejected."""
    with pytest.raises(InvalidArgumentError, match="must be a positive integer"):
        optimize_images_module._parse_args_to_options(["jbig2_group_size=0"])


def test_optimize_options_explicit_group_size_overrides_default():
    """Passing jbig2_group_size explicitly bypasses the jb2lossy-based default."""
    opts = optimize_images_module.OptimizeOptions(
        jobs=0, optimize=2, jpeg_quality=0, png_quality=0, jb2lossy=True, jbig2_group_size=3
    )
    assert opts.jbig2_page_group_size == 3


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

    mock_exceptions = types.ModuleType("exceptions")
    mock_exceptions.MissingDependencyError = MockMissingDependencyError
    mock_exceptions.SubprocessOutputError = MockSubprocessOutputError

    # optimize_images_pdf() imports everything from ocrmypdf.optimize inside its
    # own function body on every call, so patching sys.modules is enough on its
    # own - no reload of optimize_images_module needed.
    with patch.dict(
        sys.modules,
        {
            "ocrmypdf": MagicMock(),
            "ocrmypdf.optimize": mock_lib,
            "ocrmypdf.exceptions": mock_exceptions,
        },
    ):
        import pikepdf

        with pikepdf.open(two_page_pdf) as pdf:
            # Call the function (args: pdf, operation_args, output_filename)
            optimize_images_module.optimize_images_pdf(pdf, ["medium"], "out.pdf")

            # Check that it called the library functions
            mock_lib.extract_images_generic.assert_called()


def test_optimize_images_missing_dependency(two_page_pdf):
    """Test that MissingDependencyError is caught and raised as OperationError."""
    mock_lib = MagicMock()
    mock_lib.DEFAULT_JPEG_QUALITY = 0
    mock_lib.DEFAULT_PNG_QUALITY = 0

    mock_exceptions = types.ModuleType("exceptions")
    mock_exceptions.MissingDependencyError = MockMissingDependencyError
    mock_exceptions.SubprocessOutputError = MockSubprocessOutputError

    mock_lib.extract_images_generic.side_effect = MockMissingDependencyError("test missing tool")

    with patch.dict(
        sys.modules,
        {
            "ocrmypdf": MagicMock(),
            "ocrmypdf.optimize": mock_lib,
            "ocrmypdf.exceptions": mock_exceptions,
        },
    ):
        import pikepdf

        with pikepdf.open(two_page_pdf) as pdf:
            with pytest.raises(
                OperationError, match="An external dependency required by OCRmyPDF is missing"
            ):
                optimize_images_module.optimize_images_pdf(pdf, ["medium"], "out.pdf")


def test_optimize_images_subprocess_error(two_page_pdf):
    """Test that SubprocessOutputError is caught and raised as OperationError."""
    mock_lib = MagicMock()
    mock_lib.DEFAULT_JPEG_QUALITY = 0
    mock_lib.DEFAULT_PNG_QUALITY = 0

    mock_exceptions = types.ModuleType("exceptions")
    mock_exceptions.MissingDependencyError = MockMissingDependencyError
    mock_exceptions.SubprocessOutputError = MockSubprocessOutputError

    mock_lib.extract_images_generic.side_effect = MockSubprocessOutputError(
        "test subprocess failure"
    )

    with patch.dict(
        sys.modules,
        {
            "ocrmypdf": MagicMock(),
            "ocrmypdf.optimize": mock_lib,
            "ocrmypdf.exceptions": mock_exceptions,
        },
    ):
        import pikepdf

        with pikepdf.open(two_page_pdf) as pdf:
            with pytest.raises(
                OperationError, match="An external tool executed by OCRmyPDF failed"
            ):
                optimize_images_module.optimize_images_pdf(pdf, ["medium"], "out.pdf")


def test_optimize_images_file_not_found_error(two_page_pdf):
    """Test that FileNotFoundError is caught and raised as OperationError."""
    mock_lib = MagicMock()
    mock_lib.DEFAULT_JPEG_QUALITY = 0
    mock_lib.DEFAULT_PNG_QUALITY = 0

    mock_exceptions = types.ModuleType("exceptions")
    mock_exceptions.MissingDependencyError = MockMissingDependencyError
    mock_exceptions.SubprocessOutputError = MockSubprocessOutputError

    mock_lib.extract_images_generic.side_effect = FileNotFoundError("test executable not found")

    with patch.dict(
        sys.modules,
        {
            "ocrmypdf": MagicMock(),
            "ocrmypdf.optimize": mock_lib,
            "ocrmypdf.exceptions": mock_exceptions,
        },
    ):
        import pikepdf

        with pikepdf.open(two_page_pdf) as pdf:
            with pytest.raises(
                OperationError, match="Failed to execute an underlying system tool"
            ):
                optimize_images_module.optimize_images_pdf(pdf, ["medium"], "out.pdf")


# --- 4. Intermediate files live in a temp dir, not beside the output ---


def _ocrmypdf_modules(mock_lib):
    mock_exceptions = types.ModuleType("exceptions")
    mock_exceptions.MissingDependencyError = MockMissingDependencyError
    mock_exceptions.SubprocessOutputError = MockSubprocessOutputError
    return {
        "ocrmypdf": MagicMock(),
        "ocrmypdf.optimize": mock_lib,
        "ocrmypdf.exceptions": mock_exceptions,
    }


def _mock_lib(extract_side_effect):
    lib = MagicMock()
    lib.DEFAULT_JPEG_QUALITY = 0
    lib.DEFAULT_PNG_QUALITY = 0
    lib.extract_images_generic.side_effect = extract_side_effect
    return lib


def test_optimize_images_works_in_temp_dir_and_leaves_output_dir_alone(two_page_pdf, tmp_path):
    """Regression: intermediates used to go in <output dir>/images and were never removed."""
    import pikepdf

    out_dir = tmp_path / "outdir"
    out_dir.mkdir()
    seen = {}

    def fake_extract(pdf, root, options):
        seen["root"] = root
        seen["existed"] = root.is_dir()
        return [], []

    with patch.dict(sys.modules, _ocrmypdf_modules(_mock_lib(fake_extract))):
        with pikepdf.open(two_page_pdf) as pdf:
            optimize_images_module.optimize_images_pdf(pdf, ["medium"], str(out_dir / "out.pdf"))

    assert seen["existed"]  # the work dir really existed while the helpers ran
    assert seen["root"].parent != out_dir
    assert not seen["root"].exists()  # ... and was removed afterwards
    assert list(out_dir.iterdir()) == []  # nothing (e.g. images/) left beside the output


def test_optimize_images_does_not_touch_existing_images_dir(two_page_pdf, tmp_path):
    import pikepdf

    keep = tmp_path / "images" / "keep.txt"
    keep.parent.mkdir()
    keep.write_text("mine")

    with patch.dict(sys.modules, _ocrmypdf_modules(_mock_lib(lambda *a: ([], [])))):
        with pikepdf.open(two_page_pdf) as pdf:
            optimize_images_module.optimize_images_pdf(pdf, ["medium"], str(tmp_path / "out.pdf"))

    assert [p.name for p in (tmp_path / "images").iterdir()] == ["keep.txt"]
    assert keep.read_text() == "mine"


def test_optimize_images_temp_dir_removed_on_error(two_page_pdf, tmp_path):
    import pikepdf

    seen = {}

    def failing_extract(pdf, root, options):
        seen["root"] = root
        raise MockMissingDependencyError("test missing tool")

    with patch.dict(sys.modules, _ocrmypdf_modules(_mock_lib(failing_extract))):
        with pikepdf.open(two_page_pdf) as pdf:
            with pytest.raises(OperationError):
                optimize_images_module.optimize_images_pdf(
                    pdf, ["medium"], str(tmp_path / "out.pdf")
                )

    assert not seen["root"].exists()


def test_optimize_images_non_debug_level_skips_ocrmypdf_logger_setup(two_page_pdf, tmp_path):
    import pikepdf

    with patch.object(optimize_images_module.logger, "getEffectiveLevel", return_value=20):
        with patch.dict(sys.modules, _ocrmypdf_modules(_mock_lib(lambda *a: ([], [])))):
            with pikepdf.open(two_page_pdf) as pdf:
                res = optimize_images_module.optimize_images_pdf(
                    pdf, ["medium"], str(tmp_path / "out.pdf")
                )
    assert res.success is True


def test_optimize_images_group_size_all_resolves_to_page_count(two_page_pdf, tmp_path):
    """jbig2_group_size=all resolves against the actual page count before use."""
    import pikepdf

    seen = {}

    def fake_extract(pdf_arg, root, options):
        seen["jbig2_page_group_size"] = options.jbig2_page_group_size
        return {}

    mock_lib = _mock_lib(lambda *a: ([], []))
    mock_lib.extract_images_jbig2.side_effect = fake_extract

    with patch.dict(sys.modules, _ocrmypdf_modules(mock_lib)):
        with pikepdf.open(two_page_pdf) as pdf:
            optimize_images_module.optimize_images_pdf(
                pdf, ["jbig2_lossy", "jbig2_group_size=all"], str(tmp_path / "out.pdf")
            )

    assert seen["jbig2_page_group_size"] == len(pdf.pages) == 2
