import pikepdf
import pytest

from pdftl.core.types import OpResult
from pdftl.operations.burst import burst_pdf


def test_burst_basic(two_page_pdf):
    """Test standard bursting of a 2-page PDF."""
    # The fixture returns a path, so we must open it
    with pikepdf.open(two_page_pdf) as pdf:
        # burst_pdf now returns an OpResult
        result = burst_pdf([pdf])

        assert isinstance(result, OpResult)
        assert result.success

        # The generator is inside result.data
        results = list(result.data)

        assert len(results) == 2

        filename1, pdf1 = results[0]
        assert filename1 == "pg_0001.pdf"
        assert len(pdf1.pages) == 1

        filename2, pdf2 = results[1]
        assert filename2 == "pg_0002.pdf"
        assert len(pdf2.pages) == 1


def test_burst_custom_pattern(two_page_pdf):
    """Test that output_pattern argument works."""
    with pikepdf.open(two_page_pdf) as pdf:
        result = burst_pdf([pdf], output_pattern="page_%d.pdf")
        assert result.success

        # Unwrap result.data to access the generator
        results = list(result.data)

        assert len(results) == 2
        assert results[0][0] == "page_1.pdf"
        assert results[1][0] == "page_2.pdf"


def test_burst_invalid_pattern(two_page_pdf):
    """Test that the ValueError is raised for bad patterns."""
    with pikepdf.open(two_page_pdf) as pdf:
        result = burst_pdf([pdf], output_pattern="bad_filename.pdf")

        # The ValueError is raised inside the generator, so we must access
        # result.pdf and try to iterate it to trigger the error.
        with pytest.raises(ValueError, match="Output pattern must include"):
            list(result.data)


def test_burst_multiple_inputs(two_page_pdf):
    """Test passing multiple PDF documents at once."""
    with pikepdf.open(two_page_pdf) as pdf:
        # Pass the same PDF object twice to simulate multiple inputs
        result = burst_pdf([pdf, pdf])
        assert result.success

        # Unwrap result.pdf
        results = list(result.data)

        # 2 pages * 2 inputs = 4 output files
        assert len(results) == 4
        # Counter should increment continuously
        assert results[0][0] == "pg_0001.pdf"
        assert results[3][0] == "pg_0004.pdf"


from unittest.mock import MagicMock, patch


def test_burst_pdf_default_pattern():
    """
    Covers line 78: pattern = "pg_%04d.pdf" when pattern is None.
    """
    # Setup a mock PDF with one page
    mock_pdf = MagicMock()
    mock_page = MagicMock()
    mock_pdf.pages = [mock_page]
    del mock_pdf.Root.PageLabels  # so dump_data call doesn't complain

    # We patch pikepdf.Pdf so that Pdf.new() returns a Mock instead of a real object.
    # This prevents the TypeError when appending our mock_page.
    with patch("pikepdf.Pdf") as MockPdf:
        # Call burst with None. This creates the generator.
        result = burst_pdf([mock_pdf], output_pattern=None)

        # We must iterate the generator to execute the body of the function
        generated_files = list(result.data)

        # Verify the default pattern was applied (pg_0001.pdf)
        assert generated_files[0][0] == "pg_0001.pdf"


from unittest.mock import call

from pdftl.operations.burst import burst_cli_hook


def test_burst_cli_hook_success():
    """Test that the hook iterates the generator and calls pipeline.save_pdf_file."""
    # 1. Setup mocks
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()

    # Create dummy data to be yielded by the generator
    dummy_pdf_1 = MagicMock()
    dummy_pdf_2 = MagicMock()

    def mock_generator():
        yield ("page_1.pdf", dummy_pdf_1)
        yield ("page_2.pdf", dummy_pdf_2)

    # Wrap in OpResult
    result = OpResult(success=True, data=mock_generator())

    # 2. Execute hook
    burst_cli_hook(result, mock_stage, mock_pipeline)

    # 3. Assertions
    # Verify pipeline.save_pdf_file was called for each yielded item
    expected_calls = [
        call(dummy_pdf_1, "page_1.pdf", mock_stage),
        call(dummy_pdf_2, "page_2.pdf", mock_stage),
    ]
    mock_pipeline.save_pdf_file.assert_has_calls(expected_calls)
    assert mock_pipeline.save_pdf_file.call_count == 2


def test_burst_cli_hook_no_generator(caplog):
    """Test the 'if not burst_generator' guard clause (Line 45)."""
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()

    # Result with no data
    result = OpResult(success=True, data=None)

    with caplog.at_level("DEBUG"):
        burst_cli_hook(result, mock_stage, mock_pipeline)

    # Assertions
    assert "No burst_generator" in caplog.text
    mock_pipeline.save_pdf_file.assert_not_called()


def test_burst_cli_hook_empty_generator():
    """Test behavior when the generator is valid but yields nothing."""
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()

    def empty_gen():
        if False:
            yield  # Pythonic way to make an empty generator

    result = OpResult(success=True, data=empty_gen())

    burst_cli_hook(result, mock_stage, mock_pipeline)

    # Assertions
    mock_pipeline.save_pdf_file.assert_not_called()
