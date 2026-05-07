from unittest.mock import MagicMock, call, patch

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError, OperationError
from pdftl.operations.burst import burst_cli_hook, burst_pdf


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
    """Test that the InvalidArgumentError is raised for bad patterns."""
    with pikepdf.open(two_page_pdf) as pdf:
        result = burst_pdf([pdf], output_pattern="bad_filename.pdf")

        # The ValueError is raised inside the generator, so we must access
        # result.data (the result pdf) and try to iterate it to trigger the error.
        with pytest.raises(InvalidArgumentError, match="Output pattern must include"):
            list(result.data)


def test_burst_multiple_inputs(two_page_pdf):
    """Test passing multiple PDF documents at once."""
    with pikepdf.open(two_page_pdf) as pdf:
        # Pass the same PDF object twice to simulate multiple inputs
        result = burst_pdf([pdf, pdf])
        assert result.success

        # Unwrap result.data (the result pdf)
        results = list(result.data)

        # 2 pages * 2 inputs = 4 output files
        assert len(results) == 4
        # Counter should increment continuously
        assert results[0][0] == "pg_0001.pdf"
        assert results[3][0] == "pg_0004.pdf"


def test_burst_pdf_default_pattern(two_page_pdf):
    """
    Covers line 78: pattern = "pg_%04d.pdf" when pattern is None.
    """
    with pikepdf.open(two_page_pdf) as pdf:
        result = burst_pdf([pdf], output_pattern=None)

        # We must iterate the generator to execute the body of the function
        generated_files = list(result.data)

        # Verify the default pattern was applied (pg_0001.pdf)
        assert generated_files[0][0] == "pg_0001.pdf"


def test_burst_cli_hook_success():
    """Test that the hook iterates the generator and calls pipeline.save_pdf_file."""
    # 1. Setup mocks
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()

    # Create dummy data to be yielded by the generator
    dummy_pdf_1 = MagicMock()
    dummy_pdf_2 = MagicMock()
    dummy_input_pdf = pikepdf.new()

    def mock_generator():
        yield ("page_1.pdf", dummy_pdf_1)
        yield ("page_2.pdf", dummy_pdf_2)

    # Wrap in OpResult
    result = OpResult(success=True, data=mock_generator(), pdf=dummy_input_pdf)

    # 2. Execute hook
    with patch("pdftl.operations.burst.pdftl.api.dump_data") as mock_dump:
        burst_cli_hook(result, mock_stage, mock_pipeline)
        mock_dump.assert_called_once_with(result.pdf, output="doc_data.txt", run_cli_hook=True)

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
    mock_pipeline = MagicMock()
    mock_stage = MagicMock()
    # Create a mock that satisfies isinstance(obj, pikepdf.Pdf)
    mock_pdf = MagicMock(spec=pikepdf.Pdf)

    def empty_gen():
        if False:
            yield

    # Pass the mock_pdf here!
    result = OpResult(success=True, data=empty_gen(), pdf=mock_pdf)

    with patch("pdftl.operations.burst.pdftl.api.dump_data") as mock_dump:
        burst_cli_hook(result, mock_stage, mock_pipeline)
        mock_dump.assert_called_once_with(result.pdf, output="doc_data.txt", run_cli_hook=True)

    # Assertions
    mock_pipeline.save_pdf_file.assert_not_called()


# --- Imports from your module ---
from pdftl.operations.burst import get_effective_specs


@pytest.fixture
def mock_pdf():
    """Provides a dummy PDF object for the tests."""
    return MagicMock(name="SourcePDF")


# --- 1. Testing Valid Specs ---
@pytest.mark.parametrize(
    "input_specs, mock_pages, expected_calls, expected_output",
    [
        # Case 1: Standard specs are ignored and passed through
        (["1-5", "odd", "even"], [], [], ["1-5", "odd", "even"]),
        # Case 2: Basic "level" spec is processed (case-insensitive)
        (["level2"], [1, 5, 12], [(2, False)], ["1,5,12"]),
        (["LEVEL2"], [1, 5, 12], [(2, False)], ["1,5,12"]),
        # Case 3: "level<n>onl" spec is processed (last_level_only=True)
        (["level3only"], [4, 8], [(3, True)], ["4,8"]),
        (["Level3Only"], [4, 8], [(3, True)], ["4,8"]),
        # Case 4: Mixed list of standard specs and level specs
        (["1-3", "level1", "end-5"], [10, 20], [(1, False)], ["1-3", "10,20", "end-5"]),
        # Case 5: Multiple level specs in the same list
        (["level1", "level2only"], [7, 9], [(1, False), (2, True)], ["7,9", "7,9"]),
    ],
)
@patch("pdftl.operations.burst.get_outlines_to_level_pages")
def test_get_effective_specs_valid(
    mock_get_pages, mock_pdf, input_specs, mock_pages, expected_calls, expected_output
):
    # Setup the mock to return our predetermined list of pages
    mock_get_pages.return_value = mock_pages

    # Run the function
    result = get_effective_specs(mock_pdf, input_specs)

    # Assert the output matches exactly what we expect
    assert result == expected_output

    # Assert the mocked helper was called the correct number of times
    assert mock_get_pages.call_count == len(expected_calls)

    # Assert the mocked helper was called with the correct arguments
    for level, eq_flag in expected_calls:
        mock_get_pages.assert_any_call(mock_pdf, level, last_level_only=eq_flag)


# --- 2. Testing Invalid Inputs and Exceptions ---
@pytest.mark.parametrize(
    "bad_spec, expected_error_match",
    [
        ("level0", "must be at least 1"),
        ("level-5", "must be at least 1"),
        ("levelabc", "invalid literal for int"),
        ("levelXYZonly", "invalid literal for int"),
    ],
)
@patch("pdftl.operations.burst.get_outlines_to_level_pages")
def test_get_effective_specs_invalid(mock_get_pages, mock_pdf, bad_spec, expected_error_match):
    # We don't expect the helper to ever be called because it should fail during parsing
    with pytest.raises(InvalidArgumentError, match=expected_error_match):
        get_effective_specs(mock_pdf, [bad_spec])

    mock_get_pages.assert_not_called()


import pytest

from pdftl.operations.burst import _parse_size_to_bytes


def test_parse_size_to_bytes():
    # Test Megabytes
    assert _parse_size_to_bytes("5M") == 5 * 1024 * 1024
    assert _parse_size_to_bytes("2.5MB") == int(2.5 * 1024 * 1024)

    # Test Kilobytes
    assert _parse_size_to_bytes("500K") == 500 * 1024
    assert _parse_size_to_bytes("100.5KB") == int(100.5 * 1024)

    # Test Raw Bytes
    assert _parse_size_to_bytes("1048576") == 1048576

    # Test Error Handling (Lines 299-300)
    with pytest.raises(InvalidArgumentError, match="Invalid size format"):
        _parse_size_to_bytes("5GB")  # Unsupported unit

    with pytest.raises(InvalidArgumentError, match="Invalid size format"):
        _parse_size_to_bytes("not_a_number")


def test_burst_multiple_sizes_raises_error():
    # You may need to mock 'opened_pdfs' depending on your existing fixtures.
    # A simple list with a dummy object usually works for argument routing tests.
    mock_pdfs = ["dummy_pdf_object"]

    with pytest.raises(InvalidArgumentError, match="More than one `size` spec passed"):
        burst_pdf(mock_pdfs, operation_args=["size5M", "size10M"])


import logging

from pdftl.operations.burst import _generate_burst_chunks, get_chunk_size


@pytest.fixture
def dummy_5_page_pdf():
    """Helper fixture to generate a real, empty 5-page PDF in memory."""
    pdf = pikepdf.Pdf.new()
    for _ in range(5):
        pdf.add_blank_page(page_size=(612, 792))
    return pdf


def test_burst_page_exceeds_max_size_warning(dummy_5_page_pdf, caplog):
    """Tests lines 240-250 where a single page is bigger than max_bytes."""

    # Force max_bytes to be ridiculously small (10 bytes) so even 1 page fails the limit
    max_bytes = 10

    with caplog.at_level(logging.WARNING):
        generator = _generate_burst_chunks(
            opened_pdfs=[dummy_5_page_pdf],
            specs=["1"],
            output_pattern="foo%03d.pdf",
            max_bytes=max_bytes,
        )
        results = list(generator)

    # CHANGED: It processed a 5 page PDF where every page failed the size check,
    # so it should fallback to yielding all 5 individual pages.
    assert len(results) == 5
    assert len(results[0][1].pages) == 1

    # Ensure the warning was triggered
    assert "exceeds the maximum limit" in caplog.text


def test_burst_by_size_binary_search(dummy_5_page_pdf):
    # PDF libraries heavily compress/deduplicate identical blank pages.
    # To guarantee a max of 2 pages, we measure the exact sizes dynamically.
    two_page_size = get_chunk_size(dummy_5_page_pdf, 0, 1)
    three_page_size = get_chunk_size(dummy_5_page_pdf, 0, 2)

    # Set max_bytes right between a 2-page and 3-page document
    max_bytes = (two_page_size + three_page_size) // 2

    generator = _generate_burst_chunks(
        opened_pdfs=[dummy_5_page_pdf],
        specs=["1"],
        output_pattern="chunk_%02d.pdf",
        max_bytes=max_bytes,
    )

    results = list(generator)

    # We expect 5 pages split into chunks of max 2 pages -> 3 total chunks (2, 2, 1)
    assert len(results) == 3

    assert results[0][0] == "chunk_01.pdf"
    assert len(results[0][1].pages) == 2

    assert results[1][0] == "chunk_02.pdf"
    assert len(results[1][1].pages) == 2

    assert results[2][0] == "chunk_03.pdf"
    assert len(results[2][1].pages) == 1


def test_burst_pdf_standard_spec_routing():
    """Covers line 165 by routing a standard, non-size spec through the wrapper."""
    mock_pdfs = ["dummy_pdf_object"]

    # Passing "1-3" triggers the `else` block (line 165)
    # Passing "size5M" triggers the `if` block
    result = burst_pdf(mock_pdfs, operation_args=["1-3", "size5M"])

    assert result.success is True
    assert result.pdf == "dummy_pdf_object"


def test_burst_cli_hook_raises_on_missing_pdf():
    # Pass a result where success is True but pdf is None
    result = OpResult(success=True, data=[("file.pdf", MagicMock())], pdf=None)
    with pytest.raises(OperationError, match="Invalid result: not a PDF"):
        burst_cli_hook(result, MagicMock(), MagicMock())
